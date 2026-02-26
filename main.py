#!/usr/bin/env python3
"""PhoneBridge — entry point."""
import sys, os, argparse, socket
import logging

def send_ipc_to_running(command: bytes):
    path = f"/tmp/phonebridge-{os.getuid()}.sock"
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(path)
        sock.send(command)
        sock.close()
        return True
    except:
        return False

def main():
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

    log_path = setup_logging()
    log = logging.getLogger(__name__)

    # GLib mainloop integration for D-Bus signals
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    app = QApplication(sys.argv)
    app.setApplicationName("PhoneBridge")
    app.setQuitOnLastWindowClosed(False)

    from ui.window import PhoneBridgeWindow
    window = PhoneBridgeWindow()

    # ── IPC socket for --toggle ──────────────────────────────
    sock_path = f"/tmp/phonebridge-{os.getuid()}.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    server.bind(sock_path)
    server.listen(1)
    server.setblocking(False)

    notifier = QSocketNotifier(server.fileno(), QSocketNotifier.Type.Read)
    def on_toggle():
        try:
            conn, _ = server.accept()
            msg = conn.recv(16)
            conn.close()
            if msg == b"toggle":
                if window.isVisible():
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

    # ── System tray ──────────────────────────────────────────
    tray = QSystemTrayIcon(app)
    tray_icon = QIcon.fromTheme("smartphone")
    if tray_icon.isNull():
        tray_icon = QIcon.fromTheme("phone")
    if tray_icon.isNull():
        tray_icon = app.style().standardIcon(app.style().StandardPixmap.SP_DesktopIcon)
    tray.setIcon(tray_icon)
    tray.setToolTip("PhoneBridge")
    menu = QMenu()
    menu.addAction("Open PhoneBridge",   window.show_and_raise)
    menu.addAction("Check Connectivity", window.run_startup_check)
    menu.addSeparator()
    menu.addAction("Quit", lambda: (
        os.unlink(sock_path) if os.path.exists(sock_path) else None,
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
    if args.background or args.toggle:
        from backend.startup_check import StartupChecker
        QTimer.singleShot(2500, lambda: StartupChecker(window).run_and_show())
    else:
        window.show_and_raise()

    sys.exit(app.exec())

if __name__ == '__main__':
    main()
