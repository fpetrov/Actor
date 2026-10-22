using Actor.Networking;
using UnityEngine;

namespace Actor.Presentation
{
    [RequireComponent(typeof(SocialBody))]
    public sealed class SocialBodyArt : MonoBehaviour
    {
#if !UNITY_SERVER
        private SocialBody body;
        private SpriteRenderer view,mark;
        private string applied;
        private int appliedIndex=-1;
        private void Awake(){body=GetComponent<SocialBody>();view=GetComponentInChildren<SpriteRenderer>();}
        private void LateUpdate()
        {
            if(view==null||body==null||!body.IsSpawned)return;
            var art=AtmosphericArtCatalog.Load();if(art==null)return;
            if(applied!=body.VictimId||appliedIndex!=body.CosmeticIndex)
            {
                applied=body.VictimId;appliedIndex=body.CosmeticIndex;view.sprite=art.folds[2];view.sharedMaterial=art.paletteMaterial;view.color=Color.white;
                view.transform.localRotation=Quaternion.identity;view.transform.localScale=Vector3.one;view.transform.localPosition=Vector3.zero;
                view.sortingOrder=-1;
                if(mark==null){var root=new GameObject("Cosmetic Stitch");root.transform.SetParent(view.transform,false);root.transform.localPosition=new Vector3(.13f,-.1f,-.001f);root.transform.localScale=Vector3.one*.28f;mark=root.AddComponent<SpriteRenderer>();mark.sortingOrder=0;}
                mark.sprite=art.identityMarks[appliedIndex];
                var block=new MaterialPropertyBlock();block.SetColor("_SkinColor",SocialAvatarArt.Fabric(appliedIndex));view.SetPropertyBlock(block);
            }
            view.enabled=!SocialKillPresentation.IsAnimating(body.BodyId);if(mark!=null)mark.enabled=view.enabled;
        }
#endif
    }
}
