"""Deterministic sanitizer regression tests for audit evidence collection."""

from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SED_RULES = REPO_ROOT / "scripts" / "audit_sanitize.sed"


def _sanitize(text: str) -> str:
    proc = subprocess.run(
        ["sed", "-E", "-f", str(SED_RULES)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    return proc.stdout


def test_rotating_api_key_and_token_are_redacted():
    raw = "X-API-Key: abcdefghijklmnopqrstuvwxyz123456\nlongtoken=ABCDEFGHIJKLMNOPQRSTUVWXYZ012345\n"
    out = _sanitize(raw)
    assert "<API_KEY_REDACTED>" in out
    assert "abcdefghijklmnopqrstuvwxyz123456" not in out
    assert "<TOKEN_REDACTED>" in out


def test_identity_and_network_markers_are_redacted():
    raw = "user@host /home/raed 100.127.0.90 3C:B0:ED:92:B6:90 nodekey:abcd1234 a9fe30c209da40d4bddce484a2c4112a\n"
    out = _sanitize(raw)
    assert "<USER>@<HOST>" in out
    assert "/home/<USER>" in out
    assert "<IP_REDACTED>" in out
    assert "<MAC_REDACTED>" in out
    assert "nodekey:<REDACTED>" in out
    assert ("<HEX32_REDACTED>" in out) or ("<TOKEN_REDACTED>" in out)
