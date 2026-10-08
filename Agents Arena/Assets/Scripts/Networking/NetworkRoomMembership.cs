using System;
using Actor.Characters;
using Actor.World;
using Unity.Collections;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    [RequireComponent(typeof(ActorCharacter), typeof(NetworkObject), typeof(CapsuleCollider2D))]
    [DefaultExecutionOrder(50)]
    public sealed class NetworkRoomMembership : NetworkBehaviour
    {
        public struct Membership : INetworkSerializable, IEquatable<Membership>
        {
            public FixedString64Bytes Room;
            public uint Revision;

            public Membership(string room, uint revision)
            { Room = new FixedString64Bytes(room); Revision = revision; }

            public void NetworkSerialize<T>(BufferSerializer<T> serializer) where T : IReaderWriter
            { serializer.SerializeValue(ref Room); serializer.SerializeValue(ref Revision); }

            public bool Equals(Membership other) => Room.Equals(other.Room) && Revision == other.Revision;
        }

        private readonly NetworkVariable<Membership> membership = new(default,
            NetworkVariableReadPermission.Everyone, NetworkVariableWritePermission.Server);
        private ActorCharacter actor;
        private CapsuleCollider2D feet;
        private bool initialized;

        public string CurrentRoomId => membership.Value.Room.Length > 0 ? membership.Value.Room.ToString() : RoomId.MainHall;
        public uint Revision => membership.Value.Revision;
        public event Action<string, string, uint> RoomChanged;

        private void Awake()
        {
            actor = GetComponent<ActorCharacter>();
            feet = GetComponent<CapsuleCollider2D>();
        }

        public override void OnNetworkSpawn()
        {
            initialized = false;
            membership.OnValueChanged += OnMembershipChanged;
            if (IsServer)
            {
                string initial = RoomVolume2D.TryResolve(feet.bounds.center, null, out string room) ? room : RoomId.MainHall;
                membership.Value = new Membership(initial, 0);
            }
            actor.SetCurrentRoom(CurrentRoomId);
            initialized = true;
        }

        private void LateUpdate()
        {
            if (!IsSpawned || !IsServer) return;
            // Room truth follows the post-physics capsule center. Predicted presentation
            // and commands from external clients cannot write this replicated state.
            string previous = CurrentRoomId;
            if (!RoomVolume2D.TryResolve(feet.bounds.center, previous, out string room) || room == previous) return;
            membership.Value = new Membership(room, unchecked(membership.Value.Revision + 1));
        }

        private void OnMembershipChanged(Membership previous, Membership current)
        {
            actor.SetCurrentRoom(current.Room.ToString());
            if (initialized && previous.Room.Length > 0 && !previous.Room.Equals(current.Room))
                RoomChanged?.Invoke(previous.Room.ToString(), current.Room.ToString(), current.Revision);
        }

        public override void OnNetworkDespawn()
        {
            initialized = false;
            membership.OnValueChanged -= OnMembershipChanged;
        }
    }
}
