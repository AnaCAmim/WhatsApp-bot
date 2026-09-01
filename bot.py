"""
bot.py
------
Camada de automação do WhatsApp Web via Selenium.
Toda a lógica de "clicar no navegador" fica isolada aqui, para que a
interface gráfica (gui.py) só precise chamar métodos simples.
"""

import re
import time
import random
import threading
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
)

WHATSAPP_URL = "https://web.whatsapp.com"


class WhatsAppBot:
    """Encapsula a automação do WhatsApp Web via Selenium."""

    def __init__(self, profile_path: Path, log_callback=print):
        self.profile_path = Path(profile_path).resolve()
        self.log = log_callback
        self.driver = None
        self._stop_event = threading.Event()

    # ---------------------------------------------------------------
    # Infraestrutura
    # ---------------------------------------------------------------
    def _criar_driver(self):
        options = Options()
        options.add_argument("--window-size=1200,900")
        options.add_argument(f"--user-data-dir={self.profile_path}")
        options.add_argument("--disable-notifications")
        # Observação: WhatsApp Web costuma falhar em modo headless
        # (detecção de automação / QR code), por isso a janela fica visível.
        return webdriver.Chrome(options=options)

    def stop(self):
        """Sinaliza para interromper um disparo em massa em andamento."""
        self._stop_event.set()

    def _checar_stop(self):
        if self._stop_event.is_set():
            raise InterruptedError("Disparo interrompido pelo usuário.")

    # ---------------------------------------------------------------
    # Login / sessão
    # ---------------------------------------------------------------
    def conectar(self, timeout=180) -> bool:
        """Abre o navegador e aguarda o QR Code ser escaneado (ou sessão salva ser reconhecida)."""
        self._stop_event.clear()
        if self.driver is None:
            self.log("Abrindo navegador...")
            self.driver = self._criar_driver()

        self.driver.get(WHATSAPP_URL)
        self.log("Aguardando autenticação (escaneie o QR Code se solicitado)...")
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.ID, "pane-side"))
            )
            self.log("Login realizado com sucesso!")
            return True
        except TimeoutException:
            self.log("Timeout aguardando login.")
            self._salvar_screenshot("login_timeout.png")
            return False

    def esta_conectado(self) -> bool:
        if self.driver is None:
            return False
        try:
            self.driver.find_element(By.ID, "pane-side")
            return True
        except NoSuchElementException:
            return False

    def encerrar(self):
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    # ---------------------------------------------------------------
    # Utilidades
    # ---------------------------------------------------------------
    @staticmethod
    def normalizar_telefone(numero: str) -> str:
        """Mantém apenas dígitos. O número deve incluir o DDI (ex.: 55 + DDD + número)."""
        return re.sub(r"\D", "", numero or "")

    def _salvar_screenshot(self, nome):
        try:
            self.driver.save_screenshot(nome)
            self.log(f"Screenshot salvo: {nome}")
        except Exception:
            pass

    # ---------------------------------------------------------------
    # Envio
    # ---------------------------------------------------------------
    def enviar_mensagem(self, telefone: str, mensagem: str, timeout=30) -> bool:
        """Envia uma mensagem de texto para um número específico."""
        numero = self.normalizar_telefone(telefone)
        if not numero:
            self.log(f"Número inválido: {telefone!r}")
            return False

        url = f"{WHATSAPP_URL}/send?phone={numero}&text&type=phone_number&app_absent=0"
        self.driver.get(url)

        try:
            caixa_texto = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'footer div[contenteditable="true"]')
                )
            )
        except TimeoutException:
            self.log(f"[{telefone}] Não foi possível abrir a conversa "
                      f"(número inválido, sem WhatsApp, ou carregamento lento).")
            return False

        try:
            caixa_texto.click()
            linhas = mensagem.split("\n")
            for i, linha in enumerate(linhas):
                caixa_texto.send_keys(linha)
                if i < len(linhas) - 1:
                    caixa_texto.send_keys(Keys.SHIFT, Keys.ENTER)
            caixa_texto.send_keys(Keys.ENTER)
            self.log(f"[{telefone}] Mensagem enviada.")
            return True
        except ElementClickInterceptedException:
            self.log(f"[{telefone}] Falha ao clicar na caixa de mensagem.")
            return False
        except Exception as exc:
            self.log(f"[{telefone}] Erro ao enviar: {exc}")
            return False

    def enviar_midia(self, telefone: str, caminho_arquivo: str, legenda: str = "", timeout=30) -> bool:
        """Envia uma imagem ou vídeo (com legenda opcional) para um número específico."""
        numero = self.normalizar_telefone(telefone)
        if not numero:
            self.log(f"Número inválido: {telefone!r}")
            return False

        caminho = Path(caminho_arquivo)
        if not caminho.is_file():
            self.log(f"Arquivo de mídia não encontrado: {caminho_arquivo}")
            return False

        url = f"{WHATSAPP_URL}/send?phone={numero}&type=phone_number&app_absent=0"
        self.driver.get(url)

        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'footer div[contenteditable="true"]'))
            )
        except TimeoutException:
            self.log(f"[{telefone}] Não foi possível abrir a conversa "
                      f"(número inválido, sem WhatsApp, ou carregamento lento).")
            return False

        try:
            botao_anexar = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, 'span[data-icon="plus-rounded"], span[data-icon="attach-menu-plus"], '
                                       'span[data-icon="clip"], div[title="Anexar"]')
                )
            )
            botao_anexar.click()
        except TimeoutException:
            self.log(f"[{telefone}] Não foi possível abrir o menu de anexos.")
            return False

        try:
            input_arquivo = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[accept*="image"]'))
            )
            input_arquivo.send_keys(str(caminho.resolve()))
        except TimeoutException:
            self.log(f"[{telefone}] Não foi possível anexar o arquivo (campo de upload não encontrado).")
            return False

        if legenda:
            try:
                caixa_legenda = WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'div[contenteditable="true"][data-tab]'))
                )
                linhas = legenda.split("\n")
                for i, linha in enumerate(linhas):
                    caixa_legenda.send_keys(linha)
                    if i < len(linhas) - 1:
                        caixa_legenda.send_keys(Keys.SHIFT, Keys.ENTER)
            except TimeoutException:
                pass

        try:
            botao_enviar = WebDriverWait(self.driver, timeout).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, 'span[data-icon="send"], span[data-icon="wds-ic-send-filled"]')
                )
            )
            botao_enviar.click()
            time.sleep(2)
            self.log(f"[{telefone}] Mídia enviada.")
            return True
        except TimeoutException:
            self.log(f"[{telefone}] Não foi possível enviar a mídia.")
            return False

    def enviar_em_massa(self, contatos, mensagem_template, delay_min=5, delay_max=12,
                         progresso_callback=None, caminho_midia: str = None):
        """
        contatos: lista de dicts {"nome": str, "telefone": str}
        mensagem_template: string podendo conter o marcador {nome}
        progresso_callback: função opcional chamada como progresso_callback(atual, total)
        caminho_midia: caminho opcional de imagem/vídeo enviado junto com a mensagem (como legenda)

        Retorna um resumo: {"enviados": int, "falhas": int, "interrompido": bool}
        """
        self._stop_event.clear()
        enviados, falhas = 0, 0
        total = len(contatos)

        for i, contato in enumerate(contatos, start=1):
            try:
                self._checar_stop()
            except InterruptedError as exc:
                self.log(str(exc))
                if progresso_callback:
                    progresso_callback(i - 1, total)
                return {"enviados": enviados, "falhas": falhas, "interrompido": True}

            nome = contato.get("nome", "")
            telefone = contato.get("telefone", "")
            texto = mensagem_template.replace("{nome}", nome)

            if caminho_midia:
                ok = self.enviar_midia(telefone, caminho_midia, texto)
            else:
                ok = self.enviar_mensagem(telefone, texto)
            if ok:
                enviados += 1
            else:
                falhas += 1

            if progresso_callback:
                progresso_callback(i, total)

            if i < total:
                espera = random.uniform(delay_min, delay_max)
                self.log(f"Aguardando {espera:.1f}s antes do próximo envio...")
                intervalos = max(1, int(espera * 10))
                for _ in range(intervalos):
                    if self._stop_event.is_set():
                        break
                    time.sleep(0.1)

        return {"enviados": enviados, "falhas": falhas, "interrompido": False}
