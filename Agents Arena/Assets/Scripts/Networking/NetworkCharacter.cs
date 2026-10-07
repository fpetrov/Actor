using Actor.Characters;
using Actor.Presentation;
using Unity.Collections;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    [RequireComponent(typeof(ActorCharacter), typeof(CharacterMotor2D), typeof(NetworkObject))]
    [DefaultExecutionOrder(-50)]
    public sealed class NetworkCharacter : NetworkBehaviour, ICharacterMotionState
    {
        private sealed class RemoteCommands : IMovementInputSource
        {
            public Vector2 Value;
            public float ReceivedAt = -100;
            public float Timeout;
            public Vector2 ReadMovement() => Time.unscaledTime - ReceivedAt <= Timeout ? Value : Vector2.zero;
        }

        [SerializeField, Range(10, 60)] private float inputSendRate = 20f;
        [SerializeField, Range(0.1f, 1f)] private float staleInputTimeout = 0.25f;
        private readonly NetworkVariable<Vector2> velocity = new();
        private readonly NetworkVariable<byte> facing = new((byte)FacingDirection.Down);
        private readonly NetworkVariable<FixedString64Bytes> actorId = new();
        private readonly NetworkVariable<FixedString128Bytes> playerName = new();
        private ActorCharacter actor;
        private CharacterMotor2D motor;
        private PlayerInputSource keyboard;
        private IMovementInputSource localCommands;
        private RemoteCommands remoteCommands;
        private float nextSend;
        private uint sequence;
        private uint lastSequence;
        private bool hasSequence;
        private float lastAcceptedAt = -100;

        public string PlayerName => playerName.Value.ToString();
        public Vector2 Velocity => IsServer ? motor.Velocity : velocity.Value;
        public FacingDirection Facing => IsServer ? motor.Facing : (FacingDirection)facing.Value;
        public bool IsMoving => Velocity.sqrMagnitude > 0.0025f;
        public float MaxSpeed => motor.MaxSpeed;

        private void Awake()
        {
            actor = GetComponent<ActorCharacter>();
            motor = GetComponent<CharacterMotor2D>();
            keyboard = GetComponent<PlayerInputSource>();
        }

        public override void OnNetworkSpawn()
        {
            actor.SetCurrentRoom("main-hall");
            motor.enabled = IsServer;
            actor.enabled = IsServer;
            keyboard.enabled = IsClient && IsOwner;
            localCommands = keyboard;
            if (IsServer)
            {
                remoteCommands = new RemoteCommands { Timeout = staleInputTimeout };
                actor.SetInputSource(remoteCommands);
                actorId.Value = new FixedString64Bytes(actor.ActorId);
                var session = NetworkManager.GetComponent<MatchSession>();
                if (session.TryGetApprovedPlayer(OwnerClientId, out var claims))
                    playerName.Value = new FixedString128Bytes(claims.playerName);
            }
            else
            {
                actor.SetMotionState(this);
                foreach (var view in GetComponentsInChildren<CharacterAnimationView>()) view.SetMotionState(this);
                actorId.OnValueChanged += OnIdentityChanged;
                if (actorId.Value.Length > 0) actor.SetIdentity(actorId.Value.ToString());
            }
#if UNITY_SERVER
            foreach (var view in GetComponentsInChildren<CharacterAnimationView>()) view.enabled = false;
            foreach (var renderer in GetComponentsInChildren<Renderer>()) renderer.enabled = false;
            foreach (var animator in GetComponentsInChildren<Animator>()) animator.enabled = false;
#else
            if (IsOwner && Camera.main != null) Camera.main.GetComponent<CameraFollow2D>()?.Follow(transform);
#endif
        }

        private void OnIdentityChanged(FixedString64Bytes previous, FixedString64Bytes current)
        {
            if (current.Length > 0) actor.SetIdentity(current.ToString());
        }

        public void SetLocalInputSource(IMovementInputSource source) => localCommands = source;

        private void Update()
        {
            if (!IsSpawned || !IsClient || !IsOwner || Time.unscaledTime < nextSend) return;
            nextSend = Time.unscaledTime + 1f / inputSendRate;
            Vector2 intent = localCommands?.ReadMovement() ?? Vector2.zero;
            if (!float.IsFinite(intent.x) || !float.IsFinite(intent.y)) intent = Vector2.zero;
            SubmitMovementRpc(Vector2.ClampMagnitude(intent, 1f), ++sequence);
        }

        [Rpc(SendTo.Server, Delivery = RpcDelivery.Unreliable, InvokePermission = RpcInvokePermission.Owner)]
        private void SubmitMovementRpc(Vector2 input, uint inputSequence)
        {
            if (!IsServer || !float.IsFinite(input.x) || !float.IsFinite(input.y)) return;
            if (hasSequence && unchecked((int)(inputSequence - lastSequence)) <= 0) return;
            // Discard excess commands instead of accumulating a reliable movement queue.
            if (Time.unscaledTime - lastAcceptedAt < 1f / 60f) return;
            hasSequence = true;
            lastSequence = inputSequence;
            lastAcceptedAt = Time.unscaledTime;
            remoteCommands.Value = Vector2.ClampMagnitude(input, 1f);
            remoteCommands.ReceivedAt = lastAcceptedAt;
        }

        private void LateUpdate()
        {
            if (!IsSpawned || !IsServer) return;
            velocity.Value = motor.Velocity;
            facing.Value = (byte)motor.Facing;
        }

        public override void OnNetworkDespawn()
        {
            actorId.OnValueChanged -= OnIdentityChanged;
            keyboard.enabled = false;
            actor.SetInputSource(null);
        }
    }
}
