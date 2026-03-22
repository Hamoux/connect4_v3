"""
Connexion BGA — préférez les variables d'environnement BGA_USERNAME / BGA_PASSWORD.
Si l'automatisation échoue (captcha, changement HTML), bascule sur saisie manuelle.
"""

from __future__ import annotations

import logging
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger(__name__)


def try_automated_login(driver, username: str, password: str, timeout: int = 25) -> bool:
    """Tente de remplir le formulaire sur /account. Retourne True si succès probable."""
    if not username or not password:
        return False
    try:
        driver.get("https://boardgamearena.com/account")
        time.sleep(1.5)
        wait = WebDriverWait(driver, timeout)

        # Champs fréquents (BGA peut utiliser name="email" ou id)
        user_el = None
        for sel in (
            (By.NAME, "email"),
            (By.ID, "email_input"),
            (By.CSS_SELECTOR, "input[type=email]"),
            (By.CSS_SELECTOR, "input[name=username]"),
        ):
            try:
                user_el = wait.until(EC.presence_of_element_located(sel))
                break
            except Exception:
                continue
        if user_el is None:
            return False

        pass_el = None
        for sel in ((By.NAME, "password"), (By.CSS_SELECTOR, "input[type=password]")):
            try:
                pass_el = driver.find_element(*sel)
                break
            except Exception:
                continue
        if pass_el is None:
            return False

        user_el.clear()
        user_el.send_keys(username)
        pass_el.clear()
        pass_el.send_keys(password)

        for sel in (
            (By.CSS_SELECTOR, "button[type=submit]"),
            (By.CSS_SELECTOR, "input[type=submit]"),
            (By.XPATH, "//button[contains(., 'Connexion')]"),
            (By.XPATH, "//button[contains(., 'Log in')]"),
        ):
            try:
                btn = driver.find_element(*sel)
                driver.execute_script("arguments[0].click();", btn)
                break
            except Exception:
                continue

        time.sleep(3.0)
        url = driver.current_url or ""
        if "account" not in url.lower() or "logout" in (driver.page_source or "").lower():
            log.info("Connexion automatique probablement réussie.")
            return True
    except Exception as e:
        log.warning("Échec login automatique: %s", e)
    return False


def manual_login_prompt(driver) -> None:
    """Attend que l'utilisateur se connecte dans la fenêtre."""
    driver.get("https://boardgamearena.com/account")
    time.sleep(1.0)
    input("Connecte-toi dans le navigateur, puis appuie sur Entrée…")
