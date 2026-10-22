using UnityEngine;
#if !UNITY_SERVER
using Actor.UI;
using UnityEngine.UIElements;
#endif

namespace Actor.World
{
    [ExecuteAlways]
    public sealed class RoomWayfinding2D : MonoBehaviour
    {
        [System.Serializable] public struct Marker { public Vector2 world; public string text; }
        [SerializeField] private Marker[] markers;
        public void Configure(Marker[] values){markers=values;Clear();}
#if !UNITY_SERVER
        private Label[] labels;
        private void LateUpdate()
        {
            if(!Application.isPlaying||markers==null)return;var layer=ArenaUI.WorldLayer;if(layer==null)return;
            if(labels==null||labels.Length!=markers.Length){Clear();labels=new Label[markers.Length];}
            for(int i=0;i<markers.Length;++i)
            {
                labels[i]??=new Label{enableRichText=false,pickingMode=PickingMode.Ignore};
                if(labels[i].parent!=layer)layer.Add(labels[i]);labels[i].text=markers[i].text;
                ArenaUI.WorldLabel(labels[i],markers[i].world,156);
            }
        }
        private void Clear(){if(labels!=null)foreach(var label in labels)label?.RemoveFromHierarchy();labels=null;}
        private void OnDisable()=>Clear();
        private void OnDestroy()=>Clear();
#else
        private void Clear() { }
#endif
    }
}
