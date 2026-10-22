"""Pure camera intentions over fresh, actually presented public views only.

No transport, roster discovery, position prediction or presentation ACK lives
here. Native must also obtain scheduler.camera_change_allowed, revalidate the
exact current scope/sequence, send the typed command, and feed subsequent real
PresentedView records back before considering a switch confirmed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import random
import time

from .contracts import PresentedView, Scope, bounded_text, integer


ANCHORS = ("main-hall", "engineering", "security", "storage")
_FRESH_SECONDS = .5


@dataclass(frozen=True)
class ViewIntent:
    scope: Scope
    presentation_sequence: int
    expires_at_unix_ms: int
    focus_actor_id: str = ""
    anchor_id: str = ""

    def __post_init__(self):
        if not isinstance(self.scope, Scope): raise ValueError("A typed scope is required")
        integer(self.presentation_sequence, 1); integer(self.expires_at_unix_ms, 1)
        bounded_text(self.focus_actor_id, 128, empty=True)
        if type(self.anchor_id) is not str or self.anchor_id and self.anchor_id not in ANCHORS:
            raise ValueError("Only authored public anchors are allowed")
        if bool(self.focus_actor_id) == bool(self.anchor_id):
            raise ValueError("Exactly one camera target is required")


class CameraDirector:
    """Fair, bounded rotation; each confirmed camera samples one 18–26s dwell.

    Fresh empty views may seek a public anchor after five observed-empty
    seconds and ten confirmed-camera seconds. Missing frames are not empty
    observations, and rejected/timed-out requests retain the normal backoff.

    A result saying accepted never confirms a camera. One pending intention is
    retained until a real newer presented epoch matches its target, or a bounded
    rejection/timeout re-arms the dwell. Candidates come from the current
    actually rendered public camera selector (legacy: visible actors), never
    from old facts, a meeting roster, hidden positions or inferred life.
    """
    def __init__(self, *, clock=time.monotonic, wall_clock=time.time, rng=None):
        self.clock, self.wall_clock = clock, wall_clock
        self.rng = rng if rng is not None else random.Random()
        self.view = None
        self._visible = (); self._candidates = (); self._denied_until = {}
        self._target = ("", "")
        self._due = None; self._pending = None; self._pending_at = None
        self._camera_confirmed_at = None; self._empty_since = None; self._seek_not_before = 0
        self._actor_order = {}; self._anchor_order = {anchor: 0 for anchor in ANCHORS}
        self._visits = 0; self._last_clock = None; self._terminal = False
        self._proposed = self._confirmed = self._rejected = self._timed_out = 0
        self._empty_seeks = 0

    def _now(self):
        now = self.clock()
        if (type(now) not in (int, float) or not math.isfinite(now) or now < 0
                or self._last_clock is not None and now < self._last_clock):
            raise ValueError("A monotonic finite clock is required")
        self._last_clock = now
        return now

    def _arm(self, now):
        seconds = self.rng.uniform(18.0, 26.0)
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 18 <= seconds <= 26:
            raise ValueError("Dwell sampler returned an invalid interval")
        self._due = now + seconds

    def _fresh(self, view, now):
        wall = self.wall_clock()
        if type(wall) not in (int, float) or not math.isfinite(wall): return False
        return (0 <= now - view.presentation.received_at_monotonic <= _FRESH_SECONDS
            and view.observed_at_unix_ms <= wall * 1000 < view.expires_at_unix_ms)

    @staticmethod
    def _state(view):
        camera = view.current.get("camera")
        actors = view.current.get("actors")
        visible = view.current.get("visible_actor_ids")
        if type(camera) is not dict or type(actors) is not list or type(visible) is not list or len(actors) > 6 or len(visible) > 6:
            raise ValueError("A bounded public camera and visible actors are required")
        anchor, focus = camera.get("anchor_id"), camera.get("focus_actor_id")
        bounded_text(focus, 128, empty=True)
        if type(anchor) is not str or anchor and anchor not in ANCHORS or anchor and focus:
            raise ValueError("Unknown current public camera")
        ids = []
        for row in actors:
            if type(row) is not dict: raise ValueError("Typed visible actors are required")
            identity = row.get("actor_id"); bounded_text(identity, 128); ids.append(identity)
        if len(set(ids)) != len(ids) or any(type(item) is not str for item in visible) or set(ids) != set(visible) or len(set(visible)) != len(visible):
            raise ValueError("Current visible actor lists must agree")
        targets=view.current.get("camera_targets",[])
        if type(targets)is not list or len(targets)>6:
            raise ValueError("A bounded public camera target roster is required")
        candidates=[]
        for row in targets:
            if type(row)is not dict or set(row)!={"actor_id","display_name"}:
                raise ValueError("Camera targets contain only public identity and name")
            identity=row["actor_id"];bounded_text(identity,128);bounded_text(row["display_name"],128)
            candidates.append(identity)
        if len(set(candidates))!=len(candidates):raise ValueError("Duplicate public camera target")
        return (anchor, focus), tuple(sorted(ids)), tuple(sorted(candidates or ids))

    def _visit(self, target):
        anchor, focus = target; self._visits += 1
        if anchor: self._anchor_order[anchor] = self._visits
        if focus:
            if focus not in self._actor_order and len(self._actor_order) >= 6:
                del self._actor_order[min(self._actor_order, key=lambda actor: (self._actor_order[actor], actor))]
            self._actor_order[focus] = self._visits

    def observe(self, view):
        if not isinstance(view, PresentedView): raise ValueError("A typed actually presented view is required")
        now = self._now()
        if self._terminal or not self._fresh(view, now): return False
        target, visible, candidates = self._state(view)
        previous = self.view
        if previous is not None:
            old, new = previous.scope, view.scope
            if ((old.broadcast_session_id, old.match_id) != (new.broadcast_session_id, new.match_id)
                    or new.connection_generation < old.connection_generation
                    or new.connection_generation == old.connection_generation and (
                        new.view_epoch < old.view_epoch or view.sequence <= previous.sequence)):
                return False
        generation_changed = previous is not None and view.scope.connection_generation != previous.scope.connection_generation
        epoch_changed = previous is not None and view.scope.view_epoch != previous.scope.view_epoch
        camera_changed = previous is None or generation_changed or epoch_changed or target != self._target
        if generation_changed:
            self._actor_order.clear(); self._anchor_order = {anchor: 0 for anchor in ANCHORS}
            self._denied_until.clear()
        if self._pending is not None and (generation_changed or epoch_changed):
            pending = self._pending
            if (not generation_changed and view.scope.view_epoch > pending.scope.view_epoch
                    and target == (pending.anchor_id, pending.focus_actor_id)):
                self._confirmed += 1
            self._pending = self._pending_at = None
        self.view, self._target, self._visible, self._candidates = view, target, visible, candidates
        self._denied_until={actor:until for actor,until in self._denied_until.items() if actor in candidates and until>now}
        continuous_empty = (not camera_changed and previous is not None and previous.phase == "Exploration"
            and self._fresh(previous, now) and self._empty_since is not None)
        if visible or view.phase != "Exploration": self._empty_since = None
        elif not continuous_empty: self._empty_since = now
        if view.terminal or view.phase == "GameOver":
            self._terminal = True; self._pending = self._pending_at = None; self._due = None
            return True
        if camera_changed:
            self._camera_confirmed_at = now; self._seek_not_before = now
            self._visit(target); self._arm(now)
        elif previous.phase != "Exploration" and view.phase == "Exploration":
            self._arm(now)  # A meeting never causes an immediate camera cut.
        return True

    def _expire_pending(self,now):
        if self._pending is not None and now-self._pending_at>=5:
            self._timed_out+=1;self._pending=self._pending_at=None;self._arm(now)
            self._seek_not_before=self._due

    def _rotation_due(self,now):
        if self.view.phase!="Exploration" or self._due is None:return False
        empty_seek=(not self._visible and self._empty_since is not None
            and now-self._empty_since>=5 and self._camera_confirmed_at is not None
            and now-self._camera_confirmed_at>=10 and now>=self._seek_not_before)
        return now>=self._due or empty_seek

    @property
    def change_due(self):
        """A due camera gets a speech boundary even under continuous P2 work."""
        now=self._now()
        if self.view is None or self._terminal or not self._fresh(self.view,now):return False
        self._expire_pending(now)
        return self._pending is not None or self._rotation_due(now)

    def next_intent(self):
        now = self._now(); view = self.view
        if view is None or self._terminal or not self._fresh(view, now): return None
        had_pending=self._pending is not None
        self._expire_pending(now)
        if had_pending or not self._rotation_due(now):return None
        if now < self._due: self._empty_seeks += 1
        alternatives = tuple(actor for actor in self._candidates
            if actor != self._target[1] and self._denied_until.get(actor,0)<=now)
        if alternatives:
            focus = min(alternatives, key=lambda actor: (self._actor_order.get(actor, 0), actor))
            anchor = ""
        else:
            anchor = min((item for item in ANCHORS if item != self._target[0]),
                key=lambda item: (self._anchor_order[item], ANCHORS.index(item)))
            focus = ""
        self._pending = ViewIntent(view.scope, view.sequence, view.expires_at_unix_ms, focus, anchor)
        self._pending_at = now; self._proposed += 1
        return self._pending

    def selection_result(self, intent, accepted):
        if not isinstance(intent, ViewIntent) or type(accepted) is not bool:
            raise ValueError("A typed exact selection result is required")
        if self._terminal or self._pending != intent: return False
        if not accepted:
            if intent.focus_actor_id:
                # A uniform rejection is neither a visit nor evidence of life.
                # Temporarily defer that ID so it cannot starve other targets.
                self._denied_until[intent.focus_actor_id]=self._now()+60
            self._rejected += 1; self._pending = self._pending_at = None; self._arm(self._now())
            self._seek_not_before = self._due
        return True

    def report(self):
        return {"intents": self._proposed, "confirmed_presented_switches": self._confirmed,
            "rejected_selections": self._rejected, "selection_timeouts": self._timed_out,
            "empty_frame_seeks": self._empty_seeks,
            "pending": self._pending is not None, "remembered_focus_count": len(self._actor_order),
            "deferred_target_count":len(self._denied_until),
            "terminal": self._terminal}
