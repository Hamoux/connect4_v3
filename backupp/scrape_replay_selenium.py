# scrape_replay_selenium.py
# ============================================================
# Mission 3.3 / 3.4 (BGA -> DB)
# ✅ Login BGA manuel (Chrome)
# ✅ Récupère une liste de joueurs automatiquement depuis le classement Connect4 (ou player_ids.json)
# ✅ Pour chaque joueur -> récupère ses parties terminées (tables)
# ✅ Pour chaque table -> récupère les coups via:
#    1) /archive/replay/... (g_gamelogs) si dispo
#    2) sinon fallback /gamereview?table=... (Historique de la partie) ✅ (ton cas)
# ✅ Importe dans PostgreSQL via bga_import.import_bga_moves
#
# Fichiers attendus dans le même dossier:
# - bga_import.py (avec import_bga_moves)
# - game.py / db.py etc (via bga_import)
# Optionnel:
# - player_ids.json : liste d'ids joueurs
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

# DB / format
ROWS = 9
COLS = 9
CONFIANCE = 3               # adapte si tu veux (0=perdre exprès, 1=random, 2..., 3...)

# Scrap limites
MAX_PLAYERS = 80            # nombre max de joueurs à traiter
MAX_TABLES_PER_PLAYER = 10  # nombre max de parties par joueur
SCROLL_STEPS = 20           # scroll sur classement pour charger + de joueurs
SLEEP_SCROLL = 0.6
PAUSE_BETWEEN_PLAYERS = 0.7

# Domaine (sera auto-fix après login)
BASE = "https://boardgamearena.com"

PROJECT_DIR = Path(__file__).resolve().parent
OUT_DIR = PROJECT_DIR / "scraped_moves"
OUT_DIR.mkdir(exist_ok=True)

PLAYER_IDS_FILE = PROJECT_DIR / "player_ids.json"  # optionnel


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
    Tu te connectes à la main. Ensuite on récupère le domaine réel (fr., en., www)
    pour que toutes les requêtes suivantes utilisent EXACTEMENT le même domaine (cookies ok).
    """
    global BASE

    print("🔐 Ouverture BGA login (manuel)...")
    driver.get(f"{BASE}/account")

    print("👉 Connecte-toi MANUELLEMENT dans Chrome.")
    input("✅ Quand tu es connectée (avatar visible), appuie sur ENTER...")

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

    uniq = list(dict.fromkeys(ids))
    uniq = uniq[:max_players]

    print(f"✅ player_ids trouvés depuis classement = {len(uniq)} (max={max_players})")
    if uniq:
        print("   sample:", uniq[:10])
    return uniq


def load_player_ids():
    """
    Si player_ids.json existe, on l'utilise.
    Sinon on renvoie None (et on collectera depuis classement).
    """
    if PLAYER_IDS_FILE.exists():
        try:
            data = json.loads(PLAYER_IDS_FILE.read_text(encoding="utf-8"))
            ids = [str(x) for x in data if str(x).isdigit()]
            ids = list(dict.fromkeys(ids))
            print(f"✅ player_ids chargés depuis {PLAYER_IDS_FILE.name} = {len(ids)}")
            return ids
        except Exception as e:
            print("⚠️ player_ids.json illisible, on ignore. Erreur:", e)
    return None


# ============================================================
# 1) TABLE IDS depuis gamestats (profil parties)
# ============================================================
def get_connect4_table_ids(driver, player_id: str, game_id: int, finished: int, limit: int):
    """
    Ouvre /gamestats?player=...&game_id=1186&finished=1
    et extrait des #TABLEID.
    """
    url = f"{BASE}/gamestats?player={player_id}&game_id={game_id}&finished={finished}"
    driver.get(url)
    time.sleep(2)

    for _ in range(10):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.7)

    html = driver.page_source
    table_ids = sorted(set(re.findall(r"#(\d{6,})", html)))
    table_ids = [t for t in table_ids if t.isdigit()]
    table_ids = table_ids[:limit]

    print(f"   📌 {len(table_ids)} tables trouvées (player={player_id})")
    return table_ids


# ============================================================
# 2) Trouver /archive/replay/... depuis /table?table=...
# ============================================================
def resolve_real_replay_url_from_table(driver, table_id: str):
    """
    Va sur /table?table=xxxxxx et tente de récupérer un lien /archive/replay/...
    """
    table_url = f"{BASE}/table?table={table_id}"
    driver.get(table_url)

    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    # Essayer de cliquer/attendre un lien replay
    try:
        a = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href*="/archive/replay/"]')))
        href = a.get_attribute("href")
        if href:
            return href
    except Exception:
        pass

    # Fallback regex dans HTML
    html = driver.page_source
    m = re.search(r'(/archive/replay/[^"\']+)', html)
    if m:
        rel = m.group(1)
        return rel if rel.startswith("http") else urljoin(BASE, rel)

    return None


# ============================================================
# 3) Extraction coups via g_gamelogs (replay archive)
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
# 4) Fallback extraction via /gamereview?table=...
# ============================================================
def extract_moves_from_gamereview(driver, table_id: str):
    """
    Ouvre /gamereview?table=... et extrait les coups depuis "Historique de la partie".
    On lit le texte: "... place un pion dans la colonne X"
    + on essaie de récupérer player_id depuis les liens /player?id=...
    """
    url = f"{BASE}/gamereview?table={table_id}"
    driver.get(url)

    WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(1.5)

    # Mapping pseudo -> player_id (liens profils visibles)
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

    # Lire tout le texte de la page (plus robuste que de dépendre d'une classe CSS)
    page_text = driver.find_element(By.TAG_NAME, "body").text

    # Matches FR:
    # "Scarlet Fu place un pion dans la colonne 1"
    # (parfois pseudo avec tirets/underscores)
    pattern = re.compile(r"^(.+?)\s+place un pion dans la colonne\s+(\d+)\s*$", re.MULTILINE)
    rows = pattern.findall(page_text)

    moves = []
    move_id = 1
    for player_name, col_str in rows:
        player_name = player_name.strip()
        col = int(col_str)

        pid = name_to_pid.get(player_name, "unknown")
        moves.append({"move_id": move_id, "col": col, "player_id": str(pid)})
        move_id += 1

    return moves


# ============================================================
# 5) Import DB
# ============================================================
def import_into_db(moves):
    # import local pour éviter erreurs si tu testes juste sans DB
    from bga_import import import_bga_moves
    pid = import_bga_moves(moves, rows=ROWS, cols=COLS, confiance=CONFIANCE)
    return pid


# ============================================================
# MAIN
# ============================================================
def main():
    driver = make_driver(headless=False)

    try:
        login_bga_manual(driver)

        # 1) player ids depuis fichier si dispo
        player_ids = load_player_ids()

        # 2) sinon depuis classement
        if not player_ids:
            player_ids = collect_player_ids_from_ranking(
                driver,
                max_players=MAX_PLAYERS,
                scroll_steps=SCROLL_STEPS
            )
            if player_ids:
                PLAYER_IDS_FILE.write_text(json.dumps(player_ids, indent=2), encoding="utf-8")
                print(f"💾 Sauvegardé dans {PLAYER_IDS_FILE.name}")

        if not player_ids:
            print("❌ Aucun player_id trouvé.")
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

                # 1) Essayer archive replay (g_gamelogs)
                replay_url = resolve_real_replay_url_from_table(driver, tid)
                moves = []

                if replay_url:
                    print("      🎬 Archive replay:", replay_url)
                    moves = extract_moves_from_replay_url(driver, replay_url)
                    if moves:
                        print(f"      ✅ {len(moves)} coups (archive)")
                else:
                    print("      ⚠️ Pas de /archive/replay trouvé -> fallback gamereview")

                # 2) Fallback gamereview (Historique)
                if not moves:
                    moves = extract_moves_from_gamereview(driver, tid)
                    if moves:
                        print(f"      ✅ {len(moves)} coups (gamereview)")

                if not moves:
                    print("      ❌ Aucun coup trouvé (skip)")
                    continue

                # Sauvegarde moves (debug / preuve)
                out_path = OUT_DIR / f"moves_player_{player_id}_table_{tid}.json"
                out_path.write_text(json.dumps(moves, indent=2), encoding="utf-8")

                # Import DB
                try:
                    id_partie = import_into_db(moves)
                    print("      💾 Import DB OK id_partie =", id_partie)
                    total_imported += 1
                except Exception as e:
                    print("      ❌ Import DB FAILED:", e)

            time.sleep(PAUSE_BETWEEN_PLAYERS)

        print("\n==============================")
        print(f"🎉 Terminé. Tables vues={total_seen}, parties importées={total_imported}")
        print(f"📁 JSON moves enregistrés dans: {OUT_DIR}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
