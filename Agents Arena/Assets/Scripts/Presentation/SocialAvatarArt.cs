using System.Collections.Generic;
using Actor.Networking;
using UnityEngine;

namespace Actor.Presentation
{
    // Local cosmetic state only. The same stable ActorId chooses a mantle on all
    // permitted clients; alignment, duty, input source and names are never read.
    public sealed class SocialAvatarArt : MonoBehaviour
    {
#if !UNITY_SERVER
        private NetworkCharacter character;
        private Animator animator;
        private SpriteRenderer renderer, mark;
        private RuntimeAnimatorController previousController;
        private Material previousMaterial;
        private MaterialPropertyBlock properties;
        private string appliedId;
        private int appliedIndex=-1;
        private static readonly Dictionary<string,int> cosmetics=new();
        internal static void ClearCosmetics()=>cosmetics.Clear();
        internal static bool TryCosmetic(string actor,out int index)=>cosmetics.TryGetValue(actor,out index);
        private Vector3 originalScale;
        private double impulseUntil;
        public void Impulse(){if(!Actor.UI.ArenaUI.ReducedMotion)impulseUntil=Time.realtimeSinceStartupAsDouble+.14;}
        public static Color Fabric(int index)
        {
            string[] colors={"#95B6A4","#D9947E","#A7ACC9","#E9C781","#B5C0C2","#819CA7"};
            ColorUtility.TryParseHtmlString(colors[Mathf.Clamp(index,0,5)],out var color);return color;
        }
        private void Awake()
        {
            character=GetComponent<NetworkCharacter>();var visual=transform.Find("Visual");if(visual==null)return;
            animator=visual.GetComponent<Animator>();renderer=visual.GetComponent<SpriteRenderer>();
            if(animator!=null)previousController=animator.runtimeAnimatorController;if(renderer!=null)previousMaterial=renderer.sharedMaterial;
            if(renderer!=null)originalScale=renderer.transform.localScale;
        }
        private void LateUpdate()
        {
            if(character==null||!character.IsSpawned||renderer==null||animator==null)return;
            if(!SocialVisibilitySystem.IsSocial(character.NetworkManager))return;
            var art=AtmosphericArtCatalog.Load();string id=character.AuthoritativeActorId;
            if(art==null||string.IsNullOrEmpty(id))return;
            int index=character.CosmeticIndex;
            if(appliedId!=id||appliedIndex!=index)
            {
                appliedId=id;appliedIndex=index;
                if(cosmetics.Count<64||cosmetics.ContainsKey(id))cosmetics[id]=index;
                var state=animator.GetCurrentAnimatorStateInfo(0);animator.runtimeAnimatorController=art.characterController;
                animator.Play(state.fullPathHash,0,state.normalizedTime%1);renderer.sharedMaterial=art.paletteMaterial;
                properties??=new MaterialPropertyBlock();renderer.GetPropertyBlock(properties);properties.SetColor("_SkinColor",Fabric(index));renderer.SetPropertyBlock(properties);
                if(mark==null){var root=new GameObject("Cosmetic Stitch");root.transform.SetParent(renderer.transform,false);mark=root.AddComponent<SpriteRenderer>();}
                mark.sprite=art.identityMarks[index];mark.sortingLayerID=renderer.sortingLayerID;mark.sortingOrder=renderer.sortingOrder+1;
                mark.transform.localPosition=new Vector3(0,.39f,-.001f);mark.transform.localScale=Vector3.one*.45f;
            }
            float impulse=Mathf.Clamp01((float)((impulseUntil-Time.realtimeSinceStartupAsDouble)/.14));
            float pulse=Mathf.Sin(impulse*Mathf.PI);renderer.transform.localScale=Vector3.Scale(originalScale,new Vector3(1+.045f*pulse,1-.035f*pulse,1));
            var facing=character.Facing;mark.enabled=renderer.enabled&&renderer.gameObject.activeInHierarchy
                &&facing!=Actor.Characters.FacingDirection.Up&&facing!=Actor.Characters.FacingDirection.UpLeft&&facing!=Actor.Characters.FacingDirection.UpRight;
        }
        private void OnDestroy()
        {
            if(animator!=null)animator.runtimeAnimatorController=previousController;
            if(renderer!=null){renderer.sharedMaterial=previousMaterial;renderer.SetPropertyBlock(null);renderer.transform.localScale=originalScale;}if(mark!=null)Destroy(mark.gameObject);
        }
#endif
    }
}
