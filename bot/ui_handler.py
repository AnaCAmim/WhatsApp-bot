from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

from admin.state import app_state


class UIInterruptionHandler:
    """Detecta e trata modais/popups que interrompem o WhatsApp Web.

    A regra é conservadora: apenas botões de dispensa conhecidos são clicados
    automaticamente. Qualquer modal desconhecido bloqueia o processamento,
    gera evento e screenshot para análise manual.
    """

    SAFE_DISMISS_TEXTS = {
        "agora não",
        "nao agora",
        "não agora",
        "not now",
        "fechar",
        "close",
        "entendi",
        "got it",
    }

    DIALOG_SELECTORS = (
        '[role="dialog"]',
        '[aria-modal="true"]',
    )

    UNKNOWN_EVENT_COOLDOWN_SECONDS = 60

    def __init__(self, driver):
        self.driver = driver
        self._last_unknown_fingerprint = None
        self._last_unknown_at = None

    @staticmethod
    def _normalize(value: str | None) -> str:
        return " ".join((value or "").strip().lower().split())

    @staticmethod
    def _fingerprint(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def _find_dialogs(self):
        dialogs = []
        seen_ids = set()

        for selector in self.DIALOG_SELECTORS:
            try:
                for element in self.driver.find_elements(By.CSS_SELECTOR, selector):
                    try:
                        if not element.is_displayed():
                            continue

                        if element.id in seen_ids:
                            continue

                        seen_ids.add(element.id)
                        dialogs.append(element)
                    except StaleElementReferenceException:
                        continue
            except WebDriverException:
                continue

        return dialogs

    def _extract_button_text(self, button) -> str:
        try:
            candidates = (
                button.text,
                button.get_attribute("aria-label"),
                button.get_attribute("title"),
            )

            for candidate in candidates:
                normalized = self._normalize(candidate)
                if normalized:
                    return normalized
        except StaleElementReferenceException:
            pass

        return ""

    def _try_dismiss_dialog(self, dialog):
        try:
            buttons = dialog.find_elements(
                By.CSS_SELECTOR,
                'button, [role="button"]',
            )
        except (StaleElementReferenceException, WebDriverException):
            return None

        for button in buttons:
            try:
                if not button.is_displayed() or not button.is_enabled():
                    continue

                button_text = self._extract_button_text(button)

                if button_text not in self.SAFE_DISMISS_TEXTS:
                    continue

                try:
                    button.click()
                except ElementClickInterceptedException:
                    self.driver.execute_script("arguments[0].click();", button)

                app_state.add_event(
                    "INFO",
                    "popup_dismissed",
                    f"Popup conhecido fechado pelo botão '{button_text}'.",
                )

                return button_text

            except (
                StaleElementReferenceException,
                WebDriverException,
            ):
                continue

        return None

    def _dialog_summary(self, dialog) -> str:
        try:
            text = " ".join((dialog.text or "").split())
            if text:
                return text[:400]
        except StaleElementReferenceException:
            pass

        return "Popup sem texto legível."

    def _save_unknown_popup(self, fingerprint: str):
        debug_dir = Path("debug")
        debug_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = debug_dir / f"unknown_popup_{timestamp}_{fingerprint}.png"

        try:
            self.driver.save_screenshot(str(path))
            return str(path)
        except WebDriverException:
            return None

    def _should_emit_unknown_event(self, fingerprint: str) -> bool:
        now = datetime.now()

        if fingerprint != self._last_unknown_fingerprint:
            self._last_unknown_fingerprint = fingerprint
            self._last_unknown_at = now
            return True

        if self._last_unknown_at is None:
            self._last_unknown_at = now
            return True

        elapsed = (now - self._last_unknown_at).total_seconds()

        if elapsed >= self.UNKNOWN_EVENT_COOLDOWN_SECONDS:
            self._last_unknown_at = now
            return True

        return False

    def handle(self):
        dialogs = self._find_dialogs()

        if not dialogs:
            app_state.ui_cleared()
            return {
                "blocked": False,
                "dismissed": False,
            }

        # Pode haver mais de um modal no DOM. Fechamos somente os conhecidos.
        for dialog in dialogs:
            dismissed_by = self._try_dismiss_dialog(dialog)

            if dismissed_by:
                app_state.ui_cleared()
                return {
                    "blocked": False,
                    "dismissed": True,
                    "button": dismissed_by,
                }

        # Restou modal sem regra segura de fechamento.
        dialog = dialogs[0]
        summary = self._dialog_summary(dialog)
        fingerprint = self._fingerprint(summary)
        screenshot = None

        if self._should_emit_unknown_event(fingerprint):
            screenshot = self._save_unknown_popup(fingerprint)

            app_state.add_event(
                "WARN",
                "unknown_popup",
                f"Popup desconhecido bloqueando a interface: {summary}",
            )

        app_state.ui_blocked_by(
            message=summary,
            screenshot=screenshot,
        )

        return {
            "blocked": True,
            "dismissed": False,
            "text": summary,
            "screenshot": screenshot,
            "fingerprint": fingerprint,
        }
