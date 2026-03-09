#!/usr/bin/env python3
"""PhoneBridge — entry point."""
import sys, os, argparse, socket, json
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
        # Recovery path: owner may have died between lock check and IPC server
        # availability, or may be wedged during startup. Retry lock acquisition
        # briefly before dropping the request.
        retry_deadline = time.monotonic() + 3.0
        while time.monotonic() < retry_deadline:
            time.sleep(0.15)
            lock_fd, is_owner = _acquire_singleton_lock()
            if is_owner:
                break
            if wait_and_send_ipc(command, timeout_ms=250, step_ms=50):
                print(f"PhoneBridge singleton: forwarded IPC command={command!r} (late)", file=sys.stderr)
                return
        if is_owner:
            print("PhoneBridge singleton: recovered stale lock owner; launching new instance", file=sys.stderr)
        else:
            print(
                f"PhoneBridge singleton: lock denied and IPC forward timed out command={command!r}; "
                "request dropped to preserve singleton behavior.",
                file=sys.stderr,
            )
            return

    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
    from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen
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
    # Ensure compositor/taskbar maps this process to phonebridge.desktop even
    # when launched from scripts (e.g. toggle keybind) instead of app launcher.
    try:
        app.setDesktopFileName("phonebridge")
    except Exception:
        pass
    app.setQuitOnLastWindowClosed(False)

    def _icon_has_pixels(icon: QIcon) -> bool:
        if icon.isNull():
            return False
        try:
            return not icon.pixmap(24, 24).isNull()
        except Exception:
            return False

    def _build_fallback_icon() -> QIcon:
        # Programmatic icon that does not depend on SVG/icon theme plugins.
        size = 64
        pm = QPixmap(size, size)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Device body
        p.setPen(QPen(QColor("#0f172a"), 2))
        p.setBrush(QColor("#111827"))
        p.drawRoundedRect(10, 4, 44, 56, 10, 10)

        # Screen
        p.setPen(QPen(QColor("#94a3b8"), 1))
        p.setBrush(QColor("#1f2937"))
        p.drawRoundedRect(16, 11, 32, 40, 5, 5)

        # Home dot
        p.setPen(QPen(QColor("#e2e8f0"), 1))
        p.setBrush(QColor("#e2e8f0"))
        p.drawEllipse(30, 53, 4, 4)
        p.end()
        return QIcon(pm)

    icon_path = os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps/phonebridge.svg")
    # Build a single QIcon to share across app, window, and tray so they
    # always render identically (fromTheme is unreliable inside bwrap).
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
    else:
        app_icon = QIcon.fromTheme("phonebridge")
    if not _icon_has_pixels(app_icon):
        app_icon = QIcon.fromTheme("phonebridge")
    if not _icon_has_pixels(app_icon):
        app_icon = QIcon.fromTheme("smartphone")
    if not _icon_has_pixels(app_icon):
        app_icon = QIcon.fromTheme("phone")
    if not _icon_has_pixels(app_icon):
        app_icon = _build_fallback_icon()
    if _icon_has_pixels(app_icon):
        app.setWindowIcon(app_icon)

    from ui.window import PhoneBridgeWindow
    window = PhoneBridgeWindow()
    if not app_icon.isNull():
        try:
            window.setWindowIcon(app_icon)
        except Exception:
            pass

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

    def _files_page():
        window.show_and_raise(reason="ipc:files_page")
        window.go_to("files")
        return window.get_page("files")

    def _dispatch_ipc_json(payload: dict) -> None:
        cmd = str((payload or {}).get("cmd") or "").strip().lower()
        if cmd == "goto":
            page = str((payload or {}).get("page") or "").strip()
            if page:
                log.info("IPC: goto page=%s", page)
                window.show_and_raise(reason=f"ipc_json:goto:{page}")
                window.go_to(page)
            return
        if cmd == "files_refresh":
            page = _files_page()
            if page and hasattr(page, "refresh"):
                log.info("IPC: files_refresh")
                page.refresh()
            return
        if cmd == "files_open":
            folder_id = str((payload or {}).get("folder_id") or "").strip()
            page = _files_page()
            ok = bool(page and hasattr(page, "open_folder_by_id") and page.open_folder_by_id(folder_id))
            log.info("IPC: files_open folder_id=%s ok=%s", folder_id, ok)
            return
        if cmd == "files_add_custom":
            folder_id = str((payload or {}).get("folder_id") or "").strip()
            name = str((payload or {}).get("name") or "").strip()
            path = str((payload or {}).get("path") or "").strip()
            page = _files_page()
            ok = bool(
                page
                and hasattr(page, "add_custom_folder_for_test")
                and page.add_custom_folder_for_test(folder_id, name, path)
            )
            log.info("IPC: files_add_custom folder_id=%s ok=%s path=%s", folder_id, ok, path)
            return
        if cmd == "files_remove_custom":
            folder_id = str((payload or {}).get("folder_id") or "").strip()
            page = _files_page()
            ok = bool(
                page
                and hasattr(page, "remove_custom_folder_for_test")
                and page.remove_custom_folder_for_test(folder_id)
            )
            log.info("IPC: files_remove_custom folder_id=%s ok=%s", folder_id, ok)
            return
        if cmd == "files_mkdir":
            folder_id = str((payload or {}).get("folder_id") or "").strip()
            name = str((payload or {}).get("name") or "").strip()
            page = _files_page()
            ok = bool(
                page
                and hasattr(page, "create_subfolder_for_test")
                and page.create_subfolder_for_test(folder_id, name)
            )
            log.info("IPC: files_mkdir folder_id=%s name=%s ok=%s", folder_id, name, ok)
            return
        if cmd == "test_call_event":
            event = str((payload or {}).get("event") or "").strip() or "ringing"
            number = str((payload or {}).get("number") or "").strip()
            name = str((payload or {}).get("name") or number or "Test Call").strip()
            log.info("IPC: test_call_event event=%s number=%s name=%s", event, number, name)
            normalized = event.strip().lower()
            if normalized in {"ringing", "incoming", "incoming_call", "talking", "active", "answered"}:
                window._suspend_poll_until = time.time() + 10.0
            elif normalized in {"ended", "end", "missed", "missed_call", "rejected", "declined"}:
                window._suspend_poll_until = time.time() + 1.0
            window._on_call_received(event, number, name)
            return
        log.warning("IPC: unsupported json command=%s", cmd)

    def _dispatch_ipc_message(msg: bytes) -> None:
        if not msg:
            return
        text = msg.decode("utf-8", "replace").strip()
        if not text:
            return
        if text.startswith("{"):
            try:
                payload = json.loads(text)
            except Exception:
                log.exception("IPC: invalid JSON payload")
            else:
                if isinstance(payload, dict):
                    _dispatch_ipc_json(payload)
                else:
                    log.warning("IPC: JSON payload must be an object")
            return
        if msg == b"toggle":
            if window.isVisible() and window.isActiveWindow():
                window.hide()
            else:
                window.show_and_raise(reason="ipc:toggle")
            return
        if msg == b"quit":
            log.info("IPC: quit request received")
            app.quit()
            return
        if msg == b"show":
            window.show_and_raise(reason="ipc:show")
            return
        if text.startswith("goto:"):
            page = text.split(":", 1)[1].strip()
            if page:
                log.info("IPC: goto page=%s", page)
                window.show_and_raise(reason=f"ipc:goto:{page}")
                window.go_to(page)
            return
        if msg == b"test_call":
            log.info("IPC: test_call trigger received")
            popup = window._ensure_call_popup()
            log.info("IPC: deferring handle_call_event 150ms")
            from PyQt6.QtCore import QTimer as _QT
            window._suspend_poll_until = time.time() + 300
            def _deferred_call():
                popup.handle_call_event("+0000000000", "Test Call", "ringing")
                popup._stop_state_watcher()
                log.info("IPC: test_call popup active (poll+watcher suppressed)")
            _QT.singleShot(150, _deferred_call)
            return
        if msg == b"test_missed":
            log.info("IPC: test_missed trigger received")
            popup = window._ensure_call_popup()
            popup.handle_call_event("+0000000000", "Test Missed", "missed_call")
            log.info("IPC: test_missed popup shown")
            return
        if msg == b"noop":
            return
        log.warning("IPC: unsupported raw command=%r", msg)

    def on_toggle():
        try:
            conn, _ = server.accept()
            msg = conn.recv(4096).strip()
            conn.close()
            _dispatch_ipc_message(msg)
        except Exception:
            log.exception("Failed to process toggle IPC message")

    notifier.activated.connect(on_toggle)
    app.aboutToQuit.connect(_cleanup_ipc)

    # ── System tray ──────────────────────────────────────────
    tray = None
    tray_retry_timer = QTimer(app)
    tray_retry_timer.setInterval(3000)
    tray_retry_timer.setSingleShot(False)

    def _resolve_tray_icon() -> QIcon:
        if not app_icon.isNull():
            return app_icon
        return app.style().standardIcon(app.style().StandardPixmap.SP_DesktopIcon)

    def _install_tray() -> bool:
        nonlocal tray
        if tray is not None:
            return True
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False

        tray = QSystemTrayIcon(app)
        tray.setIcon(_resolve_tray_icon())
        tray.setToolTip("PhoneBridge")
        menu = QMenu()
        menu.addAction("Open PhoneBridge",   window.show_and_raise)
        check_action = menu.addAction("Check Connectivity  →")

        def _open_connectivity_from_tray():
            from PyQt6.QtGui import QCursor
            window.run_startup_check(
                from_tray=True,
                anchor_pos=QCursor.pos(),
                close_on_mouse_leave=False,
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
            lambda r: window.show_and_raise(reason="tray:click")
            if r == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        tray.show()
        return True

    def _retry_tray_install():
        if _install_tray():
            tray_retry_timer.stop()
            log.info("System tray icon ready")

    if _install_tray():
        log.info("PhoneBridge started (pid=%s, socket=%s, log=%s)", os.getpid(), sock_path, log_path)
    else:
        log.warning("System tray unavailable at startup; will retry until available")
        tray_retry_timer.timeout.connect(_retry_tray_install)
        tray_retry_timer.start()
        log.info("PhoneBridge started without tray yet (pid=%s, socket=%s, log=%s)", os.getpid(), sock_path, log_path)

    # ── Startup ──────────────────────────────────────────────
    import backend.settings_store as settings
    if not args.background:
        window.show_and_raise(reason="startup:foreground")

    sys.exit(app.exec())

if __name__ == '__main__':
    main()
