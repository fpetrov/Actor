using Actor.GameAdapter;
using Actor.Networking;
using UnityEngine;
#if !UNITY_SERVER
using UnityEngine.UIElements;
#endif
namespace Actor.UI
{
    public sealed class ControlModeView : MonoBehaviour
    {
#if !UNITY_SERVER
        private VisualElement root;
        private Button toggle;
        private Label status;
        private ActorControlController controller;
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void StartView()
        { if (!Actor.World.UICompatibilityProbe.Requested) new GameObject("Control mode view").AddComponent<ControlModeView>(); }
        private void Update()
        {
            if (controller == null) controller = FindAnyObjectByType<ActorControlController>();
            if (root == null && ArenaUI.HudLayer != null)
            {
                root = ArenaUI.Element("row", ArenaUI.HudLayer);
                root.style.position = Position.Absolute; root.style.right = 16; root.style.top = 66;
                toggle = ArenaUI.Button("", () =>
                {
                    if (controller == null) return;
                    var card = ArenaUI.OpenModal("Choose local control");
                    if (card == null) return;
                    ArenaUI.Text(controller.DisplayDetail, "muted", card);
                    ArenaUI.Text("Human uses this keyboard. ACTOR Runtime connects to your separately launched local agent.", "body", card);
                    ArenaUI.Button("Human", () => { controller.SetMode(ActorControlMode.Human); ArenaUI.CloseModal(); }, "primary", card);
                    ArenaUI.Button("ACTOR Runtime", () => { controller.SetMode(ActorControlMode.Runtime); ArenaUI.CloseModal(); }, "", card);
                }, "quiet", root);
                status = ArenaUI.Text("", "muted", root);
            }
            if (root == null) return;
            ArenaUI.Show(root, controller != null && Unity.Netcode.NetworkManager.Singleton != null && Unity.Netcode.NetworkManager.Singleton.IsConnectedClient);
            if (controller == null) return;
            toggle.text = controller.Mode == ActorControlMode.Human ? "Human ▾" : "ACTOR Runtime ▾";
            status.text = controller.DisplayState;
        }
        private void OnDestroy() => root?.RemoveFromHierarchy();
#endif
    }
}
