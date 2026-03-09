"""Regression coverage for KDE call-signal registration fallback ordering."""

from __future__ import annotations

from backend import kde_signals


class _Log:
    def __init__(self):
        self.info_calls = []
        self.warning_calls = []
        self.error_calls = []

    def info(self, message, *args):
        self.info_calls.append(message % args if args else message)

    def warning(self, message, *args):
        self.warning_calls.append(message % args if args else message)

    def error(self, message, *args):
        self.error_calls.append(message % args if args else message)


class _KC:
    def __init__(self, fail_indices=None):
        self._calls = []
        self._fail_indices = set(fail_indices or ())
        self.log = _Log()

    def _dev(self, plugin=None):
        return f"/device/{plugin or 'root'}"

    def _add_signal_receiver(self, callback, **kwargs):
        idx = len(self._calls)
        self._calls.append(dict(kwargs))
        if idx in self._fail_indices:
            raise RuntimeError(f"fail-{idx}")
        return object()


def test_connect_call_signal_stops_after_primary_success():
    kc = _KC()

    assert kde_signals.connect_call_signal(kc, lambda *_a: None) is True
    assert len(kc._calls) == 1
    assert kc._calls[0]["bus_name"] == kde_signals.BUS_NAME
    assert kc._calls[0]["path"] == "/device/telephony"


def test_connect_call_signal_uses_second_fallback_if_primary_fails():
    kc = _KC(fail_indices={0})

    assert kde_signals.connect_call_signal(kc, lambda *_a: None) is True
    assert len(kc._calls) == 2
    assert "bus_name" not in kc._calls[1]
    assert kc._calls[1]["path"] == "/device/telephony"


def test_connect_call_signal_uses_device_path_only_if_other_attempts_fail():
    kc = _KC(fail_indices={0, 1})

    assert kde_signals.connect_call_signal(kc, lambda *_a: None) is True
    assert len(kc._calls) == 3
    assert kc._calls[2]["path"] == "/device/root"
