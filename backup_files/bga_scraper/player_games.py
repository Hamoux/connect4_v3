"""
Historique des parties d'un joueur : clic « Voir plus » jusqu'à épuisement,
collecte des IDs de tables Connect Four 9x9.
"""

from __future__ import annotations

import logging
import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger(__name__)


def _click_see_more_until_done(driver, max_clicks: int = 800) -> int:
    """Clique sur Voir plus / See more / Afficher plus jusqu'à disparition du bouton."""
    clicks = 0
    selectors = [
        "//a[contains(., 'Voir plus')]",
        "//a[contains(., 'voir plus')]",
        "//button[contains(., 'Voir plus')]",
        "//a[contains(., 'See more')]",
        "//button[contains(., 'See more')]",
        "//span[contains(., 'Voir plus')]/ancestor::a",
        "//*[contains(@class,'loadmore')]",
    ]
    while clicks < max_clicks:
        found = False
        for xp in selectors:
            try:
                els = driver.find_elements(By.XPATH, xp)
                for el in els:
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        clicks += 1
                        found = True
                        time.sleep(1.2)
                        break
                if found:
                    break
            except Exception:
                continue
        if not found:
            break
    log.info("See more: %s clics", clicks)
    return clicks


def page_mentions_nine_by_nine(driver) -> bool:
    t = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
    return (
        "9x9" in t
        or "9 × 9" in t
        or ("9" in t and "colonnes" in t and "9" in t.split("colonnes")[0][-20:])
        or "nine" in t and "column" in t
    )


def collect_table_ids_from_player_page(driver, player_id: str) -> list[str]:
    """Charge la page joueur + liste complète, extrait les table= dans les liens gamereview."""
    base = "https://boardgamearena.com"
    url = f"{base}/player?id={player_id}"
    driver.get(url)
    time.sleep(2.0)
    _click_see_more_until_done(driver)

    html = driver.page_source or ""
    ids = set()
    for m in re.finditer(r"gamereview\?table=(\d+)", html):
        ids.add(m.group(1))
    for m in re.finditer(r"table=(\d+).*connectfour", html, re.I):
        ids.add(m.group(1))
    log.info("Joueur %s: %s tables (brut)", player_id, len(ids))
    return sorted(ids)


def filter_nine_by_nine_tables(driver, table_ids: list[str]) -> list[str]:
    """Ouvre chaque gamereview rapidement et ne garde que les parties 9x9 (heuristique texte)."""
    ok: list[str] = []
    base = "https://boardgamearena.com"
    for tid in table_ids:
        try:
            driver.get(f"{base}/gamereview?table={tid}")
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(1.0)
            if not page_mentions_nine_by_nine(driver):
                # second critère : colonne max dans les logs
                body = (driver.find_element(By.TAG_NAME, "body").text or "")
                cols = [int(x) for x in re.findall(r"colonne\s+(\d+)", body, re.I)]
                if cols and max(cols) <= 7:
                    continue
            ok.append(tid)
        except Exception as e:
            log.warning("Table %s: %s", tid, e)
    return ok
