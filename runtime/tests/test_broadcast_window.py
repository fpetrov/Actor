from dataclasses import replace
import pytest

from actor_broadcast.obs import WindowBinding
from actor_broadcast.window import DisplayBounds, OwnedWindowLocator, WindowError, WindowInfo


class Backend:
    def __init__(self):
        self.rows = [WindowInfo(42, 100, 200, 100, 800, 628)]
        self.displays = [DisplayBounds(0, 0, 1920, 1080)]
        self.allowed = True
        self.pids = []
    def windows_for_pid(self, pid): self.pids.append(pid); return self.rows
    def display_bounds(self): return self.displays
    def screen_capture_allowed(self): return self.allowed


def locator(backend, alive=lambda: True): return OwnedWindowLocator(100, alive=alive, backend=backend)


def test_exact_owned_window_binding_and_outer_bounds_not_framebuffer():
    backend = Backend(); owned = locator(backend)
    assert owned.preflight() is True
    assert owned.locate() == WindowBinding(42, 100, 800, 628)
    assert owned.verify(WindowBinding(42, 100, 800, 628)) is True
    assert backend.pids == [100, 100]
    assert owned.verify(WindowBinding(42, 101, 800, 628)) is False
    assert owned.verify(WindowBinding(43, 100, 800, 628)) is False


def test_foreign_windows_are_never_fallbacks_and_ambiguous_own_windows_fail():
    b = Backend(); b.rows = [replace(b.rows[0], owner_pid=200)]
    with pytest.raises(WindowError, match='missing_or_ambiguous'): locator(b).locate()
    b.rows = [WindowInfo(42, 100, 200, 100, 800, 628), WindowInfo(43, 100, 200, 100, 640, 360)]
    with pytest.raises(WindowError, match='missing_or_ambiguous'): locator(b).locate()


@pytest.mark.parametrize('change', [dict(on_screen=False), dict(layer=1), dict(alpha=0), dict(alpha=float('nan')),
    dict(x=-1), dict(x=1500), dict(width=0), dict(width=800.5), dict(y=float('nan')), dict(height=2200)])
def test_hidden_offscreen_overlay_nonfinite_and_unusable_windows_fail_closed(change):
    b = Backend(); b.rows = [replace(b.rows[0], **change)]
    assert locator(b).verify(WindowBinding(42, 100, 800, 628)) is False
    with pytest.raises(WindowError): locator(b).locate()


def test_native_resize_requires_new_binding_and_negative_display_origin_is_valid():
    b = Backend(); owned = locator(b); binding = owned.locate()
    b.rows = [replace(b.rows[0], width=1280)]
    assert owned.verify(binding) is False
    b.displays = [DisplayBounds(-1920, 0, 1920, 1080)]
    b.rows = [replace(b.rows[0], x=-1600, width=800)]
    assert owned.locate() == WindowBinding(42, 100, 800, 628)


def test_permission_is_preflight_only_and_process_exit_during_lookup_fails():
    b = Backend(); b.allowed = False
    assert locator(b).preflight() is False
    # Python never captures; OBS has its own separate TCC permission and proof.
    assert locator(b).locate() == WindowBinding(42, 100, 800, 628)
    assert locator(b).verify(WindowBinding(42, 100, 800, 628)) is True
    b.allowed = True
    values = iter([True, False])
    with pytest.raises(WindowError, match='not_alive'): locator(b, lambda: next(values)).locate()
    assert locator(b, lambda: False).preflight() is False


def test_capacity_and_invalid_display_fail_closed():
    b = Backend(); b.rows *= 33
    with pytest.raises(WindowError, match='capacity'): locator(b).locate()
    b = Backend(); b.displays = [DisplayBounds(0, 0, float('inf'), 1080)]
    with pytest.raises(WindowError, match='display_bounds'): locator(b).locate()
