import os
import sys
import time
from flask import Flask, render_template, jsonify, request
import uuid


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

AI_PLAYER = "J"  # l'IA joue Jaune
DIFF_TO_DEPTH = {"easy": 2, "medium": 4, "hard": 6}

ai_engine = MinimaxAI(ROWS, COLS)

# =======================
# MULTI-GAME STORAGE & HELPERS
# =======================
# in-memory map of active online games (key=id_partie)
games = {}

# template for new state objects; some entries are filled lazily
_default_state_template = {
    "id_partie": None,
    "mode": "WEB",          # "WEB" (online) or "LOCAL" (UI only)
    "type_partie": "IA",    # "IA" or "HUMAIN"
    "status": "Aucune partie",

    "ai_enabled": True,
    "ai_depth": 4,

    "board": None,            # will be created per-instance
    "current_player": "R",
    "game_over": False,
    "starting_player": None,
    "ai_player": "J",

    "signature": "init",
    "last_situation_id": None,

    "winning_line": None,

    # used only for online games to track who has joined
    # store as a list so it serializes cleanly to JSON
    "client_ids": [],
}


def make_fresh_state():
    """Return a brand-new state dictionary (deep copy of template)."""
    s = {k: (v.copy() if isinstance(v, (list, dict)) else v) for k, v in _default_state_template.items()}
    # board must be a fresh 2‑D list
    s["board"] = [[0 for _ in range(COLS)] for _ in range(ROWS)]
    s["client_ids"] = []
    return s


def get_game_state(game_id):
    """Look up the state for the given game_id (int) or None.
    Returns None if no such game.
    """
    if game_id is None:
        return state
    return games.get(game_id)


def register_client(game, client_id):
    """Register a new client (browser session) for *game*.
    If more than two unique clients try to join an online game, raise
    ValueError("Partie pleine").  client_id may be None; in that case
    we do nothing (legacy requests).

    Also assign the client to a color slot (client_r/client_j) based on
    starting_player so we can later check turn ownership.
    """
    if not client_id:
        return
    clients = game.setdefault("client_ids", [])
    # already known?
    if client_id in clients:
        return
    if len(clients) >= 2:
        raise ValueError("Partie pleine")
    clients.append(client_id)

    # assign colour slot for human-vs-human web games
    if game.get("mode") == "WEB" and game.get("type_partie") == "HUMAIN":
        # determine preferred order: starting_player goes first
        first = game.get("starting_player", "R")
        second = "J" if first == "R" else "R"
        # if slot not yet filled, put this client there
        if game.get(f"client_{first.lower()}") is None:
            game[f"client_{first.lower()}"] = client_id
        elif game.get(f"client_{second.lower()}") is None and client_id != game.get(f"client_{first.lower()}"):
            game[f"client_{second.lower()}"] = client_id

# global fallback state (for old single‑game behaviour)
state = make_fresh_state()
state["mode"] = "WEB"

# old shared state is removed; use make_fresh_state()/games map instead

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
    """Create database tables if they don't already exist.

    This makes deploying to a fresh PostgreSQL instance (like Render's)
    completely hands‑off; the app will boot even when the schema is empty.
    """
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

# ensure database schema exists at startup
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

# ---- partie
def create_partie_db():
    sig = f"init_{int(time.time() * 1000)}"
    row = q_one(
        """
        INSERT INTO partie (mode, type_partie, status, joueur_depart, signature, rows, cols, nb_colonnes, confiance)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id_partie
        """,
        ("WEB", state["type_partie"], "EN_COURS", "R", sig, ROWS, COLS, COLS, CONFIANCE_WEB),
    )
    return int(row["id_partie"]), sig

def update_partie_signature_db(id_partie, signature):
    try:
        exec_sql("UPDATE partie SET signature=%s WHERE id_partie=%s", (signature, id_partie))
    except psycopg2.errors.UniqueViolation:
        # some signatures may collide due to global uniqueness constraint; ignore
        pass

# ---- situation
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

# ---- finish
def try_finish_partie_db(id_partie, winner, ligne=None):
    exec_sql("UPDATE partie SET status=%s WHERE id_partie=%s", ("TERMINEE", id_partie))
    exec_sql("UPDATE partie SET joueur_gagnant=%s WHERE id_partie=%s", (winner, id_partie))
    if ligne is not None:
        exec_sql("UPDATE partie SET ligne_gagnante=%s WHERE id_partie=%s", (ligne, id_partie))

# =======================
# Game logic
# =======================
def reset_state_new_game(s=None):
    """Reset the given state object (or global state if None) for a fresh game."""
    if s is None:
        s = state
    s["board"] = [[0 for _ in range(COLS)] for _ in range(ROWS)]
    s["current_player"] = "R"
    s["starting_player"] = None
    s["game_over"] = False
    s["signature"] = "init"
    s["last_situation_id"] = None
    s["id_partie"] = None
    s["winning_line"] = None
    s["status"] = "Aucune partie"

def find_winning_line(r, c, s=None):
    if s is None:
        s = state
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

def best_ai_col(board, ai_player, depth):
    valid = ai_engine.valid_cols(board)
    if not valid:
        return None

    best_score = -10**18
    best_col = valid[0]

    # ordered_valid_cols existe dans ton ai.py
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

def apply_move(col):
    """Joue un coup pour state['current_player'].
       Retourne (placed_row, win_line_or_None, joueur_qui_a_joue)."""
    if col is None or not isinstance(col, int) or not (0 <= col < COLS):
        raise ValueError("col invalide")

    # drop
    placed_row = None
    for r in range(ROWS - 1, -1, -1):
        if state["board"][r][col] == 0:
            state["board"][r][col] = state["current_player"]
            placed_row = r
            break
    if placed_row is None:
        raise ValueError("colonne pleine")

    # signature init_... -> vide au 1er coup
    if str(state["signature"]).startswith("init_"):
        state["signature"] = ""
    state["signature"] += str(col + 1)
    numero = len(state["signature"])

    plateau = board_to_text(state["board"])
    joueur = state["current_player"]

    sid = insert_situation_db(state["id_partie"], numero, plateau, joueur, state["last_situation_id"])
    link_situations_db(state["last_situation_id"], sid)
    state["last_situation_id"] = sid

    update_partie_signature_db(state["id_partie"], state["signature"])

    line = find_winning_line(placed_row, col)
    return placed_row, line, joueur

def finalize_win(winner, line, s=None):
    if s is None:
        s = state
    s["game_over"] = True
    s["status"] = "TERMINEE"
    s["winning_line"] = [[r, c] for (r, c) in line]
    ligne_txt = str(s["winning_line"])  # format [[8,2],[7,2],...]
    try_finish_partie_db(s["id_partie"], winner, ligne=ligne_txt)

# =======================
# Routes
# =======================
@app.get("/")
def home():
    return render_template("index.html")



def _export_state(game):
    # return copy without internal keys
    if game is None:
        return None
    g = dict(game)
    clients = g.pop("client_ids", [])
    # expose number of joined clients so UI can show waiting indicator
    g["player_count"] = len(clients)
    return g


@app.get("/api/state")
def api_state():
    # return the state for a particular game if requested
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

    mode = str(data.get("mode") or "IA").upper()  # "IA", "LOCAL", "ONLINE"
    diff = str(data.get("difficulty") or "medium").lower()  # easy/medium/hard
    starting_player = str(data.get("starting_player") or "R").upper()

    # for online human-vs-human the colour is chosen randomly and the client may pass nothing
    if mode == "ONLINE":
        # ignore caller value; pick red or yellow at random
        import random
        starting_player = random.choice(["R", "J"])

    # LOCAL games never touch the database or shared memory
    if mode == "LOCAL":
        g = make_fresh_state()
        g["mode"] = "LOCAL"
        g["type_partie"] = "HUMAIN"
        g["ai_enabled"] = False
        # starting player logic
        g["current_player"] = starting_player if starting_player in ("R","J") else "R"
        g["starting_player"] = g["current_player"]
        g["winning_line"] = None
        g["status"] = "EN_COURS"
        # we don't register client ids for pure local
        return jsonify(g)

    # ONLINE / IA mode – attempt to match or create server‑tracked game
    if mode == "ONLINE" and not data.get("game_id"):
        # look for an existing waiting human game with <2 clients
        for existing in games.values():
            if (
                existing.get("mode") == "WEB"
                and existing.get("type_partie") == "HUMAIN"
                and not existing.get("game_over")
                and len(existing.get("client_ids", [])) < 2
            ):
                try:
                    register_client(existing, client_id)
                except ValueError:
                    pass
                # matched existing room, return its exported state
                return jsonify(_export_state(existing))

    # otherwise create a new game
    g = make_fresh_state()
    g["mode"] = "WEB"
    g["type_partie"] = "IA" if mode == "IA" else "HUMAIN"
    g["ai_enabled"] = (mode == "IA")
    g["ai_depth"] = DIFF_TO_DEPTH.get(diff, 4)

    # honour starting player
    g["current_player"] = starting_player if starting_player in ("R","J") else "R"
    g["starting_player"] = g["current_player"]
    if g["ai_enabled"]:
        g["ai_player"] = "J" if g["current_player"] == "R" else "R"

    # prepare colour slots for two humans
    g["client_r"] = None
    g["client_j"] = None

    pid, sig = create_partie_db()
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

    # work on the chosen game or fall back to global
    global state
    state = game or state

    # block moves until opponent joined
    if state.get("mode") == "WEB" and state.get("type_partie") == "HUMAIN":
        if len(state.get("client_ids", [])) < 2:
            return jsonify({"error": "En attente d'un adversaire."}), 400

    # enforce that only the client whose colour matches current_player may play
    if state.get("mode") == "WEB" and state.get("type_partie") == "HUMAIN" and client_id:
        # if the player has already been assigned a colour, they may not play the opposite one
        if state.get("client_r") == client_id and state["current_player"] == "J":
            return jsonify({"error": "Ce n'est pas ton tour."}), 400
        if state.get("client_j") == client_id and state["current_player"] == "R":
            return jsonify({"error": "Ce n'est pas ton tour."}), 400

        # otherwise, enforce expected slot if it exists (once two different clients are present)
        expected = None
        if state["current_player"] == "R":
            expected = state.get("client_r")
        else:
            expected = state.get("client_j")
        if expected and client_id != expected:
            return jsonify({"error": "Ce n'est pas ton tour."}), 400

    if state["id_partie"] is None and state.get("mode") != "LOCAL":
        return jsonify({"error": "Aucune partie. Clique sur 'Nouvelle partie'."}), 400
    if state["game_over"]:
        return jsonify(_export_state(state))

    # Si IA active et c’est son tour -> on bloque (elle jouera via /api/ai_move)
    if state.get("ai_enabled", False) and state["current_player"] == state.get("ai_player"):
        return jsonify({"error": "C'est au tour de l'IA."}), 400

    try:
        _, line, joueur = apply_move(col)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if line:
        finalize_win(joueur, line)
        return jsonify(_export_state(state))

    # switch player
    state["current_player"] = "J" if state["current_player"] == "R" else "R"
    return jsonify(_export_state(state))

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

    global state
    state = game or state

    if state["id_partie"] is None and state.get("mode") != "LOCAL":
        return jsonify({"error": "Aucune partie"}), 400
    if state["game_over"]:
        return jsonify(_export_state(state))
    if not state.get("ai_enabled", False):
        return jsonify({"error": "IA désactivée"}), 400
    if state["current_player"] != state.get("ai_player"):
        return jsonify({"error": "Ce n'est pas au tour de l'IA"}), 400

    ai_col = best_ai_col(state["board"], state.get("ai_player"), depth=int(state.get("ai_depth", 4)))
    if ai_col is None:
        return jsonify({"error": "Aucun coup IA possible"}), 400

    _, line, joueur = apply_move(ai_col)

    if line:
        finalize_win(joueur, line)
        return jsonify(_export_state(state))

    # switch back to humain
    state["current_player"] = "R" if state["current_player"] == "J" else "J"
    return jsonify(_export_state(state))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
