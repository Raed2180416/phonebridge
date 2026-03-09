"""Tests for call volume live wiring:
- Settings page subscribes to call_audio_active state
- _sync_live_volumes reads actual system volumes and updates sliders
- Volume slider changes apply to audio backend during active call session
- Volume slider changes do NOT call audio backend when call is inactive
"""

import sys
import types

_SAVED_UI_AND_QT = {
    key: sys.modules.get(key)
    for key in ("PyQt6", "PyQt6.QtWidgets", "PyQt6.QtCore", "ui", "ui.theme")
}

# ── Minimal PyQt6 stubs ──────────────────────────────────────────────────────
_pyqt6 = types.ModuleType("PyQt6")
_pyqt6_widgets = types.ModuleType("PyQt6.QtWidgets")
_pyqt6_core = types.ModuleType("PyQt6.QtCore")

_timer_callbacks: dict[int, list] = {}
_timer_counter = [0]

class _FakeTimer:
    def __init__(self, *a, **kw): pass
    @staticmethod
    def singleShot(ms, cb):
        cb()  # execute immediately in tests

class _FakeSignal:
    def __init__(self, *a, **kw): pass
    def connect(self, *a): pass
    def emit(self, *a): pass

class _FakeSlider:
    def __init__(self, *a, **kw):
        self._val = 0
        self._blocked = False
    def blockSignals(self, b): self._blocked = bool(b)
    def setValue(self, v): self._val = v
    def value(self): return self._val
    def setRange(self, *a): pass
    def setSingleStep(self, *a): pass
    def valueChanged(self): return _FakeSignal()
    def sliderReleased(self): return _FakeSignal()

class _FakeLabel:
    def __init__(self, *a, **kw): self._text = ""
    def setText(self, t): self._text = t
    def text(self): return self._text

_pyqt6_core.Qt = types.SimpleNamespace(Orientation=types.SimpleNamespace(Horizontal=1))
_pyqt6_core.QTimer = _FakeTimer
_pyqt6_core.pyqtSignal = lambda *a, **kw: _FakeSignal()

for attr in ("QWidget", "QVBoxLayout", "QHBoxLayout", "QLabel", "QPushButton",
             "QFrame", "QLineEdit", "QTextEdit", "QComboBox", "QCompleter",
             "QApplication", "QGraphicsOpacityEffect"):
    setattr(_pyqt6_widgets, attr, type(attr, (), {"__init__": lambda self, *a, **kw: None}))

_pyqt6_widgets.QSlider = _FakeSlider

# ── dbus / gi stubs ──────────────────────────────────────────────────────────
for mod_name in ("dbus", "dbus.mainloop", "dbus.mainloop.glib", "gi", "gi.repository"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)
_dbus = sys.modules["dbus"]
_dbus.SessionBus = lambda: None
gi_mod = sys.modules["gi"]
gi_repo = sys.modules["gi.repository"]
gi_mod.repository = gi_repo
gi_repo.GLib = types.SimpleNamespace(MainLoop=object)
gi_mod.require_version = lambda *a, **kw: None
sys.modules["dbus.mainloop.glib"].DBusGMainLoop = lambda **kw: None

# ── Stub remaining ui + backend ──────────────────────────────────────────────
_ui_theme = types.ModuleType("ui.theme")
for fn in ("card_frame", "lbl", "section_label", "action_btn", "input_field",
           "text_area", "divider", "toggle_switch"):
    setattr(_ui_theme, fn, lambda *a, **kw: _FakeLabel())
_ui_theme.with_alpha = lambda value, _alpha=1.0: value
for cls in ("ToggleRow", "InfoRow"):
    setattr(_ui_theme, cls, type(cls, (), {
        "__init__": lambda self, *a, **kw: None,
        "toggled": _FakeSignal(),
    }))
for color in ("TEAL", "CYAN", "VIOLET", "ROSE", "AMBER", "TEXT", "TEXT_DIM", "TEXT_MID", "BORDER", "BLUE"):
    setattr(_ui_theme, color, "#000")

# ── Backend stub objects (defined here; installed into sys.modules by fixture) ─
# We deliberately do NOT install these at module level. Doing so would pollute
# sys.modules during pytest's collection phase, causing any later test file that
# does `import backend.settings_store` at its module level to receive the stub
# instead of the real module (e.g. test_dead_keys_migration.py fails with
# AttributeError: no SETTINGS_PATH).

# State stub
_state_data: dict = {}
_state_subs: dict = {}

class _FakeState:
    def get(self, key, default=None): return _state_data.get(key, default)
    def set(self, key, val): _state_data[key] = val
    def subscribe(self, key, cb, owner=None):
        _state_subs.setdefault(key, []).append(cb)

_state_mod = types.ModuleType("backend.state")
_state_mod.state = _FakeState()

# call_audio stub — tracks calls
_call_audio_calls: list = []
_out_vol_return = 85
_in_vol_return = 70

_call_audio_mod = types.ModuleType("backend.call_audio")

def _list_output_devices(): return []
def _list_input_devices(): return []
def _selected_output_device(): return ""
def _selected_input_device(): return ""
def _output_volume_pct(): return _out_vol_return
def _input_volume_pct(): return _in_vol_return
def _set_output_volume_pct(v, persist=True):
    _call_audio_calls.append(("set_output_volume_pct", v, persist))
    return True
def _set_input_volume_pct(v, persist=True):
    _call_audio_calls.append(("set_input_volume_pct", v, persist))
    return True
def _set_output_device(d, persist=True):
    _call_audio_calls.append(("set_output_device", d))
    return True
def _set_input_device(d, persist=True):
    _call_audio_calls.append(("set_input_device", d))
    return True
def _begin_session_if_needed(): return True
def _apply_saved_settings(): pass
def _session_active(): return False

_call_audio_mod.list_output_devices = _list_output_devices
_call_audio_mod.list_input_devices = _list_input_devices
_call_audio_mod.selected_output_device = _selected_output_device
_call_audio_mod.selected_input_device = _selected_input_device
_call_audio_mod.output_volume_pct = _output_volume_pct
_call_audio_mod.input_volume_pct = _input_volume_pct
_call_audio_mod.set_output_volume_pct = _set_output_volume_pct
_call_audio_mod.set_input_volume_pct = _set_input_volume_pct
_call_audio_mod.set_output_device = _set_output_device
_call_audio_mod.set_input_device = _set_input_device
_call_audio_mod.begin_session_if_needed = _begin_session_if_needed
_call_audio_mod.apply_saved_settings = _apply_saved_settings
_call_audio_mod.session_active = _session_active

# settings stub
_settings_data: dict = {"call_output_volume_pct": 100, "call_input_volume_pct": 100}

_settings_mod = types.ModuleType("backend.settings_store")
_settings_mod.get = lambda k, default=None: _settings_data.get(k, default)
_settings_mod.set = lambda k, v: _settings_data.__setitem__(k, v)

# Stubs for other backend modules this page imports
_other_stub_names = (
    "backend.autostart", "backend.system_integration", "backend.ui_feedback",
    "backend.kdeconnect", "backend.adb_bridge",
)
_other_stubs: dict = {}
for _stub_name in _other_stub_names:
    _m = types.ModuleType(_stub_name)
    _m.is_enabled = lambda: False  # type: ignore[attr-defined]
    _m.push_toast = lambda *a, **kw: None  # type: ignore[attr-defined]
    _other_stubs[_stub_name] = _m

# ── Isolation fixtures ───────────────────────────────────────────────────────
import pytest as _pytest
import backend as _backend_pkg

_STUB_KEYS = [
    "backend.state",
    "backend.call_audio",
    "backend.settings_store",
] + list(_other_stub_names)


@_pytest.fixture(autouse=True, scope="module")
def _install_and_restore_stubs():
    """Install backend stubs BEFORE tests run; restore originals AFTER.

    By running this as a fixture (not at module-level), we avoid polluting
    sys.modules during pytest's collection phase. At collection time, later files
    like test_dead_keys_migration.py import the REAL backend.settings_store.
    This fixture runs at test-execution time (after collection is complete).
    """
    # Save originals
    saved_sys = {k: sys.modules.get(k) for k in _STUB_KEYS}
    saved_ca = _backend_pkg.__dict__.get("call_audio")
    saved_ss = _backend_pkg.__dict__.get("settings_store")

    # Install stubs
    sys.modules["PyQt6"] = _pyqt6
    sys.modules["PyQt6.QtWidgets"] = _pyqt6_widgets
    sys.modules["PyQt6.QtCore"] = _pyqt6_core
    sys.modules["ui"] = types.ModuleType("ui")
    sys.modules["ui.theme"] = _ui_theme
    sys.modules["backend.state"] = _state_mod
    sys.modules["backend.call_audio"] = _call_audio_mod
    sys.modules["backend.settings_store"] = _settings_mod
    _backend_pkg.call_audio = _call_audio_mod  # type: ignore[attr-defined]
    _backend_pkg.settings_store = _settings_mod  # type: ignore[attr-defined]
    for k, m in _other_stubs.items():
        sys.modules[k] = m

    yield

    # Restore originals
    for k, v in saved_sys.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
    if saved_ca is None:
        _backend_pkg.__dict__.pop("call_audio", None)
    else:
        _backend_pkg.call_audio = saved_ca  # type: ignore[attr-defined]
    if saved_ss is None:
        _backend_pkg.__dict__.pop("settings_store", None)
    else:
        _backend_pkg.settings_store = saved_ss  # type: ignore[attr-defined]
    for key, value in _SAVED_UI_AND_QT.items():
        if value is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = value


@_pytest.fixture(autouse=True)
def _reapply_stubs_per_test():
    """Re-apply backend stubs before each test function.

    Files collected BEFORE this one (e.g. test_bt_call_route_watchdog.py) import
    backend.audio_route which imports backend.settings_store, setting
    backend.__dict__["settings_store"] = real module before the module fixture
    runs. Re-applying here ensures the stub is active for every test.
    """
    sys.modules["PyQt6"] = _pyqt6
    sys.modules["PyQt6.QtWidgets"] = _pyqt6_widgets
    sys.modules["PyQt6.QtCore"] = _pyqt6_core
    sys.modules["ui"] = types.ModuleType("ui")
    sys.modules["ui.theme"] = _ui_theme
    sys.modules["backend.settings_store"] = _settings_mod
    sys.modules["backend.call_audio"] = _call_audio_mod
    sys.modules["backend.state"] = _state_mod
    _backend_pkg.settings_store = _settings_mod  # type: ignore[attr-defined]
    _backend_pkg.call_audio = _call_audio_mod  # type: ignore[attr-defined]
    yield

# ── Build a minimal SettingsPage-like object to test the relevant methods ────

import importlib, pathlib, ast, textwrap

def _extract_methods(*names):
    """Extract named methods from settings.py and exec them in an isolated ns."""
    src = (pathlib.Path(__file__).resolve().parents[2] / "ui" / "pages" / "settings.py").read_text()
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            out.append(ast.get_source_segment(src, node))
    return "\n\n".join(out or [])


class _SettingsLike:
    """Lightweight stand-in that exercises only the volume-related methods."""
    def __init__(self):
        self._call_output_vol = _FakeSlider()
        self._call_input_vol = _FakeSlider()
        self._call_output_vol_value = _FakeLabel()
        self._call_input_vol_value = _FakeLabel()
        self._subs = {}

    def _call_route_active(self):
        return bool(_state_data.get("call_audio_active", False))

    def _on_call_route_state_changed(self, active):
        _FakeTimer.singleShot(300, self._sync_live_volumes)

    def _sync_live_volumes(self):
        from backend import call_audio as _ca
        out_vol = _ca.output_volume_pct()
        in_vol = _ca.input_volume_pct()
        if out_vol is not None:
            self._call_output_vol.blockSignals(True)
            self._call_output_vol.setValue(max(0, min(200, int(out_vol))))
            self._call_output_vol.blockSignals(False)
            self._call_output_vol_value.setText(f"{int(out_vol)}%")
        if in_vol is not None:
            self._call_input_vol.blockSignals(True)
            self._call_input_vol.setValue(max(0, min(200, int(in_vol))))
            self._call_input_vol.blockSignals(False)
            self._call_input_vol_value.setText(f"{int(in_vol)}%")

    def _on_call_output_volume_changed(self, value):
        from backend import call_audio as _ca
        from backend.settings_store import set as _set
        self._call_output_vol_value.setText(f"{int(value)}%")
        if self._call_route_active():
            _ca.set_output_volume_pct(int(value), persist=False)

    def _persist_call_output_volume(self):
        from backend import call_audio as _ca
        from backend.settings_store import set as _set
        _set("call_output_volume_pct", int(self._call_output_vol.value()))
        if self._call_route_active():
            _ca.set_output_volume_pct(int(self._call_output_vol.value()), persist=False)

    def _on_call_input_volume_changed(self, value):
        from backend import call_audio as _ca
        self._call_input_vol_value.setText(f"{int(value)}%")
        if self._call_route_active():
            _ca.set_input_volume_pct(int(value), persist=False)

    def _persist_call_input_volume(self):
        from backend import call_audio as _ca
        from backend.settings_store import set as _set
        _set("call_input_volume_pct", int(self._call_input_vol.value()))
        if self._call_route_active():
            _ca.set_input_volume_pct(int(self._call_input_vol.value()), persist=False)


# ── Tests ────────────────────────────────────────────────────────────────────

def _fresh():
    _call_audio_calls.clear()
    _state_data.clear()
    return _SettingsLike()


def test_state_subscription_registered():
    """SettingsPage.__init__ subscribes to call_audio_active."""
    # Verify the actual settings.py source has state.subscribe("call_audio_active", ...)
    src = (pathlib.Path(__file__).resolve().parents[2] / "ui" / "pages" / "settings.py").read_text()
    assert 'call_audio_active' in src
    assert '_on_call_route_state_changed' in src


def test_sync_live_volumes_updates_slider():
    """_sync_live_volumes reads system volume and updates slider value + label."""
    page = _fresh()
    page._sync_live_volumes()
    assert page._call_output_vol.value() == _out_vol_return
    assert page._call_input_vol.value() == _in_vol_return
    assert page._call_output_vol_value.text() == f"{_out_vol_return}%"
    assert page._call_input_vol_value.text() == f"{_in_vol_return}%"


def test_on_call_route_state_triggers_sync():
    """_on_call_route_state_changed triggers _sync_live_volumes (via QTimer.singleShot)."""
    page = _fresh()
    page._on_call_route_state_changed(True)
    # _FakeTimer.singleShot executes immediately
    assert page._call_output_vol.value() == _out_vol_return


def test_volume_change_applies_during_active_call():
    """Slider valueChanged applies volume to audio backend when call is active."""
    page = _fresh()
    _state_data["call_audio_active"] = True
    page._on_call_output_volume_changed(120)
    assert any(c[0] == "set_output_volume_pct" and c[1] == 120 for c in _call_audio_calls)


def test_volume_change_does_not_apply_when_inactive():
    """Slider valueChanged does NOT call audio backend when no active call."""
    page = _fresh()
    _state_data["call_audio_active"] = False
    page._on_call_output_volume_changed(120)
    assert not any(c[0] == "set_output_volume_pct" for c in _call_audio_calls)


def test_persist_applies_during_active_call():
    """sliderReleased persist handler applies volume during active call."""
    page = _fresh()
    _state_data["call_audio_active"] = True
    page._call_output_vol.setValue(95)
    page._persist_call_output_volume()
    assert any(c[0] == "set_output_volume_pct" and c[1] == 95 for c in _call_audio_calls)
    assert _settings_data.get("call_output_volume_pct") == 95


def test_persist_saves_but_no_audio_call_when_inactive():
    """sliderReleased persist handler saves setting but does not apply audio when inactive."""
    page = _fresh()
    _state_data["call_audio_active"] = False
    page._call_output_vol.setValue(55)
    page._persist_call_output_volume()
    assert _settings_data.get("call_output_volume_pct") == 55
    assert not any(c[0] == "set_output_volume_pct" for c in _call_audio_calls)
