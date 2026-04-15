import os
import sys
import time
import uuid
import ast
from flask import Flask, render_template, jsonify, request

import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from ai import MinimaxAI  # noqa

app = Flask(__name__)

ROWS = 9
COLS = 9
CONFIANCE_WEB = 2

DEFAULT_DEPTH = 4
MIN_DEPTH = 2
MAX_DEPTH = 9

ai_engine = MinimaxAI(ROWS, COLS)
games = {}


def normalize_depth(value, default=DEFAULT_DEPTH):
    try:
        d = int(value)
    except (TypeError, ValueError):
        return default
    return max(MIN_DEPTH, min(MAX_DEPTH, d))


def make_empty_state():
    return {
        "id_partie": None,
        "mode": "LOCAL",
        "type_partie": "HUMAIN",
        "status": "Aucune partie",
        "ai_enabled": False,
        "ai_depth": DEFAULT_DEPTH,
        "ai_player": None,
        "ai_players": {"R": False, "J": False},
        "board": [[0 for _ in range(COLS)] for _ in range(ROWS)],
        "current_player": "R",
        "game_over": False,
        "starting_player": "R",
        "signature": "init",
        "last_situation_id": None,
        "winning_line": None,
        "player_count": 0,
        "client_r": None,
        "client_j": None,
        "player_r_name": "Joueur Rouge",
        "player_j_name": "Joueur Jaune",
    }


def make_fresh_state():
    return {
        "id_partie": None,
        "mode": "WEB",
        "type_partie": "IA",
        "status": "Aucune partie",
        "ai_enabled": True,
        "ai_depth": DEFAULT_DEPTH,
        "ai_player": "J",
        "ai_players": {"R": False, "J": True},
        "board": [[0 for _ in range(COLS)] for _ in range(ROWS)],
        "current_player": "R",
        "game_over": False,
        "starting_player": "R",
        "signature": "init",
        "last_situation_id": None,
        "winning_line": None,
        "client_ids": [],
        "client_r": None,
        "client_j": None,
        "player_r_name": "Joueur Rouge",
        "player_j_name": "Joueur Jaune",
    }


def normalize_game_id(game_id):
    if game_id is None:
        return None
    try:
        return int(game_id)
    except (TypeError, ValueError):
        return None


def get_game_state(game_id):
    """
    Retourne l'état de la partie, en vérifiant que le cache mémoire
    est cohérent avec la DB (protection contre les workers multi-process).
    """
    game_id = normalize_game_id(game_id)
    if game_id is None:
        return None

    game = games.get(game_id)
    if game is not None:
        # Vérifier que la signature en mémoire correspond à la DB
        # (évite les collisions entre workers gunicorn)
        try:
            row = q_one("SELECT signature FROM partie WHERE id_partie=%s", (game_id,))
            if row and row["signature"] != game.get("signature"):
                # Cache obsolète : recharger depuis la DB
                game = load_game_from_db(game_id)
                if game is not None:
                    games[game_id] = game
                return game
        except Exception:
            pass  # En cas d'erreur DB, on utilise le cache mémoire
        return game

    game = load_game_from_db(game_id)
    if game is not None:
        games[game_id] = game

    return game


def register_client(game, client_id):
    if game is None or not client_id:
        return

    if game.get("mode") != "WEB" or game.get("type_partie") != "HUMAIN":
        return

    clients = game.setdefault("client_ids", [])

    if client_id in clients:
        return

    if len(clients) >= 2:
        raise ValueError("Partie pleine")

    clients.append(client_id)

    first = game.get("starting_player", "R")
    second = "J" if first == "R" else "R"

    if game.get(f"client_{first.lower()}") is None:
        game[f"client_{first.lower()}"] = client_id
    elif game.get(f"client_{second.lower()}") is None:
        game[f"client_{second.lower()}"] = client_id


def export_state(game):
    if game is None:
        return make_empty_state()

    g = dict(game)
    clients = list(g.pop("client_ids", []))
    g["player_count"] = len(clients)
    return g


def get_conn():
    host = os.getenv("PGHOST", "localhost")
    port = int(os.getenv("PGPORT", "5432"))
    dbname = os.getenv("PGDATABASE", "Connect4DB")
    user = os.getenv("PGUSER", "postgres")
    password = os.getenv("PGPASSWORD", "Celina123")

    return psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
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


def text_to_board(plateau_text):
    if not plateau_text:
        return [[0 for _ in range(COLS)] for _ in range(ROWS)]

    lines = plateau_text.strip().splitlines()
    board = []

    for line in lines:
        row = []
        for ch in line.strip():
            if ch == "0":
                row.append(0)
            elif ch in ("R", "J"):
                row.append(ch)
            else:
                row.append(0)
        board.append(row)

    while len(board) < ROWS:
        board.append([0 for _ in range(COLS)])

    board = board[:ROWS]
    for i in range(len(board)):
        if len(board[i]) < COLS:
            board[i] += [0] * (COLS - len(board[i]))
        board[i] = board[i][:COLS]

    return board


def load_game_from_db(game_id):
    partie = q_one(
        "SELECT * FROM partie WHERE id_partie=%s",
        (game_id,)
    )
    if not partie:
        return None

    g = make_fresh_state()
    g["id_partie"] = int(partie["id_partie"])
    g["mode"] = "WEB"
    g["type_partie"] = partie["type_partie"] or "HUMAIN"
    g["status"] = partie["status"] or "EN_COURS"
    g["starting_player"] = (partie["joueur_depart"] or "R").upper()
    g["signature"] = partie["signature"] or "init"
    g["winning_line"] = None
    g["game_over"] = (g["status"] == "TERMINEE")

    if g["type_partie"] == "IA":
        g["ai_enabled"] = True
        g["ai_depth"] = DEFAULT_DEPTH
        g["ai_player"] = "J"
        g["ai_players"] = {"R": False, "J": True}
    else:
        g["ai_enabled"] = False
        g["ai_depth"] = DEFAULT_DEPTH
        g["ai_player"] = None
        g["ai_players"] = {"R": False, "J": False}

    last_sit = q_one(
        """
        SELECT *
        FROM situation
        WHERE id_partie=%s
        ORDER BY numero_coup DESC, id_situation DESC
        LIMIT 1
        """,
        (game_id,)
    )

    move_count = 0
    if last_sit:
        g["board"] = text_to_board(last_sit["plateau"])
        g["last_situation_id"] = int(last_sit["id_situation"])
        move_count = int(last_sit["numero_coup"] or 0)
    else:
        g["board"] = [[0 for _ in range(COLS)] for _ in range(ROWS)]
        g["last_situation_id"] = None

    if partie.get("ligne_gagnante"):
        try:
            g["winning_line"] = ast.literal_eval(partie["ligne_gagnante"])
        except Exception:
            g["winning_line"] = None

    if g["game_over"]:
        winner = partie.get("joueur_gagnant")
        g["current_player"] = winner if winner in ("R", "J") else g["starting_player"]
    else:
        if move_count % 2 == 0:
            g["current_player"] = g["starting_player"]
        else:
            g["current_player"] = "J" if g["starting_player"] == "R" else "R"

    g["client_ids"] = []
    g["client_r"] = None
    g["client_j"] = None

    return g


def create_partie_db(type_partie, joueur_depart):
    sig = f"init_{uuid.uuid4().hex[:12]}_{int(time.time() * 1000)}"

    row = q_one(
        """
        INSERT INTO partie (mode, type_partie, status, joueur_depart, signature, rows, cols, nb_colonnes, confiance)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id_partie
        """,
        ("WEB", type_partie, "EN_COURS", joueur_depart, sig, ROWS, COLS, COLS, CONFIANCE_WEB),
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


def immediate_win_or_block(board, player):
    opponent = "J" if player == "R" else "R"
    valid = ai_engine.valid_cols(board)

    for col in valid:
        r = ai_engine.next_open_row(board, col)
        if r is None:
            continue
        board[r][col] = player
        ok = ai_engine.winner_on_board(board) == player
        board[r][col] = 0
        if ok:
            return col

    for col in valid:
        r = ai_engine.next_open_row(board, col)
        if r is None:
            continue
        board[r][col] = opponent
        ok = ai_engine.winner_on_board(board) == opponent
        board[r][col] = 0
        if ok:
            return col

    return None


def best_ai_col(board, ai_player, depth, moves_history=None):
    valid = ai_engine.valid_cols(board)
    if not valid:
        return None

    # ── Bibliothèque d'ouverture ──────────────────────────────────────────────
    if moves_history is not None:
        opening_col = ai_engine.get_opening_move(tuple(moves_history))
        if opening_col is not None and opening_col in valid:
            return opening_col

    # ── Victoire ou blocage immédiat ─────────────────────────────────────────
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
        score = ai_engine.minimax(
            board=board,
            depth=depth - 1,
            alpha=-10**18,
            beta=10**18,
            maximizing=False,
            ai_player=ai_player
        )
        board[r][col] = 0

        if score > best_score:
            best_score = score
            best_col = col

    return best_col


def find_winning_line(r, c, s):
    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
    player = s["board"][r][c]

    for dr, dc in directions:
        coords = []
        for i in range(-3, 4):
            nr = r + dr * i
            nc = c + dc * i

            if 0 <= nr < ROWS and 0 <= nc < COLS and s["board"][nr][nc] == player:
                coords.append((nr, nc))
                if len(coords) == 4:
                    return coords
            else:
                coords = []

    return None


def apply_move(col, s):
    if col is None or not isinstance(col, int) or not (0 <= col < COLS):
        raise ValueError("Colonne invalide")

    placed_row = None

    for r in range(ROWS - 1, -1, -1):
        if s["board"][r][col] == 0:
            s["board"][r][col] = s["current_player"]
            placed_row = r
            break

    if placed_row is None:
        raise ValueError("Colonne pleine")

    if str(s["signature"]).startswith("init_"):
        s["signature"] = ""

    s["signature"] += str(col + 1)
    numero = len(s["signature"])

    if s["id_partie"] is not None:
        plateau = board_to_text(s["board"])
        joueur = s["current_player"]

        sid = insert_situation_db(
            s["id_partie"],
            numero,
            plateau,
            joueur,
            s["last_situation_id"]
        )
        link_situations_db(s["last_situation_id"], sid)
        s["last_situation_id"] = sid

        update_partie_signature_db(s["id_partie"], s["signature"])

    line = find_winning_line(placed_row, col, s)
    return placed_row, line, s["current_player"]


def finalize_win(winner, line, s):
    s["game_over"] = True
    s["status"] = "TERMINEE"
    s["winning_line"] = [[r, c] for (r, c) in line]

    if s["id_partie"] is not None:
        try_finish_partie_db(
            s["id_partie"],
            winner,
            ligne=str(s["winning_line"])
        )


def current_color_is_ai(s):
    ai_players = s.get("ai_players") or {"R": False, "J": False}
    return bool(ai_players.get(s["current_player"], False))


def signature_to_moves(sig):
    """
    Convertit une signature en liste de colonnes 0-based.
    Robuste : ignore les caractères non numériques et vérifie les bornes.
    """
    s = str(sig or "")
    if s.startswith("init_"):
        s = ""
    out = []
    for ch in s:
        if ch.isdigit():
            d = int(ch)
            if 1 <= d <= COLS:
                out.append(d - 1)
            # Si d == 0 ou > COLS, on ignore silencieusement
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Routes existantes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/state")
def api_state():
    game_id = request.args.get("game_id", type=int)
    client_id = request.args.get("client_id")

    if game_id is None:
        return jsonify(make_empty_state())

    game = get_game_state(game_id)
    if game is None:
        return jsonify({"error": "Partie introuvable"}), 404

    try:
        register_client(game, client_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(export_state(game))


@app.post("/api/new")
def api_new():
    data = request.json or {}
    client_id = data.get("client_id")

    mode = str(data.get("mode") or "IA").upper()
    depth = normalize_depth(data.get("difficulty"), DEFAULT_DEPTH)
    starting_player = str(data.get("starting_player") or "R").upper()
    human_player = str(data.get("human_player") or "R").upper()

    if starting_player not in ("R", "J"):
        starting_player = "R"

    if human_player not in ("R", "J"):
        human_player = "R"

    player_r_name = str(data.get("player_r_name") or "Joueur Rouge").strip()
    player_j_name = str(data.get("player_j_name") or "Joueur Jaune").strip()

    if mode == "ONLINE":
        import random
        starting_player = random.choice(["R", "J"])

    if mode == "LOCAL":
        g = make_empty_state()
        g["mode"] = "LOCAL"
        g["type_partie"] = "HUMAIN"
        g["status"] = "EN_COURS"
        g["current_player"] = starting_player
        g["starting_player"] = starting_player
        g["player_r_name"] = player_r_name or "Joueur Rouge"
        g["player_j_name"] = player_j_name or "Joueur Jaune"
        g["ai_players"] = {"R": False, "J": False}
        g["ai_enabled"] = False
        g["ai_player"] = None
        g["ai_depth"] = depth
        return jsonify(g)

    g = make_fresh_state()
    g["mode"] = "WEB"
    g["type_partie"] = "IA" if mode == "IA" else "HUMAIN"
    g["ai_depth"] = depth
    g["starting_player"] = starting_player
    g["current_player"] = starting_player
    g["player_r_name"] = player_r_name or "Joueur Rouge"
    g["player_j_name"] = player_j_name or "Joueur Jaune"

    if g["type_partie"] == "IA":
        ai_player = "J" if human_player == "R" else "R"
        g["ai_enabled"] = True
        g["ai_player"] = ai_player
        g["ai_players"] = {"R": ai_player == "R", "J": ai_player == "J"}

        if ai_player == "R":
            g["player_r_name"] = "IA"
        else:
            g["player_j_name"] = "IA"
    else:
        g["ai_enabled"] = False
        g["ai_player"] = None
        g["ai_players"] = {"R": False, "J": False}

    pid, sig = create_partie_db(g["type_partie"], g["starting_player"])
    g["id_partie"] = pid
    g["signature"] = sig
    g["status"] = "EN_COURS"

    games[pid] = g
    ai_engine.clear_cache()  # Vider le cache uniquement à la nouvelle partie

    try:
        register_client(g, client_id)
    except ValueError:
        pass

    return jsonify(export_state(g))


@app.post("/api/set_ai_color")
def api_set_ai_color():
    data = request.json or {}
    game_id = normalize_game_id(data.get("game_id"))
    client_id = data.get("client_id")
    color = str(data.get("color") or "").upper()
    enabled = bool(data.get("enabled"))
    player_r_name = str(data.get("player_r_name") or "Joueur Rouge").strip()
    player_j_name = str(data.get("player_j_name") or "Joueur Jaune").strip()

    if color not in ("R", "J"):
        return jsonify({"error": "Couleur invalide"}), 400

    game = get_game_state(game_id)
    if game is None:
        return jsonify({"error": "Partie introuvable"}), 404

    try:
        register_client(game, client_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    s = game

    if s["game_over"]:
        return jsonify({"error": "Partie terminée"}), 400

    if s["mode"] == "WEB" and s["type_partie"] == "HUMAIN":
        my_color = None
        if s.get("client_r") == client_id:
            my_color = "R"
        elif s.get("client_j") == client_id:
            my_color = "J"

        if my_color is None:
            return jsonify({"error": "Impossible de déterminer ta couleur."}), 403

        if my_color != color:
            return jsonify({"error": "Tu ne peux modifier que ta propre couleur."}), 403

    ai_players = dict(s.get("ai_players") or {"R": False, "J": False})
    ai_players[color] = enabled
    s["ai_players"] = ai_players
    s["ai_enabled"] = bool(ai_players["R"] or ai_players["J"])

    if s["type_partie"] != "IA":
        enabled_colors = [c for c in ("R", "J") if ai_players[c]]
        s["ai_player"] = enabled_colors[0] if len(enabled_colors) == 1 else None

    if enabled:
        if color == "R":
            s["player_r_name"] = "IA"
        else:
            s["player_j_name"] = "IA"
    else:
        if color == "R":
            s["player_r_name"] = player_r_name or "Joueur Rouge"
        else:
            s["player_j_name"] = player_j_name or "Joueur Jaune"

    return jsonify(export_state(s))


@app.post("/api/play")
def api_play():
    data = request.json or {}
    col = data.get("col")
    client_id = data.get("client_id")
    game_id = normalize_game_id(data.get("game_id"))

    game = get_game_state(game_id)
    if game is None:
        return jsonify({"error": "Partie introuvable"}), 404

    try:
        register_client(game, client_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    s = game

    if s["id_partie"] is None:
        return jsonify({"error": "Aucune partie. Clique sur Nouvelle partie."}), 400

    if s["game_over"]:
        return jsonify(export_state(s))

    if s.get("mode") == "WEB" and s.get("type_partie") == "HUMAIN":
        if len(s.get("client_ids", [])) < 2:
            return jsonify({"error": "En attente d'un adversaire."}), 400

    if s.get("mode") == "WEB" and s.get("type_partie") == "HUMAIN" and client_id:
        if s.get("client_r") == client_id and s["current_player"] == "J":
            return jsonify({"error": "Ce n'est pas ton tour."}), 400

        if s.get("client_j") == client_id and s["current_player"] == "R":
            return jsonify({"error": "Ce n'est pas ton tour."}), 400

        expected = s.get("client_r") if s["current_player"] == "R" else s.get("client_j")
        if expected and client_id != expected:
            return jsonify({"error": "Ce n'est pas ton tour."}), 400

    if current_color_is_ai(s):
        return jsonify({"error": "C'est au tour de l'IA."}), 400

    try:
        _, line, joueur = apply_move(col, s)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if line:
        finalize_win(joueur, line, s)
        return jsonify(export_state(s))

    s["current_player"] = "J" if s["current_player"] == "R" else "R"
    return jsonify(export_state(s))


@app.post("/api/ai_move")
def api_ai_move():
    data = request.json or {}
    game_id = normalize_game_id(data.get("game_id"))

    game = get_game_state(game_id)
    if game is None:
        return jsonify({"error": "Partie introuvable"}), 404

    s = game

    if s["game_over"]:
        return jsonify(export_state(s))

    if not current_color_is_ai(s):
        return jsonify({"error": "Ce n'est pas au tour de l'IA"}), 400

    depth = int(s.get("ai_depth", DEFAULT_DEPTH))
    ai_player = s.get("current_player")

    # Extraire l'historique des coups pour la bibliothèque d'ouverture
    moves_history = signature_to_moves(s.get("signature", ""))

    try:
        # On ne vide plus le cache entre les coups pour réutiliser la table de transposition
        ai_col = best_ai_col(
            [row[:] for row in s["board"]],
            ai_player,
            depth,
            moves_history=moves_history
        )
    except Exception as e:
        return jsonify({"error": f"Erreur Minimax: {str(e)}"}), 500

    if ai_col is None:
        return jsonify({"error": "Aucun coup IA possible"}), 400

    _, line, joueur = apply_move(ai_col, s)

    if line:
        finalize_win(joueur, line, s)
        return jsonify(export_state(s))

    s["current_player"] = "R" if s["current_player"] == "J" else "J"
    return jsonify(export_state(s))


@app.post("/api/local_ai_move")
def api_local_ai_move():
    data = request.json or {}
    board = data.get("board")
    player = str(data.get("player") or "").upper()
    depth = normalize_depth(data.get("depth"), DEFAULT_DEPTH)
    moves_history = data.get("moves_history")  # optionnel, liste 0-based

    if player not in ("R", "J"):
        return jsonify({"error": "Joueur IA invalide"}), 400

    if not isinstance(board, list) or len(board) != ROWS:
        return jsonify({"error": "Plateau invalide"}), 400

    try:
        board_copy = [row[:] for row in board]
        col = best_ai_col(
            board_copy,
            player,
            depth,
            moves_history=moves_history if isinstance(moves_history, list) else None
        )
    except Exception as e:
        return jsonify({"error": f"Erreur pendant le calcul Minimax local: {str(e)}"}), 500

    if col is None:
        return jsonify({"error": "Aucun coup possible"}), 400

    return jsonify({"col": col})


@app.post("/api/hint")
def api_hint():
    data = request.json or {}
    game_id = normalize_game_id(data.get("game_id"))

    game = get_game_state(game_id)
    if game is None:
        return jsonify({"error": "Partie introuvable"}), 404

    s = game

    if s.get("game_over"):
        return jsonify({"error": "Partie terminée"}), 400

    depth = int(s.get("ai_depth", DEFAULT_DEPTH))
    player = s.get("current_player", "R")
    board_copy = [row[:] for row in s["board"]]
    moves_history = signature_to_moves(s.get("signature", ""))

    try:
        col = best_ai_col(board_copy, player, depth, moves_history=moves_history)
    except Exception as e:
        return jsonify({"error": f"Erreur Minimax hint: {str(e)}"}), 500

    if col is None:
        return jsonify({"error": "Aucun coup possible"}), 400

    return jsonify({"suggested_col": col})


# ─────────────────────────────────────────────────────────────────────────────
# NOUVELLES ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/predict")
def api_predict():
    """
    Prédit qui va gagner et dans combien de coups.
    Body: { game_id?, board?, current_player?, depth? }
    """
    data = request.json or {}
    game_id = normalize_game_id(data.get("game_id"))
    depth = normalize_depth(data.get("depth"), DEFAULT_DEPTH)

    # Mode partie existante ou plateau libre
    if game_id is not None:
        game = get_game_state(game_id)
        if game is None:
            return jsonify({"error": "Partie introuvable"}), 404
        board = [row[:] for row in game["board"]]
        current_player = game["current_player"]
        if game["game_over"]:
            return jsonify({
                "winner": game["current_player"],
                "moves": 0,
                "certain": True,
                "message": f"{game['current_player']} a déjà gagné."
            })
    else:
        board_raw = data.get("board")
        current_player = str(data.get("current_player") or "R").upper()
        if not isinstance(board_raw, list) or len(board_raw) != ROWS:
            return jsonify({"error": "Plateau invalide"}), 400
        # Normaliser : 0, null, "0", False → 0 ; "R"/"J" → gardés
        board = []
        for row in board_raw:
            normalized = []
            for cell in (row or []):
                if cell in ("R", "J"):
                    normalized.append(cell)
                else:
                    normalized.append(0)
            # Compléter si la ligne est trop courte
            while len(normalized) < COLS:
                normalized.append(0)
            board.append(normalized[:COLS])
        # Compléter si le plateau est trop court
        while len(board) < ROWS:
            board.append([0] * COLS)

    try:
        result = ai_engine.predict_winner(board, current_player, depth=depth)
    except Exception as e:
        return jsonify({"error": f"Erreur prédiction: {str(e)}"}), 500

    winner = result.get("winner")
    moves = result.get("moves")
    certain = result.get("certain", False)

    if winner == "draw":
        message = "Match nul inévitable."
    if winner == "draw":
        message = "Match nul inévitable."
    elif winner and certain and moves is not None:
        color_name = "Rouge" if winner == "R" else "Jaune"
        message = f"{color_name} gagne en {moves} coup{'s' if moves > 1 else ''} (victoire forcée)."
    elif winner and certain:
        color_name = "Rouge" if winner == "R" else "Jaune"
        message = f"{color_name} a une victoire forcée."
    elif winner and moves is not None:
        color_name = "Rouge" if winner == "R" else "Jaune"
        message = f"{color_name} gagne en ~{moves} coups (estimation)."
    elif winner:
        color_name = "Rouge" if winner == "R" else "Jaune"
        message = f"{color_name} a l'avantage."
    else:
        message = "Position équilibrée."

    return jsonify({
        "winner": winner,
        "moves": moves,
        "certain": certain,
        "message": message
    })


@app.post("/api/paint")
def api_paint():
    """
    Mode peinture : applique un plateau peint librement et déduit à qui c'est de jouer.
    Body: {
        game_id?,           # si on part d'une partie existante
        board,              # plateau complet 9x9 (liste de listes)
        starting_player?    # 'R' ou 'J', pour déduire à qui c'est de jouer
    }
    Retourne l'état mis à jour avec current_player déduit et validation.
    """
    data = request.json or {}
    board_raw = data.get("board")
    starting_player = str(data.get("starting_player") or "R").upper()
    game_id = normalize_game_id(data.get("game_id"))

    if not isinstance(board_raw, list) or len(board_raw) != ROWS:
        return jsonify({"error": "Plateau invalide (doit être 9x9)"}), 400

    # Normaliser le plateau
    board = []
    for row in board_raw:
        if not isinstance(row, list) or len(row) != COLS:
            return jsonify({"error": "Plateau invalide (ligne incorrecte)"}), 400
        normalized_row = []
        for cell in row:
            if cell in ("R", "J"):
                normalized_row.append(cell)
            else:
                normalized_row.append(0)
        board.append(normalized_row)

    # Compter les pions
    nb_r = sum(1 for r in range(ROWS) for c in range(COLS) if board[r][c] == "R")
    nb_j = sum(1 for r in range(ROWS) for c in range(COLS) if board[r][c] == "J")

    # Déduire à qui c'est de jouer
    valid = True
    inferred_player = None

    if starting_player == "R":
        if nb_r == nb_j:
            inferred_player = "R"
        elif nb_r == nb_j + 1:
            inferred_player = "J"
        else:
            valid = False
    else:  # starting_player == "J"
        if nb_j == nb_r:
            inferred_player = "J"
        elif nb_j == nb_r + 1:
            inferred_player = "R"
        else:
            valid = False

    # Vérifier s'il y a déjà un gagnant
    winner_now = ai_engine.winner_on_board(board)

    if not valid:
        return jsonify({
            "error": f"Position invalide : {nb_r} pions rouges et {nb_j} pions jaunes. "
                     f"La différence doit être ≤ 1.",
            "nb_red": nb_r,
            "nb_yellow": nb_j
        }), 400

    # Mettre à jour la partie si elle existe, sinon créer un état temporaire
    if game_id is not None:
        game = get_game_state(game_id)
        if game and not game["game_over"]:
            game["board"] = board
            game["current_player"] = inferred_player
            if winner_now:
                game["game_over"] = True
                game["status"] = "TERMINEE"
                game["current_player"] = winner_now
            return jsonify({
                **export_state(game),
                "inferred_player": inferred_player,
                "nb_red": nb_r,
                "nb_yellow": nb_j,
                "winner_detected": winner_now
            })

    # Retour état peint sans partie DB
    return jsonify({
        "board": board,
        "current_player": inferred_player,
        "starting_player": starting_player,
        "inferred_player": inferred_player,
        "nb_red": nb_r,
        "nb_yellow": nb_j,
        "winner_detected": winner_now,
        "game_over": bool(winner_now),
        "valid": True
    })


@app.post("/api/simulate")
def api_simulate():
    """
    Simule la fin de partie en faisant jouer 2 IA coup par coup.
    Retourne le vrai gagnant et le nombre de coups.
    Body: { board, current_player, depth?, max_moves? }
    """
    data = request.json or {}
    board_raw = data.get("board")
    current_player = str(data.get("current_player") or "R").upper()
    depth = normalize_depth(data.get("depth"), DEFAULT_DEPTH)
    max_moves = int(data.get("max_moves") or 81)  # max 9x9

    if current_player not in ("R", "J"):
        return jsonify({"error": "Joueur invalide"}), 400

    if not isinstance(board_raw, list) or len(board_raw) != ROWS:
        return jsonify({"error": "Plateau invalide"}), 400

    # Normaliser le board
    board = []
    for row in board_raw:
        normalized = []
        for cell in (row or []):
            if cell in ("R", "J"):
                normalized.append(cell)
            else:
                normalized.append(0)
        while len(normalized) < COLS:
            normalized.append(0)
        board.append(normalized[:COLS])
    while len(board) < ROWS:
        board.append([0] * COLS)

    # Vérifier si déjà gagné
    winner_now = ai_engine.winner_on_board(board)
    if winner_now:
        return jsonify({
            "winner": winner_now,
            "moves": 0,
            "certain": True,
            "message": f"{'Rouge' if winner_now == 'R' else 'Jaune'} a déjà gagné."
        })

    # Simuler coup par coup avec les 2 IA
    sim_board = [row[:] for row in board]
    player = current_player
    moves_played = 0

    try:
        for _ in range(max_moves):
            valid = ai_engine.valid_cols(sim_board)
            if not valid:
                return jsonify({
                    "winner": "draw",
                    "moves": moves_played,
                    "certain": True,
                    "message": "Match nul."
                })

            col = best_ai_col(sim_board, player, depth)
            if col is None:
                break

            r = ai_engine.next_open_row(sim_board, col)
            if r is None:
                break

            sim_board[r][col] = player
            moves_played += 1

            winner = ai_engine.winner_on_board(sim_board)
            if winner:
                color_name = "Rouge" if winner == "R" else "Jaune"
                return jsonify({
                    "winner": winner,
                    "moves": moves_played,
                    "certain": True,
                    "message": f"{color_name} gagne en {moves_played} coup{'s' if moves_played > 1 else ''} (simulation depth={depth})."
                })

            player = "J" if player == "R" else "R"

    except Exception as e:
        return jsonify({"error": f"Erreur simulation: {str(e)}"}), 500

    return jsonify({
        "winner": None,
        "moves": moves_played,
        "certain": False,
        "message": "Simulation incomplète — partie équilibrée."
    })


@app.post("/api/paint_hint")
def api_paint_hint():
    """
    Depuis un plateau peint librement, retourne le meilleur coup IA.
    Body: { board, current_player, depth? }
    """
    data = request.json or {}
    board_raw = data.get("board")
    current_player = str(data.get("current_player") or "R").upper()
    depth = normalize_depth(data.get("depth"), DEFAULT_DEPTH)

    if current_player not in ("R", "J"):
        return jsonify({"error": "Joueur invalide"}), 400

    if not isinstance(board_raw, list) or len(board_raw) != ROWS:
        return jsonify({"error": "Plateau invalide"}), 400

    board = [row[:] for row in board_raw]

    try:
        col = best_ai_col(board, current_player, depth)
    except Exception as e:
        return jsonify({"error": f"Erreur IA: {str(e)}"}), 500

    if col is None:
        return jsonify({"error": "Aucun coup possible"}), 400

    return jsonify({"suggested_col": col})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)