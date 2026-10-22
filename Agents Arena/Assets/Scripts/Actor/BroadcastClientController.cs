using System;
using System.Collections;
using System.Text;
using Actor.Contracts;
using Actor.Lobby;
using Actor.Networking;
using Actor.Presentation;
using Actor.Sdk;
using Actor.Transport.Grpc;
using UnityEngine;

namespace Actor.GameAdapter
{
    public sealed class BroadcastClientController : MonoBehaviour
    {
#if !UNITY_SERVER
        private MatchSession match; private BroadcastSession session;
        private ActorBridgeClient bridge; private BroadcastGameAdapter adapter; private BridgeEndpoint endpoint;
        private bool stopped; private double retryAt; private int retries;
        [Serializable] private sealed class ObserverAdmission
        {
            public string lobbyId, roomCode, host, joinTicket, expiresAt, gameMode, broadcastSessionId, observerId;
            public int port; public ulong connectionGeneration;
            public override string ToString() => "ObserverAdmission [private]";
        }
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Bootstrap()
        {
            if (Array.IndexOf(Environment.GetCommandLineArgs(), "--broadcast-observer") < 0 || FindAnyObjectByType<BroadcastClientController>() != null) return;
            var root = new GameObject("ACTOR Broadcast Observer"); DontDestroyOnLoad(root); root.AddComponent<BroadcastClientController>();
        }
        private static string Argument(string key, string fallback = null)
        { string[] args = Environment.GetCommandLineArgs(); int i = Array.IndexOf(args, key); return i >= 0 && i + 1 < args.Length ? args[i + 1] : fallback; }
        private IEnumerator Start()
        {
            double until = Time.realtimeSinceStartupAsDouble + 10;
            while (match == null && Time.realtimeSinceStartupAsDouble < until) { match = FindAnyObjectByType<MatchSession>(); yield return null; }
            if (match == null) yield break;
            string code = Argument("--auto-room"), ticket = Environment.GetEnvironmentVariable("ACTOR_OBSERVER_TICKET");
            string encodedAdmission = Environment.GetEnvironmentVariable("ACTOR_OBSERVER_ADMISSION");
            Environment.SetEnvironmentVariable("ACTOR_OBSERVER_TICKET", null);
            Environment.SetEnvironmentVariable("ACTOR_OBSERVER_ADMISSION", null);
            ObserverAdmission admission = null;
            ObserverTicketClaims identity = null;
            try
            {
                if (!string.IsNullOrEmpty(encodedAdmission))
                {
                    if (Encoding.UTF8.GetByteCount(encodedAdmission) > 4096) throw new ArgumentException();
                    admission = JsonUtility.FromJson<ObserverAdmission>(encodedAdmission); if (admission == null) throw new ArgumentException();
                    if (code != null && code != admission.roomCode) throw new ArgumentException();
                    code = admission.roomCode; ticket = admission.joinTicket;
                }
                if (ticket == null || !ticket.StartsWith(ObserverTicketCodec.WirePrefix, StringComparison.Ordinal) || ticket.Length > 1024 + ObserverTicketCodec.WirePrefix.Length) throw new ArgumentException();
                string[] parts = ticket.Substring(ObserverTicketCodec.WirePrefix.Length).Split('.'); if (parts.Length != 2) throw new ArgumentException();
                string encoded = parts[0].Replace('-', '+').Replace('_', '/'); encoded = encoded.PadRight((encoded.Length + 3) / 4 * 4, '=');
                identity = JsonUtility.FromJson<ObserverTicketClaims>(new UTF8Encoding(false, true).GetString(Convert.FromBase64String(encoded)));
                if (identity == null) throw new ArgumentException();
                endpoint = new BridgeEndpoint(Argument("--actor-endpoint", "http://127.0.0.1:50051"), Environment.GetEnvironmentVariable("ACTOR_BRIDGE_TOKEN"));
            }
            catch (Exception error) when (error is ArgumentException or FormatException or DecoderFallbackException or UriFormatException)
            { Debug.LogWarning("ACTOR_BROADCAST admission_or_local_bridge_unavailable"); yield break; }
            if (!ValidRoomCode(code)) yield break;
            LobbyInfo room = null; string failure = null;
            if (admission != null)
            {
                if (admission.broadcastSessionId != identity.broadcastSessionId || admission.observerId != identity.observerId
                    || admission.connectionGeneration != identity.connectionGeneration || admission.lobbyId != identity.lobbyId
                    || admission.port < 1024 || admission.port > 65535 || string.IsNullOrWhiteSpace(admission.host) || admission.host.Length > 253) yield break;
                room = new LobbyInfo { lobbyId = admission.lobbyId, roomCode = admission.roomCode, publicHost = admission.host,
                    gamePort = admission.port, gameMode = admission.gameMode };
            }
            else yield return new LobbyApiClient(match.ClientSettings).Get(code, value => room = value, error => failure = "room_unavailable");
            if (failure != null || room == null || room.gameMode != "SocialDeduction" || !ObserverTicketCodec.ClaimsMatch(identity, room.lobbyId,
                match.ClientSettings.gameVersion, match.ClientSettings.protocolVersion, DateTimeOffset.UtcNow.ToUnixTimeSeconds())) yield break;
            session = BroadcastSession.Ensure(Unity.Netcode.NetworkManager.Singleton);
            match.ConnectObserver(new JoinInfo { lobbyId = room.lobbyId, roomCode = room.roomCode, gameMode = room.gameMode,
                host = room.publicHost, port = room.gamePort, joinTicket = ticket }, identity);
            Environment.SetEnvironmentVariable("ACTOR_OBSERVER_TICKET", null);
            Environment.SetEnvironmentVariable("ACTOR_OBSERVER_ADMISSION", null);
            if (session.GetComponent<BroadcastViewPresenter>() == null) session.gameObject.AddComponent<BroadcastViewPresenter>();
        }
        private static bool ValidRoomCode(string code)
        {
            if (code == null || code.Length != 6) return false;
            foreach (char c in code) if (!(c >= 'A' && c <= 'Z' || c >= '0' && c <= '9')) return false;
            return true;
        }
        private void Update()
        {
            if (stopped || session?.ExpectedScope == null || endpoint == null || match == null || !match.IsConnected) return;
            if (bridge == null)
            { adapter = new BroadcastGameAdapter(session, Application.version); bridge = new ActorBridgeClient(adapter, () => new GrpcActorTransport(), new ActorBridgeOptions { ObservationRateHz = 10 }); _ = bridge.StartAsync(endpoint); }
            bridge.Pump(Time.realtimeSinceStartupAsDouble);
            if (bridge.State == ActorConnectionState.Error && bridge.CanRetry && retries < 4 && Time.realtimeSinceStartupAsDouble >= retryAt)
            { retryAt = Time.realtimeSinceStartupAsDouble + Math.Min(10, 1 << retries++); bridge.Abort(); _ = bridge.StartAsync(endpoint); }
        }
        private void OnDestroy() { stopped = true; bridge?.Abort(); bridge?.Dispose(); adapter?.Dispose(); }
#endif
    }
}
