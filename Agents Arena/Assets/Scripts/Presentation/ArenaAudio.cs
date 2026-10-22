using UnityEngine;
#if !UNITY_SERVER
using System.Collections;
using Actor.UI;
#endif

namespace Actor.Presentation
{
    public sealed class ArenaAudio : MonoBehaviour
    {
        public enum Cue { Hover, Click, Message, TaskAccepted, TaskCompleted, Meeting, VoteSubmitted, Emergency, Death, KillImpact, GameOver }
#if !UNITY_SERVER
        private static ArenaAudio instance;
        private AtmosphericArtCatalog art;
        private double hoverAt;
        private static ArenaAudio Ensure()
        {
            if (Application.isBatchMode || SystemInfo.graphicsDeviceType==UnityEngine.Rendering.GraphicsDeviceType.Null) return null;
            if(instance!=null)return instance;
            var root=new GameObject("ACTOR Original Audio");DontDestroyOnLoad(root);instance=root.AddComponent<ArenaAudio>();return instance;
        }
        private void Awake(){art=AtmosphericArtCatalog.Load();ArenaUI.SettingsChanged+=ApplySettings;ApplySettings();}
        private void OnDestroy(){ArenaUI.SettingsChanged-=ApplySettings;if(instance==this)instance=null;}
        private void ApplySettings(){foreach(var source in GetComponentsInChildren<AudioSource>())source.volume=ArenaUI.EffectsVolume;}
        private AudioSource Play(Cue cue,Vector3 position,bool spatial)
        {
            int index=(int)cue;if(art==null||art.cues==null||index>=art.cues.Length||art.cues[index]==null)return null;
            if(cue==Cue.Hover){double now=Time.realtimeSinceStartupAsDouble;if(now-hoverAt<.075)return null;hoverAt=now;}
            var root=new GameObject("Permitted "+cue);root.transform.SetParent(transform,false);root.transform.position=spatial&&Camera.main!=null?new Vector3(position.x,position.y,Camera.main.transform.position.z):position;
            var source=root.AddComponent<AudioSource>();source.clip=art.cues[index];source.volume=ArenaUI.EffectsVolume;source.playOnAwake=false;
            source.spatialBlend=spatial?.35f:0;source.minDistance=1;source.maxDistance=5;source.rolloffMode=AudioRolloffMode.Linear;
            source.Play();StartCoroutine(Remove(root,source.clip.length+.05f));return source;
        }
        private static IEnumerator Remove(GameObject root,float delay){yield return new WaitForSecondsRealtime(delay);if(root!=null)Destroy(root);}
        public static void PlayUI(Cue cue)=>Ensure()?.Play(cue,Vector3.zero,false);
        internal static AudioSource PlayWitness(Cue cue,Vector3 position)
            =>float.IsFinite(position.x)&&float.IsFinite(position.y)?Ensure()?.Play(cue,position,true):null;
#else
        public static void PlayUI(Cue cue) { }
#endif
    }
}
