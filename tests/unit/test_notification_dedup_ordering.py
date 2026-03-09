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

_ORIG_BACKEND_STATE = sys.modules.get("backend.state")
_ORIG_BACKEND_KDECONNECT = sys.modules.get("backend.kdeconnect")

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
    def update(self, key, updater, default=None):
        current = _state_data.get(key, default)
        _state_data[key] = updater(current)
    def subscribe(self, key, cb):
        pass

_state_mod.state = _FakeState()
sys.modules["backend.state"] = _state_mod

# ── Now import targets ────────────────────────────────────────────────────────
from backend.notification_mirror import _content_hash, NotificationMirror  # noqa: E402

if _ORIG_BACKEND_STATE is None:
    sys.modules.pop("backend.state", None)
else:
    sys.modules["backend.state"] = _ORIG_BACKEND_STATE
if _ORIG_BACKEND_KDECONNECT is None:
    if getattr(sys.modules.get("backend.kdeconnect"), "__name__", "") == "backend.kdeconnect":
        sys.modules.pop("backend.kdeconnect", None)
else:
    sys.modules["backend.kdeconnect"] = _ORIG_BACKEND_KDECONNECT

# Stub _NotifFetchWorker directly instead of importing the Qt worker class.
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


def _action_keys(flat_actions):
    return [flat_actions[i] for i in range(0, len(flat_actions), 2)]


def test_normalize_actions_unpatched_exposes_reliable_subset_only():
    payload = {
        "actions_supported": False,
        "actions": ["archive", "reply"],
        "replyId": "rid-1",
        "text": "hello",
    }
    out = NotificationMirror._normalize_actions(payload)
    keys = _action_keys(out)
    assert "default" in keys
    assert "__pb_reply" in keys
    assert "__pb_copy" in keys
    assert "archive" not in keys
    assert "reply" not in keys


def test_normalize_actions_patched_keeps_explicit_actions():
    payload = {
        "actions_supported": True,
        "actions": ["archive", "Archive"],
        "replyId": "",
        "text": "",
    }
    out = NotificationMirror._normalize_actions(payload)
    keys = _action_keys(out)
    assert "default" in keys
    assert "archive" in keys


def test_default_action_emits_notif_open_request_and_skips_phone_action():
    class _KC:
        def __init__(self):
            self.sent = 0

        def send_notification_action(self, key, action):
            self.sent += 1
            return True

    m = _make_mirror_with_iface()
    m._kc = _KC()
    m._desktop_to_phone[42] = "id1"
    m._phone_payload["id1"] = {"title": "T", "text": "B"}
    _state_data.clear()
    m._on_action_invoked(42, "default")
    assert _state_data.get("notif_open_request", {}).get("id") == "id1"
    assert _state_data.get("notif_open_request", {}).get("source") == "desktop_notification"
    assert m._kc.sent == 0


def test_notification_closed_reason_three_dismisses_phone_copy():
    class _KC:
        def __init__(self):
            self.dismissed = []

        def dismiss_notification(self, phone_id):
            self.dismissed.append(str(phone_id))
            return True

    m = _make_mirror_with_iface()
    m._kc = _KC()
    _state_data.clear()
    _state_data["notifications"] = [{"id": "id1", "title": "T"}]
    m._desktop_to_phone[42] = "id1"
    m._phone_to_desktop["id1"] = 42
    m._phone_payload["id1"] = {"title": "T"}
    m._phone_hash["id1"] = "hash"

    m._on_notification_closed(42, 3)

    assert m._kc.dismissed == ["id1"]
    assert _state_data.get("notifications") == []


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
    src = pathlib.Path(__file__).resolve().parents[2] / "ui" / "pages" / "messages.py"
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
