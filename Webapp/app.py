import os
import sys
import time
from flask import Flask, render_template, jsonify, request

import psycopg2
from psycopg2.extras import RealDictCursor

# ✅ IMPORTANT: ai.py est dans le dossier parent (connect4_v3/)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from ai import MinimaxAI  # noqa

app = Flask(__name__)

# =======================
# CONFIG
# =======================
ROWS = 9
COLS = 9
CONFIANCE_WEB = 2

DIFF_TO_DEPTH = {"easy": 2, "medium": 4, "hard": 6}

ai_engine = MinimaxAI(ROWS, COLS)

# =======================
# MULTI-GAME STORAGE
# =======================
games = {}

_default_state_template = {
    "id_partie": None,
    "mode": "WEB",
    "type_partie": "IA",
    "status": "Aucune partie",

    "ai_enabled": True,
    "ai_depth": 4,

    "board": None,
    "current_player": "R",
    "game_over": False,
    "starting_player": None,
    "ai_player": "J",

    "signature": "init",
    "last_situation_id": None,
    "winning_line": None,

    "client_ids": [],
}

def make_fresh_state():
    s = {k: (v.copy() if isinstance(v, (list, dict)) else v) for k, v in _default_state_template.items()}
    s["board"] = [[0 for _ in range(COLS)] for _ in range(ROWS)]
    s["client_ids"] = []
    return s

def get_game_state(game_id):
    if game_id is None:
        return state
    return games.get(game_id)

def register_client(game, client_id):
    if not client_id:
        return
    clients = game.setdefault("client_ids", [])
    if client_id in clients:
        return
    if len(clients) >= 2:
        raise ValueError("Partie pleine")
    clients.append(client_id)

    if game.get("mode") == "WEB" and game.get("type_partie") == "HUMAIN":
        first = game.get("starting_player", "R")
        second = "J" if first == "R" else "R"
        if game.get(f"client_{first.lower()}") is None:
            game[f"client_{first.lower()}"] = client_id
        elif game.get(f"client_{second.lower()}") is None and client_id != game.get(f"client_{first.lower()}"):
            game[f"client_{second.lower()}"] = client_id

state = make_fresh_state()
state["mode"] = "WEB"

# =======================
# DB helpers
# =======================
def get_conn():
    host = os.getenv("PGHOST", "localhost")
    port = int(os.getenv("PGPORT", "5432"))
    dbname = os.getenv("PGDATABASE", "Connect4DB")
    user = os.getenv("PGUSER", "postgres")
    password = os.getenv("PGPASSWORD", "Celina123")
    return psycopg2.connect(
        host=host, port=port, dbname=dbname, user=user, password=password,
        cursor_factory=RealDictCursor
    )

def ensure_tables():
    ddl_partie = """
    CREATE TABLE IF NOT EXISTS partie (
        id_partie SERIAL PRIMARY KEY,
        mode TEXT,
        type_partie TEXT,
        status TEXT,
        joueur_depart TEXT,
        signature TEXT UNIQUE,
        rows INTEGER,
        cols INTEGER,
        nb_colonnes INTEGER,
        confiance INTEGER,
        joueur_gagnant TEXT,
        ligne_gagnante TEXT
    );
    """
    ddl_situation = """
    CREATE TABLE IF NOT EXISTS situation (
        id_situation SERIAL PRIMARY KEY,
        id_partie INTEGER REFERENCES partie(id_partie),
        numero_coup INTEGER,
        plateau TEXT,
        joueur TEXT,
        precedent INTEGER,
        suivant INTEGER
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl_partie)
            cur.execute(ddl_situation)
        conn.commit()

ensure_tables()

def q_one(sql, params=()):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

def exec_sql(sql, params=()):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()

def board_to_text(board):
    return "\n".join("".join(str(x) if x == 0 else x for x in row) for row in board)

def create_partie_db(type_partie):
    sig = f"init_{int(time.time() * 1000)}"
    row = q_one(
        """
        INSERT INTO partie (mode, type_partie, status, joueur_depart, signature, rows, cols, nb_colonnes, confiance)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id_partie
        """,
        ("WEB", type_partie, "EN_COURS", "R", sig, ROWS, COLS, COLS, CONFIANCE_WEB),
    )
    return int(row["id_partie"]), sig

def update_partie_signature_db(id_partie, signature):
    try:
        exec_sql("UPDATE partie SET signature=%s WHERE id_partie=%s", (signature, id_partie))
    except psycopg2.errors.UniqueViolation:
        pass

def insert_situation_db(id_partie, numero_coup, plateau, joueur, precedent_id):
    row = q_one(
        """
        INSERT INTO situation (id_partie, numero_coup, plateau, joueur, precedent, suivant)
        VALUES (%s,%s,%s,%s,%s,%s)
        RETURNING id_situation
        """,
        (id_partie, numero_coup, plateau, joueur, precedent_id, None),
    )
    return int(row["id_situation"])

def link_situations_db(prev_id, next_id):
    if prev_id is None:
        return
    exec_sql("UPDATE situation SET suivant=%s WHERE id_situation=%s", (next_id, prev_id))
    exec_sql("UPDATE situation SET precedent=%s WHERE id_situation=%s", (prev_id, next_id))

def try_finish_partie_db(id_partie, winner, ligne=None):
    exec_sql("UPDATE partie SET status=%s WHERE id_partie=%s", ("TERMINEE", id_partie))
    exec_sql("UPDATE partie SET joueur_gagnant=%s WHERE id_partie=%s", (winner, id_partie))
    if ligne is not None:
        exec_sql("UPDATE partie SET ligne_gagnante=%s WHERE id_partie=%s", (ligne, id_partie))

# =======================
# Win check helper (pour hint / obvious move)
# =======================
def check_win(board, r, c, player):
    dirs = [(0,1),(1,0),(1,1),(1,-1)]
    for dr, dc in dirs:
        count = 1
        # forward
        rr, cc = r+dr, c+dc
        while 0 <= rr < ROWS and 0 <= cc < COLS and board[rr][cc] == player:
            count += 1
            rr += dr; cc += dc
        # backward
        rr, cc = r-dr, c-dc
        while 0 <= rr < ROWS and 0 <= cc < COLS and board[rr][cc] == player:
            count += 1
            rr -= dr; cc -= dc
        if count >= 4:
            return True
    return False

def immediate_win_or_block(board, player):
    opponent = "J" if player == "R" else "R"
    valid = ai_engine.valid_cols(board)

    # 1) win now
    for col in valid:
        r = ai_engine.next_open_row(board, col)
        if r is None: 
            continue
        board[r][col] = player
        ok = check_win(board, r, col, player)
        board[r][col] = 0
        if ok:
            return col

    # 2) block opponent
    for col in valid:
        r = ai_engine.next_open_row(board, col)
        if r is None:
            continue
        board[r][col] = opponent
        ok = check_win(board, r, col, opponent)
        board[r][col] = 0
        if ok:
            return col

    return None

# =======================
# AI move selection
# =======================
def best_ai_col(board, ai_player, depth):
    valid = ai_engine.valid_cols(board)
    if not valid:
        return None

    # ✅ stratégie “coup évident d’abord”
    obvious = immediate_win_or_block(board, ai_player)
    if obvious is not None:
        return obvious

    best_score = -10**18
    best_col = valid[0]

    for col in ai_engine.ordered_valid_cols(board, ai_player, maximizing=True):
        r = ai_engine.next_open_row(board, col)
        if r is None:
            continue
        board[r][col] = ai_player
        score = ai_engine.minimax(board, depth-1, -10**18, 10**18, False, ai_player)
        board[r][col] = 0

        if score > best_score:
            best_score = score
            best_col = col

    return best_col

# =======================
# Game logic
# =======================
def find_winning_line(r, c, s):
    directions = [(0,1), (1,0), (1,1), (1,-1)]
    player = s["board"][r][c]
    for dr, dc in directions:
        coords = []
        for i in range(-3, 4):
            nr, nc = r + dr*i, c + dc*i
            if 0 <= nr < ROWS and 0 <= nc < COLS and s["board"][nr][nc] == player:
                coords.append((nr, nc))
                if len(coords) == 4:
                    return coords
            else:
                coords = []
    return None

def apply_move(col, s):
    if col is None or not isinstance(col, int) or not (0 <= col < COLS):
        raise ValueError("col invalide")

    placed_row = None
    for r in range(ROWS - 1, -1, -1):
        if s["board"][r][col] == 0:
            s["board"][r][col] = s["current_player"]
            placed_row = r
            break
    if placed_row is None:
        raise ValueError("colonne pleine")

    if str(s["signature"]).startswith("init_"):
        s["signature"] = ""
    s["signature"] += str(col + 1)
    numero = len(s["signature"])

    plateau = board_to_text(s["board"])
    joueur = s["current_player"]

    sid = insert_situation_db(s["id_partie"], numero, plateau, joueur, s["last_situation_id"])
    link_situations_db(s["last_situation_id"], sid)
    s["last_situation_id"] = sid

    update_partie_signature_db(s["id_partie"], s["signature"])

    line = find_winning_line(placed_row, col, s)
    return placed_row, line, joueur

def finalize_win(winner, line, s):
    s["game_over"] = True
    s["status"] = "TERMINEE"
    s["winning_line"] = [[r, c] for (r, c) in line]
    try_finish_partie_db(s["id_partie"], winner, ligne=str(s["winning_line"]))

# =======================
# Routes
# =======================
@app.get("/")
def home():
    return render_template("index.html")

def _export_state(game):
    if game is None:
        return None
    g = dict(game)
    clients = g.pop("client_ids", [])
    g["player_count"] = len(clients)
    return g

@app.get("/api/state")
def api_state():
    game_id = request.args.get("game_id", type=int)
    client_id = request.args.get("client_id")

    game = get_game_state(game_id)
    if game_id is not None and game is None:
        return jsonify({"error": "Partie introuvable"}), 404

    try:
        register_client(game, client_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(_export_state(game))

@app.post("/api/new")
def api_new():
    data = request.json or {}
    client_id = data.get("client_id")

    mode = str(data.get("mode") or "IA").upper()      # IA / LOCAL / ONLINE
    diff = str(data.get("difficulty") or "medium").lower()
    starting_player = str(data.get("starting_player") or "R").upper()

    # ONLINE: starting player random (comme avant)
    if mode == "ONLINE":
        import random
        starting_player = random.choice(["R", "J"])

    # LOCAL: UI only
    if mode == "LOCAL":
        g = make_fresh_state()
        g["mode"] = "LOCAL"
        g["type_partie"] = "HUMAIN"
        g["ai_enabled"] = False
        g["ai_depth"] = 0
        g["current_player"] = starting_player if starting_player in ("R", "J") else "R"
        g["starting_player"] = g["current_player"]
        g["status"] = "EN_COURS"
        g["winning_line"] = None
        return jsonify(g)

    # ✅ ONLINE matchmaking: rejoindre une partie humaine en attente
    if mode == "ONLINE" and not data.get("game_id"):
        for existing in games.values():
            if (
                existing.get("mode") == "WEB"
                and existing.get("type_partie") == "HUMAIN"
                and not existing.get("game_over")
                and len(existing.get("client_ids", [])) < 2
            ):
                try:
                    register_client(existing, client_id)
                    return jsonify(_export_state(existing))
                except ValueError:
                    pass  # partie pleine -> continue

    # Sinon: créer une nouvelle partie WEB
    g = make_fresh_state()
    g["mode"] = "WEB"
    g["type_partie"] = "IA" if mode == "IA" else "HUMAIN"
    g["ai_enabled"] = (mode == "IA")
    g["ai_depth"] = DIFF_TO_DEPTH.get(diff, 4)

    g["current_player"] = starting_player if starting_player in ("R", "J") else "R"
    g["starting_player"] = g["current_player"]

    if g["ai_enabled"]:
        g["ai_player"] = "J" if g["current_player"] == "R" else "R"
    else:
        g["ai_player"] = None

    # slots couleur ONLINE HUMAIN
    g["client_r"] = None
    g["client_j"] = None

    pid, sig = create_partie_db(g["type_partie"])
    g["id_partie"] = pid
    g["signature"] = sig
    g["status"] = "EN_COURS"
    g["winning_line"] = None

    games[pid] = g
    try:
        register_client(g, client_id)
    except ValueError:
        pass

    return jsonify(_export_state(g))

@app.post("/api/play")
def api_play():
    data = request.json or {}
    col = data.get("col", None)
    client_id = data.get("client_id")
    game_id = data.get("game_id")

    game = get_game_state(game_id)
    if game_id is not None and game is None:
        return jsonify({"error": "Partie introuvable"}), 404

    try:
        register_client(game, client_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    s = game or state

    if s["id_partie"] is None and s.get("mode") != "LOCAL":
        return jsonify({"error": "Aucune partie. Clique sur 'Nouvelle partie'."}), 400
    if s["game_over"]:
        return jsonify(_export_state(s))

    # ✅ ONLINE HUMAIN: attendre 2 joueurs
    if s.get("mode") == "WEB" and s.get("type_partie") == "HUMAIN":
        if len(s.get("client_ids", [])) < 2:
            return jsonify({"error": "En attente d'un adversaire."}), 400

    # ✅ ONLINE HUMAIN: bloquer si pas ton tour
    if s.get("mode") == "WEB" and s.get("type_partie") == "HUMAIN" and client_id:
        if s.get("client_r") == client_id and s["current_player"] == "J":
            return jsonify({"error": "Ce n'est pas ton tour."}), 400
        if s.get("client_j") == client_id and s["current_player"] == "R":
            return jsonify({"error": "Ce n'est pas ton tour."}), 400

        expected = s.get("client_r") if s["current_player"] == "R" else s.get("client_j")
        if expected and client_id != expected:
            return jsonify({"error": "Ce n'est pas ton tour."}), 400

    # IA: bloquer si c'est au tour IA
    if s.get("ai_enabled", False) and s["current_player"] == s.get("ai_player"):
        return jsonify({"error": "C'est au tour de l'IA."}), 400

    try:
        _, line, joueur = apply_move(col, s)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if line:
        finalize_win(joueur, line, s)
        return jsonify(_export_state(s))

    s["current_player"] = "J" if s["current_player"] == "R" else "R"
    return jsonify(_export_state(s))

@app.post("/api/ai_move")
def api_ai_move():
    data = request.json or {}
    client_id = data.get("client_id")
    game_id = data.get("game_id")

    game = get_game_state(game_id)
    if game_id is not None and game is None:
        return jsonify({"error": "Partie introuvable"}), 404
    try:
        register_client(game, client_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    s = game or state

    if s["id_partie"] is None and s.get("mode") != "LOCAL":
        return jsonify({"error": "Aucune partie"}), 400
    if s["game_over"]:
        return jsonify(_export_state(s))
    if not s.get("ai_enabled", False):
        return jsonify({"error": "IA désactivée"}), 400
    if s["current_player"] != s.get("ai_player"):
        return jsonify({"error": "Ce n'est pas au tour de l'IA"}), 400

    depth = int(s.get("ai_depth", 4))
    ai_player = s.get("ai_player")
    ai_col = best_ai_col(s["board"], ai_player, depth=depth)
    if ai_col is None:
        return jsonify({"error": "Aucun coup IA possible"}), 400

    _, line, joueur = apply_move(ai_col, s)

    if line:
        finalize_win(joueur, line, s)
        return jsonify(_export_state(s))

    s["current_player"] = "R" if s["current_player"] == "J" else "J"
    return jsonify(_export_state(s))

# ✅ NOUVEAU : suggestion IA sans jouer
@app.post("/api/hint")
def api_hint():
    data = request.json or {}
    game_id = data.get("game_id")

    game = get_game_state(game_id)
    if game_id is not None and game is None:
        return jsonify({"error": "Partie introuvable"}), 404

    s = game or state
    if s.get("game_over"):
        return jsonify({"error": "Partie terminée"}), 400

    depth = int(s.get("ai_depth", 4))
    player = s.get("current_player", "R")

    board_copy = [row[:] for row in s["board"]]
    col = best_ai_col(board_copy, player, depth=depth)
    if col is None:
        return jsonify({"error": "Aucun coup possible"}), 400

    return jsonify({"suggested_col": col})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)