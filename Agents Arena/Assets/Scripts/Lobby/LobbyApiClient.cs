using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

namespace Actor.Lobby
{
    public sealed class LobbyApiClient
    {
        private readonly LobbyClientSettings settings;
        public LobbyApiClient(LobbyClientSettings settings) => this.settings = settings;
        public IEnumerator List(Action<LobbyList> success, Action<string> failure)
            => Request("GET", "/api/v1/lobbies", null, success, failure);
        public IEnumerator Get(string code, Action<LobbyInfo> success, Action<string> failure)
            => Request("GET", "/api/v1/lobbies/" + Uri.EscapeDataString(code.Trim().ToUpperInvariant()), null, success, failure);
        public IEnumerator Create(string displayName, Action<LobbyInfo> success, Action<string> failure)
            => Request("POST", "/api/v1/lobbies", JsonUtility.ToJson(new CreateLobbyBody
            { displayName = displayName, gameVersion = settings.gameVersion, protocolVersion = settings.protocolVersion }), success, failure);
        public IEnumerator Join(string code, string name, Action<JoinInfo> success, Action<string> failure)
            => Request("POST", "/api/v1/lobbies/" + Uri.EscapeDataString(code.Trim().ToUpperInvariant()) + "/join",
                JsonUtility.ToJson(new JoinLobbyBody { playerName = name.Trim(), gameVersion = settings.gameVersion, protocolVersion = settings.protocolVersion }), success, failure);

        private IEnumerator Request<T>(string method, string path, string body, Action<T> success, Action<string> failure)
        {
            using (var request = new UnityWebRequest(settings.apiBaseUrl.TrimEnd('/') + path, method))
            {
                request.downloadHandler = new DownloadHandlerBuffer();
                request.timeout = 10;
                if (body != null)
                {
                    request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
                    request.SetRequestHeader("Content-Type", "application/json");
                }
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    ErrorEnvelope error = null;
                    try { error = JsonUtility.FromJson<ErrorEnvelope>(request.downloadHandler.text); }
                    catch (ArgumentException) { }
                    failure(error?.error?.message ?? "Server is unavailable. Check your connection and try again.");
                    yield break;
                }
                T response;
                try { response = JsonUtility.FromJson<T>(request.downloadHandler.text); }
                catch (ArgumentException) { failure("The server returned an invalid response."); yield break; }
                success(response);
            }
        }
    }
}
