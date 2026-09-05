import os
from pathlib import Path
from urllib.parse import quote

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException


class WhatsAppSendError(RuntimeError):
    pass


class WhatsAppSender:
    def __init__(self, driver):
        self.driver = driver

    def send(self, phone, text, message_type="text", media_path=None):
        personalized_text = text or ""
        self._open_phone_chat(phone, personalized_text if message_type == "text" else "")

        if message_type == "text":
            if not personalized_text.strip():
                raise WhatsAppSendError("Mensagem de texto vazia.")
            self._send_prefilled_text()
            return

        if message_type not in {"image", "video", "document"}:
            raise WhatsAppSendError(f"Tipo de mensagem inválido: {message_type}")

        if not media_path:
            raise WhatsAppSendError("Arquivo de mídia não informado.")

        self._send_attachment(
            message_type=message_type,
            media_path=media_path,
            caption=personalized_text,
        )

    def _open_phone_chat(self, phone, prefilled_text=""):
        url = f"https://web.whatsapp.com/send?phone={quote(str(phone))}"
        if prefilled_text:
            url += f"&text={quote(prefilled_text)}"

        self.driver.get(url)

        try:
            WebDriverWait(self.driver, 25, poll_frequency=0.5).until(
                lambda d: self._chat_ready(d) or self._invalid_phone(d),
                message=f"Conversa do número {phone} não carregou.",
            )
        except TimeoutException as exc:
            raise WhatsAppSendError(str(exc)) from exc

        if self._invalid_phone(self.driver):
            raise WhatsAppSendError(f"Número inválido ou indisponível no WhatsApp: {phone}")

    @staticmethod
    def _chat_ready(driver):
        mains = driver.find_elements(By.ID, "main")
        return any(main.is_displayed() for main in mains)

    @staticmethod
    def _invalid_phone(driver):
        try:
            text = (driver.find_element(By.TAG_NAME, "body").text or "").casefold()
        except Exception:
            return False

        markers = (
            "phone number shared via url is invalid",
            "número de telefone compartilhado por url é inválido",
            "numero de telefone compartilhado por url e invalido",
            "não está no whatsapp",
            "nao esta no whatsapp",
        )
        return any(marker in text for marker in markers)

    def _find_composer(self):
        selectors = (
            '#main footer div[contenteditable="true"][role="textbox"]',
            '#main div[contenteditable="true"][role="textbox"]',
            '#main footer div[contenteditable="true"]',
        )

        for selector in selectors:
            elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
            for element in reversed(elements):
                if element.is_displayed():
                    return element
        return None

    def _send_prefilled_text(self):
        composer = WebDriverWait(self.driver, 15, poll_frequency=0.5).until(
            lambda d: self._find_composer() or False,
            message="Campo de mensagem não encontrado.",
        )
        composer.send_keys(Keys.ENTER)

    def _click_attach(self):
        selectors = (
            '#main button[aria-label*="Anex"]',
            '#main button[aria-label*="Attach"]',
            '#main [data-testid="clip"]',
            '#main span[data-icon="plus-rounded"]',
            '#main span[data-icon="plus"]',
        )

        for selector in selectors:
            elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                if element.is_displayed():
                    self.driver.execute_script("arguments[0].click();", element)
                    return True
        return False

    def _select_file_input(self, message_type):
        inputs = self.driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
        visible_or_hidden = list(inputs)

        for element in visible_or_hidden:
            accept = (element.get_attribute("accept") or "").lower()

            if message_type == "image" and "image" in accept:
                return element
            if message_type == "video" and "video" in accept:
                return element
            if message_type == "document" and "image" not in accept and "video" not in accept:
                return element

        return visible_or_hidden[-1] if visible_or_hidden else None

    def _send_attachment(self, message_type, media_path, caption=""):
        path = Path(media_path).resolve()
        if not path.exists():
            raise WhatsAppSendError(f"Arquivo não encontrado: {path}")

        if not self._click_attach():
            raise WhatsAppSendError("Botão de anexo não encontrado.")

        file_input = WebDriverWait(self.driver, 10, poll_frequency=0.5).until(
            lambda d: self._select_file_input(message_type) or False,
            message="Input de upload não encontrado após abrir anexos.",
        )
        file_input.send_keys(str(path))

        # Aguarda preview/modal de envio.
        WebDriverWait(self.driver, 20, poll_frequency=0.5).until(
            lambda d: bool(
                d.find_elements(By.CSS_SELECTOR, '[role="dialog"]')
                or d.find_elements(By.CSS_SELECTOR, '[data-animate-modal-popup="true"]')
            ),
            message="Preview do anexo não apareceu.",
        )

        if caption.strip():
            caption_boxes = self.driver.find_elements(
                By.CSS_SELECTOR,
                '[role="dialog"] div[contenteditable="true"][role="textbox"], '
                '[data-animate-modal-popup="true"] div[contenteditable="true"][role="textbox"]',
            )
            for box in reversed(caption_boxes):
                if box.is_displayed():
                    box.click()
                    box.send_keys(caption)
                    break

        send_selectors = (
            '[role="dialog"] span[data-icon="send"]',
            '[role="dialog"] button[aria-label*="Enviar"]',
            '[role="dialog"] button[aria-label*="Send"]',
            '[data-animate-modal-popup="true"] span[data-icon="send"]',
        )

        for selector in send_selectors:
            elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                if element.is_displayed():
                    self.driver.execute_script("arguments[0].click();", element)
                    return

        raise WhatsAppSendError("Botão de enviar anexo não encontrado.")
