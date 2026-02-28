#!/usr/bin/env python3
"""PhoneBridge — entry point."""
import sys, os, argparse, socket
import importlib
import logging
import shutil
import subprocess
import time

try:
    import fcntl
except Exception:  # pragma: no cover - non-POSIX fallback
    fcntl = None


def _is_known_runtime_issue(exc: Exception) -> bool:
    msg = str(exc or "")
    if "libGL.so.1" in msg:
        return True
    if isinstance(exc, ModuleNotFoundError):
        mod = getattr(exc, "name", "") or ""
        if mod == "dbus" or mod.startswith("dbus."):
            return True
    if "No module named 'dbus'" in msg:
        return True
    return False


def _query_system_site_packages() -> str | None:
    candidates = []
    user = os.environ.get("USER")
    if user:
        candidates.append(f"/etc/profiles/per-user/{user}/bin/python")
    candidates.extend(["/run/current-system/sw/bin/python3", "python3"])

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            result = subprocess.run(
                [candidate, "-c", "import site; print(site.getsitepackages()[0])"],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            continue
        if result.returncode == 0:
            site_dir = (result.stdout or "").strip()
            if site_dir:
                return site_dir
    return None


def _ensure_runtime_or_reexec():
    if os.environ.get("PHONEBRIDGE_BOOTSTRAPPED") == "1":
        return
    if not sys.platform.startswith("linux"):
        return
    try:
        importlib.import_module("PyQt6.QtWidgets")
        importlib.import_module("dbus.mainloop.glib")
        return
    except Exception as exc:
        if not _is_known_runtime_issue(exc):
            return

    steam_run = shutil.which("steam-run")
    if not steam_run:
        print(
            "PhoneBridge bootstrap failed: 'steam-run' is required.\n"
            "Install it with:\n"
            "  nix profile install nixpkgs#steam-run",
            file=sys.stderr,
        )
        raise SystemExit(2)

    env = os.environ.copy()
    env["PHONEBRIDGE_BOOTSTRAPPED"] = "1"
    sys_site = _query_system_site_packages()
    if sys_site:
        current = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{sys_site}:{current}" if current else sys_site

    # Re-exec via steam-run so Qt wheel runtime deps (libGL) are available.
    argv = [steam_run, sys.executable, os.path.abspath(__file__), *sys.argv[1:]]
    os.execvpe(steam_run, argv, env)

def send_ipc_to_running(command: bytes):
    for path in _candidate_socket_paths():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.4)
                sock.connect(path)
                sock.sendall(command)
            return True
        except OSError:
            continue
    return False


def wait_and_send_ipc(command: bytes, timeout_ms: int = 5000, step_ms: int = 100) -> bool:
    deadline = time.monotonic() + (max(0, int(timeout_ms)) / 1000.0)
    step_s = max(10, int(step_ms)) / 1000.0
    while time.monotonic() < deadline:
        if send_ipc_to_running(command):
            return True
        time.sleep(step_s)
    return False


def _acquire_singleton_lock() -> tuple[int | None, bool]:
    if fcntl is None:
        return None, True
    path = _lock_path()
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return None, False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd, True
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return None, False


def _release_singleton_lock(fd: int | None) -> None:
    if fd is None:
        return
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
    try:
        os.close(fd)
    except OSError:
        pass


def _ipc_base_dir() -> str:
    uid = os.getuid()
    candidates = [
        os.environ.get("XDG_RUNTIME_DIR", "").strip(),
        f"/run/user/{uid}",
        "/tmp",
    ]
    for path in candidates:
        if not path:
            continue
        if os.path.isdir(path) and os.access(path, os.W_OK | os.X_OK):
            return path
    return "/tmp"


def _socket_path() -> str:
    return os.path.join(_ipc_base_dir(), f"phonebridge-{os.getuid()}.sock")


def _candidate_socket_paths() -> list[str]:
    primary = _socket_path()
    legacy_tmp = f"/tmp/phonebridge-{os.getuid()}.sock"
    out = [primary]
    if legacy_tmp != primary:
        out.append(legacy_tmp)
    return out


def _lock_path() -> str:
    return os.path.join(_ipc_base_dir(), f"phonebridge-{os.getuid()}.lock")

def main():
    _ensure_runtime_or_reexec()

    parser = argparse.ArgumentParser()
    parser.add_argument('--background', action='store_true')
    parser.add_argument('--toggle',     action='store_true')
    args = parser.parse_args()

    command = b"show"
    if args.toggle:
        command = b"toggle"
    elif args.background:
        command = b"noop"

    # Fast-path for existing pre-lock process generations.
    if send_ipc_to_running(command):
        return

    lock_fd, is_owner = _acquire_singleton_lock()
    if not is_owner:
        # Strict single-instance mode: forward and exit instead of launching.
        if wait_and_send_ipc(command, timeout_ms=5000, step_ms=100):
            print(f"PhoneBridge singleton: forwarded IPC command={command!r}", file=sys.stderr)
            return
        print(
            f"PhoneBridge singleton: lock denied and IPC forward timed out command={command!r}; "
            "request dropped to preserve singleton behavior.",
            file=sys.stderr,
        )
        return

    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
    from PyQt6.QtGui import QIcon
    from PyQt6.QtCore import QTimer, QSocketNotifier
    import dbus.mainloop.glib
    from backend.logger import setup_logging
    from backend.system_integration import ensure_system_integration

    log_path = setup_logging()
    log = logging.getLogger(__name__)
    log.info("Singleton ownership acquired (pid=%s lock_fd=%s)", os.getpid(), lock_fd)
    ensure_system_integration(os.path.dirname(os.path.abspath(__file__)))

    # GLib mainloop integration for D-Bus signals
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    app = QApplication(sys.argv)
    app.setApplicationName("PhoneBridge")
    app.setQuitOnLastWindowClosed(False)
    icon_path = os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps/phonebridge.svg")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    from ui.window import PhoneBridgeWindow
    window = PhoneBridgeWindow()

    # ── IPC socket for --toggle ──────────────────────────────
    sock_path = _socket_path()
    server = None
    notifier = None

    def _safe_unlink_ipc_socket():
        try:
            if os.path.exists(sock_path):
                os.unlink(sock_path)
        except OSError:
            log.exception("Failed to unlink IPC socket: %s", sock_path)

    def _cleanup_ipc():
        nonlocal server, notifier, lock_fd
        if notifier is not None:
            try:
                notifier.setEnabled(False)
                notifier.deleteLater()
            except Exception:
                pass
            notifier = None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
            server = None
        _safe_unlink_ipc_socket()
        _release_singleton_lock(lock_fd)
        lock_fd = None

    def _socket_reachable(path: str) -> bool:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                sock.connect(path)
                return True
        except OSError:
            return False

    if os.path.exists(sock_path):
        if _socket_reachable(sock_path):
            log.error("IPC socket already active at %s while owner lock held; refusing duplicate startup", sock_path)
            _release_singleton_lock(lock_fd)
            raise SystemExit(3)
        log.info("Removing stale IPC socket at %s", sock_path)
        _safe_unlink_ipc_socket()

    try:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(sock_path)
        server.listen(1)
        server.setblocking(False)
    except OSError:
        log.exception("Failed to initialize IPC socket at %s; refusing startup", sock_path)
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        _release_singleton_lock(lock_fd)
        _safe_unlink_ipc_socket()
        raise SystemExit(3)

    notifier = QSocketNotifier(server.fileno(), QSocketNotifier.Type.Read)

    def on_toggle():
        try:
            conn, _ = server.accept()
            msg = conn.recv(16)
            conn.close()
            if msg == b"toggle":
                if window.isVisible() and window.isActiveWindow():
                    window.hide()
                else:
                    window.show_and_raise()
            elif msg == b"show":
                window.show_and_raise()
            elif msg == b"noop":
                pass
        except Exception:
            log.exception("Failed to process toggle IPC message")

    notifier.activated.connect(on_toggle)
    app.aboutToQuit.connect(_cleanup_ipc)

    # ── System tray ──────────────────────────────────────────
    tray = QSystemTrayIcon(app)
    tray_icon = QIcon.fromTheme("phonebridge")
    if tray_icon.isNull() and os.path.exists(icon_path):
        tray_icon = QIcon(icon_path)
    if tray_icon.isNull():
        tray_icon = QIcon.fromTheme("smartphone")
    if tray_icon.isNull():
        tray_icon = QIcon.fromTheme("phone")
    if tray_icon.isNull():
        tray_icon = app.style().standardIcon(app.style().StandardPixmap.SP_DesktopIcon)
    tray.setIcon(tray_icon)
    tray.setToolTip("PhoneBridge")
    menu = QMenu()
    menu.addAction("Open PhoneBridge",   window.show_and_raise)
    check_action = menu.addAction("Check Connectivity  →")
    def _open_connectivity_from_tray():
        from PyQt6.QtGui import QCursor
        window.run_startup_check(
            from_tray=True,
            anchor_pos=QCursor.pos(),
            close_on_mouse_leave=True,
        )
    check_action.triggered.connect(_open_connectivity_from_tray)
    check_action.setToolTip("Run startup diagnostics popout now")
    check_action.setStatusTip("Run startup diagnostics popout now")
    menu.addSeparator()

    audio_action = menu.addAction("Route Phone Audio")
    audio_action.setCheckable(True)
    from backend.state import state
    from backend import audio_route
    def _toggle_audio(checked):
        if checked:
            audio_route.set_source("ui_global_toggle", True)
            audio_route.sync()
        else:
            audio_route.clear_all()
    audio_action.triggered.connect(_toggle_audio)

    def _sync_audio_tray(enabled):
        audio_action.blockSignals(True)
        audio_action.setChecked(bool(enabled))
        audio_action.blockSignals(False)
    state.subscribe("audio_redirect_enabled", _sync_audio_tray)
    _sync_audio_tray(state.get("audio_redirect_enabled"))

    menu.addSeparator()
    menu.addAction("Quit", window.quit_app)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda r: window.show_and_raise()
        if r == QSystemTrayIcon.ActivationReason.Trigger else None
    )
    tray.show()
    log.info("PhoneBridge started (pid=%s, socket=%s, log=%s)", os.getpid(), sock_path, log_path)

    # ── Startup ──────────────────────────────────────────────
    import backend.settings_store as settings
    if args.background and bool(settings.get("startup_check_on_login", True)):
        QTimer.singleShot(1200, lambda: window.run_startup_check(background_mode=True))
    if not args.background:
        window.show_and_raise()

    sys.exit(app.exec())

if __name__ == '__main__':
    main()
