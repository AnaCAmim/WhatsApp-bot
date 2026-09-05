from collections import deque
from datetime import datetime
from threading import RLock


class ApplicationState:
    def __init__(self):
        self._lock = RLock()

        self.bot_running = False
        self.bot_paused = False
        self.whatsapp_connected = False
        self.selenium_running = False

        self.mode = "headless"
        self.stage = "stopped"

        self.started_at = None
        self.last_heartbeat = None
        self.last_health_check = None
        self.last_message_scan = None

        self.messages_received = 0
        self.messages_sent = 0
        self.messages_pending = 0

        self.errors = 0
        self.last_error = None
        self.last_error_at = None

        self.recovery_attempt = 0
        self.recovery_reason = None
        self.last_recovery_at = None

        self.unread_chats = []
        self.recent_messages = deque(maxlen=20)
        self.events = deque(maxlen=50)

        # Interrupções da interface do WhatsApp Web
        self.ui_blocked = False
        self.ui_message = None
        self.ui_screenshot = None
        self.last_ui_interruption_at = None

    # ========================================================
    # EVENTS
    # ========================================================

    def add_event(self, level, event_type, message):
        with self._lock:
            self.events.appendleft(
                {
                    "timestamp": datetime.now().isoformat(),
                    "level": level,
                    "type": event_type,
                    "message": message,
                }
            )

    # ========================================================
    # BOT
    # ========================================================

    def bot_starting(self):
        with self._lock:
            self.bot_running = True
            self.bot_paused = False
            self.stage = "starting"

            if self.started_at is None:
                self.started_at = datetime.now()

            self.add_event("INFO", "bot_starting", "Bot iniciado.")

    def bot_ready(self):
        with self._lock:
            self.bot_running = True
            self.stage = "paused" if self.bot_paused else "running"
            self.add_event("INFO", "bot_ready", "Bot pronto para operação.")

    def bot_stopped(self):
        with self._lock:
            self.bot_running = False
            self.bot_paused = False
            self.whatsapp_connected = False
            self.selenium_running = False
            self.stage = "stopped"
            self.add_event("INFO", "bot_stopped", "Bot finalizado.")

    def is_paused(self):
        with self._lock:
            return self.bot_paused

    def pause_bot(self):
        with self._lock:
            if not self.bot_running:
                return False

            self.bot_paused = True
            self.stage = "paused"
            self.add_event("INFO", "bot_paused", "Automação pausada.")
            return True

    def resume_bot(self):
        with self._lock:
            if not self.bot_running:
                return False

            self.bot_paused = False
            self.stage = "running"
            self.add_event("INFO", "bot_resumed", "Automação retomada.")
            return True

    # ========================================================
    # STAGE
    # ========================================================

    def set_stage(self, stage):
        with self._lock:
            self.stage = stage

    # ========================================================
    # SELENIUM
    # ========================================================

    def selenium_started(self, mode="headless"):
        with self._lock:
            self.selenium_running = True
            self.mode = mode

    def selenium_stopped(self):
        with self._lock:
            self.selenium_running = False

    # ========================================================
    # WHATSAPP
    # ========================================================

    def whatsapp_connect(self):
        with self._lock:
            was_connected = self.whatsapp_connected
            self.whatsapp_connected = True

            if not was_connected:
                self.add_event("INFO", "whatsapp_connected", "WhatsApp conectado.")

    def whatsapp_disconnect(self):
        with self._lock:
            was_connected = self.whatsapp_connected
            self.whatsapp_connected = False

            if was_connected:
                self.add_event(
                    "WARN",
                    "whatsapp_disconnected",
                    "WhatsApp desconectado.",
                )

    # ========================================================
    # HEARTBEAT / HEALTH
    # ========================================================

    def heartbeat(self):
        with self._lock:
            self.last_heartbeat = datetime.now()

    def health_check(self):
        with self._lock:
            self.last_health_check = datetime.now()

    def mark_degraded(self, reason):
        with self._lock:
            self.stage = "degraded"
            self.recovery_reason = reason

    def health_restored(self):
        with self._lock:
            if self.stage == "degraded":
                self.stage = "paused" if self.bot_paused else "running"
                self.recovery_reason = None
                self.add_event(
                    "INFO",
                    "health_restored",
                    "Saúde da sessão restaurada.",
                )

    # ========================================================
    # RECOVERY
    # ========================================================

    def recovery_started(self, reason):
        with self._lock:
            self.stage = "reconnecting"
            self.recovery_reason = reason
            self.recovery_attempt = 0
            self.add_event(
                "WARN",
                "recovery_started",
                f"Recuperação iniciada: {reason}",
            )

    def recovery_attempted(self, attempt):
        with self._lock:
            self.recovery_attempt = attempt
            self.add_event(
                "INFO",
                "recovery_attempt",
                f"Tentativa de reconexão #{attempt}",
            )

    def recovery_succeeded(self):
        with self._lock:
            self.last_recovery_at = datetime.now()
            self.recovery_attempt = 0
            self.recovery_reason = None
            self.selenium_running = True
            self.whatsapp_connected = True
            self.stage = "paused" if self.bot_paused else "running"
            self.add_event(
                "INFO",
                "recovery_success",
                "Reconexão concluída com sucesso.",
            )

    # ========================================================
    # SCANNER / MESSAGES
    # ========================================================

    def update_unread_chats(self, chats):
        with self._lock:
            self.unread_chats = list(chats)
            self.last_message_scan = datetime.now()
            self.messages_pending = sum(
                chat.get("unread_count", 0) for chat in self.unread_chats
            )

    def initialize_messages(self, received_count, recent_messages):
        with self._lock:
            self.messages_received = int(received_count)
            self.recent_messages.clear()

            # list_recent() vem do mais novo para o mais antigo.
            for message in reversed(list(recent_messages)):
                self.recent_messages.appendleft(dict(message))

    def record_message(self, message):
        with self._lock:
            self.messages_received += 1
            self.recent_messages.appendleft(dict(message))

    def record_sent_message(self):
        with self._lock:
            self.messages_sent += 1

    # ========================================================
    # UI INTERRUPTIONS
    # ========================================================

    def ui_blocked_by(self, message, screenshot=None):
        with self._lock:
            self.ui_blocked = True
            self.ui_message = str(message) if message else "Popup desconhecido."

            if screenshot:
                self.ui_screenshot = str(screenshot)

            self.last_ui_interruption_at = datetime.now()

            # Não sobrescreve estados de falha/reconexão mais importantes.
            if self.stage not in {"error", "reconnecting", "waiting_login"}:
                self.stage = "ui_blocked"

    def ui_cleared(self):
        with self._lock:
            was_blocked = self.ui_blocked
            self.ui_blocked = False
            self.ui_message = None

            if self.stage == "ui_blocked":
                self.stage = "paused" if self.bot_paused else "running"

            if was_blocked:
                self.add_event(
                    "INFO",
                    "ui_unblocked",
                    "Interrupção da interface removida.",
                )

    # ========================================================
    # ERROR
    # ========================================================

    def register_error(self, error, fatal=False):
        with self._lock:
            self.errors += 1
            self.last_error = str(error)
            self.last_error_at = datetime.now()

            if fatal:
                self.stage = "error"

            self.add_event("ERROR", "error", str(error))

    # ========================================================
    # SERIALIZAÇÃO
    # ========================================================

    def to_dict(self):
        with self._lock:
            uptime_seconds = 0

            if self.started_at:
                uptime_seconds = int(
                    (datetime.now() - self.started_at).total_seconds()
                )

            return {
                "bot": {
                    "running": self.bot_running,
                    "paused": self.bot_paused,
                    "stage": self.stage,
                },
                "whatsapp": {
                    "connected": self.whatsapp_connected,
                },
                "selenium": {
                    "running": self.selenium_running,
                    "mode": self.mode,
                },
                "uptime_seconds": uptime_seconds,
                "started_at": (
                    self.started_at.isoformat() if self.started_at else None
                ),
                "last_heartbeat": (
                    self.last_heartbeat.isoformat()
                    if self.last_heartbeat
                    else None
                ),
                "last_health_check": (
                    self.last_health_check.isoformat()
                    if self.last_health_check
                    else None
                ),
                "messages": {
                    "received": self.messages_received,
                    "sent": self.messages_sent,
                    "pending": self.messages_pending,
                },
                "recent_messages": list(self.recent_messages),
                "errors": self.errors,
                "last_error": self.last_error,
                "last_error_at": (
                    self.last_error_at.isoformat()
                    if self.last_error_at
                    else None
                ),
                "recovery": {
                    "attempt": self.recovery_attempt,
                    "reason": self.recovery_reason,
                    "last_recovery_at": (
                        self.last_recovery_at.isoformat()
                        if self.last_recovery_at
                        else None
                    ),
                },
                "scanner": {
                    "last_scan_at": (
                        self.last_message_scan.isoformat()
                        if self.last_message_scan
                        else None
                    ),
                    "unread_chat_count": len(self.unread_chats),
                    "unread_chats": list(self.unread_chats),
                },
                "ui": {
                    "blocked": self.ui_blocked,
                    "message": self.ui_message,
                    "screenshot": self.ui_screenshot,
                    "last_interruption_at": (
                        self.last_ui_interruption_at.isoformat()
                        if self.last_ui_interruption_at
                        else None
                    ),
                },
                "events": list(self.events),
            }


app_state = ApplicationState()
