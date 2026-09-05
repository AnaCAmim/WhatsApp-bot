from pathlib import Path
import time

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from admin.state import app_state


WHATSAPP_URL = "https://web.whatsapp.com"
PROFILE_PATH = Path("chrome-profile").resolve()


def criar_driver(headless=True):
    options = Options()
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-data-dir={PROFILE_PATH}")

    if headless:
        options.add_argument("--headless")

    driver = webdriver.Chrome(options=options)
    app_state.selenium_started(mode="headless" if headless else "normal")
    return driver


def esta_logado(driver):
    try:
        elementos = driver.find_elements(By.CSS_SELECTOR, "#pane-side")
        return any(elemento.is_displayed() for elemento in elementos)
    except Exception:
        return False


def login_necessario(driver):
    try:
        canvases = driver.find_elements(By.TAG_NAME, "canvas")
        if any(canvas.is_displayed() for canvas in canvases):
            return True
    except Exception:
        pass

    try:
        elementos = driver.find_elements(By.CSS_SELECTOR, "[data-ref]")
        for elemento in elementos:
            data_ref = elemento.get_attribute("data-ref")
            if elemento.is_displayed() and data_ref and len(data_ref) > 20:
                return True
    except Exception:
        pass

    return False


def tentar_sessao_headless(timeout=45):
    app_state.set_stage("checking_session")
    driver = criar_driver(headless=True)

    try:
        driver.get(WHATSAPP_URL)

        WebDriverWait(driver, timeout, poll_frequency=1).until(
            lambda d: esta_logado(d) or login_necessario(d)
        )

        if esta_logado(driver):
            app_state.whatsapp_connect()
            return driver

    except TimeoutException:
        pass
    except Exception:
        driver.quit()
        app_state.selenium_stopped()
        raise

    driver.quit()
    app_state.selenium_stopped()
    return None


def realizar_login_visual():
    app_state.set_stage("waiting_login")
    driver = criar_driver(headless=False)

    try:
        driver.get(WHATSAPP_URL)

        print("Faça o login no WhatsApp Web. Escaneie o QR Code, se necessário.")

        WebDriverWait(driver, 180, poll_frequency=1).until(
            lambda d: esta_logado(d)
        )

        app_state.whatsapp_connect()
        return True

    except TimeoutException:
        return False

    finally:
        driver.quit()
        app_state.selenium_stopped()


def abrir_headless_pos_login():
    time.sleep(2)
    driver = criar_driver(headless=True)

    try:
        driver.get(WHATSAPP_URL)

        WebDriverWait(driver, 60, poll_frequency=1).until(
            lambda d: esta_logado(d)
        )

        app_state.whatsapp_connect()
        return driver

    except TimeoutException:
        driver.quit()
        app_state.selenium_stopped()
        app_state.whatsapp_disconnect()
        return None


def obter_driver_autenticado():
    driver = tentar_sessao_headless()
    if driver:
        return driver

    login_ok = realizar_login_visual()
    if not login_ok:
        return None

    return abrir_headless_pos_login()
