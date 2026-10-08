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
        public static event System.Action<NetworkCharacter> LocalOwnerSpawned;
        private sealed class RemoteCommands : IMovementInputSource
        {
            public Vector2 Value;
            public float ReceivedAt = -100;
            public float Timeout;
            public Vector2 ReadMovement() => Time.unscaledTime - ReceivedAt <= Timeout ? Value : Vector2.zero;
        }

        [SerializeField, Range(10, 60)] private float inputSendRate = 30f;
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
        private Vector2 lastSentInput;
        private uint appliedSequence, snapshotSequence;
        private float appliedSeconds, nextSnapshot;
        private OwnerMovementPrediction prediction;
        private Transform visual;
        private Vector2 authoritativePosition, authoritativeVelocity;
        private FacingDirection authoritativeFacing = FacingDirection.Down;
        private bool hasAuthoritativeSnapshot;
        private uint authoritativeSnapshotSequence;
        private float lastAuthoritativeSnapshotAt;

        public string PlayerName => playerName.Value.ToString();
        public Vector2 Velocity => prediction != null ? prediction.Velocity : IsServer ? motor.Velocity : velocity.Value;
        public FacingDirection Facing => prediction != null ? prediction.Facing : IsServer ? motor.Facing : (FacingDirection)facing.Value;
        public bool IsMoving => Velocity.sqrMagnitude > 0.0025f;
        public float MaxSpeed => motor.MaxSpeed;
        public Vector3 PresentationPosition => visual != null ? visual.position : transform.position;
        // A coherent post-physics sample for external adapters, independent of interpolation/prediction.
        public Vector2 AuthoritativePosition => IsServer ? motor.Position : authoritativePosition;
        public Vector2 AuthoritativeVelocity => IsServer ? motor.Velocity : authoritativeVelocity;
        public FacingDirection AuthoritativeFacing => IsServer ? motor.Facing : authoritativeFacing;
        public string AuthoritativeActorId => actorId.Value.ToString();
        public bool HasAuthoritativeSnapshot => IsServer || hasAuthoritativeSnapshot;
        public float AuthoritativeSnapshotAge => IsServer ? 0 : Time.unscaledTime - lastAuthoritativeSnapshotAt;

        private void Awake()
        {
            actor = GetComponent<ActorCharacter>();
            motor = GetComponent<CharacterMotor2D>();
            keyboard = GetComponent<PlayerInputSource>();
            visual = transform.Find("Visual");
        }

        public override void OnNetworkSpawn()
        {
            authoritativePosition = transform.position;
            authoritativeVelocity = Vector2.zero;
            hasAuthoritativeSnapshot = false;
            authoritativeSnapshotSequence = 0;
            lastAuthoritativeSnapshotAt = -100;
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
                if (IsOwner)
                    prediction = new OwnerMovementPrediction(motor, GetComponent<CapsuleCollider2D>(), transform.position);
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
            if (IsOwner && Camera.main != null) Camera.main.GetComponent<CameraFollow2D>()?.Follow(visual != null ? visual : transform);
            if (IsClient && IsOwner) LocalOwnerSpawned?.Invoke(this);
#endif
        }

        private void OnIdentityChanged(FixedString64Bytes previous, FixedString64Bytes current)
        {
            if (current.Length > 0) actor.SetIdentity(current.ToString());
        }

        public void SetLocalInputSource(IMovementInputSource source) => localCommands = source;

        private void Update()
        {
            if (!IsSpawned || !IsClient || !IsOwner) return;
            Vector2 intent = localCommands?.ReadMovement() ?? Vector2.zero;
            if (!float.IsFinite(intent.x) || !float.IsFinite(intent.y)) intent = Vector2.zero;
            intent = Vector2.ClampMagnitude(intent, 1f);
            float now = Time.unscaledTime;
            // Press/release/direction changes bypass the periodic send interval.
            if (now >= nextSend || (intent - lastSentInput).sqrMagnitude > 0.0001f)
            {
                nextSend = now + 1f / inputSendRate;
                lastSentInput = intent;
                ++sequence;
                prediction?.Record(sequence, intent, now);
                SubmitMovementRpc(intent, sequence);
            }
            prediction?.Advance(intent, now);
        }

        [Rpc(SendTo.Server, Delivery = RpcDelivery.Unreliable, InvokePermission = RpcInvokePermission.Owner)]
        private void SubmitMovementRpc(Vector2 input, uint inputSequence)
        {
            if (!IsServer || !float.IsFinite(input.x) || !float.IsFinite(input.y)) return;
            if (hasSequence && unchecked((int)(inputSequence - lastSequence)) <= 0) return;
            // Discard excess commands instead of accumulating a reliable movement queue.
            if (Time.unscaledTime - lastAcceptedAt < 1f / 120f) return;
            hasSequence = true;
            lastSequence = inputSequence;
            lastAcceptedAt = Time.unscaledTime;
            remoteCommands.Value = Vector2.ClampMagnitude(input, 1f);
            remoteCommands.ReceivedAt = lastAcceptedAt;
        }

        private void FixedUpdate()
        {
            if (!IsSpawned || !IsServer) return;
            // Tag the intent actually used by the upcoming physics step, not a newly arrived RPC.
            if (appliedSequence != lastSequence) { appliedSequence = lastSequence; appliedSeconds = 0; }
            appliedSeconds += Time.fixedDeltaTime;
            motor.SetDesiredMovement(remoteCommands.ReadMovement());
        }

        private void LateUpdate()
        {
            if (!IsSpawned) return;
            if (!IsServer)
            {
                if (prediction != null && visual != null)
                {
                    Vector2 position = prediction.RenderedPosition;
                    visual.position = new Vector3(position.x, position.y, transform.position.z);
                }
                return;
            }
            velocity.Value = motor.Velocity;
            facing.Value = (byte)motor.Facing;
            if (Time.unscaledTime >= nextSnapshot)
            {
                nextSnapshot = Time.unscaledTime + 1f / 30f;
                ReconcileOwnerRpc(++snapshotSequence, appliedSequence, appliedSeconds, motor.Position, motor.Velocity, (byte)motor.Facing);
            }
        }

        [Rpc(SendTo.Owner, Delivery = RpcDelivery.Unreliable, InvokePermission = RpcInvokePermission.Server)]
        private void ReconcileOwnerRpc(uint snapshot, uint acknowledgedInput, float seconds,
            Vector2 position, Vector2 authoritativeVelocity, byte direction)
        {
            if (hasAuthoritativeSnapshot && unchecked((int)(snapshot - authoritativeSnapshotSequence)) <= 0) return;
            authoritativeSnapshotSequence = snapshot;
            lastAuthoritativeSnapshotAt = Time.unscaledTime;
            authoritativePosition = position;
            this.authoritativeVelocity = authoritativeVelocity;
            authoritativeFacing = (FacingDirection)direction;
            hasAuthoritativeSnapshot = true;
            prediction?.Reconcile(snapshot, acknowledgedInput, seconds, position, authoritativeVelocity,
                (FacingDirection)direction, Time.unscaledTime);
        }

        public override void OnNetworkDespawn()
        {
            actorId.OnValueChanged -= OnIdentityChanged;
            keyboard.enabled = false;
            actor.SetInputSource(null);
            prediction = null;
            hasAuthoritativeSnapshot = false;
        }
    }
}
