"""
Orchestrateur : leaderboard → joueurs → parties → import DB (déduplication).
"""

from __future__ import annotations

import argparse
import logging
import random
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from bga_import import import_bga_moves
from bga_puppet import extract_moves_connect4_from_gamereview
from bga_scraper import leaderboard, login, player_games
from db.insert import attach_hashes_after_import, should_skip_import
from db.models import migrate
from utils.config import load_config

log = logging.getLogger(__name__)


def _build_driver(chrome_profile_dir: str | None) -> webdriver.Chrome:
    opts = Options()
    if chrome_profile_dir:
        opts.add_argument(f"--user-data-dir={chrome_profile_dir}")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    return webdriver.Chrome(options=opts)


def scrape_pipeline(top_n: int | None = None, player_ids: list[str] | None = None) -> None:
    cfg = load_config()
    migrate()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    driver = _build_driver(cfg.chrome_profile_dir)
    try:
        if not login.try_automated_login(driver, cfg.bga_username, cfg.bga_password):
            login.manual_login_prompt(driver)

        players = player_ids
        if not players:
            raw = leaderboard.fetch_top_player_ids(driver, top_n=top_n or cfg.top_n_players)
            players = [p["player_id"] for p in raw]

        for pid in players:
            log.info("=== Joueur %s ===", pid)
            tables = player_games.collect_table_ids_from_player_page(driver, pid)
            tables = player_games.filter_nine_by_nine_tables(driver, tables)
            for tid in tables:
                time.sleep(random.uniform(cfg.request_delay_min, cfg.request_delay_max))
                moves, preview = extract_moves_connect4_from_gamereview(driver, tid)
                if not moves:
                    log.warning("Pas de coups table %s preview=%s", tid, preview[:3] if preview else None)
                    continue
                cols_seq = [int(m["col"]) for m in sorted(moves, key=lambda x: int(x.get("move_id", 0)))]
                from utils.hashing import canonical_signature_from_cols

                sig = canonical_signature_from_cols(cols_seq, cfg.cols)
                if should_skip_import(signature=sig, bga_table_id=str(tid), moves_for_hash=moves, cols=cfg.cols):
                    log.info("Doublon ignoré table %s", tid)
                    continue
                try:
                    idp = import_bga_moves(
                        moves,
                        rows=cfg.rows,
                        cols=cfg.cols,
                        confiance=3,
                        mode="SCRAPER",
                        type_partie="HUMAIN",
                    )
                    attach_hashes_after_import(
                        idp, moves, cfg.cols, bga_table_id=str(tid), data_source="SCRAPER"
                    )
                    log.info("Import OK id_partie=%s table=%s", idp, tid)
                except Exception as e:
                    log.error("Import échoué table %s: %s", tid, e)
    finally:
        driver.quit()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=None)
    ap.add_argument("--players", nargs="*", default=None, help="IDs joueurs BGA explicites")
    args = ap.parse_args()
    scrape_pipeline(top_n=args.top_n, player_ids=args.players)
