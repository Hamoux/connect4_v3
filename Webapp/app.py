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

AI_PLAYER = "J"  # l'IA joue Jaune
DIFF_TO_DEPTH = {"easy": 2, "medium": 4, "hard": 6}

ai_engine = MinimaxAI(ROWS, COLS)

# =======================
# STATE (server memory)
# =======================
state = {
    "id_partie": None,
    "mode": "WEB",
    "type_partie": "IA",          # "IA" ou "HUMAIN"
    "status": "Aucune partie",

    "ai_enabled": True,
    "ai_depth": 4,

    "board": [[0 for _ in range(COLS)] for _ in range(ROWS)],
    "current_player": "R",
    "game_over": False,
    "starting_player": None,
    "ai_player": "J",

    "signature": "init",
    "last_situation_id": None,

    "winning_line": None,         # [[r,c],[r,c],[r,c],[r,c]]
}

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
def reset_state_new_game():
    state["board"] = [[0 for _ in range(COLS)] for _ in range(ROWS)]
    state["current_player"] = "R"
    state["starting_player"] = None
    state["game_over"] = False
    state["signature"] = "init"
    state["last_situation_id"] = None
    state["id_partie"] = None
    state["winning_line"] = None
    state["status"] = "Aucune partie"

def find_winning_line(r, c):
    directions = [(0,1), (1,0), (1,1), (1,-1)]
    player = state["board"][r][c]
    for dr, dc in directions:
        coords = []
        for i in range(-3, 4):
            nr, nc = r + dr*i, c + dc*i
            if 0 <= nr < ROWS and 0 <= nc < COLS and state["board"][nr][nc] == player:
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

def finalize_win(winner, line):
    state["game_over"] = True
    state["status"] = "TERMINEE"
    state["winning_line"] = [[r, c] for (r, c) in line]
    ligne_txt = str(state["winning_line"])  # format [[8,2],[7,2],...]
    try_finish_partie_db(state["id_partie"], winner, ligne=ligne_txt)

# =======================
# Routes
# =======================
@app.get("/")
def home():
    return render_template("index.html")

@app.get("/api/state")
def api_state():
    return jsonify(state)

@app.post("/api/new")
def api_new():
    reset_state_new_game()

    data = request.json or {}
    mode = str(data.get("mode") or "IA").upper()             # "IA" / "HUMAIN"
    diff = str(data.get("difficulty") or "medium").lower()  # easy/medium/hard
    starting_player = str(data.get("starting_player") or "R").upper()

    state["type_partie"] = "IA" if mode == "IA" else "HUMAIN"
    state["ai_enabled"] = (mode == "IA")
    state["ai_depth"] = DIFF_TO_DEPTH.get(diff, 4)

    # honor requested starting player (UI choice). Do not change DB insertion.
    state["current_player"] = starting_player if starting_player in ("R","J") else "R"
    state["starting_player"] = state["current_player"]
    # choose AI side opposite the human when IA enabled
    if state["ai_enabled"]:
        state["ai_player"] = "J" if state["current_player"] == "R" else "R"

    pid, sig = create_partie_db()
    state["id_partie"] = pid
    state["signature"] = sig
    state["status"] = "EN_COURS"
    state["winning_line"] = None

    return jsonify(state)

@app.post("/api/play")
def api_play():
    data = request.json or {}
    col = data.get("col", None)

    if state["id_partie"] is None:
        return jsonify({"error": "Aucune partie. Clique sur 'Nouvelle partie'."}), 400
    if state["game_over"]:
        return jsonify(state)

    # Si IA active et c’est son tour -> on bloque (elle jouera via /api/ai_move)
    if state.get("ai_enabled", False) and state["current_player"] == state.get("ai_player"):
        return jsonify({"error": "C'est au tour de l'IA."}), 400

    try:
        _, line, joueur = apply_move(col)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if line:
        finalize_win(joueur, line)
        return jsonify(state)

    # switch player
    state["current_player"] = "J" if state["current_player"] == "R" else "R"
    return jsonify(state)

@app.post("/api/ai_move")
def api_ai_move():
    if state["id_partie"] is None:
        return jsonify({"error": "Aucune partie"}), 400
    if state["game_over"]:
        return jsonify(state)
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
        return jsonify(state)

    # switch back to humain
    state["current_player"] = "R" if state["current_player"] == "J" else "J"
    return jsonify(state)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))   # Render provides PORT
    app.run(host="0.0.0.0", port=port, debug=False)
