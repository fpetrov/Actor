using UnityEngine;
#if !UNITY_SERVER
using System.Collections.Generic;
using Actor.Presentation;
using Actor.UI;
using UnityEngine.UIElements;
#endif

namespace Actor.Networking
{
    [RequireComponent(typeof(NetworkCharacter))]
    public sealed class NetworkNameplate : MonoBehaviour
    {
#if !UNITY_SERVER
        private static readonly List<NetworkNameplate> Active = new(NameplateLayoutRules.Capacity);
        private static readonly NameplateLayoutRules.Entry[] Layout = new NameplateLayoutRules.Entry[NameplateLayoutRules.Capacity];
        private static int layoutFrame = -1;
        private NetworkCharacter character;
        private Transform visual;
        private Label label;
        private void Awake(){character=GetComponent<NetworkCharacter>();visual=transform.Find("Visual");}
        private void OnEnable(){Active.Add(this);}
        private bool Prepare(VisualElement layer)
        {
            if(character==null||!character.IsSpawned||visual==null||!visual.gameObject.activeInHierarchy||string.IsNullOrEmpty(character.PlayerName))
            {if(label!=null)ArenaUI.Show(label,false);return false;}
            label??=new Label{enableRichText=false,pickingMode=PickingMode.Ignore};if(label.parent!=layer)layer.Add(label);
            label.AddToClassList("player-nameplate");
            label.text=character.PlayerName;ArenaUI.WorldLabel(label,character.PresentationPosition+Vector3.up*1.1f,NameplateLayoutRules.Width);
            return label.style.display.value!=DisplayStyle.None;
        }
        private void LateUpdate()
        {
            if(layoutFrame==Time.frameCount)return;
            var layer=ArenaUI.WorldLayer;if(layer==null||layer.panel==null)return;
            layoutFrame=Time.frameCount;int count=0;
            for(int i=0;i<Active.Count;i++)
            {
                var item=Active[i];if(item==null||!item.isActiveAndEnabled||!item.Prepare(layer))continue;
                if(count==Layout.Length){ArenaUI.Show(item.label,false);continue;}
                Layout[count++]=new NameplateLayoutRules.Entry{Id=item.character.NetworkObjectId,SourceIndex=i,
                    X=item.label.style.left.value.value,Y=item.label.style.top.value.value};
            }
            for(int i=count;i<Layout.Length;i++)Layout[i]=default;
            NameplateLayoutRules.Arrange(Layout,count,layer.contentRect.width,layer.contentRect.height);
            for(int i=0;i<count;i++)
            {
                var entry=Layout[i];var item=Active[entry.SourceIndex];
                if(!entry.Placed){ArenaUI.Show(item.label,false);continue;}
                item.label.style.left=entry.X;item.label.style.top=entry.Y;
            }
        }
        private void Release(){Active.Remove(this);label?.RemoveFromHierarchy();label=null;}
        private void OnDisable(){Release();}
        private void OnDestroy(){Release();}
#endif
    }
}
