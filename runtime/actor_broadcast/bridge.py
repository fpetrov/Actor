"""Reuse the typed Core bridge, admitting only the observer's narrow manifest."""
from actor_runtime.errors import ActorError
from actor_runtime.server import RuntimeServer

ALLOWED_CAPABILITIES = frozenset({"agents-arena.broadcast-presented-view", "agents-arena.broadcast-presented",
                                "agents-arena.broadcast-select-view", "agents-arena.broadcast-view-selection-result"})


class BroadcastBridge(RuntimeServer):
    def __init__(self, registry, token, *, observer_id, game_version="0.10.0", **options):
        super().__init__(registry, token, **options)
        self.observer_id, self.game_version = observer_id, game_version

    def _publish_session(self, session):
        manifest = session.manifest
        if (manifest.game_id != "agents-arena" or manifest.game_version != self.game_version
                or manifest.self_actor_id != self.observer_id
                or set(session.capabilities) != ALLOWED_CAPABILITIES
                or any(other is not session and not other.closed.is_set() for other in self.sessions)):
            raise ActorError("broadcast_manifest_rejected", "Only this admitted observer may use the commentary bridge")
        super()._publish_session(session)
