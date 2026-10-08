using System;
using Actor.Networking;
using Actor.Protocol.Core.V1;
using Actor.Protocol.Standard.V1;
using Actor.Sdk;
using AgentsArena.ActorProtocol.V1;
using UnityEngine;

namespace Actor.GameAdapter
{
    public sealed class AgentsArenaGameAdapter : IActorGameAdapter, IDisposable
    {
        private readonly NetworkCharacter character;
        private readonly AgentInputSource input;
        private readonly NetworkRoomMembership membership;
        private readonly Func<bool> agentControlEnabled;
        private AgentsArenaActorExtension arenaExtension;
        public GameIdentity Identity { get; }
        public string SelfActorId { get; }
        internal bool HasFreshState => character != null && character.IsSpawned && character.HasAuthoritativeSnapshot
            && character.AuthoritativeSnapshotAge <= 0.5f;
        internal string CurrentRoom => membership.CurrentRoomId;

        public AgentsArenaGameAdapter(NetworkCharacter character, AgentInputSource input, string gameVersion, Func<bool> agentControlEnabled)
        {
            this.character = character;
            this.input = input;
            this.agentControlEnabled = agentControlEnabled;
            membership = character.GetComponent<NetworkRoomMembership>();
            Identity = new GameIdentity("agents-arena", gameVersion);
            SelfActorId = character.AuthoritativeActorId;
        }

        public void Register(ActorCapabilityRegistry registry)
        {
            registry.RegisterExtension(new StandardLocomotionExtension(this));
            arenaExtension = new AgentsArenaActorExtension(this);
            registry.RegisterExtension(arenaExtension);
            membership.RoomChanged += OnRoomChanged;
        }

        internal Transform2DObservation CaptureTransform()
        {
            if (!HasFreshState) return null;
            Vector2 position = character.AuthoritativePosition;
            float angle = (int)character.AuthoritativeFacing * Mathf.PI / 4;
            return new Transform2DObservation { X = position.x, Y = position.y, FacingX = Mathf.Sin(angle), FacingY = -Mathf.Cos(angle) };
        }
        internal Motion2DObservation CaptureMotion()
        {
            if (!HasFreshState) return null;
            Vector2 velocity = character.AuthoritativeVelocity;
            return new Motion2DObservation { VelocityX = velocity.x, VelocityY = velocity.y, IsMoving = velocity.sqrMagnitude > 0.0025f };
        }
        internal CommandOutcome Move(CommandContext context, Locomotion2DCommand command)
        {
            if (!agentControlEnabled() || character == null || !character.IsSpawned || !character.IsOwner)
                return CommandOutcome.Rejected("ActorNotControlled", "ACTOR control is not enabled for this owned actor.");
            if (!HasFreshState)
            { input.Clear(); return CommandOutcome.Rejected("AuthoritativeStateUnavailable", "A fresh game-server state is required for agent control."); }
            return input.Apply(context, command);
        }
        private void OnRoomChanged(string previous, string current, uint revision)
            => arenaExtension.Publish(previous, current, SelfActorId, revision);
        public void OnDisconnected() { if (input != null) input.Clear(); }
        public void Dispose()
        {
            if (membership != null) membership.RoomChanged -= OnRoomChanged;
            OnDisconnected();
        }

        private sealed class StandardLocomotionExtension : IActorExtension
        {
            private readonly AgentsArenaGameAdapter game;
            public ExtensionPackage Package { get; } = ActorStandardExtensionPackage.Create();
            public StandardLocomotionExtension(AgentsArenaGameAdapter game) => this.game = game;
            public void Register(IActorExtensionRegistrar registry)
            {
                registry.RegisterObservation<Transform2DObservation>("actor.standard.transform2d",
                    "Coherent server-simulated position and facing from an owner physics snapshot.", _ => game.CaptureTransform());
                registry.RegisterObservation<Motion2DObservation>("actor.standard.motion2d",
                    "Velocity and movement flag from the same authoritative physics snapshot.", _ => game.CaptureMotion());
                registry.RegisterCommand<Locomotion2DCommand>("actor.standard.locomotion2d",
                    "Latest normalized movement intent for the owned multiplayer character.", game.Move, CommandDelivery.Latest);
            }
        }
    }

    public sealed class AgentsArenaActorExtension : IActorExtension
    {
        private readonly AgentsArenaGameAdapter game;
        private ActorEventPublisher<RoomChangedEvent> changed;
        public ExtensionPackage Package { get; } = AgentsArenaExtensionPackage.Create();
        public AgentsArenaActorExtension(AgentsArenaGameAdapter game) => this.game = game;
        public void Register(IActorExtensionRegistrar registry)
        {
            registry.RegisterObservation<RoomMembershipObservation>("agents-arena.room-membership",
                "Server-determined room containing this actor.", _ => new RoomMembershipObservation { RoomId = game.CurrentRoom });
            changed = registry.RegisterEvent<RoomChangedEvent>("agents-arena.room-changed", "Authoritative room membership changed.");
        }
        public void Publish(string previous, string current, string actorId, uint revision)
            => changed.Publish(new RoomChangedEvent { PreviousRoomId = previous, CurrentRoomId = current }, actorId, revision);
    }
}
