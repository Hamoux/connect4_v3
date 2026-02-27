import os
import psycopg2
from psycopg2.extras import RealDictCursor

def get_conn():
    host = os.getenv("PGHOST", "localhost")
    port = int(os.getenv("PGPORT", "5432"))
    dbname = os.getenv("PGDATABASE", "Connect4DB")  # ✅ ton nom exact
    user = os.getenv("PGUSER", "postgres")
    password = os.getenv("Celina123")  # set via env; can be None if local trust auth  # mets en variable d'env si tu veux

    return psycopg2.connect(
        host=host, port=port, dbname=dbname, user=user, password=password,
        cursor_factory=RealDictCursor
    )

def board_to_text(board):
    return "\n".join("".join(str(x) for x in row) for row in board)

def moves_signature(history):
    # signature = colonnes jouées (1..cols)
    return "".join(str(c + 1) for (_r, c, _p) in history)

# db.py
def create_partie(mode, type_partie, status, joueur_depart, rows=None, cols=None, nb_colonnes=None, confiance=None, signature=None):
    cols_sql = ["mode", "type_partie", "status", "joueur_depart"]
    vals = [mode, type_partie, status, joueur_depart]

    if rows is not None:
        cols_sql.append("rows"); vals.append(rows)
    if cols is not None:
        cols_sql.append("cols"); vals.append(cols)
    if nb_colonnes is not None:
        cols_sql.append("nb_colonnes"); vals.append(nb_colonnes)
    if confiance is not None:
        cols_sql.append("confiance"); vals.append(confiance)
    if signature is not None:
        cols_sql.append("signature"); vals.append(signature)

    placeholders = ", ".join(["%s"] * len(vals))
    sql = f"""
    INSERT INTO partie ({", ".join(cols_sql)})
    VALUES ({placeholders})
    RETURNING id_partie;
    """

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, tuple(vals))
            pid = cur.fetchone()["id_partie"]
            return pid
    finally:
        conn.close()



def insert_situation(id_partie, numero_coup, plateau, joueur, precedent=None, suivant=None):
    sql = """
    INSERT INTO situation (id_partie, numero_coup, plateau, joueur, precedent, suivant)
    VALUES (%s, %s, %s, %s, %s, %s)
    RETURNING id_situation;
    """
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (id_partie, numero_coup, plateau, joueur, precedent, suivant))
            return cur.fetchone()["id_situation"]
    finally:
        conn.close()

def update_links(precedent_id, suivant_id):
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE situation SET suivant=%s WHERE id_situation=%s;", (suivant_id, precedent_id))
            cur.execute("UPDATE situation SET precedent=%s WHERE id_situation=%s;", (precedent_id, suivant_id))
    finally:
        conn.close()

def finish_partie(id_partie, status, joueur_gagnant=None, ligne_gagnante=None, signature=None):
    sql = """
    UPDATE partie
    SET status=%s, joueur_gagnant=%s, ligne_gagnante=%s, signature=%s
    WHERE id_partie=%s;
    """
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (status, joueur_gagnant, ligne_gagnante, signature, id_partie))
    finally:
        conn.close()

def delete_partie(id_partie):
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM partie WHERE id_partie=%s;", (id_partie,))
    finally:
        conn.close()

def mirror_moves_signature(sig: str, cols: int) -> str:
    # sig contient des colonnes en 1..cols
    out = []
    for ch in sig:
        if not ch.isdigit():
            continue
        old = int(ch)
        out.append(str(cols + 1 - old))
    return "".join(out)

def canonical_signature_from_history(history, cols: int) -> str:
    # signature normale
    sig = "".join(str(c + 1) for (_r, c, _p) in history)
    # signature miroir
    msig = mirror_moves_signature(sig, cols)
    # canonique = la plus petite (lexicographique)
    return min(sig, msig)

def update_partie_signature(id_partie, signature):
    sql = """
    UPDATE partie
    SET signature=%s
    WHERE id_partie=%s;
    """
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (signature, id_partie))
    finally:
        conn.close()


def canonical_signature_from_moves(moves, cols: int) -> str:
    """Compute canonical signature from a list of moves dicts with 'col' (1..cols)."""
    sig = "".join(str(int(m["col"])) for m in moves)
    msig = mirror_moves_signature(sig, cols)
    return min(sig, msig)
