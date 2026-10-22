using Actor.Networking;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Actor.Presentation
{
    // Client-only attachment to permitted spawned objects. It never adds a
    // NetworkBehaviour or changes the network prefab's authority layout.
    public sealed class AtmosphericPresentationHost : MonoBehaviour
    {
#if !UNITY_SERVER
        private double nextScan;
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Bootstrap()
        {SceneManager.sceneLoaded-=Loaded;SceneManager.sceneLoaded+=Loaded;Loaded(SceneManager.GetActiveScene(),LoadSceneMode.Single);}
        private static void Loaded(Scene scene,LoadSceneMode mode)
        {
            if(scene.name!="SocialDeduction"||Application.isBatchMode||SystemInfo.graphicsDeviceType==UnityEngine.Rendering.GraphicsDeviceType.Null)return;
            foreach(var root in scene.GetRootGameObjects())if(root.GetComponent<AtmosphericPresentationHost>()!=null)return;
            SocialAvatarArt.ClearCosmetics();
            var host=new GameObject("Atmospheric Client Presentation");SceneManager.MoveGameObjectToScene(host,scene);host.AddComponent<AtmosphericPresentationHost>();host.AddComponent<SocialKillPresentation>();host.AddComponent<StationAtmosphere2D>();
        }
        private void Update()
        {
            if(Time.realtimeSinceStartupAsDouble<nextScan)return;nextScan=Time.realtimeSinceStartupAsDouble+.1;
            foreach(var character in FindObjectsByType<NetworkCharacter>(FindObjectsSortMode.None))
                if(character.IsSpawned&&SocialVisibilitySystem.IsSocial(character.NetworkManager)&&character.GetComponent<SocialAvatarArt>()==null)character.gameObject.AddComponent<SocialAvatarArt>();
            foreach(var body in FindObjectsByType<SocialBody>(FindObjectsSortMode.None))
                if(body.IsSpawned&&body.GetComponent<SocialBodyArt>()==null)body.gameObject.AddComponent<SocialBodyArt>();
        }
#endif
    }
}
