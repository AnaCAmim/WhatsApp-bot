import re
import unicodedata

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
)

from admin.state import app_state
from bot.models import UnreadChat


class UnreadChatScanner:
    UI_FILTER_NAMES = {
        "tudo",
        "all",
        "não lidas",
        "nao lidas",
        "unread",
        "favoritos",
        "favorites",
        "grupos",
        "groups",
        "arquivadas",
        "archived",
    }

    def __init__(self, driver):
        self.driver = driver
        self._previous_counts = {}
        self._normalized_ui_filter_names = {
            self._normalize_chat_name(value)
            for value in self.UI_FILTER_NAMES
        }

    @staticmethod
    def _normalize(value):
        if not value:
            return ""

        value = value.lower()
        value = unicodedata.normalize("NFKD", value)

        return "".join(
            character
            for character in value
            if not unicodedata.combining(character)
        )

    @staticmethod
    def _normalize_chat_name(value):
        if not value:
            return ""

        value = unicodedata.normalize("NFKC", value)

        value = re.sub(
            r"[\u200b\u200c\u200d\u2060\ufeff]",
            "",
            value
        )

        return value.strip().casefold()

    def _is_ui_filter_name(self, name):
        if not name:
            return True

        normalized = self._normalize_chat_name(name)

        return normalized in self._normalized_ui_filter_names

    def _is_inside_filter_area(self, element):
        try:
            return bool(
                self.driver.execute_script(
                    """
                    const element = arguments[0];

                    return !!(
                        element.closest('[role="tablist"]')
                        ||
                        element.closest('[role="tab"]')
                    );
                    """,
                    element
                )
            )
        except Exception:
            return False

    def _is_real_chat_row(self, row):
        if row is None:
            return False

        try:
            role = (
                row.get_attribute("role")
                or ""
            ).strip().lower()

            if role in ("button", "tab", "tablist"):
                return False

            if self._is_inside_filter_area(row):
                return False

            has_chat_title = bool(
                row.find_elements(
                    By.CSS_SELECTOR,
                    (
                        '[data-testid="cell-frame-title"], '
                        'span[title]'
                    )
                )
            )

            if not has_chat_title:
                return False

            return True

        except Exception:
            return False

    def _extract_unread_count(self, label):
        normalized = self._normalize(label)

        unread_terms = (
            "mensagem nao lida",
            "mensagens nao lidas",
            "unread message",
            "unread messages",
        )

        if not any(
            term in normalized
            for term in unread_terms
        ):
            return None

        match = re.search(r"\d+", normalized)

        if match:
            return int(match.group())

        return 1

    def _find_chat_row(self, element):
        try:
            return self.driver.execute_script(
                """
                const element = arguments[0];

                return (
                    element.closest(
                        '[data-testid="cell-frame-container"]'
                    )
                    ||
                    element.closest(
                        '[role="row"][data-testid^="list-item-"]'
                    )
                    ||
                    element.closest(
                        '[role="row"]'
                    )
                    ||
                    element.closest(
                        '[role="listitem"]'
                    )
                    ||
                    element.closest(
                        '[tabindex="0"]'
                    )
                    ||
                    null
                );
                """,
                element
            )
        except Exception:
            return None

    def _extract_chat_name(self, row):
        if row is None:
            return None

        try:
            title_elements = row.find_elements(
                By.CSS_SELECTOR,
                (
                    '[data-testid="cell-frame-title"], '
                    'span[title]'
                )
            )

            for element in title_elements:
                try:
                    title = (
                        element.get_attribute("title")
                        or element.text
                        or ""
                    ).strip()

                    if not title:
                        continue

                    if self._is_ui_filter_name(title):
                        continue

                    return title

                except StaleElementReferenceException:
                    continue

        except StaleElementReferenceException:
            return None

        return None

    def _find_chat_by_name(self, chat_name):
        expected = self._normalize_chat_name(chat_name)

        if not expected:
            return None

        if self._is_ui_filter_name(chat_name):
            return None

        try:
            pane = self.driver.find_element(
                By.ID,
                "pane-side"
            )
        except Exception:
            return None

        candidates = pane.find_elements(
            By.CSS_SELECTOR,
            (
                '[data-testid="cell-frame-title"], '
                'span[title]'
            )
        )

        for title_element in candidates:
            try:
                if not title_element.is_displayed():
                    continue

                if self._is_inside_filter_area(title_element):
                    continue

                title = (
                    title_element.get_attribute("title")
                    or title_element.text
                    or ""
                )

                if self._is_ui_filter_name(title):
                    continue

                normalized = self._normalize_chat_name(title)

                if normalized != expected:
                    continue

                row = self._find_chat_row(title_element)

                if not self._is_real_chat_row(row):
                    continue

                return row

            except StaleElementReferenceException:
                continue

            except Exception:
                continue

        return None

    def debug_visible_chat_names(self):
        try:
            pane = self.driver.find_element(
                By.ID,
                "pane-side"
            )

            elements = pane.find_elements(
                By.CSS_SELECTOR,
                (
                    '[data-testid="cell-frame-title"], '
                    'span[title]'
                )
            )

            names = []

            for element in elements:
                try:
                    if not element.is_displayed():
                        continue

                    if self._is_inside_filter_area(element):
                        continue

                    value = (
                        element.get_attribute("title")
                        or element.text
                        or ""
                    ).strip()

                    if not value:
                        continue

                    if self._is_ui_filter_name(value):
                        continue

                    names.append(value)

                except Exception:
                    continue

            return list(dict.fromkeys(names))

        except Exception:
            return []

    def scan(self):
        pane = self.driver.find_element(
            By.ID,
            "pane-side"
        )

        candidates = pane.find_elements(
            By.CSS_SELECTOR,
            "[aria-label]"
        )

        chats = []
        processed_rows = set()

        for candidate in candidates:
            try:
                if not candidate.is_displayed():
                    continue

                if self._is_inside_filter_area(candidate):
                    continue

                label = candidate.get_attribute("aria-label")

                unread_count = self._extract_unread_count(label)

                if unread_count is None:
                    continue

                row = self._find_chat_row(candidate)

                if row is None:
                    continue

                if not self._is_real_chat_row(row):
                    continue

                row_id = row.id

                if row_id in processed_rows:
                    continue

                chat_name = self._extract_chat_name(row)

                if not chat_name:
                    continue

                if self._is_ui_filter_name(chat_name):
                    continue

                located_row = self._find_chat_by_name(chat_name)

                if located_row is None:
                    continue

                processed_rows.add(row_id)

                chats.append(
                    UnreadChat(
                        name=chat_name,
                        unread_count=unread_count,
                        label=label or ""
                    )
                )

            except StaleElementReferenceException:
                continue

            except Exception:
                continue

        return chats

    def scan_and_publish(self):
        chats = self.scan()

        current_counts = {
            chat.name: chat.unread_count
            for chat in chats
        }

        for chat in chats:
            previous = self._previous_counts.get(
                chat.name,
                0
            )

            if chat.unread_count > previous:
                difference = chat.unread_count - previous

                app_state.add_event(
                    "INFO",
                    "unread_message",
                    (
                        f"{chat.name}: "
                        f"+{difference} mensagem(ns) "
                        f"não lida(s)"
                    )
                )

                print(
                    f"[NOVA MENSAGEM] "
                    f"{chat.name} "
                    f"({chat.unread_count} não lidas)"
                )

        self._previous_counts = current_counts

        app_state.update_unread_chats(
            [
                chat.to_dict()
                for chat in chats
            ]
        )

        return chats

    def _is_chat_open(self, chat_name):
        expected = self._normalize_chat_name(chat_name)

        try:
            candidates = self.driver.find_elements(
                By.CSS_SELECTOR,
                (
                    '#main header '
                    '[data-testid="conversation-info-header-chat-title"], '
                    '#main header span[title], '
                    'header '
                    '[data-testid="conversation-info-header-chat-title"]'
                )
            )

            for element in candidates:
                try:
                    value = (
                        element.get_attribute("title")
                        or element.text
                        or ""
                    )

                    if (
                        self._normalize_chat_name(value)
                        == expected
                    ):
                        return True

                except StaleElementReferenceException:
                    continue

            mains = self.driver.find_elements(
                By.ID,
                "main"
            )

            return any(
                main.is_displayed()
                for main in mains
            )

        except Exception:
            return False

    def open_chat(self, chat_name, timeout=15):
        print()
        print(
            f"[CHAT] Tentando abrir: "
            f"{chat_name}"
        )

        try:
            row = WebDriverWait(
                self.driver,
                timeout,
                poll_frequency=0.5
            ).until(
                lambda d:
                    self._find_chat_by_name(chat_name)
                    or False,
                message=(
                    f"Conversa '{chat_name}' "
                    f"não encontrada na lista."
                )
            )

        except TimeoutException:
            print()
            print(
                "========== DEBUG LISTA =========="
            )

            print(
                "Procurando:",
                repr(chat_name)
            )

            print(
                "Normalizado:",
                repr(
                    self._normalize_chat_name(chat_name)
                )
            )

            print("Conversas visíveis:")

            visible_names = self.debug_visible_chat_names()

            if visible_names:
                for name in visible_names:
                    print(" -", repr(name))
            else:
                print(
                    " - nenhuma conversa "
                    "detectada no DOM visível"
                )

            print(
                "================================="
            )

            raise

        print(
            f"[CHAT] Conversa encontrada: "
            f"{chat_name}"
        )

        self.driver.execute_script(
            """
            arguments[0].scrollIntoView({
                block: "center",
                inline: "nearest"
            });
            """,
            row
        )

        try:
            ActionChains(
                self.driver
            ).move_to_element(
                row
            ).pause(
                0.2
            ).click().perform()

            print(
                "[CHAT] Click via ActionChains"
            )

        except Exception as action_error:
            print(
                "[CHAT] ActionChains falhou:",
                action_error
            )

            self.driver.execute_script(
                "arguments[0].click();",
                row
            )

            print(
                "[CHAT] Click via JavaScript"
            )

        WebDriverWait(
            self.driver,
            timeout,
            poll_frequency=0.5
        ).until(
            lambda d:
                self._is_chat_open(chat_name),
            message=(
                f"Conversa '{chat_name}' "
                f"foi encontrada e clicada, "
                f"mas não foi confirmada como aberta."
            )
        )

        print(
            f"[CHAT] Conversa aberta: "
            f"{chat_name}"
        )

        return True
