using System;
using System.Collections.Generic;
using UnityEngine;

namespace Actor.World
{
    // Public authored map data. RootPosition is the transform/motor coordinate;
    // terminal InteractionPoint is a capsule-center coordinate instead.
    public sealed class SocialWorldDefinition : MonoBehaviour
    {
        [Serializable] public struct Node { public string id, roomId; public Vector2 rootPosition; }
        [Serializable] public struct Edge { public string from, to; }
        [SerializeField] private Node[] nodes = Array.Empty<Node>();
        [SerializeField] private Edge[] edges = Array.Empty<Edge>();
        [SerializeField] private Vector2 capsuleOffset;
        public IReadOnlyList<Node> Nodes => nodes;
        public IReadOnlyList<Edge> Edges => edges;
        public Vector2 CapsuleOffset => capsuleOffset;
        public void Configure(Node[] authoredNodes, Edge[] authoredEdges, Vector2 offset)
        { nodes = authoredNodes; edges = authoredEdges; capsuleOffset = offset; }
    }
}
