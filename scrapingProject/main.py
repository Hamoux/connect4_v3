import os
import time
import random
import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class BGABot:
    def __init__(self, chrome_version=144):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        user_data_path = os.path.join(script_dir, "profile")

        options = uc.ChromeOptions()
        options.add_argument(f"--user-data-dir={user_data_path}")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--start-maximized")

        print(f"Launching Chrome v{chrome_version}...")
        self.driver = uc.Chrome(options=options, version_main=chrome_version)
        self.driver.set_page_load_timeout(30)
        self.wait = WebDriverWait(self.driver, 20)

    def login(self):
        print("Opening BGA... Please log in manually if prompted.")
        self.driver.get("https://en.boardgamearena.com/account")
        login_wait = WebDriverWait(self.driver, 600)
        login_wait.until(lambda d: "account" not in d.current_url)
        print("\n--- LOGIN DETECTED ---")
        time.sleep(2)

    def navigate_to_game(self, game_name="connectfour"):
        url = f"https://boardgamearena.com/gamepanel?game={game_name}"
        print(f"Navigating to: {url}")
        self.driver.get(url)

    def start_table(self):

        print("🔍 Monitoring table state (Waiting for Start or Accept)...")

        # Locators
        start_xpath = "//a[contains(@class, 'bga-button')]//div[contains(text(), 'Démarrer')]"
        accept_id = "ags_start_game_accept"
        board_id = "board"

        while True:
            # 1. First, clear any trophy/award popups that block the screen
            self.clear_popups()

            try:
                # 2. Check if the match has already started and board is visible
                # This is our exit condition.
                board_elements = self.driver.find_elements(By.ID, board_id)
                if board_elements and board_elements[0].is_displayed():
                    print("✅ Game board detected! Transitioning to play loop.")
                    return True

                # 3. Check for the 'Accepter' button (Appears when opponent is found)
                accept_btns = self.driver.find_elements(By.ID, accept_id)
                if accept_btns and accept_btns[0].is_displayed():
                    print("✅ Opponent found! Clicking 'Accepter'...")
                    self.driver.execute_script("arguments[0].click();", accept_btns[0])
                    time.sleep(2)
                    continue  # Stay in loop to wait for the actual board load

                # 4. Check for 'Démarrer' button (Initial table creation)
                start_btns = self.driver.find_elements(By.XPATH, start_xpath)
                if start_btns and start_btns[0].is_displayed():
                    print("✅ Clicking 'Démarrer' to open the table...")
                    self.driver.execute_script("arguments[0].click();", start_btns[0])
                    time.sleep(2)
                    continue

                # 5. Check 'current_player_is_active' in body (Fallback turn detection)
                body_class = self.driver.find_element(By.TAG_NAME, "body").get_attribute("class")
                if "current_player_is_active" in body_class:
                    print("✅ Active turn detected via body class. Let's go!")
                    return True

                # Small sleep to prevent crashing the CPU
                time.sleep(2)

            except WebDriverException as e:
                print(f"⌛ Connection unstable, retrying... ({e})")
                time.sleep(2)
            except Exception as e:
                # General safety to keep the loop running
                time.sleep(2)

    def accept_and_load_board(self):
        print("🚀 Bot en attente d'un adversaire... (Ne fermez pas la fenêtre)")
        accept_id = "ags_start_game_accept"

        while True:



            try:
                # 1. Try to find the 'Accepter' button
                accept_btns = self.driver.find_elements(By.ID, accept_id)
                if accept_btns and accept_btns[0].is_displayed():
                    print("✅ Joueur trouvé ! Clic sur 'Accepter'...")
                    self.driver.execute_script("arguments[0].click();", accept_btns[0])
                    time.sleep(2)

                # 2. Check if the board is loaded
                board_elements = self.driver.find_elements(By.ID, "board")
                if board_elements and board_elements[0].is_displayed():
                    self.counter = self.counter+1

                    print("✅ Plateau de jeu détecté. La partie commence !")
                    return True

                time.sleep(2)  # Poll every 2 seconds
            except Exception:
                time.sleep(2)

    def play_random_move(self):
        try:
            # 1. Check for End of Game first
            title_text = self.driver.find_element(By.ID, "pagemaintitletext").text
            if "Fin de la partie" in title_text or "Victoire" in title_text:
                print(f"🏁 Game Over Detected: {title_text}")
                return "GAME_OVER"

            # 2. Turn Detection
            is_active = self.driver.find_elements(By.CSS_SELECTOR, "body.current_player_is_active")
            if not is_active:
                return "WAITING"

            print("🎲 My turn! Playing...")
            clickable_squares = self.driver.find_elements(By.CSS_SELECTOR, "#board .square.possibleMove")

            if clickable_squares:
                target = random.choice(clickable_squares)
                self.driver.execute_script("arguments[0].click();", target)
                time.sleep(3)
                return "MOVED"

            return "WAITING"

        except Exception as e:
            print(f"⌛ Polling game state...")
            return "WAITING"

    def close(self):
        print("\nBot terminé. Appuyez sur Entrée pour fermer.")
        input()
        self.driver.quit()

    def clear_popups(self):
        """Detects trophy or achievement popups and clicks 'Continuer'."""
        try:
            # Look for any button where the ID starts with 'continue_btn_'
            # BGA uses dynamic numbers, so [id^='...'] is the most reliable way.
            popups = self.driver.find_elements(By.CSS_SELECTOR, "div[id^='continue_btn_']")

            for popup in popups:
                if popup.is_displayed():
                    print("🏆 Trophy popup detected! Clearing...")
                    self.driver.execute_script("arguments[0].click();", popup)
                    time.sleep(1)  # Wait for the fade-out animation
                    # Recursively check again in case there is a second popup
                    self.clear_popups()
        except Exception:
            pass  # We don't want the janitor to crash the bot

    def select_realtime_mode(self):
        """Loops until the 'Temps Réel' mode is successfully selected and confirmed."""
        print("🔄 Entrée dans la boucle de sélection du mode...")

        while True:
            try:
                # 1. Wait for the main dropdown button to be present
                # Use a specific selector for the button inside the mode-select block
                dropdown_button = self.wait.until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, ".panel-block--buttons__mode-select .bga-dropdown-button")
                ))

                # 2. Check if it's ALREADY in Realtime mode
                # We check the text inside the button
                current_mode_text = dropdown_button.text.upper()
                if "TEMPS RÉEL" in current_mode_text:
                    print("✅ Mode Temps Réel confirmé.")
                    return True

                print(f"🧐 Mode actuel : '{current_mode_text}'. Tentative de basculement...")

                # 3. Open the dropdown
                self.driver.execute_script("arguments[0].click();", dropdown_button)
                time.sleep(1.5)

                # 4. Look for the Realtime option
                # We use a broad search to find the specific realtime option button
                realtime_option = WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, ".bga-dropdown-option-realtime")
                ))

                # 5. Click it
                self.driver.execute_script("arguments[0].click();", realtime_option)
                print("🖱️ Clic sur l'option 'Temps Réel' effectué.")

                # Wait a moment for BGA to update the UI
                time.sleep(2)

            except Exception as e:
                print("⌛ Échec de la sélection, nouvelle tentative dans 2s...")
                # If we get stuck, a small refresh might help
                # self.driver.refresh()
                time.sleep(2)


# --- MAIN EXECUTION ---
if __name__ == "__main__":
    bot = BGABot(chrome_version=144)
    counter =0
    try:
        bot.login()

        while True:  # Outer loop for multiple games
            print("\n🚀 Starting a new session...")
            bot.navigate_to_game("connectfour")
            bot.select_realtime_mode()

            if bot.start_table():
                print("--- MATCH STARTED ---")
                counter = counter+1
                print(f"------------------------we are playing  game number {counter} -----------------------------\n")


                # Inner loop for the current match
                game_in_progress = True
                while game_in_progress:
                    status = bot.play_random_move()

                    if status == "GAME_OVER":
                        print("♻️ Game ended. Preparing to start a new one in 10 seconds...")
                        time.sleep(10)  # Wait to let the results process
                        game_in_progress = False  # Break inner loop to restart outer loop

                    time.sleep(3)  # Check turn every 3 seconds

    except Exception as main_error:
        print(f"Fatal Error: {main_error}")
    finally:
        bot.close()