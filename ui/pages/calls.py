"""Calls page — place calls, contacts, call history"""
import logging
import threading

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QFrame,
                              QGridLayout)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from ui.theme import (card_frame, lbl, section_label, action_btn, input_field,
                      divider, TEAL, CYAN, VIOLET, ROSE, AMBER, TEXT,
                      TEXT_DIM, TEXT_MID, BORDER)
from backend.kdeconnect import KDEConnect
from backend.adb_bridge import ADBBridge
from backend.ui_feedback import push_toast
from backend import audio_route
from backend import call_controls
from backend.call_routing import outbound_origin_active, seed_outbound_call_session
import backend.settings_store as settings
from backend.state import state
import time

log = logging.getLogger(__name__)


class CallsHistoryWorker(QThread):
    done = pyqtSignal(list)

    def __init__(self, target, limit=40):
        super().__init__()
        self._target = target
        self._limit = limit

    def run(self):
        rows = ADBBridge(self._target or None).get_recent_calls(limit=self._limit)
        self.done.emit(rows or [])


class ContactsWorker(QThread):
    done = pyqtSignal(list)

    def __init__(self, target, limit=500):
        super().__init__()
        self._target = target
        self._limit = limit

    def run(self):
        rows = ADBBridge(self._target or None).get_contacts(limit=self._limit)
        self.done.emit(rows or [])


class CallRouteWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, preferred_name: str = ""):
        super().__init__()
        self._preferred_name = str(preferred_name or "")

    def _cancel_requested(self) -> bool:
        return bool(self.isInterruptionRequested())

    @staticmethod
    def _cancel_route() -> None:
        audio_route.set_source("call_pc_active", False)
        try:
            audio_route.sync_result(call_retry_ms=0, suspend_ui_global=True)
        except Exception:
            pass

    def _prepare_bt_call_route(self):
        try:
            from backend.bluetooth_manager import BluetoothManager

            mgr = BluetoothManager()
            if not mgr.available():
                return
            hints = [
                self._preferred_name,
                settings.get("device_name", ""),
                "nothing",
                "phone",
                "a059",
            ]
            connected = mgr.connected_phone_macs(hints)
            if (not connected) and bool(settings.get("auto_bt_connect", True)):
                mgr.auto_connect_phone(hints, call_ready_only=False)
                connected = mgr.connected_phone_macs(hints)
            for mac in connected:
                mgr.connect_call_profiles(mac)
        except Exception:
            return

    def run(self):
        if self._cancel_requested():
            self._cancel_route()
            return
        self._prepare_bt_call_route()
        if self._cancel_requested():
            self._cancel_route()
            return
        audio_route.set_source("call_pc_active", True)
        result = audio_route.sync_result(
            call_retry_ms=8000,
            retry_step_ms=300,
            suspend_ui_global=True,
            cancel_check=self._cancel_requested,
        )
        if result.status == "cancelled":
            self._cancel_route()
            return
        if not result.ok:
            self._cancel_route()
        self.done.emit(result)


class CallsPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self.kc  = KDEConnect()
        self.adb = ADBBridge()
        self._call_history = []
        self._contacts = []
        self._last_history_refresh = 0.0
        self._history_busy = False
        self._history_worker = None
        self._contacts_busy = False
        self._contacts_worker = None
        self._call_route_worker = None
        self._call_route_request_id = 0
        self._call_cleanup_busy = False
        self._call_started = False
        self._pc_route_requested = False
        self._pc_route_retry_after_connect_done = False
        self._pending_dial_name = ""
        self._build()
        from backend.state import state
        state.subscribe("call_route_ui_state", self._on_call_route_ui_state_changed, owner=self)
        state.subscribe("call_ui_state", self._on_call_ui_state_changed, owner=self)
        self._on_call_route_ui_state_changed(state.get("call_route_ui_state", {}))
        self.refresh()

    def _on_call_route_ui_state_changed(self, payload):
        if not hasattr(self, "_call_route_hint"):
            return
        row = dict(payload or {})
        status = str(row.get("status") or "phone").strip().lower()
        speaker = str(row.get("speaker_target") or "Phone").strip() or "Phone"
        mic = str(row.get("mic_target") or "Phone").strip() or "Phone"
        reason = str(row.get("reason") or "").strip()
        if status == "pending":
            text = "Route: Preparing laptop audio..."
            color = AMBER
        elif status == "laptop":
            text = f"Route: {speaker} speaker · {mic} mic"
            color = TEAL
        elif status == "failed":
            text = f"Route: Phone · {reason or 'laptop route failed'}"
            color = ROSE
        else:
            text = f"Route: {speaker} speaker · {mic} mic"
            color = TEXT_DIM
        self._call_route_hint.setText(text)
        self._call_route_hint.setStyleSheet(f"color:{color};font-size:10px;background:transparent;border:none;")

    def _on_call_ui_state_changed(self, payload):
        if not hasattr(self, "_call_state_hint"):
            return
        row = payload or {}
        raw_status = str(row.get("phase") or row.get("status") or "idle").strip().lower()
        status = raw_status.replace("_", " ").title()
        number = str(row.get("number") or "").strip()
        name = str(row.get("display_name") or row.get("contact_name") or "").strip()
        who = name or number
        self._call_state_hint.setText(f"Call State: {status}{' · ' + who if who else ''}")
        # Keep live controls interactive through the whole active-call lifecycle,
        # not only after "talking", so users can end/mute/route immediately.
        self._call_started = raw_status in {
            "dialing",
            "ringing",
            "incoming",
            "callreceived",
            "talking",
            "active",
        }

        outbound_origin = state.get("outbound_call_origin", {}) or {}
        origin_tag = str(state.get("call_origin", "unknown") or "unknown")
        live_outbound_origin = outbound_origin_active(outbound_origin, now_ms=int(time.time() * 1000))
        origin_outbound = origin_tag == "calls_page_outbound" and raw_status in {"dialing", "talking", "active"}
        outbound_active = (
            live_outbound_origin
            or origin_outbound
        )

        # Incoming/on-phone calls must default to phone audio unless user explicitly
        # requests transfer from the laptop UI.
        if raw_status in {"ringing", "incoming", "callreceived"} and not outbound_active:
            self._pc_route_requested = False
            self._pc_route_retry_after_connect_done = False

        if raw_status in {"ended", "disconnected", "idle", "missed_call"}:
            self._pc_route_requested = False
            self._pc_route_retry_after_connect_done = False
            self._cancel_call_route_worker()
            if bool(state.get("call_muted", False)):
                self._clear_call_mute_async()
        if (
            raw_status in {"talking", "active"}
            and self._pc_route_requested
            and outbound_active
            and (self._call_route_worker is None)
            and not self._pc_route_retry_after_connect_done
            and (not bool(state.get("call_audio_active", False)))
        ):
            self._pc_route_retry_after_connect_done = True
            state.set_many(
                {
                    "call_route_status": "pending_pc",
                    "call_route_reason": "Preparing laptop call audio...",
                    "call_route_backend": "none",
                }
            )
            self._start_call_route_attempt(number, who or number, intent="outbound_auto")

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24,24,24,24)
        layout.setSpacing(14)
        layout.addWidget(lbl("Calls", 22, bold=True))
        guide = card_frame()
        gl = QVBoxLayout(guide)
        gl.setContentsMargins(16, 12, 16, 12)
        gl.setSpacing(4)
        gl.addWidget(section_label("Flow"))
        gl.addWidget(lbl("1) Dial or choose contact  2) In-call controls appear in the popup  3) History and contacts stay here", 11, TEXT_DIM))
        layout.addWidget(guide)

        # ── Dialpad ──────────────────────────────────────────────
        dial_frame = card_frame()
        dl = QVBoxLayout(dial_frame)
        dl.setContentsMargins(20,16,20,18)
        dl.setSpacing(10)
        dl.addWidget(section_label("Dial"))

        self._dial_input = input_field("Phone number or contact…")
        dl.addWidget(self._dial_input)

        # Dialpad grid
        pad = QGridLayout()
        pad.setSpacing(8)
        digits = [
            ("1",""),("2",""),("3",""),
            ("4",""),("5",""),("6",""),
            ("7",""),("8",""),("9",""),
            ("*",""),("0","+"),("#",""),
        ]
        for i, (d, sub) in enumerate(digits):
            btn = QPushButton()
            btn.setFixedSize(64,48)
            main_lbl = d
            sub_lbl  = f"\n{sub}" if sub else ""
            btn.setText(f"{main_lbl}{sub_lbl}")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.04);
                    border: 1px solid rgba(255,255,255,0.07);
                    border-radius: 12px;
                    color: white;
                    font-size: 16px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background: rgba(167,139,250,0.08);
                    border-color: rgba(167,139,250,0.2);
                }}
                QPushButton:pressed {{
                    background: rgba(167,139,250,0.15);
                }}
            """)
            digit = d
            btn.clicked.connect(lambda _, x=digit: self._append_digit(x))
            pad.addWidget(btn, i//3, i%3)

        dl.addLayout(pad)

        call_row = QHBoxLayout()
        call_row.setSpacing(8)
        call_btn = action_btn("Call", TEAL)
        call_btn.clicked.connect(self._place_call)
        clear_btn = action_btn("⌫", ROSE)
        clear_btn.setFixedWidth(60)
        clear_btn.clicked.connect(lambda: self._dial_input.setText(
            self._dial_input.text()[:-1]))
        call_row.addWidget(call_btn)
        call_row.addWidget(clear_btn)
        dl.addLayout(call_row)

        self._call_route_hint = lbl("Route: Phone speaker · Phone mic", 10, TEXT_DIM)
        self._call_state_hint = lbl("Call State: Idle", 10, TEXT_DIM)
        dl.addWidget(self._call_route_hint)
        dl.addWidget(self._call_state_hint)
        layout.addWidget(dial_frame)

        # ── Contacts ──────────────────────────────────────────────
        contacts_frame = card_frame()
        cl = QVBoxLayout(contacts_frame)
        cl.setContentsMargins(20,16,20,16)
        cl.setSpacing(10)

        chdr = QHBoxLayout()
        self._contacts_toggle = QPushButton("▸")
        self._contacts_toggle.setFixedSize(24, 24)
        self._contacts_toggle.setStyleSheet(f"""
            QPushButton {{
                background:transparent;border:none;color:{TEXT_DIM};font-size:14px;
            }}
            QPushButton:hover {{ color:{TEAL}; }}
        """)
        self._contacts_toggle.clicked.connect(self._toggle_contacts_section)
        chdr.addWidget(self._contacts_toggle)
        chdr.addWidget(section_label("Contacts"))
        chdr.addStretch()
        sync_btn = QPushButton("Sync ↻")
        sync_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent;border:none;
                color:{TEXT_DIM};font-size:11px;
            }}
            QPushButton:hover {{ color:{TEAL}; }}
        """)
        sync_btn.clicked.connect(self._sync_contacts)
        chdr.addWidget(sync_btn)
        cl.addLayout(chdr)

        self._contacts_body = QWidget()
        self._contacts_body_layout = QVBoxLayout(self._contacts_body)
        self._contacts_body_layout.setContentsMargins(0, 0, 0, 0)
        self._contacts_body_layout.setSpacing(8)

        self._contact_search = input_field("Search contacts…")
        self._contact_search.textChanged.connect(self._filter_contacts)
        self._contacts_body_layout.addWidget(self._contact_search)

        self._contacts_list = QVBoxLayout()
        self._contacts_list.setSpacing(4)
        self._contacts_body_layout.addLayout(self._contacts_list)

        load_btn = action_btn("Load Contacts", CYAN)
        load_btn.clicked.connect(self._load_contacts)
        self._contacts_body_layout.addWidget(load_btn)
        self._contacts_body.setVisible(False)
        cl.addWidget(self._contacts_body)
        layout.addWidget(contacts_frame)

        # ── Call History ──────────────────────────────────────────
        hist_frame = card_frame()
        hl = QVBoxLayout(hist_frame)
        hl.setContentsMargins(20,16,20,16)
        hl.setSpacing(8)
        hhdr = QHBoxLayout()
        self._history_toggle = QPushButton("▸")
        self._history_toggle.setFixedSize(24, 24)
        self._history_toggle.setStyleSheet(f"""
            QPushButton {{
                background:transparent;border:none;color:{TEXT_DIM};font-size:14px;
            }}
            QPushButton:hover {{ color:{TEAL}; }}
        """)
        self._history_toggle.clicked.connect(self._toggle_history_section)
        hhdr.addWidget(self._history_toggle)
        hhdr.addWidget(section_label("Recent Calls"))
        hhdr.addStretch()
        hl.addLayout(hhdr)

        self._history_body = QWidget()
        self._history_body_layout = QVBoxLayout(self._history_body)
        self._history_body_layout.setContentsMargins(0, 0, 0, 0)
        self._history_body_layout.setSpacing(6)
        self._history_list = QVBoxLayout()
        self._history_list.setSpacing(4)
        self._history_body_layout.addLayout(self._history_list)
        self._empty_hist = lbl("No recent calls", 12, TEXT_DIM)
        self._history_list.addWidget(self._empty_hist)
        self._history_body.setVisible(False)
        hl.addWidget(self._history_body)
        layout.addWidget(hist_frame)
        layout.addStretch()

    def _append_digit(self, digit):
        self._dial_input.setText(self._dial_input.text() + digit)

    def _place_call(self):
        number = self._dial_input.text().strip()
        if not number:
            return
        display_name = str(self._pending_dial_name or number).strip() or number
        self._pc_route_requested = True
        self._pc_route_retry_after_connect_done = False
        state.set_many(
            {
                "outbound_call_origin": {
                    "source": "calls_page",
                    "number": number,
                    "display_name": display_name,
                    "ts_ms": int(time.time() * 1000),
                    "active": True,
                },
                "call_origin": "calls_page_outbound",
                "call_local_end_action": "",
                "call_muted": False,
                "call_route_status": "phone",
                "call_route_reason": "Call audio on phone until call is active",
                "call_route_backend": "none",
            }
        )
        session = seed_outbound_call_session(
            number,
            display_name,
            now_ms=int(time.time() * 1000),
            origin="calls_page_outbound",
            audio_target="pending_pc",
        )
        state.set_many(
            {
                "call_state": {
                    "event": session.phase,
                    "number": session.number,
                    "contact_name": session.display_name,
                },
                "call_ui_state": session.to_public_row(),
            }
        )
        self._launch_outbound_call_async(number)
        # Show outbound popup immediately instead of waiting for DBus/offhook
        # transitions, which can be delayed or flaky on some stacks.
        win = self.window()
        if win is not None and hasattr(win, "_on_call_received"):
            try:
                QTimer.singleShot(0, lambda n=number, d=display_name: win._on_call_received("ringing", n, d, source="user_action"))
            except Exception:
                pass
        self._pending_dial_name = ""

    def _launch_outbound_call_async(self, number: str):
        dial_number = str(number or "").strip()
        if not dial_number:
            return

        def _job():
            ok, out = self.adb._run(
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.CALL",
                "-d",
                f"tel:{dial_number}",
            )
            if ok:
                return
            log.warning("Outbound call launch failed number=%s detail=%s", dial_number, str(out or "").strip())
            try:
                push_toast("Call launch failed", "warning", 1800)
            except Exception:
                log.debug("Failed pushing outbound call failure toast", exc_info=True)

        threading.Thread(target=_job, daemon=True, name="pb-place-call").start()

    def _sync_contacts(self):
        self.kc.sync_contacts()
        QTimer.singleShot(2000, self._load_contacts)

    def _load_contacts(self):
        contacts = self.kc.get_cached_contacts()
        if contacts:
            self._apply_contacts(contacts)
            return
        if self._contacts_busy:
            return
        self._contacts_busy = True
        self._contacts_list.addWidget(lbl("Loading contacts…", 12, TEXT_DIM))
        self._contacts_worker = ContactsWorker("", limit=500)
        self._contacts_worker.done.connect(self._on_contacts_loaded)
        self._contacts_worker.finished.connect(self._contacts_worker.deleteLater)
        self._contacts_worker.start()

    def _on_contacts_loaded(self, contacts):
        self._contacts_busy = False
        self._apply_contacts(contacts or [])

    def _apply_contacts(self, contacts):
        # Deduplicate by phone while preserving first-seen order
        seen = set()
        deduped = []
        for c in contacts:
            phone = (c.get("phone") or "").strip()
            if not phone or phone in seen:
                continue
            seen.add(phone)
            deduped.append({
                "name": (c.get("name") or phone).strip(),
                "phone": phone,
            })
        self._contacts = deduped
        state.set("call_contacts_cache", deduped)
        self._render_contacts(self._contacts)

    def _toggle_contacts_section(self):
        expanded = self._contacts_body.isHidden()
        self._contacts_body.setHidden(not expanded)
        self._contacts_toggle.setText("▾" if expanded else "▸")
        if expanded and not self._contacts:
            self._load_contacts()

    def _toggle_history_section(self):
        expanded = self._history_body.isHidden()
        self._history_body.setHidden(not expanded)
        self._history_toggle.setText("▾" if expanded else "▸")
        if expanded and not self._call_history:
            self._load_history_from_phone()

    def _filter_contacts(self, query):
        if not query:
            self._render_contacts(self._contacts)
            return
        q = query.lower()
        filtered = [c for c in self._contacts
                    if q in c.get("name","").lower() or q in c.get("phone","")]
        self._render_contacts(filtered)

    def _render_contacts(self, contacts):
        while self._contacts_list.count():
            item = self._contacts_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not contacts:
            self._contacts_list.addWidget(
                lbl("No contacts loaded · tap Sync first", 12, TEXT_DIM))
            return

        for c in contacts[:30]:
            row = QWidget()
            row.setStyleSheet("""
                QWidget { background:transparent;border:none; }
                QWidget:hover { background:rgba(255,255,255,0.04); }
            """)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8,7,8,7)
            rl.setSpacing(12)

            avatar = QLabel(c.get("name","?")[0].upper() if c.get("name") else "?")
            avatar.setFixedSize(34,34)
            avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            avatar.setStyleSheet(f"""
                QLabel {{
                    background: rgba(167,139,250,0.1);
                    border: 1px solid rgba(167,139,250,0.2);
                    border-radius: 10px;
                    color: {TEAL};
                    font-size: 14px;
                    font-weight: 600;
                }}
            """)
            rl.addWidget(avatar)

            info = QVBoxLayout()
            info.setSpacing(1)
            info.addWidget(lbl(c.get("name","Unknown"), 13, bold=True))
            info.addWidget(lbl(c.get("phone",""), 11, TEXT_DIM, mono=True))
            rl.addLayout(info)
            rl.addStretch()

            call_btn = QPushButton("📞")
            call_btn.setFixedSize(30,30)
            call_btn.setStyleSheet(f"""
                QPushButton {{
                    background:rgba(167,139,250,0.08);
                    border:1px solid rgba(167,139,250,0.2);
                    border-radius:8px;font-size:14px;
                }}
                QPushButton:hover {{ background:rgba(167,139,250,0.18); }}
            """)
            phone = c.get("phone","")
            call_btn.clicked.connect(lambda _, p=phone: self._call_contact(p))
            rl.addWidget(call_btn)

            self._contacts_list.addWidget(row)

    def _call_contact(self, phone):
        if phone:
            self._dial_input.setText(phone)
            self._pending_dial_name = next((c.get("name", "") for c in self._contacts if c.get("phone") == phone), "")
            self._place_call()

    def _start_call_route_attempt(self, number, contact_name, *, intent):
        if self._call_route_worker is not None:
            try:
                if self._call_route_worker.isRunning():
                    return
            except RuntimeError:
                self._call_route_worker = None
        request_id = self._call_route_request_id + 1
        self._call_route_request_id = request_id
        ctx = {
            "number": str(number or ""),
            "contact_name": str(contact_name or number or ""),
            "intent": str(intent or "transfer"),
        }
        worker = CallRouteWorker(preferred_name=str(contact_name or number or ""))
        worker._request_context = ctx
        self._call_route_worker = worker
        worker.done.connect(lambda result, rid=request_id, w=worker: self._on_call_route_done(result, rid, w))
        worker.finished.connect(lambda rid=request_id, w=worker: self._on_call_route_worker_finished(w, rid))
        worker.start()

    def _on_call_route_done(self, result, request_id, worker):
        if request_id != self._call_route_request_id or self._call_route_worker is not worker:
            return
        ctx = dict(getattr(worker, "_request_context", {}) or {})
        number = str(ctx.get("number") or "")
        contact_name = str(ctx.get("contact_name") or number)
        intent = str(ctx.get("intent") or "transfer")
        if result.ok and result.status == "active":
            row = dict(state.get("call_ui_state", {}) or {})
            row.update(
                {
                    "phase": "talking",
                    "status": "talking",
                    "number": number,
                    "display_name": contact_name,
                    "contact_name": contact_name,
                    "audio_target": "pc",
                    "updated_at": int(time.time() * 1000),
                }
            )
            state.set_many(
                {
                    "call_state": {
                        "event": "talking",
                        "number": number,
                        "contact_name": contact_name,
                    },
                    "call_ui_state": row,
                }
            )
            if intent == "outbound_auto":
                push_toast(f"Calling {number} on laptop audio", "success", 1700)
            elif intent == "answer":
                push_toast("Call answered on laptop audio", "success", 1600)
            else:
                push_toast("Transferred call audio to laptop", "success", 1600)
            return
        self._pc_route_requested = False
        row = dict(state.get("call_ui_state", {}) or {})
        row.update(
            {
                "phase": "talking",
                "status": "talking",
                "number": number,
                "display_name": contact_name,
                "contact_name": contact_name,
                "audio_target": "phone",
                "updated_at": int(time.time() * 1000),
            }
        )
        state.set_many(
            {
                "call_state": {
                    "event": "talking",
                    "number": number,
                    "contact_name": contact_name,
                },
                "call_ui_state": row,
                "call_route_status": "pc_failed",
                "call_route_reason": str(result.reason or "Laptop call audio unavailable"),
                "call_route_backend": "none",
            }
        )
        if intent == "outbound_auto":
            push_toast(f"Calling {number} (laptop audio unavailable)", "warning", 2000)
        elif intent == "answer":
            push_toast("Call answered, but laptop audio route failed", "warning", 1900)
        else:
            push_toast("Could not switch call audio route", "warning", 1900)

    def _on_call_route_worker_finished(self, worker, request_id):
        if self._call_route_worker is worker and request_id == self._call_route_request_id:
            self._call_route_worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass

    def _cancel_call_route_worker(self):
        worker = self._call_route_worker
        if worker is None:
            return
        self._call_route_request_id += 1
        self._call_route_worker = None
        try:
            if worker.isRunning():
                worker.requestInterruption()
                audio_route.set_source("call_pc_active", False)
                worker.wait(800)
        except Exception:
            pass

    def _clear_call_mute_async(self):
        if self._call_cleanup_busy:
            return
        self._call_cleanup_busy = True

        def _job():
            try:
                row = dict(state.get("call_ui_state", {}) or {})
                phase = str(row.get("phase") or row.get("status") or "").strip().lower()
                if phase in {"ringing", "talking", "dialing", "incoming", "callreceived", "active"}:
                    return
                call_controls.set_call_muted(False)
            finally:
                self._call_cleanup_busy = False

        threading.Thread(target=_job, daemon=True, name="pb-call-mute-clear").start()

    @staticmethod
    def _release_bt_call_route() -> bool:
        try:
            from backend.bluetooth_manager import BluetoothManager

            mgr = BluetoothManager()
            if not mgr.available():
                return False
            hints = [
                settings.get("device_name", ""),
                "nothing",
                "phone",
                "a059",
            ]
            changed, _ = mgr.release_call_audio_route(hints, force_disconnect=True)
            return bool(changed)
        except Exception:
            return False

    def add_call(self, event, number, contact_name):
        """Called by window when telephony signal fires"""
        entry = {
            "event": event,
            "number": number,
            "name": contact_name or number,
            "date_ms": int(time.time() * 1000),
        }
        self._call_history.insert(0, entry)
        state.set("recent_calls_cache", list(self._call_history[:40]))
        self._refresh_history()

    def _refresh_history(self):
        while self._history_list.count():
            item = self._history_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._call_history:
            self._history_list.addWidget(
                lbl("No recent calls", 12, TEXT_DIM))
            return

        for call in self._call_history[:20]:
            event = call.get("event", "")
            if event in {"talking", "incoming"}:
                color, ico = TEAL, "📥"
            elif event in {"outgoing", "ringing"}:
                color, ico = CYAN, "📤"
            elif event in {"missed_call", "missed", "rejected"}:
                color, ico = ROSE, "📵"
            else:
                color, ico = TEXT_DIM, "📞"
            row = QWidget()
            row.setStyleSheet("background:transparent;border:none;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8,6,8,6)
            rl.setSpacing(10)
            rl.addWidget(lbl(ico, 18))
            rl.addWidget(lbl(call.get("name", call.get("number", "Unknown")), 13, bold=True))
            rl.addStretch()
            rl.addWidget(lbl(call.get("number", ""), 11, TEXT_DIM, mono=True))
            call_btn = QPushButton("Call")
            call_btn.setStyleSheet(f"""
                QPushButton {{
                    background:rgba(167,139,250,0.08);
                    border:1px solid rgba(167,139,250,0.2);
                    border-radius:7px;color:{TEAL};padding:4px 8px;font-size:10px;
                }}
                QPushButton:hover {{ background:rgba(167,139,250,0.18); }}
            """)
            num = call.get("number", "")
            call_btn.clicked.connect(lambda _, n=num: self._call_contact(n))
            rl.addWidget(call_btn)
            self._history_list.addWidget(row)

    def refresh(self):
        now = time.time()
        if now - self._last_history_refresh > 20:
            self._last_history_refresh = now
            self._load_history_from_phone()
        if not self._contacts_body.isHidden() and not self._contacts and not self._contacts_busy:
            self._load_contacts()

    def _load_history_from_phone(self):
        if self._history_busy:
            return
        self._history_busy = True
        self._history_worker = CallsHistoryWorker("", limit=40)
        self._history_worker.done.connect(self._on_history_loaded)
        self._history_worker.finished.connect(self._history_worker.deleteLater)
        self._history_worker.start()

    def _on_history_loaded(self, rows):
        self._history_busy = False
        self._call_history = list(rows or [])
        state.set("recent_calls_cache", self._call_history)
        self._refresh_history()
