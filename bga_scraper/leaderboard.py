"""
Extraction des meilleurs joueurs Connect Four sur le classement BGA.
"""

from __future__ import annotations

import logging
import re
import time

from selenium.webdriver.common.by import By

log = logging.getLogger(__name__)


def fetch_top_player_ids(driver, game_slug: str = "connectfour", top_n: int = 30) -> list[dict[str, str]]:
    """
    Retourne [{"player_id": "...", "name": "..."}, ...]
    """
    base = "https://boardgamearena.com"
    url = f"{base}/leaderboard?game={game_slug}"
    driver.get(url)
    time.sleep(2.5)

    out: list[dict[str, str]] = []
    seen: set[str] = set()

    # Liens joueur
    links = driver.find_elements(By.CSS_SELECTOR, "a[href*='player']")
    for a in links:
        try:
            href = a.get_attribute("href") or ""
            m = re.search(r"player[?&]id=(\d+)", href, re.I) or re.search(r"/player/(\d+)", href)
            if not m:
                continue
            pid = m.group(1)
            if pid in seen:
                continue
            name = (a.text or "").strip() or pid
            seen.add(pid)
            out.append({"player_id": pid, "name": name})
            if len(out) >= top_n:
                break
        except Exception:
            continue

    # Fallback : parse page source
    if len(out) < min(5, top_n):
        html = driver.page_source or ""
        for m in re.finditer(r"player\?id=(\d+)", html):
            pid = m.group(1)
            if pid not in seen:
                seen.add(pid)
                out.append({"player_id": pid, "name": pid})
            if len(out) >= top_n:
                break

    log.info("Leaderboard: %s joueurs collectés (demandé %s)", len(out), top_n)
    return out[:top_n]
