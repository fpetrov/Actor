using UnityEngine;

namespace Actor.Lobby
{
    [CreateAssetMenu(menuName = "ACTOR/Lobby Client Settings")]
    public sealed class LobbyClientSettings : ScriptableObject
    {
        public string apiBaseUrl = "http://127.0.0.1:5080";
        public string gameVersion = "0.7.0";
        public string protocolVersion = "3";
        public string gameplayScene = "MainHallMultiplayer";
        public float refreshInterval = 4f;
    }
}
