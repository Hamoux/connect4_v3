import re
import time

from bga_player_bot import BGABot
from bga_puppet import import_table_id_connect4


def get_table_id_from_url(driver):
    url = driver.current_url or ""
    m = re.search(r"table=(\d+)", url)
    return m.group(1) if m else None


if __name__ == "__main__":
    bot = BGABot()
    try:
        # 1) login manuel (détecté)
        bot.login()

        game_count = 0

        while True:
            print("\n🚀 Nouvelle partie Connect4...")
            bot.navigate_to_game("connectfour")
            bot.select_realtime_mode()

            if not bot.start_table():
                print("❌ Impossible de démarrer la table, retry...")
                time.sleep(3)
                continue

            game_count += 1
            print(f"🎮 Partie #{game_count} démarrée.")

            # 2) boucle de jeu
            while True:
                status = bot.play_random_move()

                if status == "GAME_OVER":
                    print("🏁 Fin de partie détectée.")
                    time.sleep(10)  # laisse BGA finir résultat + archive

                    table_id = get_table_id_from_url(bot.driver)
                    print("🧩 table_id détectée =", table_id)

                    if not table_id:
                        print("❌ table_id introuvable dans l’URL:", bot.driver.current_url)
                    else:
                        id_partie, err_preview = import_table_id_connect4(
                            bot.driver, table_id, rows=9, cols=9, confiance=3
                        )
                        if id_partie is None:
                            print("❌ Import échoué (moves=0). Preview:")
                            for ln in (err_preview or [])[:15]:
                                print("-", ln)
                        else:
                            print("✅ Import DB OK id_partie =", id_partie)

                    time.sleep(6)  # pause avant prochain match
                    break

                time.sleep(2)

    finally:
        bot.close()