using System;
using Actor.Protocol.Core.V1;
using Google.Protobuf;

namespace Actor.Sdk
{
    public sealed class GameIdentity
    {
        public string GameId { get; }
        public string GameVersion { get; }
        public GameIdentity(string gameId, string gameVersion)
        {
            GameId = Require.Text(gameId, nameof(gameId));
            GameVersion = Require.Text(gameVersion, nameof(gameVersion));
        }
    }

    public interface IActorGameAdapter
    {
        GameIdentity Identity { get; }
        string SelfActorId { get; }
        void Register(ActorCapabilityRegistry registry);
        void OnDisconnected();
    }

    public interface IActorExtension
    {
        ExtensionPackage Package { get; }
        void Register(IActorExtensionRegistrar registry);
    }

    // Created by the root registry for one extension. Types and envelopes are inferred.
    public interface IActorExtensionRegistrar
    {
        void RegisterObservation<T>(string capabilityId, string description,
            Func<ObservationContext, T> capture) where T : IMessage<T>, new();

        void RegisterCommand<T>(string capabilityId, string description,
            Func<CommandContext, T, CommandOutcome> handle,
            CommandDelivery delivery = CommandDelivery.Ordered) where T : IMessage<T>, new();

        ActorEventPublisher<T> RegisterEvent<T>(string capabilityId, string description)
            where T : IMessage<T>, new();
    }

    public sealed class ObservationContext
    {
        public string SelfActorId { get; }
        public string EntityId { get; }
        public ulong Revision { get; }
        public ObservationContext(string selfActorId, string entityId, ulong revision)
        { SelfActorId = selfActorId; EntityId = entityId; Revision = revision; }
    }

    public sealed class CommandContext
    {
        public string ActorId { get; }
        public ulong CommandId { get; }
        public long IssuedAtUnixMs { get; }
        public long ExpiresAtUnixMs { get; }
        internal CommandContext(CommandEnvelope command)
        {
            ActorId = command.TargetActorId;
            CommandId = command.CommandId;
            IssuedAtUnixMs = command.IssuedAtUnixMs;
            ExpiresAtUnixMs = command.IssuedAtUnixMs + command.TtlMs;
        }
    }

    public sealed class CommandOutcome
    {
        public CommandStatus Status { get; }
        public string ErrorCode { get; }
        public string Message { get; }
        public IMessage Payload { get; }
        private CommandOutcome(CommandStatus status, string errorCode, string message, IMessage payload)
        { Status = status; ErrorCode = errorCode; Message = message; Payload = payload; }

        public static CommandOutcome Accepted(IMessage payload = null) =>
            new CommandOutcome(CommandStatus.Accepted, "", "", payload);
        public static CommandOutcome Rejected(string code, string message) =>
            new CommandOutcome(CommandStatus.Rejected, code, message, null);
    }

    public static class ActorProtocol
    {
        public const uint Major = 1;
        public const uint Minor = 0;
        public const int MaxMessageBytes = 1024 * 1024;
        public const uint MaxCommandTtlMs = 5000;
        public const string TypeUrlPrefix = "type.googleapis.com/";
        public static ProtocolVersion Version => new ProtocolVersion { Major = Major, Minor = Minor };
        public static string TypeUrl<T>() where T : IMessage<T>, new() =>
            TypeUrlPrefix + ((IMessage)new T()).Descriptor.FullName;
    }

    internal static class Require
    {
        public static string Text(string value, string argument)
        {
            if (string.IsNullOrWhiteSpace(value)) throw new ArgumentException("Value cannot be empty.", argument);
            return value;
        }
    }
}
