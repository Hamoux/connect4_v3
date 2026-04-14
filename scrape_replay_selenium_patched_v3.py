# scrape_replay_selenium.py
# ============================================================
# Mission 3.3 / 3.4 (BGA -> DB)
# ✅ Login BGA manuel (Chrome)
# ✅ Récupère une liste de joueurs automatiquement depuis le classement Connect4
# ✅ Pour chaque joueur -> récupère ses parties terminées (tables)
# ✅ Pour chaque table -> lit /gamereview?table=... (size + coups)
# ✅ Importe dans PostgreSQL via bga_import.import_bga_moves
#
# Notes importantes:
# - On n'utilise PLUS player_ids.json pour éviter de dépendre d'un fichier local.
# - On importe UNIQUEMENT les parties 9x9 (BGA peut avoir d'autres tailles/variants).
# - On minimise la conso "stock replay" en évitant d'ouvrir /archive/replay/ sauf si nécessaire.
# ============================================================

import json
import time
import re
from pathlib import Path
from urllib.parse import urlparse, urljoin

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ============================================================
# CONFIG
# ============================================================

# Jeu Connect4 sur BGA
GAME_ID = 1186              # connectfour
FINISHED = 1                # 1 = terminées

# DB / format (on force 9x9 pour les imports BGA)
ROWS = 9
COLS = 9
CONFIANCE = 3               # 3=BGA/humain (voir mapping confiance)

# Import policy
ONLY_9X9 = True             # we ONLY import 9x9 games
STRICT_SIZE_CHECK = True    # if size cannot be detected => skip (recommended)

# Scrap limites (tweak)
MAX_PLAYERS = 40            # nombre max de joueurs à traiter (top du classement)
MAX_TABLES_PER_PLAYER = 80  # nombre max de parties par joueur (augmente => consomme + de quota)
SCROLL_STEPS = 20           # scroll sur classement pour charger + de joueurs
SLEEP_SCROLL = 0.6
PAUSE_BETWEEN_PLAYERS = 0.6

# Anti quota / anti-ban
PAUSE_BETWEEN_TABLES = 1.0  # pause entre tables (augmente si BGA limite)

# Domaine (sera auto-fix après login)
BASE = "https://boardgamearena.com"

def get_board_size_from_table_page(driver, table_id: str):
    """
    ✅ Reliable board size source for Connect Four on BGA:
    It is displayed on the table page in:
      <span id="gameoption_100_displayed_value">9x9</span>

    We open /table?table=... and read that value.
    Returns (rows, cols) or None.
    """
    try:
        tid = str(int(str(table_id)))  # normalize (remove leading zeros)
    except Exception:
        return None

    url = f"{BASE}/table?table={tid}"
    driver.get(url)

    try:
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.ID, "gameoption_100_displayed_value"))
        )
        time.sleep(0.6)
        el = driver.find_element(By.ID, "gameoption_100_displayed_value")
        val = (el.text or "").strip()
        m = re.search(r"(\d{1,2})\s*[x×]\s*(\d{1,2})", val)
        if m:
            r = int(m.group(1)); c = int(m.group(2))
            return (r, c)
    except Exception:
        # fallback: try to find by label "Taille du plateau" then parse nearby
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text or ""
            # look at a few lines around the label
            lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
            for i, ln in enumerate(lines):
                ll = ln.lower()
                if ("taille" in ll and "plateau" in ll) or ("board" in ll and "size" in ll):
                    window = " ".join(lines[i:i+5])
                    m = re.search(r"(\d{1,2})\s*[x×]\s*(\d{1,2})", window)
                    if m:
                        r = int(m.group(1)); c = int(m.group(2))
                        return (r, c)
        except Exception:
            pass

    return None

PROJECT_DIR = Path(__file__).resolve().parent
OUT_DIR = PROJECT_DIR / "scraped_moves"
OUT_DIR.mkdir(exist_ok=True)


# ============================================================
# DRIVER
# ============================================================
def make_driver(headless: bool = False):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1400,900")
    else:
        opts.add_argument("--start-maximized")

    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-blink-features=AutomationControlled")

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(60)
    return driver


# ============================================================
# LOGIN (manuel) + FIX DOMAINE
# ============================================================
def login_bga_manual(driver):
    """
    Connexion manuelle. Ensuite on récupère le domaine réel (fr./en./www)
    pour que toutes les requêtes suivantes utilisent EXACTEMENT le même domaine (cookies ok).
    """
    global BASE

    print("🔐 Ouverture BGA login (manuel)...")
    driver.get(f"{BASE}/account")

    print("👉 Connecte-toi MANUELLEMENT dans Chrome.")
    input("✅ Quand tu es connecté (avatar visible), appuie sur ENTER...")

    print("✅ URL actuelle :", driver.current_url)

    u = urlparse(driver.current_url)
    BASE = f"{u.scheme}://{u.netloc}"
    print("✅ BASE fixé à :", BASE)


# ============================================================
# 0) Récupérer des joueurs depuis le classement Connect4 (HTML)
# ============================================================
def collect_player_ids_from_ranking(driver, max_players: int, scroll_steps: int):
    """
    Va sur gamepanel connect4 (classement), scroll pour charger plus de joueurs,
    puis extrait les player_id depuis les liens <a href="/player?id=...">.
    """
    url = f"{BASE}/gamepanel?game=connectfour"
    print("🏁 Ouverture page classement:", url)
    driver.get(url)

    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(2)

    for _ in range(scroll_steps):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(SLEEP_SCROLL)

    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)

    anchors = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/player?id="]')
    ids = []
    for a in anchors:
        href = a.get_attribute("href") or ""
        m = re.search(r"/player\?id=(\d+)", href)
        if m:
            ids.append(m.group(1))

    uniq = list(dict.fromkeys(ids))[:max_players]
    print(f"✅ player_ids trouvés depuis classement = {len(uniq)} (max={max_players})")
    if uniq:
        return uniq
    return []


# ============================================================
# 1) TABLE IDS depuis gamestats (profil parties)
# ============================================================
def _extract_table_ids_from_dom(driver):
    ids = []

    try:
        anchors = driver.find_elements(By.CSS_SELECTOR, '#gamelist_inner a[href*="/table?table="]')
        for a in anchors:
            href = a.get_attribute("href") or ""
            m = re.search(r"/table\?table=(\d+)", href)
            if m:
                ids.append(str(int(m.group(1))))
    except Exception:
        pass

    if not ids:
        html = driver.page_source or ""
        raw = re.findall(r"(?:/table\?table=|table\?table=|[?&]table=)(\d+)", html)
        for t in raw:
            try:
                n = int(t)
                if n > 0:
                    ids.append(str(n))
            except ValueError:
                pass

    seen = set()
    return [x for x in ids if not (x in seen or seen.add(x))]


def get_connect4_table_ids(driver, player_id: str, game_id: int, finished: int, limit: int):
    """
    Ouvre /gamestats?player=...&game_id=1186&finished=1
    et charge toute la liste via le bouton #see_more_tables.
    """
    url = f"{BASE}/gamestats?player={player_id}&game_id={game_id}&finished={finished}"
    driver.get(url)

    wait = WebDriverWait(driver, 25)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    wait.until(EC.presence_of_element_located((By.ID, "gamelist")))
    time.sleep(1.2)

    table_ids = _extract_table_ids_from_dom(driver)
    stable_rounds = 0

    for _ in range(50):
        if len(table_ids) >= limit:
            break

        try:
            btn = driver.find_element(By.ID, "see_more_tables")
            if not btn.is_displayed() or not btn.is_enabled():
                break
        except Exception:
            break

        before = len(table_ids)

        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.4)
            driver.execute_script("arguments[0].click();", btn)
        except Exception:
            try:
                btn.click()
            except Exception:
                break

        grown = False
        end = time.time() + 12
        while time.time() < end:
            time.sleep(0.4)
            new_ids = _extract_table_ids_from_dom(driver)
            if len(new_ids) > before:
                table_ids = new_ids
                grown = True
                break
            try:
                loading = driver.find_element(By.ID, "tablestats_loading")
                loading_style = (loading.get_attribute("style") or "").lower()
                if "display: none" in loading_style:
                    table_ids = new_ids
            except Exception:
                table_ids = new_ids

        if not grown:
            table_ids = _extract_table_ids_from_dom(driver)
            if len(table_ids) <= before:
                stable_rounds += 1
                if stable_rounds >= 2:
                    break
            else:
                stable_rounds = 0
        else:
            stable_rounds = 0

    table_ids = table_ids[:limit]
    print(f"   📌 {len(table_ids)} tables trouvées (player={player_id})")
    return table_ids


# ============================================================
# 2) Detect board size (anchored) - avoid false matches like 14:11
# ============================================================
SIZE_RE = re.compile(r"(\d{1,2})\s*[x×]\s*(\d{1,2})", re.IGNORECASE)

def detect_board_size_anchored(page_text: str):
    """
    Detect size ONLY on lines that mention board size (avoid false matches like times/scores).
    """
    if not page_text:
        return None

    lower = page_text.lower()

    # quick safe path
    if "9x9" in lower or "9×9" in lower:
        return (9, 9)

    for line in page_text.splitlines():
        l = line.strip()
        if not l:
            continue
        ll = l.lower()

        # anchors EN/FR
        anchored = (("board" in ll and "size" in ll) or ("taille" in ll and "plateau" in ll) or ("grid" in ll and "size" in ll))
        if not anchored:
            continue

        m = SIZE_RE.search(l)
        if m:
            r = int(m.group(1))
            c = int(m.group(2))
            if 4 <= r <= 20 and 4 <= c <= 20:
                return (r, c)

    return None


# ============================================================
# 3) Extraction coups via /gamereview?table=... (size + moves)
# ============================================================
def _parse_moves_from_current_review_page(driver):
    body_el = driver.find_element(By.TAG_NAME, "body")
    page_text = body_el.text or ""
    size = detect_board_size_anchored(page_text)

    name_to_pid = {}
    try:
        links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/player?id="]')
        for a in links:
            href = a.get_attribute("href") or ""
            m = re.search(r"/player\?id=(\d+)", href)
            if not m:
                continue
            pid = m.group(1)
            name = (a.text or "").strip()
            if name and name not in name_to_pid:
                name_to_pid[name] = pid
    except Exception:
        pass

    log_texts = []
    deadline = time.time() + 8
    while time.time() < deadline and not log_texts:
        try:
            logs = driver.find_elements(By.CSS_SELECTOR, "#gamelogs .gamelogreview")
            log_texts = [(x.text or "").strip() for x in logs if (x.text or "").strip()]
        except Exception:
            log_texts = []
        if log_texts:
            break
        time.sleep(0.5)

    if not log_texts:
        try:
            html = driver.page_source or ""
            log_texts = [re.sub(r"<[^>]+>", "", m).strip() for m in re.findall(r'<div[^>]+class="[^"]*gamelogreview[^"]*"[^>]*>(.*?)</div>', html, flags=re.I|re.S)]
            log_texts = [re.sub(r"\s+", " ", x) for x in log_texts if x]
        except Exception:
            log_texts = []

    if not log_texts:
        log_texts = [ln.strip() for ln in page_text.splitlines() if ln.strip()]

    place_fr = re.compile(r"^(.+?)\s+place un pion dans la colonne\s+(\d+)\s*$", re.IGNORECASE)
    place_en = re.compile(r"^(.+?)\s+(?:plays?|places?)\s+(?:a\s+)?(?:token|disc|piece)\s+in\s+(?:the\s+)?column\s+(\d+)\s*$", re.IGNORECASE)
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
        t = re.sub(r"\s+", " ", t).strip()
        m = now_color_fr.match(t) or now_color_en.match(t)
        if m:
            pname = m.group(1).strip()
            c = color_word_to_code(m.group(2))
            if pname and c in ("R", "J"):
                name_to_color[pname] = c
            continue

        m = place_fr.match(t) or place_en.match(t)
        if m:
            pname = m.group(1).strip()
            col = int(m.group(2))
            placements.append((pname, col))

    opening_seq = ["R", "J", "R"]
    moves = []
    last_color = None
    known_players = []

    for idx, (pname, col) in enumerate(placements, start=1):
        if pname not in known_players:
            known_players.append(pname)

        pid = name_to_pid.get(pname, "unknown")

        if idx <= 3:
            color = opening_seq[idx - 1]
        else:
            color = name_to_color.get(pname)
            if color not in ("R", "J") and len(known_players) == 2:
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
            "player_id": str(pid),
            "color": color,
        })

    return size, moves


def resolve_player_replay_url_from_gamereview(driver, player_id: str):
    try:
        a = driver.find_element(By.CSS_SELECTOR, f'a#choosePlayerLink_{player_id}, a[href*="/archive/replay/"][href*="player={player_id}"]')
        href = a.get_attribute("href")
        if href:
            return href
    except Exception:
        pass

    html = driver.page_source or ""
    m = re.search(r'(/archive/replay/[^"\']*?[?&]table=\d+[^"\']*?[?&]player=' + re.escape(str(player_id)) + r'[^"\']*)', html)
    if m:
        rel = m.group(1)
        return rel if rel.startswith("http") else urljoin(BASE, rel)
    return None


def extract_size_and_moves_from_gamereview(driver, table_id: str, player_id: str | None = None):
    """
    Ouvre /gamereview?table=... puis parse les logs visibles.
    Si aucun coup n'est trouvé, fallback vers /archive/replay/... pour le player courant.
    """
    url = f"{BASE}/gamereview?table={table_id}"
    driver.get(url)

    WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(1.2)

    size, moves = _parse_moves_from_current_review_page(driver)
    if moves:
        return size, moves

    replay_url = None
    if player_id is not None:
        replay_url = resolve_player_replay_url_from_gamereview(driver, str(player_id))
    if not replay_url:
        replay_url = resolve_real_replay_url_from_table(driver, table_id)

    if replay_url:
        try:
            driver.get(replay_url)
            WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(1.2)
            _size2, replay_moves = _parse_moves_from_current_review_page(driver)
            if replay_moves:
                return size, replay_moves
        except Exception:
            pass

    return size, moves


# ============================================================
# 4) (rare) Replay archive extraction via g_gamelogs
# ============================================================
EXTRACT_JS = r"""
return (function () {
  const byMove = new Map();
  for (const pkt of (window.g_gamelogs || [])) {
    const mid = Number(pkt && pkt.move_id);
    if (!Number.isFinite(mid)) continue;

    const data = (pkt.data || []);
    const disc = data.find(d => d && d.type === "playDisc");
    if (!disc || !disc.args) continue;

    const col = Number(disc.args.x);
    const pid = String(disc.args.player_id);
    if (!Number.isFinite(col)) continue;

    byMove.set(mid, { col, pid });
  }

  const moves = [...byMove.entries()]
    .sort((a,b)=>a[0]-b[0])
    .map(([move_id, v]) => ({ move_id, col: v.col, player_id: v.pid }));

  return {
    count: moves.length,
    signature: moves.map(m => m.col).join(""),
    moves
  };
})();
"""

def wait_gamelogs(driver, max_wait=30):
    end = time.time() + max_wait
    while time.time() < end:
        n = driver.execute_script("return (window.g_gamelogs && window.g_gamelogs.length) || 0;")
        if int(n) > 0:
            return True
        time.sleep(0.5)
    return False

def resolve_real_replay_url_from_table(driver, table_id: str):
    """
    Va sur /table?table=xxxxxx et tente de récupérer un lien /archive/replay/...
    """
    table_url = f"{BASE}/table?table={table_id}"
    driver.get(table_url)

    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    try:
        a = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href*="/archive/replay/"]')))
        href = a.get_attribute("href")
        if href:
            return href
    except Exception:
        pass

    html = driver.page_source or ""
    m = re.search(r'(/archive/replay/[^"\']+)', html)
    if m:
        rel = m.group(1)
        return rel if rel.startswith("http") else urljoin(BASE, rel)

    return None

def extract_moves_from_replay_url(driver, replay_url: str):
    """
    Ouvre /archive/replay/... et lit window.g_gamelogs
    """
    driver.get(replay_url)
    WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(2)

    ok = wait_gamelogs(driver, max_wait=30)
    if not ok:
        return []

    for _ in range(1, 6):
        payload = driver.execute_script(EXTRACT_JS)
        if payload and payload.get("count", 0) > 0:
            return payload["moves"]
        time.sleep(1.0)

    return []


# ============================================================
# 5) Import DB
# ============================================================
def import_into_db(moves):
    from bga_import import import_bga_moves
    id_partie = import_bga_moves(moves, rows=ROWS, cols=COLS, confiance=CONFIANCE)
    return id_partie


# ============================================================
# MAIN
# ============================================================
def main():
    driver = make_driver(headless=False)

    try:
        login_bga_manual(driver)

        # Always collect best players from ranking (no file dependency)
        player_ids = collect_player_ids_from_ranking(
            driver,
            max_players=MAX_PLAYERS,
            scroll_steps=SCROLL_STEPS
        )

        if not player_ids:
            print("❌ Aucun player_id trouvé depuis le classement.")
            return

        total_seen = 0
        total_imported = 0

        for idx, player_id in enumerate(player_ids, start=1):
            print("\n==============================")
            print(f"👤 Joueur {idx}/{len(player_ids)}: {player_id}")

            table_ids = get_connect4_table_ids(driver, player_id, GAME_ID, FINISHED, MAX_TABLES_PER_PLAYER)

            for tid in table_ids:
                total_seen += 1
                print(f"   🧩 Table: {tid}")

                # 1) Open table page: reliable size
                size = get_board_size_from_table_page(driver, tid)

                # 2) If not 9x9 => skip BEFORE opening gamereview (saves replay quota)
                if ONLY_9X9:
                    if size is None:
                        if STRICT_SIZE_CHECK:
                            print("      ⏭️  SKIP (size unknown)")
                            time.sleep(PAUSE_BETWEEN_TABLES)
                            continue
                    else:
                        r, c = size
                        if (r, c) != (9, 9):
                            print(f"      ⏭️  SKIP (size {r}x{c} not 9x9)")
                            time.sleep(PAUSE_BETWEEN_TABLES)
                            continue

                # 3) Now open gamereview to extract moves
                _size_from_gamereview, moves = extract_size_and_moves_from_gamereview(driver, tid, player_id=player_id)
                
                if not moves:
                    print("      ❌ Aucun coup trouvé (skip)")
                    time.sleep(PAUSE_BETWEEN_TABLES)
                    continue

                # Save moves for debug
                out_path = OUT_DIR / f"moves_player_{player_id}_table_{tid}.json"
                out_path.write_text(json.dumps(moves, indent=2), encoding="utf-8")

                # Import DB
                try:
                    id_partie = import_into_db(moves)
                    print("      💾 Import DB OK id_partie =", id_partie)
                    total_imported += 1
                except Exception as e:
                    print("      ❌ Import DB FAILED:", e)

                time.sleep(PAUSE_BETWEEN_TABLES)

            time.sleep(PAUSE_BETWEEN_PLAYERS)

        print("\n==============================")
        print(f"🎉 Terminé. Tables vues={total_seen}, parties importées={total_imported}")
        print(f"📁 JSON moves enregistrés dans: {OUT_DIR}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
