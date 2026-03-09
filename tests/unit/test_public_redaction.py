"""Deterministic regression tests for public redaction rules."""

from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SED_RULES = REPO_ROOT / "scripts" / "public_redact.sed"


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
    raw = "user@host /home/testuser 192.0.2.44 AA:BB:CC:DD:EE:FF nodekey:abcd1234 0123456789abcdef0123456789abcdef\n"
    out = _sanitize(raw)
    assert "<USER>@<HOST>" in out
    assert "/home/<USER>" in out
    assert "<IP_REDACTED>" in out
    assert "<MAC_REDACTED>" in out
    assert "nodekey:<REDACTED>" in out
    assert ("<HEX32_REDACTED>" in out) or ("<TOKEN_REDACTED>" in out)


def test_private_audit_tree_is_not_present():
    assert not (REPO_ROOT / "docs" / "audit").exists()


def test_public_tree_has_no_absolute_home_paths():
    scan_roots = [REPO_ROOT / "README.md", REPO_ROOT / "docs", REPO_ROOT / "backend", REPO_ROOT / "ui", REPO_ROOT / "scripts", REPO_ROOT / "tests"]
    allowlist = {
        "scripts/public_redact.sed",
        "scripts/audit_sanitize.sed",
        "tests/unit/test_public_redaction.py",
        "tests/unit/test_installed_runtime_paths.py",
    }
    hits = []
    for root in scan_roots:
        if root.is_file():
            candidates = [root]
        else:
            candidates = [path for path in root.rglob("*") if path.is_file()]
        for path in candidates:
            rel = str(path.relative_to(REPO_ROOT))
            if rel in allowlist:
                continue
            if rel.startswith("tests/hardware/.artifacts/"):
                continue
            if rel.startswith("tests/hardware/.live_acceptance_"):
                continue
            if path.name.endswith((".pyc", ".pyo")):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if "/home/" in text:
                hits.append(rel)
    assert hits == []
