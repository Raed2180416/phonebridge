#!/usr/bin/env python3
"""PhoneBridge — entry point."""
import sys, os, argparse, socket
import importlib
import logging
import shutil
import subprocess


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
    path = f"/tmp/phonebridge-{os.getuid()}.sock"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            sock.connect(path)
            sock.sendall(command)
        return True
    except OSError:
        return False

def main():
    _ensure_runtime_or_reexec()

    parser = argparse.ArgumentParser()
    parser.add_argument('--background', action='store_true')
    parser.add_argument('--toggle',     action='store_true')
    args = parser.parse_args()

    # Enforce single-instance behavior.
    if args.toggle:
        if send_ipc_to_running(b"toggle"):
            return
    elif args.background:
        if send_ipc_to_running(b"noop"):
            return
    else:
        if send_ipc_to_running(b"show"):
            return

    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
    from PyQt6.QtGui import QIcon
    from PyQt6.QtCore import QTimer, QSocketNotifier
    import dbus.mainloop.glib
    from backend.logger import setup_logging
    from backend.system_integration import ensure_system_integration

    log_path = setup_logging()
    log = logging.getLogger(__name__)
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
    sock_path = f"/tmp/phonebridge-{os.getuid()}.sock"
    server = None
    notifier = None

    def _safe_unlink_ipc_socket():
        try:
            if os.path.exists(sock_path):
                os.unlink(sock_path)
        except OSError:
            log.exception("Failed to unlink IPC socket: %s", sock_path)

    def _cleanup_ipc():
        nonlocal server, notifier
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

    _safe_unlink_ipc_socket()
    try:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(sock_path)
        server.listen(1)
        server.setblocking(False)
    except OSError:
        log.exception("Failed to initialize IPC socket at %s; continuing without local IPC", sock_path)
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
            server = None
        _safe_unlink_ipc_socket()

    if server is not None:
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
    menu.addAction("Quit", lambda: (
        _cleanup_ipc(),
        window.quit_app()
    ))
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
