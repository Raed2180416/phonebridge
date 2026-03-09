from __future__ import annotations

from backend import adb_telephony


class _FakeLog:
    def debug(self, *args, **kwargs):
        return None


class _FakeBridge:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.log = _FakeLog()
        self._fast_call_state_value = "unknown"
        self._fast_call_state_at = 0.0
        self._fast_call_state_fallback_at = 0.0

    def _resolve_target(self, allow_connect=False):
        return "serial-1"

    def _run_adb(self, *args, timeout=8):
        self.calls.append((args, timeout))
        if not self.responses:
            raise AssertionError("unexpected adb call")
        return self.responses.pop(0)

    def _run_on_serial(self, serial, *args, timeout=8, allow_connect_retry=True):
        ok, out = self._run_adb("-s", serial, *args, timeout=timeout)
        return serial, ok, out


def test_get_call_state_fast_uses_getprop_without_dumpsys_when_state_is_known(monkeypatch):
    bridge = _FakeBridge(
        [
            (True, "offhook\n"),
        ]
    )

    monkeypatch.setattr(adb_telephony.time, "monotonic", lambda: 10.0)
    assert adb_telephony.get_call_state_fast(bridge) == "offhook"
    assert len(bridge.calls) == 1
    assert "getprop" in bridge.calls[0][0]
    assert bridge._fast_call_state_value == "offhook"


def test_get_call_state_fast_rate_limits_dumpsys_fallback_and_reuses_recent_cache(monkeypatch):
    times = iter([10.0, 11.0, 12.0])
    monkeypatch.setattr(adb_telephony.time, "monotonic", lambda: next(times))

    bridge = _FakeBridge(
        [
            (False, "timeout"),
            (True, "mCallState=0\n"),
            (False, "timeout"),
        ]
    )

    assert adb_telephony.get_call_state_fast(bridge) == "idle"
    assert len(bridge.calls) == 2
    assert "dumpsys" in bridge.calls[1][0]

    # Second call is within the fallback rate-limit window, so it should not
    # invoke dumpsys again and should reuse the recent cached idle state.
    assert adb_telephony.get_call_state_fast(bridge) == "idle"
    assert len(bridge.calls) == 3
    assert "getprop" in bridge.calls[-1][0]
