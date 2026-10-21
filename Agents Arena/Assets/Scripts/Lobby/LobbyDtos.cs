using System;

namespace Actor.Lobby
{
    [Serializable] public sealed class LobbyInfo
    {
        public string lobbyId, roomCode, displayName, status, createdAt, gameVersion, protocolVersion, publicHost, gameMode;
        public int gamePort, currentPlayers, maxPlayers;
    }
    [Serializable] public sealed class LobbyList { public LobbyInfo[] lobbies; }
    [Serializable] public sealed class JoinInfo
    {
        public string lobbyId, roomCode, host, joinTicket, expiresAt, gameMode;
        public int port;
    }
    [Serializable] public sealed class CreateLobbyBody
    {
        public string displayName, gameVersion, protocolVersion, gameMode = "Sandbox";
        public int maxPlayers = 8;
    }
    [Serializable] public sealed class JoinLobbyBody { public string playerName, gameVersion, protocolVersion; }
    [Serializable] public sealed class ApiError { public string code, message; }
    [Serializable] public sealed class ErrorEnvelope { public ApiError error; }
}
