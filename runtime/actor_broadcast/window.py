"""Exact owned macOS window binding, using public CoreGraphics C APIs.

No titles, screenshots, accessibility, AppleScript or permission prompts. Native
calls are lazy; an injected backend makes the same fail-closed policy testable.
CGWindow bounds describe the native window, not Unity's framebuffer dimensions.
"""
from dataclasses import dataclass
import ctypes
import math
import sys

from .obs import WindowBinding


class WindowError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(code)


@dataclass(frozen=True)
class WindowInfo:
    window_id: int
    owner_pid: int
    x: float
    y: float
    width: float
    height: float
    on_screen: bool = True
    layer: int = 0
    alpha: float = 1.0


@dataclass(frozen=True)
class DisplayBounds:
    x: float
    y: float
    width: float
    height: float


def _finite(values):
    return all(type(v) in (int, float) and math.isfinite(v) for v in values)


class OwnedWindowLocator:
    def __init__(self, owner_pid, *, alive, backend=None):
        if type(owner_pid) is not int or owner_pid <= 0 or not callable(alive):
            raise ValueError("An exact owned process and liveness callback are required")
        self.owner_pid, self.alive = owner_pid, alive
        self._backend = backend

    @property
    def backend(self):
        if self._backend is None: self._backend = CoreGraphicsBackend()
        return self._backend

    def preflight(self):
        """Read current authorization only; never requests Screen Recording access."""
        try: return self.alive() is True and self.backend.screen_capture_allowed() is True
        except Exception: return False

    def _eligible(self):
        if self.alive() is not True: raise WindowError("observer_process_not_alive")
        displays = self.backend.display_bounds()
        if not 1 <= len(displays) <= 16 or any(not _finite((d.x, d.y, d.width, d.height))
                or d.width <= 0 or d.height <= 0 for d in displays):
            raise WindowError("display_bounds_unavailable")
        rows = self.backend.windows_for_pid(self.owner_pid)
        if len(rows) > 32: raise WindowError("owned_window_capacity")
        result = []
        for row in rows:
            if (row.owner_pid != self.owner_pid or type(row.window_id) is not int or row.window_id <= 0
                    or row.on_screen is not True or type(row.layer) is not int or row.layer != 0
                    or not _finite((row.x, row.y, row.width, row.height, row.alpha))
                    or row.alpha <= 0 or row.alpha > 1 or not 320 <= row.width <= 3840 or not 180 <= row.height <= 2160):
                continue
            # Entire native window must remain on a single actual display.
            if not any(row.x >= d.x and row.y >= d.y and row.x + row.width <= d.x + d.width
                       and row.y + row.height <= d.y + d.height for d in displays): continue
            if row.width != round(row.width) or row.height != round(row.height): continue
            result.append(WindowBinding(row.window_id, self.owner_pid, int(row.width), int(row.height)))
        if self.alive() is not True: raise WindowError("observer_process_not_alive")
        return result

    def locate(self):
        rows = self._eligible()
        if len(rows) != 1: raise WindowError("owned_window_missing_or_ambiguous")
        return rows[0]

    def verify(self, binding):
        if not isinstance(binding, WindowBinding) or binding.owner_pid != self.owner_pid: return False
        try:
            matches = [row for row in self._eligible() if row.window_id == binding.window_id]
            return len(matches) == 1 and matches[0] == binding
        except Exception: return False


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _Size(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _Rect(ctypes.Structure):
    _fields_ = [("origin", _Point), ("size", _Size)]


class CoreGraphicsBackend:
    """Lazy Darwin-only ABI. Foreign rows are discarded after reading PID only."""
    def __init__(self):
        if sys.platform != "darwin": raise WindowError("native_window_platform_unsupported")
        self.cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        pointer = ctypes.c_void_p
        self.cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        self.cg.CGWindowListCopyWindowInfo.restype = pointer
        self.cg.CGPreflightScreenCaptureAccess.argtypes = []
        self.cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        self.cg.CGGetActiveDisplayList.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        self.cg.CGGetActiveDisplayList.restype = ctypes.c_int32
        self.cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]; self.cg.CGDisplayBounds.restype = _Rect
        self.cf.CFArrayGetCount.argtypes = [pointer]; self.cf.CFArrayGetCount.restype = ctypes.c_long
        self.cf.CFArrayGetValueAtIndex.argtypes = [pointer, ctypes.c_long]; self.cf.CFArrayGetValueAtIndex.restype = pointer
        self.cf.CFDictionaryGetValue.argtypes = [pointer, pointer]; self.cf.CFDictionaryGetValue.restype = pointer
        self.cf.CFStringCreateWithCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_uint32]
        self.cf.CFStringCreateWithCString.restype = pointer
        self.cf.CFNumberGetValue.argtypes = [pointer, ctypes.c_int, pointer]; self.cf.CFNumberGetValue.restype = ctypes.c_bool
        self.cf.CFBooleanGetValue.argtypes = [pointer]; self.cf.CFBooleanGetValue.restype = ctypes.c_bool
        self.cf.CFGetTypeID.argtypes = [pointer]; self.cf.CFGetTypeID.restype = ctypes.c_ulong
        for name in ("CFArrayGetTypeID", "CFDictionaryGetTypeID", "CFNumberGetTypeID", "CFBooleanGetTypeID"):
            method = getattr(self.cf, name); method.argtypes = []; method.restype = ctypes.c_ulong
        self.cf.CFRelease.argtypes = [pointer]; self.cf.CFRelease.restype = None

    def screen_capture_allowed(self): return bool(self.cg.CGPreflightScreenCaptureAccess())

    def display_bounds(self):
        ids = (ctypes.c_uint32 * 16)(); count = ctypes.c_uint32()
        if self.cg.CGGetActiveDisplayList(16, ids, ctypes.byref(count)) != 0 or not 1 <= count.value <= 16:
            raise WindowError("display_bounds_unavailable")
        result = []
        for display in ids[:count.value]:
            rect = self.cg.CGDisplayBounds(display)
            result.append(DisplayBounds(rect.origin.x, rect.origin.y, rect.size.width, rect.size.height))
        return result

    def _get(self, dictionary, key):
        value = self.cf.CFStringCreateWithCString(None, key.encode("ascii"), 0x08000100)
        if not value: raise WindowError("window_dictionary_key_unavailable")
        try: return self.cf.CFDictionaryGetValue(dictionary, value)
        finally: self.cf.CFRelease(value)

    def _number(self, dictionary, key):
        value = self._get(dictionary, key)
        if not value or self.cf.CFGetTypeID(value) != self.cf.CFNumberGetTypeID():
            raise WindowError("window_number_unavailable")
        result = ctypes.c_double()
        if not self.cf.CFNumberGetValue(value, 13, ctypes.byref(result)): # kCFNumberDoubleType
            raise WindowError("window_number_unavailable")
        return result.value

    def _boolean(self, dictionary, key):
        value = self._get(dictionary, key)
        if not value or self.cf.CFGetTypeID(value) != self.cf.CFBooleanGetTypeID(): return False
        return bool(self.cf.CFBooleanGetValue(value))

    def windows_for_pid(self, pid):
        array = self.cg.CGWindowListCopyWindowInfo(1 | 16, 0) # on-screen, excluding desktop elements
        if not array: raise WindowError("window_list_unavailable")
        try:
            if self.cf.CFGetTypeID(array) != self.cf.CFArrayGetTypeID(): raise WindowError("window_list_unavailable")
            count = self.cf.CFArrayGetCount(array)
            if not 0 <= count <= 4096: raise WindowError("window_list_capacity")
            result = []
            for index in range(count):
                row = self.cf.CFArrayGetValueAtIndex(array, index)
                if not row or self.cf.CFGetTypeID(row) != self.cf.CFDictionaryGetTypeID(): continue
                if self._number(row, "kCGWindowOwnerPID") != pid: continue
                bounds = self._get(row, "kCGWindowBounds")
                if not bounds or self.cf.CFGetTypeID(bounds) != self.cf.CFDictionaryGetTypeID():
                    raise WindowError("owned_window_bounds_unavailable")
                number, layer = self._number(row, "kCGWindowNumber"), self._number(row, "kCGWindowLayer")
                if not number.is_integer() or not layer.is_integer(): raise WindowError("owned_window_identity_invalid")
                result.append(WindowInfo(int(number), pid, self._number(bounds, "X"), self._number(bounds, "Y"),
                    self._number(bounds, "Width"), self._number(bounds, "Height"), self._boolean(row, "kCGWindowIsOnscreen"),
                    int(layer), self._number(row, "kCGWindowAlpha")))
                if len(result) > 32: raise WindowError("owned_window_capacity")
            return result
        finally: self.cf.CFRelease(array)
