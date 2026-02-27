import time
import re
from urllib.parse import urlparse
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from bga_import import import_bga_moves


def _base_from_driver(driver) -> str:
    """
    Utilise le même domaine que la session courante (fr./en./www) pour garder cookies OK.
    """
    try:
        u = urlparse(driver.current_url or "")
        if u.scheme and u.netloc:
            return f"{u.scheme}://{u.netloc}"
    except Exception:
        pass
    return "https://boardgamearena.com"


def handle_cookies_popup(driver):
    """
    Clique sur 'Tout autoriser' ou 'Tout refuser' si popup cookies.
    """
    xpaths = [
        "//button[contains(., 'Tout autoriser')]",
        "//button[contains(., 'Tout refuser')]",
        "//a[contains(., 'Tout autoriser')]",
        "//a[contains(., 'Tout refuser')]",
    ]
    for xp in xpaths:
        try:
            btn = driver.find_element(By.XPATH, xp)
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(1.0)
                return
        except Exception:
            continue


def is_connect4_page(driver) -> bool:
    """
    Vérifie si la page correspond à Puissance Quatre (Connect4).
    """
    title = (driver.title or "").lower()
    body = ""
    try:
        body = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
    except Exception:
        pass

    keywords = ["puissance quatre", "connect four", "connectfour"]
    return any(k in title for k in keywords) or any(k in body for k in keywords)


def wait_archive_finished(driver, timeout=60) -> bool:
    """
    Attendre que la page ne soit plus en "Recherche de l'archive... Merci de patienter..."
    """
    end = time.time() + timeout
    while time.time() < end:
        try:
            txt = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
        except Exception:
            txt = ""

        if "recherche de l'archive" in txt or "merci de patienter" in txt:
            time.sleep(1.0)
            continue

        # Indices de logs
        if ("colonne" in txt) or ("column" in txt) or ("place un pion" in txt) or ("places" in txt):
            return True

        time.sleep(1.0)

    return False


def extract_moves_connect4_from_gamereview(driver, table_id: str):
    """
    Ouvre /gamereview?table=... et extrait les coups connect4.
    Retour: (moves, preview_debug_15_lignes)
    """
    base = _base_from_driver(driver)
    url = f"{base}/gamereview?table={table_id}"
    driver.get(url)

    WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    handle_cookies_popup(driver)
    time.sleep(1.0)

    # Filtre jeu
    if not is_connect4_page(driver):
        body_txt = (driver.find_element(By.TAG_NAME, "body").text or "")
        preview = [ln.strip() for ln in body_txt.splitlines() if ln.strip()][:15]
        return [], preview

    # Attendre archive chargée
    wait_archive_finished(driver, timeout=60)

    # Lire logs
    log_texts = []
    try:
        logs = driver.find_elements(By.CSS_SELECTOR, "#gamelogs .gamelogreview")
        log_texts = [(x.text or "").strip() for x in logs if (x.text or "").strip()]
    except Exception:
        log_texts = []

    if not log_texts:
        # fallback body
        page_text = driver.find_element(By.TAG_NAME, "body").text or ""
        log_texts = [ln.strip() for ln in page_text.splitlines() if ln.strip()]

    # Patterns
    place_fr = re.compile(r"^(.+?)\s+place un pion dans la colonne\s+(\d+)\s*$", re.IGNORECASE)
    # fallback permissif: capte "colonne X" ou "column X"
    place_any = re.compile(r"^(.+?)\s+.*?(?:colonne|column)\s+(\d+)\s*$", re.IGNORECASE)

    now_color_fr = re.compile(r"^(.+?)\s+joue maintenant en\s+(.+?)\s*!?\s*$", re.IGNORECASE)
    now_color_en = re.compile(r"^(.+?)\s+now plays\s+(yellow|red)\s*!?\s*$", re.IGNORECASE)

    def color_word_to_code(w: str):
        wl = (w or "").strip().lower()
        if "jaune" in wl or wl == "yellow":
            return "J"
        if "rouge" in wl or wl == "red":
            return "R"
        return None

    name_to_color = {}
    placements = []

    for t in log_texts:
        m = now_color_fr.match(t) or now_color_en.match(t)
        if m:
            pname = m.group(1).strip()
            c = color_word_to_code(m.group(2))
            if pname and c in ("R", "J"):
                name_to_color[pname] = c
            continue

        m = place_fr.match(t) or place_any.match(t)
        if m:
            pname = m.group(1).strip()
            col = int(m.group(2))
            if 1 <= col <= 30:
                placements.append((pname, col))

    # Reconstruire couleurs (règle BGA: R,J,R au départ)
    opening_seq = ["R", "J", "R"]
    moves = []
    last_color = None
    known_players = []

    for idx, (pname, col) in enumerate(placements, start=1):
        if pname not in known_players:
            known_players.append(pname)

        if idx <= 3:
            color = opening_seq[idx - 1]
        else:
            color = name_to_color.get(pname)
            if color not in ("R", "J"):
                if len(known_players) == 2:
                    other = known_players[0] if pname == known_players[1] else known_players[1]
                    other_c = name_to_color.get(other)
                    if other_c in ("R", "J"):
                        color = "J" if other_c == "R" else "R"
                if color not in ("R", "J"):
                    color = "J" if last_color == "R" else "R"

        last_color = color
        moves.append({
            "move_id": idx,
            "col": col,
            "player_name": pname,
            "player_id": "unknown",
            "color": color,
        })

    return moves, log_texts[:15]


def import_table_id_connect4(driver, table_id, rows=9, cols=9, confiance=3):
    """
    Scrape + import DB.
    Retour:
      (id_partie, None) si OK
      (None, preview_debug) si KO
    """
    table_id = str(table_id)
    moves, preview = extract_moves_connect4_from_gamereview(driver, table_id)

    if not moves:
        return None, preview

    id_partie = import_bga_moves(moves, rows=rows, cols=cols, confiance=confiance)
    return id_partie, None