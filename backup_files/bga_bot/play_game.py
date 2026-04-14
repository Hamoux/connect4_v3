"""
Bot BGA : remplace les coups aléatoires par Minimax (réutilise bga_player_bot.BGABot).
Le plateau est reconstruit depuis le DOM — les sélecteurs peuvent nécessiter un ajustement
si BGA change son HTML.
"""

from __future__ import annotations

import random
import re
import time

from selenium.webdriver.common.by import By

from bga_bot.minimax_adapter import MinimaxMoveSelector
from bga_player_bot import BGABot


def _sleep_jitter(a: float, b: float) -> None:
    time.sleep(random.uniform(a, b))


def parse_board_from_bga_dom(driver, rows: int = 9, cols: int = 9):
    """
    Tente de lire le plateau 9x9 depuis le DOM BGA.
    Stratégies multiples (classes / data-pos / ordre des 81 cases).
    """
    board = [[0 for _ in range(cols)] for _ in range(rows)]

    # 1) Cases avec coordonnées explicites (si présentes)
    try:
        cells = driver.find_elements(By.CSS_SELECTOR, "#board [data-row][data-col]")
        if cells:
            for el in cells:
                try:
                    r = int(el.get_attribute("data-row"))
                    c = int(el.get_attribute("data-col"))
                    cls = (el.get_attribute("class") or "").lower()
                    token = _class_to_token(cls)
                    if 0 <= r < rows and 0 <= c < cols and token:
                        board[r][c] = token
                except (TypeError, ValueError):
                    continue
            if _board_non_empty(board):
                return board
    except Exception:
        pass

    # 2) Grille .square en ordre ligne par ligne (81 cases)
    try:
        squares = driver.find_elements(By.CSS_SELECTOR, "#board .square")
        if len(squares) >= rows * cols:
            idx = 0
            for r in range(rows):
                for c in range(cols):
                    el = squares[idx]
                    idx += 1
                    cls = (el.get_attribute("class") or "").lower()
                    tok = _class_to_token(cls)
                    board[r][c] = tok if tok else 0
            if _board_non_empty(board):
                return board
    except Exception:
        pass

    # 3) Dernier recours : texte / style (très fragile)
    return board


def _class_to_token(cls: str):
    if "player1" in cls or "red" in cls or "color_1" in cls or "p1" in cls.split():
        return "R"
    if "player2" in cls or "yellow" in cls or "color_2" in cls or "p2" in cls.split():
        return "J"
    return 0


def _board_non_empty(board) -> bool:
    return any(board[r][c] != 0 for r in range(len(board)) for c in range(len(board[0])))


def map_column_clickable_to_col(clickable_element, driver) -> int | None:
    """Déduit la colonne 0..8 depuis une case cliquable."""
    # data-col
    for attr in ("data-col", "data-x", "id"):
        v = clickable_element.get_attribute(attr) or ""
        m = re.search(r"(\d+)", v)
        if m:
            col = int(m.group(1))
            if 0 <= col < 9:
                return col
    # position dans la liste des colonnes possibles
    try:
        poss = driver.find_elements(By.CSS_SELECTOR, "#board .square.possibleMove")
        if clickable_element in poss:
            return poss.index(clickable_element)
    except Exception:
        pass
    return None


class MinimaxBGABot(BGABot):
    def __init__(self, chrome_profile_dir=None, depth: int = 6):
        super().__init__(chrome_profile_dir=chrome_profile_dir)
        self.selector = MinimaxMoveSelector(9, 9, depth=depth)
        self.games_played_today = 0

    def play_minimax_move(self, my_color: str = "R"):
        """
        Comme play_random_move mais choisit la colonne via Minimax.
        Retourne: 'WAITING' | 'MOVED' | 'GAME_OVER'
        """
        try:
            try:
                title_text = (self.driver.find_element(By.ID, "pagemaintitletext").text or "")
                if ("Fin de la partie" in title_text) or ("Victoire" in title_text) or ("Game over" in title_text.lower()):
                    return "GAME_OVER"
            except Exception:
                pass

            is_active = self.driver.find_elements(By.CSS_SELECTOR, "body.current_player_is_active")
            if not is_active:
                return "WAITING"

            board = parse_board_from_bga_dom(self.driver)
            if not _board_non_empty(board):
                # Plateau illisible : repli sur premier coup possible (évite blocage)
                clickables = self.driver.find_elements(By.CSS_SELECTOR, "#board .square.possibleMove")
                if clickables:
                    target = clickables[0]
                    self.driver.execute_script("arguments[0].click();", target)
                    _sleep_jitter(1.8, 3.0)
                    return "MOVED"
                return "WAITING"

            col = self.selector.best_col(board, my_color)
            if col is None:
                return "WAITING"

            clickables = self.driver.find_elements(By.CSS_SELECTOR, "#board .square.possibleMove")
            chosen = None
            for el in clickables:
                mapped = map_column_clickable_to_col(el, self.driver)
                if mapped == col:
                    chosen = el
                    break
            if chosen is None and clickables:
                # aligner par index de colonne si même nombre que cols
                if len(clickables) == 1:
                    chosen = clickables[0]
                elif col < len(clickables):
                    chosen = clickables[col]

            if chosen:
                self.driver.execute_script("arguments[0].click();", chosen)
                _sleep_jitter(1.8, 3.0)
                self.selector.engine.clear_cache()
                return "MOVED"
            return "WAITING"
        except Exception:
            return "WAITING"


def run_bot_loop(depth: int = 6, daily_cap: int = 50, jitter: tuple[float, float] = (1.5, 3.0)):
    """Boucle simple : une partie, coups Minimax jusqu'à la fin. Respecte un plafond journalier approximatif."""
    bot = MinimaxBGABot(depth=depth)
    try:
        bot.login()
        bot.navigate_to_game("connectfour")
        bot.select_realtime_mode()
        bot.start_table()
        while bot.games_played_today < daily_cap:
            st = bot.play_minimax_move("R")
            if st == "GAME_OVER":
                bot.games_played_today += 1
                break
            if st == "WAITING":
                _sleep_jitter(*jitter)
    finally:
        bot.close()


if __name__ == "__main__":
    from utils.config import load_config

    cfg = load_config()
    run_bot_loop(depth=cfg.bot_depth, daily_cap=cfg.bot_daily_game_cap)
