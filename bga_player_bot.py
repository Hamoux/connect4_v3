import os
import time
import random

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class BGABot:
    def __init__(self, chrome_profile_dir=None):
        """
        Selenium normal (compatible Python 3.14).
        Utilise un profil Chrome dédié pour garder la session BGA.
        """
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # Profil CHROME dédié au bot (évite les crashs avec ton Chrome perso)
        if chrome_profile_dir is None:
            chrome_profile_dir = os.path.join(script_dir, "selenium_profile")

        opts = Options()
        opts.add_argument(f"--user-data-dir={chrome_profile_dir}")
        opts.add_argument("--start-maximized")
        opts.add_argument("--disable-popup-blocking")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        print("Launching Chrome (Selenium)...")
        self.driver = webdriver.Chrome(options=opts)
        self.driver.set_page_load_timeout(60)
        self.wait = WebDriverWait(self.driver, 20)

    def login(self):
        """
        Login manuel : si tu es déjà connecté via le profil, c’est instant.
        """
        print("Opening BGA login page... (manual login if needed)")
        self.driver.get("https://boardgamearena.com/account")
        time.sleep(2)

        print("👉 Si tu n'es pas connecté, connecte-toi maintenant dans la fenêtre Chrome.")
        print("✅ Quand tu vois ton avatar/profil, reviens ici.")
        input("Appuie sur Entrée...")

        # petit check “on a quitté /account” ou au moins la page est chargée
        time.sleep(2)
        print("✅ Login step done. Current URL =", self.driver.current_url)

    def navigate_to_game(self, game_name="connectfour"):
        url = f"https://boardgamearena.com/gamepanel?game={game_name}"
        print(f"Navigating to: {url}")
        self.driver.get(url)
        time.sleep(2)

    def clear_popups(self):
        """Ferme les popups trophées/achievements qui bloquent l'écran."""
        try:
            popups = self.driver.find_elements(By.CSS_SELECTOR, "div[id^='continue_btn_']")
            for popup in popups:
                if popup.is_displayed():
                    print("🏆 Popup detected, clearing...")
                    self.driver.execute_script("arguments[0].click();", popup)
                    time.sleep(1)
                    self.clear_popups()
        except Exception:
            pass

    def select_realtime_mode(self):
        """Boucle jusqu'à ce que le mode Temps Réel soit sélectionné."""
        print("🔄 Selecting Realtime mode...")

        while True:
            try:
                dropdown_button = self.wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, ".panel-block--buttons__mode-select .bga-dropdown-button"))
                )
                current_mode_text = (dropdown_button.text or "").upper()

                if "TEMPS RÉEL" in current_mode_text or "REALTIME" in current_mode_text:
                    print("✅ Realtime mode confirmed.")
                    return True

                print(f"🧐 Current mode: '{current_mode_text}'. Switching...")
                self.driver.execute_script("arguments[0].click();", dropdown_button)
                time.sleep(1.2)

                realtime_option = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, ".bga-dropdown-option-realtime"))
                )
                self.driver.execute_script("arguments[0].click();", realtime_option)
                time.sleep(1.5)

            except Exception:
                print("⌛ Failed switching mode, retry in 2s...")
                time.sleep(2)

    def start_table(self):
        """
        Attend Démarrer / Accepter / Board chargé.
        """
        print("🔍 Waiting for Start/Accept/Board...")

        start_xpath = "//a[contains(@class, 'bga-button')]//div[contains(text(), 'Démarrer') or contains(text(), 'Start')]"
        accept_id = "ags_start_game_accept"
        board_id = "board"

        while True:
            self.clear_popups()

            try:
                # plateau chargé
                board_elements = self.driver.find_elements(By.ID, board_id)
                if board_elements and board_elements[0].is_displayed():
                    print("✅ Board detected.")
                    return True

                # bouton accepter
                accept_btns = self.driver.find_elements(By.ID, accept_id)
                if accept_btns and accept_btns[0].is_displayed():
                    print("✅ Opponent found. Clicking 'Accepter'...")
                    self.driver.execute_script("arguments[0].click();", accept_btns[0])
                    time.sleep(2)
                    continue

                # bouton démarrer
                start_btns = self.driver.find_elements(By.XPATH, start_xpath)
                if start_btns and start_btns[0].is_displayed():
                    print("✅ Clicking 'Démarrer'...")
                    self.driver.execute_script("arguments[0].click();", start_btns[0])
                    time.sleep(2)
                    continue

                # fallback : classe tour actif
                body_class = self.driver.find_element(By.TAG_NAME, "body").get_attribute("class") or ""
                if "current_player_is_active" in body_class:
                    print("✅ Active turn detected via body class.")
                    return True

                time.sleep(2)

            except WebDriverException:
                print("⌛ WebDriver hiccup, retry...")
                time.sleep(2)
            except Exception:
                time.sleep(2)

    def play_random_move(self):
        """
        Joue un coup au hasard quand c'est notre tour.
        Retourne: 'WAITING' | 'MOVED' | 'GAME_OVER'
        """
        try:
            # fin de partie (titre)
            try:
                title_text = (self.driver.find_element(By.ID, "pagemaintitletext").text or "")
                if ("Fin de la partie" in title_text) or ("Victoire" in title_text) or ("Game over" in title_text):
                    print(f"🏁 Game Over Detected: {title_text}")
                    return "GAME_OVER"
            except Exception:
                pass

            # est-ce notre tour ?
            is_active = self.driver.find_elements(By.CSS_SELECTOR, "body.current_player_is_active")
            if not is_active:
                return "WAITING"

            print("🎲 My turn! Playing...")
            clickable = self.driver.find_elements(By.CSS_SELECTOR, "#board .square.possibleMove")
            if clickable:
                target = random.choice(clickable)
                self.driver.execute_script("arguments[0].click();", target)
                time.sleep(2.5)
                return "MOVED"

            return "WAITING"

        except Exception:
            return "WAITING"

    def close(self):
        print("\nBot terminé. Appuyez sur Entrée pour fermer.")
        input()
        try:
            self.driver.quit()
        except Exception:
            pass