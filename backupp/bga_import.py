# bga_import.py
import json
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import errors

from game import Connect4Game
from db.db import (
    create_partie, insert_situation, update_links,
    finish_partie, board_to_text, update_partie_signature, delete_partie
)

# ---------------- DB helpers ----------------
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

def find_partie_id_by_signature(signature: str):
    sql = "SELECT id_partie FROM partie WHERE signature = %s LIMIT 1;"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (signature,))
        row = cur.fetchone()
        return row["id_partie"] if row else None

def count_situations_for_partie(id_partie: int) -> int:
    sql = "SELECT COUNT(*) AS n FROM situation WHERE id_partie = %s;"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (id_partie,))
        return int(cur.fetchone()["n"])

# ---------------- Import logic ----------------
def import_bga_moves(moves, rows=9, cols=9, confiance=3):
    """
    moves: list of dicts like {"move_id":2, "col":5, "player_id":"3368422"}
    col is 1..9
    """

    # 1) tri + construction signature
    moves = sorted(moves, key=lambda m: int(m["move_id"]))
    signature = "".join(str(int(m["col"])) for m in moves)  # string de 1..9

    # 2) éviter doublons
    existing_pid = find_partie_id_by_signature(signature)
    if existing_pid is not None:
        n = count_situations_for_partie(existing_pid)
        print(f"⚠️ Signature déjà en base (id_partie={existing_pid}, situations={n}). Stop.")
        return existing_pid

    # 3) créer partie (on fixera rows/cols dans db.create_partie, voir section 3)
    pid = create_partie(
        mode="BGA",
        type_partie="HUMAIN",
        status="EN_COURS",
        joueur_depart="R",
        rows=rows,
        cols=cols,
        confiance=confiance,
        nb_colonnes=cols
    )

    # 4) set signature (sécurisé)
    try:
        update_partie_signature(pid, signature)
    except errors.UniqueViolation:
        try:
            delete_partie(pid)
        except Exception:
            pass
        existing_pid = find_partie_id_by_signature(signature)
        print(f"⚠️ Signature importée ailleurs, réutilisation id_partie={existing_pid}")
        return existing_pid

    # 5) mapping player_id -> R/J (1er joueur vu = R)
    pids = []
    for m in moves:
        if m["player_id"] not in pids:
            pids.append(m["player_id"])
    if len(pids) < 2:
        raise ValueError("Impossible: je ne vois qu'un seul player_id dans les moves")

    pid_to_color = {pids[0]: "R", pids[1]: "J"}

    # 6) rejouer
    game = Connect4Game(rows=rows, cols=cols, starting_player="R")
    prev_sid = None
    winning_line = None

    for i, mv in enumerate(moves, start=1):
        col = int(mv["col"]) - 1  # 0..8
        color = pid_to_color[str(mv["player_id"])]

        # force le joueur (important si BGA ne suit pas l'alternance parfaite)
        game.current_player = color

        ok, wl = game.drop(col)
        if not ok:
            print(f"❌ coup invalide au move #{i} (move_id={mv['move_id']}) col={col+1}")
            print("Derniers coups =", [c + 1 for (_r, c, _p) in game.history[-10:]])
            print(board_to_text(game.board))
            break

        if wl:
            winning_line = wl

        # ✅ INSERT TOUJOURS la situation APRÈS le drop, AVANT de stopper
        joueur = game.history[-1][2]
        plateau_txt = board_to_text(game.board)

        sid = insert_situation(
            id_partie=pid,
            numero_coup=i,
            plateau=plateau_txt,
            joueur=joueur,
            precedent=None,
            suivant=None
        )

        if prev_sid is not None:
            update_links(prev_sid, sid)
        prev_sid = sid

        if game.game_over:
            break

    # 7) finish_partie (gagnant en CHAR(1))
    if game.result == "Rouge":
        gagnant = "R"
    elif game.result == "Jaune":
        gagnant = "J"
    elif game.result == "Match nul":
        gagnant = "D"
    else:
        gagnant = None

    # winning_line -> texte simple (optionnel)
    ligne_txt = None
    if winning_line:
        # ex: [(r,c), ...]
        ligne_txt = json.dumps(winning_line)

    finish_partie(
        id_partie=pid,
        status="TERMINEE" if game.game_over else "EN_COURS",
        joueur_gagnant=gagnant,
        ligne_gagnante=ligne_txt,
        signature=signature
    )

    print("✅ Import terminé. id_partie =", pid, "| coups importés =", len(game.history))
    return pid

if __name__ == "__main__":
    with open("moves.json", "r", encoding="utf-8") as f:
        moves = json.load(f)
    import_bga_moves(moves, rows=9, cols=9, confiance=3)
