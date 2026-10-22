using System.Net;
using Actor.ControlPlane.Domain;

namespace Actor.ControlPlane.Api;

public static class BroadcastOperatorBoundary
{
    public static void RequireDirectLoopback(HttpContext context)
    {
        // This route is deliberately absent from the public nginx API. SSH
        // forwards terminate directly on loopback; proxy-originated requests
        // remain forbidden even if forwarded middleware rewrote their address.
        if (context.Connection.RemoteIpAddress is not { } address || !IPAddress.IsLoopback(address)
            || context.Request.Headers.ContainsKey("Forwarded")
            || context.Request.Headers.ContainsKey("X-Forwarded-For")
            || context.Request.Headers.ContainsKey("X-Original-For"))
            throw new LobbyException(403, "broadcast_loopback_required", "A direct local operator connection is required.");
    }
}
