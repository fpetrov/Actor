using System;
using System.Collections.Generic;
using Actor.Protocol.Core.V1;
using Google.Protobuf;
using Google.Protobuf.WellKnownTypes;

namespace Actor.Sdk
{
    // Called by the game thread. Transport code only dispatches buffered commands here.
    public sealed class ActorCapabilityRegistry
    {
        private sealed class Registration
        {
            public CapabilityDefinition Definition;
            public ExtensionPackage Package;
            public Func<ObservationContext, IMessage> Capture;
            public Func<CommandContext, Any, CommandOutcome> Handle;
        }

        private readonly SortedDictionary<string, ExtensionPackage> packages =
            new SortedDictionary<string, ExtensionPackage>(StringComparer.Ordinal);
        private readonly SortedDictionary<string, Registration> capabilities =
            new SortedDictionary<string, Registration>(StringComparer.Ordinal);

        public event Action<GameEventEnvelope> EventPublished;

        public void RegisterExtension(IActorExtension extension)
        {
            if (extension == null) throw new ArgumentNullException(nameof(extension));
            ExtensionPackage package = extension.Package ?? throw new ArgumentException("Extension package is required.");
            if (packages.ContainsKey(package.Id)) throw new InvalidOperationException("Duplicate extension: " + package.Id);
            if (package.SupportedProtocolMajor != ActorProtocol.Major)
                throw new InvalidOperationException("Unsupported extension protocol major: " + package.Id);

            // Commit atomically only after the extension has successfully registered everything.
            var scope = new ScopedRegistrar(this, package);
            extension.Register(scope);
            foreach (string id in scope.Registrations.Keys)
                if (capabilities.ContainsKey(id)) throw new InvalidOperationException("Duplicate capability: " + id);
            packages.Add(package.Id, package);
            foreach (var entry in scope.Registrations) capabilities.Add(entry.Key, entry.Value);
        }

        public CapabilityManifest BuildManifest(GameIdentity identity, string selfActorId)
        {
            if (identity == null) throw new ArgumentNullException(nameof(identity));
            var manifest = new CapabilityManifest
            {
                GameId = identity.GameId,
                GameVersion = identity.GameVersion,
                SelfActorId = Require.Text(selfActorId, nameof(selfActorId)),
                ProtocolVersion = ActorProtocol.Version
            };
            foreach (ExtensionPackage package in packages.Values) manifest.Extensions.Add(package.ToManifest());
            foreach (Registration registration in capabilities.Values)
                manifest.Capabilities.Add(registration.Definition.Clone());
            return manifest;
        }

        public ObservationFrame CaptureObservations(string selfActorId, string entityId, ulong revision)
        {
            var context = new ObservationContext(Require.Text(selfActorId, nameof(selfActorId)),
                Require.Text(entityId, nameof(entityId)), revision);
            var frame = new ObservationFrame { Revision = revision };
            foreach (Registration registration in capabilities.Values)
            {
                if (registration.Capture == null) continue;
                IMessage payload = registration.Capture(context);
                if (payload == null) continue; // Provider can explicitly omit unavailable observations.
                frame.Observations.Add(new ObservationEnvelope
                {
                    CapabilityId = registration.Definition.CapabilityId,
                    ExtensionId = registration.Package.Id,
                    ExtensionVersion = registration.Package.Version,
                    EntityId = context.EntityId,
                    Payload = Any.Pack(payload)
                });
            }
            return frame;
        }

        public bool TryGetCapability(string capabilityId, out CapabilityDefinition definition)
        {
            if (capabilityId != null && capabilities.TryGetValue(capabilityId, out Registration registration))
            { definition = registration.Definition.Clone(); return true; }
            definition = null;
            return false;
        }

        public CommandResult DispatchCommand(CommandEnvelope command, string selfActorId, long nowUnixMs)
        {
            if (command == null) throw new ArgumentNullException(nameof(command));
            ulong id = command.CommandId;
            if (id == 0) return Reject(id, "MalformedCommand", "Command ID must be nonzero.");
            if (command.CalculateSize() > ActorProtocol.MaxMessageBytes)
                return Reject(id, "MessageTooLarge", "Command exceeds the message size limit.");
            if (!capabilities.TryGetValue(command.CapabilityId, out Registration registration) || registration.Handle == null)
                return Reject(id, "UnknownCapability", "Command capability is not registered.");
            if (command.ExtensionId != registration.Package.Id || command.ExtensionVersion != registration.Package.Version)
                return Reject(id, "ExtensionVersionMismatch", "Command extension identity/version does not match the manifest.");
            if (command.TargetActorId != selfActorId || string.IsNullOrEmpty(selfActorId))
                return Reject(id, "ActorNotControlled", "Only the locally controlled actor accepts commands.");
            if (command.Payload == null || command.Payload.TypeUrl != registration.Definition.InputTypeUrl)
                return Reject(id, "TypeUrlMismatch", "Command payload does not match its capability type.");
            if (command.TtlMs == 0 || command.TtlMs > ActorProtocol.MaxCommandTtlMs || command.IssuedAtUnixMs <= 0 ||
                command.IssuedAtUnixMs > long.MaxValue - command.TtlMs || command.IssuedAtUnixMs > nowUnixMs + 1000)
                return Reject(id, "MalformedCommand", "Command timestamp/TTL is invalid.");
            if (nowUnixMs >= command.IssuedAtUnixMs + command.TtlMs)
                return Reject(id, "CommandTimedOut", "Command expired before game-thread dispatch.", CommandStatus.TimedOut);

            try
            {
                CommandOutcome outcome = registration.Handle(new CommandContext(command), command.Payload);
                if (outcome == null) return Reject(id, "CommandHandlerFailed", "Command handler returned no outcome.");
                var result = new CommandResult { CommandId = id, Status = outcome.Status };
                if (outcome.Status != CommandStatus.Accepted)
                    result.Error = new ProtocolError { Code = outcome.ErrorCode, Message = outcome.Message, CommandId = id };
                if (outcome.Payload != null) result.Payload = Any.Pack(outcome.Payload);
                return result;
            }
            catch (InvalidProtocolBufferException)
            { return Reject(id, "MalformedCommand", "Command payload is not valid Protobuf."); }
            catch (Exception)
            { return Reject(id, "CommandHandlerFailed", "Game command handler failed."); }
        }

        private static CommandResult Reject(ulong id, string code, string message,
            CommandStatus status = CommandStatus.Rejected) => new CommandResult
        {
            CommandId = id,
            Status = status,
            Error = new ProtocolError { Code = code, Message = message, CommandId = id }
        };

        private sealed class ScopedRegistrar : IActorExtensionRegistrar
        {
            private readonly ActorCapabilityRegistry owner;
            private readonly ExtensionPackage package;
            public readonly Dictionary<string, Registration> Registrations = new Dictionary<string, Registration>(StringComparer.Ordinal);
            public ScopedRegistrar(ActorCapabilityRegistry owner, ExtensionPackage package)
            { this.owner = owner; this.package = package; }

            private Registration Add<T>(string id, string description, CapabilityKind kind,
                CommandDelivery delivery = CommandDelivery.Unspecified) where T : IMessage<T>, new()
            {
                Require.Text(id, nameof(id));
                if (Registrations.ContainsKey(id)) throw new InvalidOperationException("Duplicate capability: " + id);
                if (!package.ContainsMessage<T>())
                    throw new InvalidOperationException("Registered message is absent from, or incompatible with, extension descriptors: " + ActorProtocol.TypeUrl<T>());
                string typeUrl = ActorProtocol.TypeUrl<T>();
                var registration = new Registration
                {
                    Package = package,
                    Definition = new CapabilityDefinition
                    {
                        CapabilityId = id,
                        Description = description ?? "",
                        Version = package.Version,
                        Kind = kind,
                        ExtensionId = package.Id,
                        InputTypeUrl = kind == CapabilityKind.Command ? typeUrl : "",
                        OutputTypeUrl = kind == CapabilityKind.Command ? "" : typeUrl,
                        CommandDelivery = delivery
                    }
                };
                Registrations.Add(id, registration);
                return registration;
            }

            public void RegisterObservation<T>(string capabilityId, string description,
                Func<ObservationContext, T> capture) where T : IMessage<T>, new()
            {
                if (capture == null) throw new ArgumentNullException(nameof(capture));
                Add<T>(capabilityId, description, CapabilityKind.Observation).Capture = context => capture(context);
            }

            public void RegisterCommand<T>(string capabilityId, string description,
                Func<CommandContext, T, CommandOutcome> handle,
                CommandDelivery delivery = CommandDelivery.Ordered) where T : IMessage<T>, new()
            {
                if (handle == null) throw new ArgumentNullException(nameof(handle));
                if (delivery != CommandDelivery.Ordered && delivery != CommandDelivery.Latest)
                    throw new ArgumentOutOfRangeException(nameof(delivery));
                Add<T>(capabilityId, description, CapabilityKind.Command, delivery).Handle =
                    (context, payload) => handle(context, payload.Unpack<T>());
            }

            public ActorEventPublisher<T> RegisterEvent<T>(string capabilityId, string description)
                where T : IMessage<T>, new()
            {
                Add<T>(capabilityId, description, CapabilityKind.Event);
                return new ActorEventPublisher<T>(capabilityId, package, envelope => owner.EventPublished?.Invoke(envelope));
            }
        }
    }

    public sealed class ActorEventPublisher<T> where T : IMessage<T>, new()
    {
        private readonly string capabilityId;
        private readonly ExtensionPackage package;
        private readonly Action<GameEventEnvelope> publish;
        internal ActorEventPublisher(string capabilityId, ExtensionPackage package, Action<GameEventEnvelope> publish)
        { this.capabilityId = capabilityId; this.package = package; this.publish = publish; }

        public void Publish(T payload, string entityId, ulong revision)
        {
            if (payload == null) throw new ArgumentNullException(nameof(payload));
            publish(new GameEventEnvelope
            {
                Revision = revision,
                CapabilityId = capabilityId,
                ExtensionId = package.Id,
                ExtensionVersion = package.Version,
                EntityId = Require.Text(entityId, nameof(entityId)),
                Payload = Any.Pack(payload)
            });
        }
    }
}
