"""Mirror/Screen page — mirror and webcam controls."""
import os
import shutil
import subprocess
import time

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QGridLayout,
)
from PyQt6.QtCore import QTimer
from ui.theme import (
    card_frame,
    lbl,
    section_label,
    action_btn,
    input_field,
    toggle_switch,
    with_alpha,
    TEAL,
    VIOLET,
    ROSE,
    TEXT,
    TEXT_DIM,
)
from ui.motion import fade_in
from backend.adb_bridge import ADBBridge
from backend.ui_feedback import push_toast
import backend.settings_store as settings
from backend import audio_route


RECORDINGS_DIR = os.path.expanduser("~/PhoneSync/PhoneBridgeRecordings")


class MirrorPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self.adb = ADBBridge()
        self._current_proc = None
        self._webcam_record_proc = None
        self._webcam_record_path = ""
        self._mode = "mirror"
        self._display_orientation = 0
        self._mirror_audio_pref_at_launch = None
        self._last_prereq_ok_at = 0.0
        self._live_state = ""
        self._mode_switch_in_progress = False
        self._mode_switch_token = 0
        self._build()

        from backend.state import state
        state.subscribe("audio_redirect_enabled", self._on_audio_redirect_state_changed, owner=self)

        self._proc_timer = QTimer(self)
        self._proc_timer.timeout.connect(self._sync_process_state)
        self._proc_timer.start(900)

    @staticmethod
    def _kill_if_alive(proc):
        try:
            if proc and proc.poll() is None:
                proc.kill()
        except Exception:
            pass

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(lbl("Screen Mirror", 22, bold=True))

        intro = card_frame()
        il = QVBoxLayout(intro)
        il.setContentsMargins(16, 12, 16, 12)
        il.setSpacing(4)
        il.addWidget(section_label("Workflow"))
        il.addWidget(lbl("1) Pick mode  2) Launch feed  3) Use controls for screenshots, record, rotate, type", 11, TEXT_DIM))
        root.addWidget(intro)

        # Mode + launch rail
        launch_card = card_frame(accent=True)
        ll = QVBoxLayout(launch_card)
        ll.setContentsMargins(18, 16, 18, 16)
        ll.setSpacing(12)

        ll.addWidget(section_label("Mode"))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self._mode_btns = {}
        for mode_id, icon, title in [
            ("mirror", "▣", "Screen Mirror"),
            ("webcam", "◉", "Webcam"),
        ]:
            btn = QPushButton(f"{icon}\n{title}")
            btn.setCheckable(True)
            btn.setChecked(mode_id == "mirror")
            btn.setFixedHeight(74)
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: rgba(255,255,255,0.03);
                    border: 1px solid rgba(255,255,255,0.08);
                    border-radius: 14px;
                    color: rgba(255,255,255,0.62);
                    font-size: 11px;
                    padding: 8px 6px;
                }}
                QPushButton:hover:!checked {{
                    background: {with_alpha(VIOLET, 0.10)};
                    border-color: {with_alpha(VIOLET, 0.36)};
                    color: {TEXT};
                }}
                QPushButton:checked {{
                    background: {with_alpha(VIOLET, 0.18)};
                    border-color: {with_alpha(VIOLET, 0.54)};
                    color: {TEXT};
                }}
            """
            )
            btn.clicked.connect(lambda _, m=mode_id: self._select_mode(m))
            mode_row.addWidget(btn)
            self._mode_btns[mode_id] = btn
        ll.addLayout(mode_row)

        launch_row = QHBoxLayout()
        launch_row.setSpacing(10)
        self._launch_btn = action_btn("Launch Mirror", TEAL)
        self._launch_btn.clicked.connect(self._toggle_launch)
        launch_row.addWidget(self._launch_btn)

        self._live_dot = QFrame()
        self._live_dot.setFixedSize(9, 9)
        self._live_dot.setStyleSheet(f"background:{TEXT_DIM};border:none;border-radius:4px;")
        launch_row.addWidget(self._live_dot)
        self._live_lbl = lbl("Idle", 11, TEXT_DIM)
        launch_row.addWidget(self._live_lbl)
        launch_row.addStretch()
        ll.addLayout(launch_row)

        self._audio_row = QWidget()
        ar = QHBoxLayout(self._audio_row)
        ar.setContentsMargins(0, 0, 0, 0)
        ar.setSpacing(10)
        ar.addWidget(lbl("Route all phone audio to PC", 11, TEXT_DIM))
        ar.addStretch()
        self._audio_toggle = toggle_switch(bool(settings.get("audio_redirect", False)), VIOLET)
        self._audio_toggle.toggled.connect(self._toggle_audio_route)
        ar.addWidget(self._audio_toggle)
        ll.addWidget(self._audio_row)

        self._status_lbl = lbl("Ready", 10, TEXT_DIM)
        ll.addWidget(self._status_lbl)
        root.addWidget(launch_card)

        controls_card = card_frame()
        cl = QVBoxLayout(controls_card)
        cl.setContentsMargins(18, 14, 18, 14)
        cl.setSpacing(10)
        cl.addWidget(section_label("Controls"))

        self._controls_grid = QGridLayout()
        self._controls_grid.setSpacing(8)

        self._btn_screenshot = self._ctrl_btn("📸", "Screenshot")
        self._btn_screenshot.clicked.connect(self._screenshot)

        self._btn_screen_record = self._ctrl_btn("📹", "Record")
        self._btn_screen_record.setCheckable(True)
        self._btn_screen_record.clicked.connect(self._toggle_screen_record)

        self._btn_rotate = self._ctrl_btn("🔄", "Rotate")
        self._btn_rotate.clicked.connect(self._rotate)

        self._btn_type = self._ctrl_btn("⌨️", "Type")
        self._btn_type.clicked.connect(self._type_text)

        self._btn_webcam_photo = self._ctrl_btn("📷", "Take Photo")
        self._btn_webcam_photo.clicked.connect(self._capture_webcam_photo)

        self._btn_webcam_video = self._ctrl_btn("🎬", "Take Video")
        self._btn_webcam_video.setCheckable(True)
        self._btn_webcam_video.clicked.connect(self._toggle_webcam_record)

        cl.addLayout(self._controls_grid)
        root.addWidget(controls_card)

        root.addStretch()
        self._update_mode_ui(animated=False)
        self.sync_global_audio_state(force=False, quiet=True)

    def _ctrl_btn(self, icon, text):
        btn = QPushButton(f"{icon}\n{text}")
        btn.setFixedHeight(64)
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
                color: rgba(255,255,255,0.62);
                font-size: 11px;
                padding: 7px 6px;
            }}
            QPushButton:hover {{
                background: {with_alpha(VIOLET, 0.10)};
                border-color: {with_alpha(VIOLET, 0.42)};
                color: {TEXT};
            }}
            QPushButton:checked {{
                background: {with_alpha(VIOLET, 0.18)};
                border-color: {with_alpha(VIOLET, 0.54)};
                color: {TEXT};
            }}
            QPushButton:disabled {{
                color: {TEXT_DIM};
                border-color: rgba(255,255,255,0.08);
                background: rgba(255,255,255,0.02);
            }}
        """
        )
        return btn

    def _set_status(self, text, level="info"):
        self._status_lbl.setText(text)
        if level == "error":
            push_toast(text, "error", 2600)
        elif level == "warning":
            push_toast(text, "warning", 2300)

    def _set_live_indicator(self, state):
        # Avoid re-applying animation/style every timer tick.
        if state == self._live_state:
            return
        self._live_state = state

        anim = getattr(self._live_dot, "_pb_breathe_anim", None)
        if anim is not None:
            try:
                anim.stop()
            except Exception:
                pass
            self._live_dot._pb_breathe_anim = None
        effect = self._live_dot.graphicsEffect()
        if effect is not None:
            effect.setOpacity(1.0)

        if state == "recording":
            self._live_dot.setStyleSheet(f"background:{ROSE};border:none;border-radius:4px;")
            self._live_lbl.setText("Recording")
            self._live_lbl.setStyleSheet(f"color:{ROSE};font-size:11px;background:transparent;border:none;")
            return

        if state == "live":
            self._live_dot.setStyleSheet(f"background:{VIOLET};border:none;border-radius:4px;")
            self._live_lbl.setText("Live")
            self._live_lbl.setStyleSheet(f"color:{VIOLET};font-size:11px;background:transparent;border:none;")
            return

        self._live_dot.setStyleSheet(f"background:{TEXT_DIM};border:none;border-radius:4px;")
        self._live_lbl.setText("Idle")
        self._live_lbl.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;background:transparent;border:none;")

    def _update_mode_ui(self, animated=True):
        for mode_id, btn in self._mode_btns.items():
            btn.setChecked(mode_id == self._mode)

        self._audio_row.setVisible(self._mode == "mirror")

        while self._controls_grid.count():
            item = self._controls_grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)

        if self._mode == "mirror":
            controls = [
                self._btn_screenshot,
                self._btn_screen_record,
                self._btn_rotate,
                self._btn_type,
            ]
        else:
            controls = [
                self._btn_webcam_photo,
                self._btn_webcam_video,
                self._btn_rotate,
            ]

        for idx, btn in enumerate(controls):
            self._controls_grid.addWidget(btn, idx // 2, idx % 2)

        if animated:
            fade_in(self, level="subtle", start=0.88, end=1.0)

        self._sync_launch_label()
        self._sync_controls_enabled()

    def _on_audio_redirect_state_changed(self, enabled):
        if hasattr(self, "_audio_toggle"):
            self._audio_toggle.blockSignals(True)
            self._audio_toggle.setChecked(bool(enabled))
            self._audio_toggle.blockSignals(False)
        # Keep live mirror stream aligned with latest global audio preference.
        if self._mode == "mirror":
            self.sync_global_audio_state(force=False, quiet=True)

    def _sync_launch_label(self):
        running = self._current_proc is not None and self._current_proc.poll() is None
        target = "Mirror" if self._mode == "mirror" else "Webcam"
        self._launch_btn.setText(f"Stop {target}" if running else f"Launch {target}")
        self._set_live_indicator("live" if running else "idle")

    def _audio_pref_enabled(self):
        from backend.state import state
        return bool(state.get("audio_redirect_enabled", False))

    def is_mirror_stream_running(self):
        return (
            self._mode == "mirror"
            and self._current_proc is not None
            and self._current_proc.poll() is None
        )

    def sync_global_audio_state(self, force=False, quiet=False):
        if self._mode_switch_in_progress:
            return
        mirror_running = self.is_mirror_stream_running()
        desired_audio_pref = self._audio_pref_enabled()
        if (
            mirror_running
            and self._mirror_audio_pref_at_launch is not None
            and bool(self._mirror_audio_pref_at_launch) != bool(desired_audio_pref)
        ):
            self._set_status("Applying audio preference to active mirror stream")
            self._start_main_stream(skip_prereqs=True)
            mirror_running = self.is_mirror_stream_running()
        audio_route.sync(self.adb, suspend_ui_global=mirror_running)
        
        if force and not quiet:
            enabled = self._audio_pref_enabled()
            self._set_status("Global audio routing enabled" if enabled else "Global audio routing disabled")

    def _select_mode(self, mode_id):
        if self._mode == mode_id or self._mode_switch_in_progress:
            return
        self._mode_switch_token += 1
        token = self._mode_switch_token
        self._mode_switch_in_progress = True
        for b in self._mode_btns.values():
            b.setEnabled(False)
        self._launch_btn.setEnabled(False)
        self._sync_controls_enabled()
        if self._proc_timer.isActive():
            self._proc_timer.stop()
        was_running = False
        try:
            was_running = self._current_proc is not None and self._current_proc.poll() is None
            if was_running:
                self._stop_main_stream(clear_status=False, sync_audio=False, wait_for_exit=True)
            self._mode = mode_id
            self._update_mode_ui(animated=False)
        finally:
            QTimer.singleShot(90, lambda t=token, wr=was_running: self._finish_mode_switch(t, wr))

    def _finish_mode_switch(self, token, was_running):
        if token != self._mode_switch_token:
            return
        if was_running:
            self._start_main_stream_if_current_switch(token)
        self._mode_switch_in_progress = False
        for b in self._mode_btns.values():
            b.setEnabled(True)
        self._launch_btn.setEnabled(True)
        self._proc_timer.start(900)
        self._sync_controls_enabled()

    def _start_main_stream_if_current_switch(self, token):
        if token != self._mode_switch_token:
            return
        self._start_main_stream(skip_prereqs=True)

    def _get_cmd(self, mode):
        target = self.adb.target
        mirror_orientation = ""
        if mode == "mirror" and self._display_orientation:
            mirror_orientation = f" \\\n  --display-orientation={self._display_orientation}"
        mirror_audio = " \\\n  --audio-source output" if self._audio_pref_enabled() else " \\\n  --no-audio"
        if mode == "mirror":
            return (
                f"scrcpy --serial {target} \\\n  --video-bit-rate 8M{mirror_audio}{mirror_orientation} \\\n  --render-driver opengl"
            )

        webcam_cmd = (
            f"scrcpy --serial {target} \\\n  --video-source=camera \\\n  --camera-facing=front \\\n  --camera-size=1280x720"
        )
        if os.path.exists("/dev/video2"):
            webcam_cmd += " \\\n  --v4l2-sink=/dev/video2"
        webcam_cmd += " \\\n  --render-driver opengl"
        return webcam_cmd

    def _toggle_launch(self):
        running = self._current_proc is not None and self._current_proc.poll() is None
        if running:
            self._stop_main_stream()
            return
        self._start_main_stream()

    def _ensure_prereqs(self, *, skip_connectivity=False):
        from backend import preflight
        if not preflight.has("adb"):
            self._set_status(preflight.missing_text("adb"), "error")
            return False
        if not preflight.has("mirror"):
            self._set_status(preflight.missing_text("mirror"), "error")
            return False

        if not skip_connectivity:
            if not self.adb.is_connected():
                self.adb.connect_wifi()
            if not self.adb.is_connected():
                self._set_status(
                    "Phone not reachable. Open Network page, verify Tailscale/KDE, then retry.",
                    "warning",
                )
                win = self.window()
                if win and hasattr(win, "go_to"):
                    win.go_to("network")
                return False

        if self._mode == "webcam" and not os.path.exists("/dev/video2"):
            push_toast(
                "Webcam stream will run, but local photo/video capture needs /dev/video2 (v4l2loopback)",
                "warning",
                3000,
            )
        self._last_prereq_ok_at = time.time()
        return True

    def _start_main_stream(self, skip_prereqs=False):
        recent_ok = (time.time() - self._last_prereq_ok_at) < 6
        if not self._ensure_prereqs(skip_connectivity=bool(skip_prereqs or recent_ok)):
            return

        self._stop_main_stream(clear_status=False, sync_audio=False)
        extra = []
        mirror_audio_pref = self._audio_pref_enabled() if self._mode == "mirror" else None
        if self._mode == "mirror" and self._display_orientation:
            extra.append(f"--display-orientation={self._display_orientation}")
        if self._mode == "mirror" and not mirror_audio_pref:
            extra.append("--no-audio")
        if self._mode == "webcam" and os.path.exists("/dev/video2"):
            extra.append("--v4l2-sink=/dev/video2")

        self._current_proc = self.adb.launch_scrcpy(self._mode, extra_args=extra)
        if not self._current_proc:
            self._set_status("Could not launch scrcpy", "error")
            return
        if self._mode == "mirror":
            self._mirror_audio_pref_at_launch = bool(mirror_audio_pref)
        else:
            self._mirror_audio_pref_at_launch = None

        QTimer.singleShot(550, self._sync_process_state)
        self._set_status(f"{self._mode.title()} launch requested")
        self.sync_global_audio_state(force=False, quiet=True)
        self._sync_launch_label()
        self._sync_controls_enabled()

    def _stop_main_stream(self, clear_status=True, *, sync_audio=True, wait_for_exit=False):
        if self._current_proc:
            proc = self._current_proc
            try:
                proc.terminate()
            except Exception:
                pass
            if wait_for_exit:
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            else:
                QTimer.singleShot(
                    1400,
                    lambda p=proc: self._kill_if_alive(p),
                )
            self._current_proc = None
        self._mirror_audio_pref_at_launch = None

        if self._mode == "webcam" and self._btn_webcam_video.isChecked():
            self._btn_webcam_video.setChecked(False)
            self._stop_webcam_recording()

        if clear_status:
            self._set_status("Stream stopped")
        if sync_audio:
            self.sync_global_audio_state(force=False, quiet=True)
        self._sync_launch_label()
        self._sync_controls_enabled()

    def _toggle_audio_route(self, checked):
        enabled = bool(checked)
        if not enabled:
            audio_route.clear_all()
        else:
            audio_route.set_source("ui_global_toggle", True)
            settings.set("audio_redirect", True)
        self.sync_global_audio_state(force=True, quiet=False)
        win = self.window()
        dash = win.get_page("dashboard") if win and hasattr(win, "get_page") else None
        if dash and hasattr(dash, "_sync_audio_route_toggle"):
            dash._sync_audio_route_toggle()

    def _toggle_screen_record(self):
        if self._btn_screen_record.isChecked():
            info = self.adb.start_screen_recording(RECORDINGS_DIR)
            if not info:
                self._btn_screen_record.setChecked(False)
                self._set_status("Screen recording already running", "warning")
                return
            self._btn_screen_record.setText("■\nStop Recording")
            self._set_status("Screen recording started")
            return

        out = self.adb.stop_screen_recording(RECORDINGS_DIR)
        self._btn_screen_record.setText("📹\nRecord")
        if out:
            self._set_status(f"Saved: {out}")
        else:
            self._set_status("Screen recording stopped")

    def _capture_webcam_photo(self):
        if not os.path.exists("/dev/video2"):
            self._set_status("Webcam capture requires /dev/video2", "warning")
            return
        if not shutil.which("ffmpeg"):
            self._set_status("ffmpeg is required for webcam capture", "warning")
            return

        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        out = os.path.join(RECORDINGS_DIR, f"webcam_{int(time.time())}.jpg")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "v4l2", "-i", "/dev/video2", "-frames:v", "1", out],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=12,
            )
        except Exception:
            pass

        if os.path.exists(out):
            self._set_status(f"Saved: {out}")
        else:
            self._set_status("Could not capture webcam frame", "warning")

    def _toggle_webcam_record(self):
        if self._btn_webcam_video.isChecked():
            self._start_webcam_recording()
            return
        self._stop_webcam_recording()

    def _start_webcam_recording(self):
        if not os.path.exists("/dev/video2"):
            self._btn_webcam_video.setChecked(False)
            self._set_status("Webcam video capture requires /dev/video2", "warning")
            return
        if not shutil.which("ffmpeg"):
            self._btn_webcam_video.setChecked(False)
            self._set_status("ffmpeg is required for webcam video", "warning")
            return

        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        self._webcam_record_path = os.path.join(RECORDINGS_DIR, f"webcam_{int(time.time())}.mkv")
        self._webcam_record_proc = subprocess.Popen(
            [
                "ffmpeg",
                "-y",
                "-f",
                "v4l2",
                "-framerate",
                "30",
                "-video_size",
                "1280x720",
                "-i",
                "/dev/video2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                self._webcam_record_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._btn_webcam_video.setText("■\nStop Video")
        self._set_status("Webcam video recording started")

    def _stop_webcam_recording(self):
        if self._webcam_record_proc:
            try:
                self._webcam_record_proc.terminate()
                self._webcam_record_proc.wait(timeout=6)
            except Exception:
                try:
                    self._webcam_record_proc.kill()
                except Exception:
                    pass
            self._webcam_record_proc = None

        self._btn_webcam_video.setText("🎬\nTake Video")
        if self._webcam_record_path:
            self._set_status(f"Saved: {self._webcam_record_path}")
            self._webcam_record_path = ""
        else:
            self._set_status("Webcam recording stopped")

    def _screenshot(self):
        path = self.adb.screenshot()
        if not path:
            self._set_status("Screenshot failed", "warning")
            return
        try:
            subprocess.Popen(["xdg-open", path])
        except Exception:
            pass
        self._set_status(f"Saved: {path}")

    def _rotate(self):
        if self.adb.rotate_display():
            self._set_status("Rotated phone display")
            return

        if self._mode == "mirror":
            self._display_orientation = (self._display_orientation + 90) % 360
            if self._current_proc and self._current_proc.poll() is None:
                self._start_main_stream(skip_prereqs=True)
            self._set_status(f"Mirror orientation fallback: {self._display_orientation}\N{DEGREE SIGN}")
            return

        self._set_status("Rotate failed on device", "warning")

    def _type_text(self):
        from PyQt6.QtWidgets import QDialog

        d = QDialog(self)
        d.setWindowTitle("Type on Phone")
        d.setStyleSheet("background:#070c17;color:white;")
        d.resize(360, 130)
        lay = QVBoxLayout(d)
        lay.addWidget(lbl("Text to type on phone:", 13))
        inp = input_field("Enter text…")
        lay.addWidget(inp)
        send = action_btn("Type →", TEAL)
        send.clicked.connect(lambda: (self.adb.send_text(inp.text()), d.close()))
        lay.addWidget(send)
        d.exec()

    def _sync_process_state(self):
        if self._mode_switch_in_progress:
            return
        running_main = self._current_proc is not None and self._current_proc.poll() is None
        if not running_main and self._current_proc is not None:
            self._current_proc = None
            self._mirror_audio_pref_at_launch = None
            self._set_status("Stream ended", "info")
            self.sync_global_audio_state(force=False, quiet=True)

        rec_proc = getattr(self.adb, "_screenrecord_proc", None)
        rec_running = rec_proc is not None and rec_proc.poll() is None
        if not rec_running and self._btn_screen_record.isChecked():
            self._btn_screen_record.blockSignals(True)
            self._btn_screen_record.setChecked(False)
            self._btn_screen_record.blockSignals(False)
            self._btn_screen_record.setText("📹\nRecord")

        webcam_recording = self._webcam_record_proc is not None and self._webcam_record_proc.poll() is None
        if self._webcam_record_proc is not None and not webcam_recording:
            self._webcam_record_proc = None
        if not webcam_recording and self._btn_webcam_video.isChecked():
            self._btn_webcam_video.blockSignals(True)
            self._btn_webcam_video.setChecked(False)
            self._btn_webcam_video.blockSignals(False)
            self._btn_webcam_video.setText("🎬\nTake Video")

        self._sync_launch_label()
        self._sync_controls_enabled()
        if rec_running or webcam_recording:
            self._set_live_indicator("recording")

    def refresh(self):
        self._sync_process_state()

    def _sync_controls_enabled(self):
        if self._mode_switch_in_progress:
            self._launch_btn.setEnabled(False)
            for btn in (
                self._btn_screenshot,
                self._btn_screen_record,
                self._btn_rotate,
                self._btn_type,
                self._btn_webcam_photo,
                self._btn_webcam_video,
            ):
                btn.setEnabled(False)
            return

        running = self._current_proc is not None and self._current_proc.poll() is None
        self._launch_btn.setEnabled(True)
        mirror_controls = [
            self._btn_screenshot,
            self._btn_screen_record,
            self._btn_rotate,
            self._btn_type,
        ]
        webcam_controls = [
            self._btn_webcam_photo,
            self._btn_webcam_video,
            self._btn_rotate,
        ]
        if self._mode == "mirror":
            for btn in mirror_controls:
                btn.setEnabled(True)
            for btn in webcam_controls:
                if btn not in mirror_controls:
                    btn.setEnabled(False)
            return
        for btn in webcam_controls:
            btn.setEnabled(running)
        for btn in mirror_controls:
            if btn not in webcam_controls:
                btn.setEnabled(False)
