import os
import time
import random
import psycopg2
from concurrent.futures import ProcessPoolExecutor, as_completed

ROWS = 9
COLS = 9
WIN = 4

# ---------- DB helpers ----------
def db_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "Connect4DB"),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD"),
    )

def board_to_text(grid):
    # grid: rows x cols with 0/'R'/'J'
    # stored as lines, top row first
    out = []
    for r in range(ROWS):
        row = []
        for c in range(COLS):
            v = grid[r][c]
            row.append(v if v in ("R", "J") else ".")
        out.append("".join(row))
    return "\n".join(out)

def canonical_signature(cols_seq):
    s = "".join(str(c) for c in cols_seq)
    m = "".join(str(COLS + 1 - c) for c in cols_seq)
    return s if s <= m else m

# ---------- Fast game state ----------
class FastGame:
    __slots__ = ("grid", "heights", "current", "history")

    def __init__(self, start="R"):
        self.grid = [[0]*COLS for _ in range(ROWS)]
        self.heights = [ROWS-1]*COLS
        self.current = start
        self.history = []  # list of (r,c,player)

    def legal_cols(self):
        return [c for c in range(COLS) if self.heights[c] >= 0]

    def drop(self, c):
        r = self.heights[c]
        if r < 0:
            return False
        p = self.current
        self.grid[r][c] = p
        self.history.append((r, c, p))
        self.heights[c] -= 1
        self.current = "J" if p == "R" else "R"
        return True

    def undo(self):
        r, c, p = self.history.pop()
        self.grid[r][c] = 0
        self.heights[c] += 1
        self.current = p

# ---------- Win check (fast enough for 9x9) ----------
DIRS = [(0,1),(1,0),(1,1),(1,-1)]
def is_win(grid, r, c, p):
    for dr, dc in DIRS:
        cnt = 1
        rr, cc = r+dr, c+dc
        while 0 <= rr < ROWS and 0 <= cc < COLS and grid[rr][cc] == p:
            cnt += 1
            rr += dr; cc += dc
        rr, cc = r-dr, c-dc
        while 0 <= rr < ROWS and 0 <= cc < COLS and grid[rr][cc] == p:
            cnt += 1
            rr -= dr; cc -= dc
        if cnt >= WIN:
            return True
    return False

def last_move(game):
    return game.history[-1] if game.history else None

# ---------- Eval heuristic ----------
def eval_grid(grid, player):
    # simple: count center control + 2/3-in-a-row potentials (cheap)
    opp = "J" if player == "R" else "R"
    score = 0

    center = COLS//2
    for r in range(ROWS):
        if grid[r][center] == player: score += 2
        if grid[r][center] == opp: score -= 2

    # count 4-windows
    def window_score(win):
        nonlocal score
        pcount = win.count(player)
        ocount = win.count(opp)
        zcount = win.count(0)
        if ocount == 0:
            if pcount == 3 and zcount == 1: score += 30
            elif pcount == 2 and zcount == 2: score += 6
        if pcount == 0:
            if ocount == 3 and zcount == 1: score -= 28
            elif ocount == 2 and zcount == 2: score -= 5

    # horizontal
    for r in range(ROWS):
        for c in range(COLS-3):
            window_score([grid[r][c+i] for i in range(4)])
    # vertical
    for c in range(COLS):
        for r in range(ROWS-3):
            window_score([grid[r+i][c] for i in range(4)])
    # diag \
    for r in range(ROWS-3):
        for c in range(COLS-3):
            window_score([grid[r+i][c+i] for i in range(4)])
    # diag /
    for r in range(3, ROWS):
        for c in range(COLS-3):
            window_score([grid[r-i][c+i] for i in range(4)])

    return score

# ---------- Minimax (alpha-beta + TT) ----------
def hash_state(game):
    # small hash: tuple of column heights + current player + top few cells
    return (tuple(game.heights), game.current)

def ordered_cols(legal):
    # center-out ordering
    center = COLS//2
    return sorted(legal, key=lambda c: abs(c-center))

def minimax(game, depth, alpha, beta, maximizing, me, tt):
    key = (hash_state(game), depth, maximizing, me)
    if key in tt:
        return tt[key]

    lm = last_move(game)
    if lm:
        r, c, p = lm
        if is_win(game.grid, r, c, p):
            val = 10_000_000 if p == me else -10_000_000
            tt[key] = val
            return val

    legal = game.legal_cols()
    if depth == 0 or not legal:
        val = eval_grid(game.grid, me)
        tt[key] = val
        return val

    if maximizing:
        best = -10**18
        for c in ordered_cols(legal):
            game.drop(c)
            val = minimax(game, depth-1, alpha, beta, False, me, tt)
            game.undo()
            if val > best: best = val
            if best > alpha: alpha = best
            if beta <= alpha: break
        tt[key] = best
        return best
    else:
        best = 10**18
        for c in ordered_cols(legal):
            game.drop(c)
            val = minimax(game, depth-1, alpha, beta, True, me, tt)
            game.undo()
            if val < best: best = val
            if best < beta: beta = best
            if beta <= alpha: break
        tt[key] = best
        return best

def choose_move(game, depth, epsilon, tt):
    legal = game.legal_cols()
    if not legal:
        return None
    if random.random() < epsilon:
        return random.choice(legal)

    me = game.current
    best_c = None
    best_v = -10**18
    for c in ordered_cols(legal):
        game.drop(c)
        v = minimax(game, depth-1, -10**18, 10**18, False, me, tt)
        game.undo()
        if v > best_v:
            best_v = v
            best_c = c
    return best_c if best_c is not None else random.choice(legal)

def simulate_one(seed, depth=3, epsilon=0.05):
    random.seed(seed)
    g = FastGame(start=random.choice(["R","J"]))
    tt = {}  # per-worker transposition

    moves = []
    while True:
        c = choose_move(g, depth, epsilon, tt)
        if c is None:
            winner = "D"
            break
        ok = g.drop(c)
        if not ok:
            winner = "D"
            break
        r, cc, p = g.history[-1]
        moves.append({"move_id": len(moves)+1, "col": c+1, "color": p})

        if is_win(g.grid, r, cc, p):
            winner = p
            break
        if not g.legal_cols():
            winner = "D"
            break

    sig = canonical_signature([m["col"] for m in moves])
    return {"moves": moves, "winner": winner, "signature": sig, "final_board": g.grid}

# ---------- DB insert (main process only) ----------
def insert_game(cur, res, confiance=2, mode="AI_LOCAL", type_partie="MINIMAX"):
    # dedupe by signature
    cur.execute("SELECT id_partie FROM partie WHERE signature=%s LIMIT 1;", (res["signature"],))
    row = cur.fetchone()
    if row:
        return row[0], False

    cur.execute(
        """
        INSERT INTO partie(mode,type_partie,status,joueur_depart,joueur_gagnant,ligne_gagnante,signature,rows,cols,confiance,nb_colonnes)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id_partie;
        """,
        (mode, type_partie, "TERMINEE", "R", res["winner"], None, res["signature"], ROWS, COLS, confiance, COLS),
    )
    idp = cur.fetchone()[0]

    # replay moves to build situations (plateau after each move)
    g = FastGame(start="R")
    prev_sid = None
    for mv in res["moves"]:
        g.current = mv["color"]
        g.drop(int(mv["col"])-1)
        plateau = board_to_text(g.grid)
        joueur = mv["color"]
        cur.execute(
            """
            INSERT INTO situation(id_partie,numero_coup,plateau,joueur,precedent,suivant)
            VALUES(%s,%s,%s,%s,%s,%s)
            RETURNING id_situation;
            """,
            (idp, int(mv["move_id"]), plateau, joueur, None, None),
        )
        sid = cur.fetchone()[0]
        if prev_sid is not None:
            cur.execute("UPDATE situation SET suivant=%s WHERE id_situation=%s;", (sid, prev_sid))
            cur.execute("UPDATE situation SET precedent=%s WHERE id_situation=%s;", (prev_sid, sid))
        prev_sid = sid

    return idp, True

def main(total_games=2000, workers=None, depth=6, epsilon=0.0):
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    print(f"LOCAL GENERATOR 9x9 | total={total_games} | workers={workers} | depth={depth} | eps={epsilon}")

    with db_conn() as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            imported = 0
            submitted = 0

            with ProcessPoolExecutor(max_workers=workers) as ex:
                futures = []
                for i in range(total_games):
                    futures.append(ex.submit(simulate_one, seed=int(time.time()*1e6) + i, depth=depth, epsilon=epsilon))
                    submitted += 1

                for fut in as_completed(futures):
                    res = fut.result()
                    idp, did_insert = insert_game(cur, res, confiance=2, mode="AI_LOCAL", type_partie="MINIMAX")
                    if did_insert:
                        imported += 1
                    if imported % 50 == 0:
                        conn.commit()
                        print(f"✅ committed imported={imported}/{submitted}")
                    if imported >= total_games:
                        break

            conn.commit()
            print("DONE. imported =", imported)

if __name__ == "__main__":
    # tweak here
    main(total_games=2000, workers=None, depth=3, epsilon=0.08)