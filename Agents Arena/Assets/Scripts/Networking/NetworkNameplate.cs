using UnityEngine;
#if !UNITY_SERVER
using Actor.UI;
using UnityEngine.UIElements;
#endif

namespace Actor.Networking
{
    [RequireComponent(typeof(NetworkCharacter))]
    public sealed class NetworkNameplate : MonoBehaviour
    {
#if !UNITY_SERVER
        private NetworkCharacter character;
        private Transform visual;
        private Label label;
        private void Awake(){character=GetComponent<NetworkCharacter>();visual=transform.Find("Visual");}
        private void LateUpdate()
        {
            if(character==null||!character.IsSpawned||visual==null||!visual.gameObject.activeInHierarchy||string.IsNullOrEmpty(character.PlayerName))
            {if(label!=null)ArenaUI.Show(label,false);return;}
            var layer=ArenaUI.WorldLayer;if(layer==null)return;
            label??=new Label{enableRichText=false,pickingMode=PickingMode.Ignore};if(label.parent!=layer)layer.Add(label);
            label.text=character.PlayerName;ArenaUI.WorldLabel(label,character.PresentationPosition+Vector3.up*1.1f,180);
        }
        private void OnDisable(){label?.RemoveFromHierarchy();label=null;}
        private void OnDestroy(){label?.RemoveFromHierarchy();label=null;}
#endif
    }
}
