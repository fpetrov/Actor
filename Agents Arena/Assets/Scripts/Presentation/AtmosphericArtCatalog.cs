using UnityEngine;

namespace Actor.Presentation
{
    // Presentation references only: no role, task, authority or network state.
    public sealed class AtmosphericArtCatalog : ScriptableObject
    {
        public AnimatorOverrideController characterController;
        public Material paletteMaterial;
        public Sprite floor, wall, reactor, serviceLight;
        [System.Serializable] public sealed class Landmark { public Sprite sprite; public Vector2 world; }
        public Landmark[] landmarks;
        public Sprite[] terminals, folds, identityMarks;
        public AudioClip[] cues;
        public static AtmosphericArtCatalog Load() => Resources.Load<AtmosphericArtCatalog>("Art/Atmospheric");
    }
}
