import hashlib
from collections import defaultdict
from datetime import datetime

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from bot.models import WhatsAppMessage


class ConversationReader:
    """
    Leitor de mensagens da conversa atualmente aberta.

    Etapa 2B:
    - captura somente mensagens textuais recebidas;
    - ignora message-out;
    - gera uma chave estável para deduplicação;
    - usa o snapshot unread_count obtido antes do clique.
    """

    def __init__(self, driver):
        self.driver = driver

    def _find_message_container(self, element):
        try:
            return self.driver.execute_script(
                """
                let current = arguments[0];

                for (let i = 0; i < 15; i++) {
                    if (!current) return null;

                    if (
                        current.classList &&
                        (
                            current.classList.contains("message-in") ||
                            current.classList.contains("message-out")
                        )
                    ) {
                        return current;
                    }

                    if (
                        current.hasAttribute &&
                        current.hasAttribute("data-id")
                    ) {
                        return current;
                    }

                    current = current.parentElement;
                }

                return null;
                """,
                element,
            )
        except Exception:
            return None

    @staticmethod
    def _is_from_me(container):
        if container is None:
            return False

        try:
            classes = (container.get_attribute("class") or "").split()
            return "message-out" in classes
        except Exception:
            return False

    def _get_source_id(self, container):
        if container is None:
            return None

        try:
            source_id = container.get_attribute("data-id")
            if source_id:
                return source_id

            return self.driver.execute_script(
                """
                let current = arguments[0];

                for (let i = 0; i < 15; i++) {
                    if (!current) return null;

                    const value =
                        current.getAttribute &&
                        current.getAttribute("data-id");

                    if (value) return value;

                    current = current.parentElement;
                }

                return null;
                """,
                container,
            )
        except Exception:
            return None

    @staticmethod
    def _extract_sender(metadata):
        if not metadata or "] " not in metadata:
            return None

        try:
            sender = metadata.split("] ", 1)[1].strip()
            if sender.endswith(":"):
                sender = sender[:-1]
            return sender.strip() or None
        except Exception:
            return None

    @staticmethod
    def _create_message_key(
        source_id,
        chat_name,
        metadata,
        content,
        occurrence,
    ):
        if source_id:
            raw = f"whatsapp:{source_id}"
        else:
            # occurrence diferencia mensagens textualmente idênticas
            # exibidas com os mesmos metadados no DOM.
            raw = "|".join(
                [
                    chat_name or "",
                    metadata or "",
                    content or "",
                    str(occurrence),
                ]
            )

        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def read_recent_incoming(self, chat_name, expected_unread):
        print()
        print(f"[READER] Lendo conversa: {chat_name}")

        WebDriverWait(
            self.driver,
            15,
            poll_frequency=0.5,
        ).until(
            lambda driver: any(
                element.is_displayed()
                for element in driver.find_elements(By.ID, "main")
            ),
            message=f"Área da conversa não apareceu para '{chat_name}'",
        )

        elements = self.driver.find_elements(
            By.CSS_SELECTOR,
            "#main [data-pre-plain-text]",
        )

        print(f"[READER] Elementos encontrados: {len(elements)}")

        # Conversas apenas com áudio/imagem/documento são válidas,
        # mas ficam fora do escopo da Etapa 2B textual.
        if not elements:
            print("[READER] Nenhuma mensagem textual encontrada.")
            return []

        messages = []
        occurrence_map = defaultdict(int)

        for element in elements:
            try:
                if not element.is_displayed():
                    continue

                metadata = (
                    element.get_attribute("data-pre-plain-text") or ""
                ).strip()
                content = (element.text or "").strip()

                if not content:
                    continue

                container = self._find_message_container(element)

                # Sem container confiável não inferimos direção da mensagem.
                if container is None:
                    continue

                if self._is_from_me(container):
                    continue

                source_id = self._get_source_id(container)
                sender = self._extract_sender(metadata)

                signature = "|".join([metadata, content])
                occurrence_map[signature] += 1
                occurrence = occurrence_map[signature]

                message_key = self._create_message_key(
                    source_id=source_id,
                    chat_name=chat_name,
                    metadata=metadata,
                    content=content,
                    occurrence=occurrence,
                )

                messages.append(
                    WhatsAppMessage(
                        message_key=message_key,
                        source_id=source_id,
                        chat_name=chat_name,
                        sender=sender,
                        content=content,
                        raw_metadata=metadata,
                        is_from_me=False,
                        captured_at=datetime.now().isoformat(),
                    )
                )

            except StaleElementReferenceException:
                continue
            except Exception as error:
                print(
                    "[READER] Erro lendo elemento:",
                    type(error).__name__,
                    error,
                )

        print(
            "[READER] Mensagens recebidas visíveis:",
            len(messages),
        )

        if expected_unread <= 0:
            return []

        # unread_count é capturado antes de abrir a conversa. Após o clique,
        # o WhatsApp normalmente remove o badge; por isso usamos o snapshot.
        recent = messages[-expected_unread:]

        print(
            "[READER] Mensagens consideradas novas:",
            len(recent),
        )

        return recent
