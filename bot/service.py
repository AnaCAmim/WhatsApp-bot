import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoSuchWindowException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.support.ui import WebDriverWait

from admin.state import app_state
from bot.auth import (
    esta_logado,
    login_necessario,
    obter_driver_autenticado,
    tentar_sessao_headless,
)
from bot.chats import UnreadChatScanner
from bot.messages import ConversationReader
from bot.sender import WhatsAppSender
from bot.ui_handler import UIInterruptionHandler
from storage.message_repository import MessageRepository
from storage.campaign_repository import CampaignRepository


class HealthStatus:
    HEALTHY = "healthy"
    TRANSIENT = "transient"
    BROWSER_DEAD = "browser_dead"
    AUTH_LOST = "auth_lost"
    UI_MISSING = "ui_missing"
    UI_BLOCKED = "ui_blocked"


class BotService:
    HEALTH_CHECK_INTERVAL = 5
    UI_CHECK_INTERVAL = 1
    MESSAGE_SCAN_INTERVAL = 2
    CAMPAIGN_POLL_INTERVAL = 1
    CAMPAIGN_SEND_INTERVAL = 5

    PANE_MISS_LIMIT = 3
    RECONNECT_ATTEMPTS = 3
    RECOVERY_COOLDOWN = 30
    BACKOFF_SECONDS = [2, 5, 10]

    def __init__(self):
        self._thread = None
        self._stop_event = threading.Event()

        self._driver = None
        self._chat_scanner = None
        self._message_reader = None
        self._ui_handler = None
        self._sender = None

        self._message_repository = MessageRepository()
        self._campaign_repository = CampaignRepository()

        self._pane_misses = 0
        self._scan_failures = 0

    # ========================================================
    # START / MAIN LOOP
    # ========================================================

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="whatsapp-bot-thread",
            daemon=True,
        )
        self._thread.start()

    def _run(self):
        app_state.bot_starting()

        try:
            app_state.initialize_messages(
                received_count=self._message_repository.count(),
                recent_messages=self._message_repository.list_recent(limit=20),
            )

            self._driver = obter_driver_autenticado()

            if self._driver is None:
                raise RuntimeError("Não foi possível autenticar o WhatsApp.")

            self._build_components()
            app_state.bot_ready()

            next_health_check = 0.0
            next_ui_check = 0.0
            next_message_scan = 0.0
            next_campaign_poll = 0.0

            while not self._stop_event.is_set():
                app_state.heartbeat()
                now = time.monotonic()

                # ==========================================
                # HEALTH CHECK
                # ==========================================
                if now >= next_health_check:
                    health = self._check_health()
                    next_health_check = now + self.HEALTH_CHECK_INTERVAL

                    if health in (
                        HealthStatus.BROWSER_DEAD,
                        HealthStatus.AUTH_LOST,
                        HealthStatus.UI_MISSING,
                    ):
                        recovered = self._recover(health)

                        if recovered:
                            next_health_check = (
                                time.monotonic() + self.HEALTH_CHECK_INTERVAL
                            )
                            next_ui_check = time.monotonic()
                            next_message_scan = time.monotonic()
                        else:
                            if self._stop_event.wait(self.RECOVERY_COOLDOWN):
                                break
                            continue

                    # Popup desconhecido não deve provocar restart do Chrome.
                    if health == HealthStatus.UI_BLOCKED:
                        self._stop_event.wait(0.5)
                        continue

                # ==========================================
                # INTERRUPÇÕES DA INTERFACE
                # ==========================================
                if (
                    now >= next_ui_check
                    and self._driver is not None
                    and self._ui_handler is not None
                    and app_state.whatsapp_connected
                ):
                    ui_result = self._handle_ui_interruptions()
                    next_ui_check = time.monotonic() + self.UI_CHECK_INTERVAL

                    if ui_result.get("blocked"):
                        # Mantém Selenium/WhatsApp ativos, porém não interage
                        # com a interface até o modal desaparecer ou ser
                        # catalogado como seguro.
                        self._stop_event.wait(0.5)
                        continue

                # ==========================================
                # FILA DE CAMPANHAS / DISPAROS
                # ==========================================
                if (
                    now >= next_campaign_poll
                    and self._driver is not None
                    and self._sender is not None
                    and app_state.whatsapp_connected
                    and not app_state.ui_blocked
                    and not app_state.is_paused()
                ):
                    sent_or_attempted = self._process_campaign_queue()

                    next_campaign_poll = time.monotonic() + (
                        self.CAMPAIGN_SEND_INTERVAL
                        if sent_or_attempted
                        else self.CAMPAIGN_POLL_INTERVAL
                    )

                    if sent_or_attempted:
                        # A navegação para outro chat altera o DOM.
                        # Dá uma pequena janela antes do próximo scan lateral.
                        next_message_scan = time.monotonic() + self.MESSAGE_SCAN_INTERVAL

                # ==========================================
                # SCANNER DE CONVERSAS NÃO LIDAS
                # ==========================================
                if (
                    now >= next_message_scan
                    and self._driver is not None
                    and self._chat_scanner is not None
                    and self._message_reader is not None
                    and app_state.whatsapp_connected
                    and not app_state.ui_blocked
                ):
                    try:
                        unread_chats = self._chat_scanner.scan_and_publish()
                        self._scan_failures = 0

                        # Pausar mantém a observação, mas impede abrir chats e
                        # persistir mensagens para processamento.
                        if unread_chats and not app_state.is_paused():
                            self._process_unread_chats(unread_chats)

                    except Exception as exc:
                        self._scan_failures += 1

                        if self._scan_failures >= 3:
                            app_state.add_event(
                                "WARN",
                                "scanner_error",
                                (
                                    "Scanner falhou "
                                    f"{self._scan_failures} vezes consecutivas: "
                                    f"{type(exc).__name__}: {exc!r}"
                                ),
                            )

                    next_message_scan = time.monotonic() + self.MESSAGE_SCAN_INTERVAL

                if app_state.is_paused():
                    self._stop_event.wait(0.5)
                    continue

                # Etapa 2B ainda não responde mensagens.
                self._stop_event.wait(0.5)

        except Exception as exc:
            app_state.register_error(
                f"{type(exc).__name__}: {exc!r}",
                fatal=True,
            )
            traceback.print_exc()

        finally:
            self._close_driver()
            app_state.bot_stopped()

    # ========================================================
    # COMPONENTES DEPENDENTES DO DRIVER
    # ========================================================

    def _build_components(self):
        if self._driver is None:
            self._chat_scanner = None
            self._message_reader = None
            self._ui_handler = None
            self._sender = None
        else:
            self._chat_scanner = UnreadChatScanner(self._driver)
            self._message_reader = ConversationReader(self._driver)
            self._ui_handler = UIInterruptionHandler(self._driver)
            self._sender = WhatsAppSender(self._driver)

        self._scan_failures = 0

    # ========================================================
    # UI INTERRUPTIONS
    # ========================================================

    def _handle_ui_interruptions(self):
        if self._ui_handler is None:
            return {
                "blocked": False,
                "dismissed": False,
            }

        try:
            return self._ui_handler.handle()
        except (
            InvalidSessionIdException,
            NoSuchWindowException,
            WebDriverException,
        ):
            # Deixa o health-check classificar corretamente como browser_dead.
            return {
                "blocked": False,
                "dismissed": False,
            }
        except Exception as exc:
            app_state.add_event(
                "WARN",
                "ui_handler_error",
                f"Falha no handler de interface: {type(exc).__name__}: {exc!r}",
            )
            return {
                "blocked": False,
                "dismissed": False,
            }

    # ========================================================
    # CAMPANHAS / DISPAROS
    # ========================================================

    def _process_campaign_queue(self):
        item = self._campaign_repository.claim_next_recipient()

        if item is None:
            return False

        recipient_id = item["recipient_id"]
        campaign_id = item["campaign_id"]
        name = item["name"]
        phone = item["phone"]

        try:
            ui_result = self._handle_ui_interruptions()
            if ui_result.get("blocked"):
                raise RuntimeError("Interface bloqueada por popup desconhecido.")

            text = (item.get("text") or "").replace("{nome}", name)

            app_state.add_event(
                "INFO",
                "campaign_sending",
                f"Campanha {campaign_id[:8]}: enviando para {name} ({phone}).",
            )

            self._sender.send(
                phone=phone,
                text=text,
                message_type=item["message_type"],
                media_path=item.get("media_path"),
            )

            self._campaign_repository.mark_sent(recipient_id)
            app_state.record_sent_message()
            app_state.add_event(
                "INFO",
                "campaign_sent",
                f"Mensagem enviada para {name} ({phone}).",
            )

        except Exception as exc:
            self._campaign_repository.mark_error(
                recipient_id,
                f"{type(exc).__name__}: {str(exc).strip() or repr(exc)}",
            )
            app_state.add_event(
                "ERROR",
                "campaign_recipient_error",
                f"Falha enviando para {name} ({phone}): {type(exc).__name__}: {exc}",
            )

        return True

    # ========================================================
    # PROCESSAMENTO DE NÃO LIDOS
    # ========================================================

    def _process_unread_chats(self, chats):
        for chat in chats:
            if self._stop_event.is_set() or app_state.is_paused():
                return

            # Popup pode surgir entre o scan e o clique na conversa.
            ui_result = self._handle_ui_interruptions()

            if ui_result.get("blocked"):
                app_state.add_event(
                    "WARN",
                    "chat_processing_blocked",
                    f"Processamento de {chat.name} adiado por popup desconhecido.",
                )
                return

            try:
                app_state.add_event(
                    "INFO",
                    "chat_processing",
                    f"Abrindo conversa {chat.name}.",
                )

                self._chat_scanner.open_chat(chat.name)

                messages = self._message_reader.read_recent_incoming(
                    chat_name=chat.name,
                    expected_unread=chat.unread_count,
                )

                if not messages:
                    app_state.add_event(
                        "WARN",
                        "message_not_extracted",
                        (
                            f"{chat.name}: havia {chat.unread_count} não lida(s), "
                            "mas nenhuma mensagem textual foi extraída."
                        ),
                    )
                    continue

                new_messages = 0

                for message in messages:
                    inserted = self._message_repository.insert_if_new(message)

                    if not inserted:
                        continue

                    new_messages += 1
                    app_state.record_message(message.to_dict())
                    app_state.add_event(
                        "INFO",
                        "message_captured",
                        f"Mensagem capturada de {chat.name}.",
                    )

                    print()
                    print("========================")
                    print(" NOVA MENSAGEM")
                    print("========================")
                    print("Chat:", message.chat_name)
                    print("Remetente:", message.sender)
                    print("Texto:", message.content)
                    print("ID:", message.message_key)

                if new_messages == 0:
                    app_state.add_event(
                        "INFO",
                        "messages_deduplicated",
                        f"{chat.name}: mensagens já conhecidas; nenhuma duplicata salva.",
                    )

            except Exception as exc:
                self._register_chat_processing_error(chat.name, exc)

    def _register_chat_processing_error(self, chat_name, exc):
        error_type = type(exc).__name__
        error_message = str(exc).strip() or repr(exc)

        full_error = (
            f"Erro processando '{chat_name}' | "
            f"{error_type}: {error_message}"
        )

        print()
        print("=" * 60)
        print(full_error)
        print("=" * 60)
        traceback.print_exc()

        try:
            debug_dir = Path("debug")
            debug_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = debug_dir / f"chat_error_{timestamp}.png"

            if self._driver is not None:
                self._driver.save_screenshot(str(screenshot_path))
                print(f"Screenshot salvo em: {screenshot_path}")

        except Exception as screenshot_error:
            print(
                "Não foi possível salvar screenshot:",
                screenshot_error,
            )

        app_state.register_error(full_error)

    # ========================================================
    # HEALTH CHECK
    # ========================================================

    def _check_health(self):
        app_state.health_check()

        if self._driver is None:
            app_state.selenium_stopped()
            app_state.whatsapp_disconnect()
            return HealthStatus.BROWSER_DEAD

        try:
            self._driver.current_url
            self._driver.execute_script("return document.readyState")
        except (
            WebDriverException,
            InvalidSessionIdException,
            NoSuchWindowException,
        ) as exc:
            app_state.register_error(
                f"Chrome/WebDriver indisponível: {type(exc).__name__}: {exc!r}"
            )
            app_state.selenium_stopped()
            app_state.whatsapp_disconnect()
            return HealthStatus.BROWSER_DEAD

        # Antes de interpretar qualquer modal como interrupção, verifica se o
        # WhatsApp voltou para a tela de autenticação/QR.
        if login_necessario(self._driver):
            self._pane_misses = 0
            app_state.ui_cleared()
            app_state.whatsapp_disconnect()
            return HealthStatus.AUTH_LOST

        # Modais conhecidos são dispensados; desconhecidos bloqueiam apenas a
        # interação, nunca provocam restart do Chrome.
        ui_result = self._handle_ui_interruptions()

        if ui_result.get("blocked"):
            self._pane_misses = 0
            return HealthStatus.UI_BLOCKED

        if esta_logado(self._driver):
            self._pane_misses = 0
            app_state.whatsapp_connect()
            app_state.health_restored()
            return HealthStatus.HEALTHY

        self._pane_misses += 1
        app_state.mark_degraded(
            f"#pane-side ausente ({self._pane_misses}/{self.PANE_MISS_LIMIT})"
        )

        if self._pane_misses < self.PANE_MISS_LIMIT:
            return HealthStatus.TRANSIENT

        return HealthStatus.UI_MISSING

    # ========================================================
    # RECOVERY
    # ========================================================

    def _recover(self, reason):
        app_state.recovery_started(reason)

        if reason == HealthStatus.UI_MISSING and self._try_refresh():
            return True

        self._close_driver()

        for attempt in range(1, self.RECONNECT_ATTEMPTS + 1):
            if self._stop_event.is_set():
                return False

            app_state.recovery_attempted(attempt)

            if attempt > 1:
                delay = self.BACKOFF_SECONDS[
                    min(attempt - 1, len(self.BACKOFF_SECONDS) - 1)
                ]
                if self._stop_event.wait(delay):
                    return False

            try:
                driver = tentar_sessao_headless(timeout=15)

                if driver:
                    self._driver = driver
                    self._build_components()
                    self._pane_misses = 0
                    app_state.recovery_succeeded()
                    return True

            except Exception as exc:
                app_state.register_error(
                    (
                        f"Falha na tentativa de reconexão {attempt}: "
                        f"{type(exc).__name__}: {exc!r}"
                    )
                )

        try:
            app_state.add_event(
                "WARN",
                "reauthentication",
                "Reconexão silenciosa falhou. Iniciando reautenticação.",
            )

            driver = obter_driver_autenticado()

            if driver:
                self._driver = driver
                self._build_components()
                self._pane_misses = 0
                app_state.recovery_succeeded()
                return True

        except Exception as exc:
            app_state.register_error(
                f"Falha na reautenticação: {type(exc).__name__}: {exc!r}"
            )

        app_state.register_error(
            "Não foi possível restaurar a sessão do WhatsApp.",
            fatal=True,
        )
        return False

    def _try_refresh(self):
        if self._driver is None:
            return False

        try:
            app_state.add_event(
                "INFO",
                "page_refresh",
                "#pane-side desapareceu. Tentando recarregar a página.",
            )

            self._driver.refresh()

            WebDriverWait(self._driver, 20, poll_frequency=1).until(
                lambda driver: esta_logado(driver) or login_necessario(driver),
                message="Refresh não restaurou login nem interface principal.",
            )

            if esta_logado(self._driver):
                self._pane_misses = 0
                self._build_components()
                app_state.recovery_succeeded()
                return True

        except TimeoutException:
            app_state.add_event(
                "WARN",
                "refresh_timeout",
                "Refresh não restaurou a interface.",
            )
        except Exception as exc:
            app_state.register_error(
                f"Erro durante refresh: {type(exc).__name__}: {exc!r}"
            )

        return False

    # ========================================================
    # SHUTDOWN
    # ========================================================

    def _close_driver(self):
        driver = self._driver

        self._driver = None
        self._chat_scanner = None
        self._message_reader = None
        self._ui_handler = None
        self._sender = None

        app_state.ui_cleared()

        if driver:
            try:
                driver.quit()
            except Exception:
                pass

        app_state.selenium_stopped()
        app_state.whatsapp_disconnect()

    def stop(self):
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())


bot_service = BotService()
