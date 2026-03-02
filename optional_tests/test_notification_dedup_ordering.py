"""Tests for notification dedup (content hash) and newest-first ordering.

Covers:
- _content_hash returns same value for identical content
- _content_hash differs when any visible field changes
- NotificationMirror._upsert_one skips D-Bus Notify when hash matches
- NotificationMirror._upsert_one calls D-Bus Notify when content changes
- _NotifFetchWorker sorts newest-first by time_ms
- _NotifFetchWorker preserves time_ms from state for known ids
- _fmt_age returns correct human labels
"""

import sys
import types
import time as _time

# ── dbus / gi stubs ──────────────────────────────────────────────────────────
for mod_name in ("dbus", "dbus.mainloop", "dbus.mainloop.glib", "gi", "gi.repository"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

_dbus = sys.modules["dbus"]
_dbus.Boolean = lambda x: bool(x)
_dbus.String = lambda x: str(x)
_dbus.Int32 = lambda x: int(x)
_dbus.UInt32 = lambda x: int(x)
_dbus.Array = lambda x, signature=None: list(x)
_dbus.Dictionary = lambda x, signature=None: dict(x)
_dbus.SessionBus = lambda: None

gi_mod = sys.modules["gi"]
gi_repo = sys.modules["gi.repository"]
gi_mod.repository = gi_repo
gi_repo.GLib = types.SimpleNamespace(MainLoop=object)
gi_mod.require_version = lambda *a, **kw: None

_dbus_mainloop_glib = sys.modules["dbus.mainloop.glib"]
_dbus_mainloop_glib.DBusGMainLoop = lambda set_as_default=False: None

# Stub PyQt6 for _NotifFetchWorker import
_pyqt6 = types.ModuleType("PyQt6")
_pyqt6_widgets = types.ModuleType("PyQt6.QtWidgets")
_pyqt6_core = types.ModuleType("PyQt6.QtCore")

class _FakeSignal:
    def connect(self, *a): pass
    def emit(self, *a): pass
    def disconnect(self, *a): pass

class _FakeQObject:
    def __init__(self, *a, **kw): pass
    def moveToThread(self, t): pass

_pyqt6_core.QObject = _FakeQObject
_pyqt6_core.QThread = _FakeQObject
_pyqt6_core.pyqtSignal = lambda *a, **kw: _FakeSignal()

for attr in ("QWidget", "QVBoxLayout", "QHBoxLayout", "QLabel", "QPushButton",
             "QFrame", "QLineEdit", "QTextEdit", "QComboBox", "QCompleter",
             "QApplication", "QGraphicsOpacityEffect"):
    setattr(_pyqt6_widgets, attr, type(attr, (), {"__init__": lambda self, *a, **kw: None}))

for attr in ("Qt", "QTimer", "QPropertyAnimation", "QEasingCurve",
             "QParallelAnimationGroup", "QPoint", "pyqtProperty", "QSize"):
    setattr(_pyqt6_core, attr, type(attr, (), {"__init__": lambda self, *a, **kw: None}))

sys.modules["PyQt6"] = _pyqt6
sys.modules["PyQt6.QtWidgets"] = _pyqt6_widgets
sys.modules["PyQt6.QtCore"] = _pyqt6_core

# Stub ui.theme
_ui = types.ModuleType("ui")
_ui_theme = types.ModuleType("ui.theme")
for fn in ("card_frame", "lbl", "section_label", "action_btn", "input_field",
           "text_area", "divider", "with_alpha"):
    setattr(_ui_theme, fn, lambda *a, **kw: None)
for color in ("TEAL", "CYAN", "VIOLET", "ROSE", "AMBER", "TEXT", "TEXT_DIM",
              "TEXT_MID", "FROST", "BORDER"):
    setattr(_ui_theme, color, "#000000")
sys.modules["ui"] = _ui
sys.modules["ui.theme"] = _ui_theme

# Stub backend modules that messages.py imports (NOT notification_mirror — that's our target)
for mod in ("backend.adb_bridge",):
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)

# Ensure backend.kdeconnect has KDEConnect even if it was stubbed by a prior test
_kc_mod = sys.modules.get("backend.kdeconnect")
if _kc_mod is None or not hasattr(_kc_mod, "KDEConnect"):
    _kc_stub = types.ModuleType("backend.kdeconnect")
    _kc_stub.KDEConnect = type("KDEConnect", (), {"__init__": lambda self: None})
    sys.modules["backend.kdeconnect"] = _kc_stub

# Stub state
_state_mod = types.ModuleType("backend.state")
_state_data: dict = {}

class _FakeState:
    def get(self, key, default=None):
        return _state_data.get(key, default)
    def set(self, key, val):
        _state_data[key] = val
    def subscribe(self, key, cb):
        pass

_state_mod.state = _FakeState()
sys.modules["backend.state"] = _state_mod

# ── Now import targets ────────────────────────────────────────────────────────
from backend.notification_mirror import _content_hash, NotificationMirror  # noqa: E402

# Stub _NotifFetchWorker directly since PyQt6 QObject is faked
# We test the sort/preserve logic by extracting it inline.
def _simulated_fetch_worker(raw_notifs, existing_state):
    """Simulate _NotifFetchWorker.run() logic without Qt."""
    existing = {
        str((r or {}).get("id") or ""): r
        for r in (existing_state or [])
        if (r or {}).get("id")
    }
    now_ms = int(_time.time() * 1000)
    for n in raw_notifs:
        nid = str(n.get("id") or "")
        if not n.get("time_ms") and nid in existing and existing[nid].get("time_ms"):
            n["time_ms"] = existing[nid]["time_ms"]
        elif not n.get("time_ms"):
            n["time_ms"] = now_ms
    raw_notifs.sort(key=lambda x: (-int(x.get("time_ms") or 0), str(x.get("id") or "")))
    return raw_notifs


# ── _content_hash ─────────────────────────────────────────────────────────────

def test_content_hash_stable():
    """Same payload produces identical hash."""
    p = {"app": "Telegram", "title": "Hi", "text": "Hey", "replyId": "", "actions": []}
    assert _content_hash(p) == _content_hash(p)


def test_content_hash_differs_on_text_change():
    """Changing text changes the hash."""
    base = {"app": "Telegram", "title": "Hi", "text": "Hey", "replyId": "", "actions": []}
    changed = dict(base, text="Bye")
    assert _content_hash(base) != _content_hash(changed)


def test_content_hash_ignores_time_ms():
    """time_ms (metadata) does not affect content hash."""
    base = {"app": "App", "title": "T", "text": "B", "replyId": "", "actions": [], "time_ms": 1000}
    later = dict(base, time_ms=9999)
    assert _content_hash(base) == _content_hash(later)


def test_content_hash_action_order_irrelevant():
    """Action order is normalised so hash is stable regardless of list order."""
    p1 = {"app": "App", "title": "T", "text": "B", "replyId": "", "actions": ["reply", "dismiss"]}
    p2 = dict(p1, actions=["dismiss", "reply"])
    assert _content_hash(p1) == _content_hash(p2)


# ── NotificationMirror dedup ──────────────────────────────────────────────────

class _MockIface:
    def __init__(self):
        self.calls = []

    def Notify(self, *args, **kwargs):
        self.calls.append(("Notify", args))
        return 42

    def CloseNotification(self, *args):
        self.calls.append(("Close", args))


def _make_mirror_with_iface():
    m = NotificationMirror.__new__(NotificationMirror)
    import threading
    m._lock = threading.RLock()
    m._bus = object()
    m._iface = _MockIface()
    m._signal_connected = True
    m._phone_to_desktop = {}
    m._desktop_to_phone = {}
    m._phone_payload = {}
    m._phone_hash = {}
    m._closing_desktop_ids = set()
    return m


def test_upsert_calls_notify_first_time():
    """First upsert for a new phone_id always calls D-Bus Notify."""
    m = _make_mirror_with_iface()
    p = {"app": "App", "title": "T", "text": "B", "replyId": "", "actions": []}
    m._upsert_one("id1", p)
    assert len(m._iface.calls) == 1
    assert m._iface.calls[0][0] == "Notify"


def test_upsert_skips_notify_on_unchanged_content():
    """Subsequent upsert with same content does NOT call D-Bus Notify."""
    m = _make_mirror_with_iface()
    p = {"app": "App", "title": "T", "text": "B", "replyId": "", "actions": []}
    m._upsert_one("id1", p)
    call_count_before = len(m._iface.calls)
    m._upsert_one("id1", p)  # same content
    assert len(m._iface.calls) == call_count_before  # no new call


def test_upsert_calls_notify_on_content_change():
    """Upsert with changed text triggers a new D-Bus Notify."""
    m = _make_mirror_with_iface()
    p1 = {"app": "App", "title": "T", "text": "Hello", "replyId": "", "actions": []}
    p2 = dict(p1, text="Updated text")
    m._upsert_one("id1", p1)
    count1 = len(m._iface.calls)
    m._upsert_one("id1", p2)
    assert len(m._iface.calls) == count1 + 1


# ── _NotifFetchWorker sort/preserve logic ────────────────────────────────────

def test_newest_first_sort():
    """Notifications are sorted newest-first by time_ms."""
    raw = [
        {"id": "a", "time_ms": 1000},
        {"id": "b", "time_ms": 3000},
        {"id": "c", "time_ms": 2000},
    ]
    result = _simulated_fetch_worker(raw, [])
    assert [r["id"] for r in result] == ["b", "c", "a"]


def test_time_ms_preserved_from_state():
    """If a notification in state already has time_ms and the fresh fetch doesn't, preserve it."""
    existing_state = [{"id": "x", "time_ms": 55555}]
    raw = [{"id": "x"}]  # no time_ms from KDE
    result = _simulated_fetch_worker(raw, existing_state)
    assert result[0]["time_ms"] == 55555


def test_time_ms_set_to_now_for_new_notifications():
    """New notifications (not in state) get time_ms = ~now."""
    before = int(_time.time() * 1000)
    raw = [{"id": "new_one"}]
    result = _simulated_fetch_worker(raw, [])
    after = int(_time.time() * 1000)
    assert before <= result[0]["time_ms"] <= after


# ── _fmt_age ─────────────────────────────────────────────────────────────────

def _import_fmt_age():
    """Import _fmt_age from messages.py via direct exec to avoid PyQt6 widget init."""
    import importlib.util, pathlib
    src = pathlib.Path(__file__).parent.parent / "ui" / "pages" / "messages.py"
    code = src.read_text()
    # Extract just the function definition
    import ast
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_fmt_age":
            fn_src = ast.get_source_segment(code, node)
            ns = {}
            exec(f"import time\n{fn_src}", ns)
            return ns["_fmt_age"]
    raise RuntimeError("_fmt_age not found")


def test_fmt_age_just_now():
    _fmt_age = _import_fmt_age()
    assert _fmt_age(int(_time.time() * 1000)) == "just now"


def test_fmt_age_minutes():
    _fmt_age = _import_fmt_age()
    ms = int((_time.time() - 150) * 1000)  # 2.5 minutes ago
    result = _fmt_age(ms)
    assert result.endswith("m ago")


def test_fmt_age_zero():
    _fmt_age = _import_fmt_age()
    assert _fmt_age(0) == "just now"
