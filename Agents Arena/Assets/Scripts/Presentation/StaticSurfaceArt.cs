using Actor.Networking;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Presentation
{
    // Four immutable room landmarks only. Dynamic sprites remain behind the
    // original fog and use the original server observer policy.
    [RequireComponent(typeof(SpriteRenderer)), DisallowMultipleComponent]
    public sealed class StaticSurfaceArt : MonoBehaviour
    {
        [SerializeField] private Vector2 frontStart, frontEnd, frontNormal;
        [SerializeField] private bool volume;
        [SerializeField] private CircleCollider2D ownHousing;
        private SpriteRenderer view;
#if !UNITY_SERVER
        private readonly RaycastHit2D[] hits = new RaycastHit2D[128];
        private readonly MaterialPropertyBlock block = new();
        private System.Func<float,float,bool> clearQuery;
        private Vector2 eye;
        public void Configure(Vector2 start,Vector2 end,Vector2 normal)
        {frontStart=start;frontEnd=end;frontNormal=normal;volume=false;}
        public void ConfigureHousing(CircleCollider2D housing){volume=true;ownHousing=housing;}
        private void Awake()
        {
            view=GetComponent<SpriteRenderer>();
            clearQuery=Clear;
            if(Application.isBatchMode||SystemInfo.graphicsDeviceType==UnityEngine.Rendering.GraphicsDeviceType.Null
                ||GetComponentInParent<NetworkObject>()!=null){view.enabled=false;enabled=false;return;}
            view.enabled=false;
        }
        private void LateUpdate()
        {
            if(view==null)return;
            var own=NetworkSocialMatch.LocalOwner;
            bool allowed=own!=null&&own.IsSpawned&&own.IsOwner&&own.IsClient&&own.HasOwnState
                &&SocialVisibilitySystem.IsSocial(own.NetworkManager);
            if(!allowed){view.enabled=false;return;}
            var character=own.GetComponent<NetworkCharacter>();var capsule=own.GetComponent<CapsuleCollider2D>();
            if(character==null||capsule==null){view.enabled=false;return;}
            eye=(Vector2)character.PresentationPosition+capsule.offset;
            float radius=(float)own.State.VisibilityRadius;var bounds=view.bounds;
            if(!float.IsFinite(eye.x)||!float.IsFinite(eye.y)||!float.IsFinite(radius)||radius<=0)
            {view.enabled=false;return;}
            if(volume)
            {
                allowed=ownHousing!=null&&ownHousing.isActiveAndEnabled
                    &&ownHousing.GetComponent<SocialVisibilityOccluder>()!=null;
                if(allowed)
                {
                    Vector2 face=ownHousing.ClosestPoint(eye);Vector2 outward=(eye-face).normalized;
                    allowed=outward.sqrMagnitude>.5f&&Clear(face.x+outward.x*.06f,face.y+outward.y*.06f)
                        &&StaticSurfaceRules.Bounds(eye.x,eye.y,radius,bounds.min.x,bounds.min.y,bounds.max.x,bounds.max.y,clearQuery);
                }
            }
            else allowed=StaticSurfaceRules.Segment(eye.x,eye.y,radius,frontStart.x,frontStart.y,frontEnd.x,frontEnd.y,
                frontNormal.x,frontNormal.y,clearQuery);
            view.enabled=allowed;if(!allowed)return;
            view.sortingOrder=2001;view.GetPropertyBlock(block);
            block.SetVector("_SurfaceEye",new Vector4(eye.x,eye.y,radius,volume?0:1));
            block.SetVector("_SurfaceStart",new Vector4(frontStart.x,frontStart.y,0,0));
            block.SetVector("_SurfaceEnd",new Vector4(frontEnd.x,frontEnd.y,0,0));
            view.SetPropertyBlock(block);
        }
        private bool Clear(float x,float y)
        {
            int count=Physics2D.Linecast(eye,new Vector2(x,y),new ContactFilter2D{useTriggers=false},hits);
            if(count==hits.Length)return false;
            for(int i=0;i<count;++i)
            {
                var hit=hits[i].collider;
                if(volume&&hit==ownHousing)continue; // Never exempt a wall or another housing.
                if(hit!=null&&hit.GetComponentInParent<SocialVisibilityOccluder>() is {isActiveAndEnabled:true})return false;
            }
            return true;
        }
        private void OnDisable(){if(view!=null)view.enabled=false;}
#else
        public void Configure(Vector2 start,Vector2 end,Vector2 normal){frontStart=start;frontEnd=end;frontNormal=normal;volume=false;}
        public void ConfigureHousing(CircleCollider2D housing){volume=true;ownHousing=housing;}
#endif
    }
}
