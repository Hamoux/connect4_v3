import argparse
import hashlib
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import psycopg2
from psycopg2.extras import RealDictCursor

ROWS = 9
COLS = 9
CONNECT = 4
DB_CONFIG = {
    "host": os.getenv("PGHOST", "localhost"),
    "port": int(os.getenv("PGPORT", "5432")),
    "dbname": os.getenv("PGDATABASE", "Connect4DB"),
    "user": os.getenv("PGUSER", "postgres"),
    "password": os.getenv("PGPASSWORD", "Celina123"),
}

CENTER = COLS // 2


def make_board():
    return [[0] * COLS for _ in range(ROWS)]


def board_key(board):
    return tuple(cell for row in board for cell in row)


def drop_piece(board, col, player):
    for r in range(ROWS - 1, -1, -1):
        if board[r][col] == 0:
            board[r][col] = player
            return r
    return -1


def undo_piece(board, row, col):
    board[row][col] = 0


def get_valid_cols(board):
    return [c for c in range(COLS) if board[0][c] == 0]


def order_moves(valid_cols, rng=None, shuffle_ties=False):
    ordered = sorted(valid_cols, key=lambda c: (abs(c - CENTER), c))
    if not shuffle_ties or rng is None:
        return ordered

    grouped = {}
    for c in ordered:
        grouped.setdefault(abs(c - CENTER), []).append(c)

    out = []
    for distance in sorted(grouped):
        cols = grouped[distance]
        rng.shuffle(cols)
        out.extend(cols)
    return out


def is_winning_move(board, row, col, player):
    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
    for dr, dc in directions:
        count = 1
        for sign in (1, -1):
            r, c = row + sign * dr, col + sign * dc
            while 0 <= r < ROWS and 0 <= c < COLS and board[r][c] == player:
                count += 1
                r += sign * dr
                c += sign * dc
        if count >= CONNECT:
            return True
    return False


def get_winning_line(board, row, col, player):
    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
    for dr, dc in directions:
        line = [(row, col)]
        for sign in (1, -1):
            r, c = row + sign * dr, col + sign * dc
            while 0 <= r < ROWS and 0 <= c < COLS and board[r][c] == player:
                line.append((r, c))
                r += sign * dr
                c += sign * dc
        if len(line) >= CONNECT:
            return line
    return []


def is_board_full(board):
    return all(board[0][c] != 0 for c in range(COLS))


def score_window(window, player):
    opp = 3 - player
    score = 0
    p_count = window.count(player)
    o_count = window.count(opp)
    empty = window.count(0)

    if p_count == 4:
        score += 10000
    elif p_count == 3 and empty == 1:
        score += 50
    elif p_count == 2 and empty == 2:
        score += 10

    if o_count == 3 and empty == 1:
        score -= 80
    if o_count == 2 and empty == 2:
        score -= 5
    return score


def score_board(board, player):
    score = 0
    center_array = [board[r][CENTER] for r in range(ROWS)]
    score += center_array.count(player) * 6

    for offset in range(1, 3):
        for c in [CENTER - offset, CENTER + offset]:
            if 0 <= c < COLS:
                col_array = [board[r][c] for r in range(ROWS)]
                score += col_array.count(player) * (4 - offset)

    for r in range(ROWS):
        for c in range(COLS - CONNECT + 1):
            score += score_window([board[r][c + i] for i in range(CONNECT)], player)

    for c in range(COLS):
        for r in range(ROWS - CONNECT + 1):
            score += score_window([board[r + i][c] for i in range(CONNECT)], player)

    for r in range(ROWS - CONNECT + 1):
        for c in range(COLS - CONNECT + 1):
            score += score_window([board[r + i][c + i] for i in range(CONNECT)], player)

    for r in range(CONNECT - 1, ROWS):
        for c in range(COLS - CONNECT + 1):
            score += score_window([board[r - i][c + i] for i in range(CONNECT)], player)

    return score


class SearchStats:
    def __init__(self):
        self.nodes = 0
        self.tt_hits = 0
        self.cutoffs = 0
        self.safe_rejections = 0
        self.safe_fallbacks = 0


def opponent_has_immediate_win(board, player_to_move):
    for col in get_valid_cols(board):
        row = drop_piece(board, col, player_to_move)
        if row != -1 and is_winning_move(board, row, col, player_to_move):
            undo_piece(board, row, col)
            return True
        if row != -1:
            undo_piece(board, row, col)
    return False


def allows_opp_immediate_win(board, col, player):
    row = drop_piece(board, col, player)
    if row == -1:
        return True
    opp = 3 - player
    bad = opponent_has_immediate_win(board, opp)
    undo_piece(board, row, col)
    return bad


def filter_safe_cols(board, cols, player, stats=None):
    safe = []
    rejected = 0
    for col in cols:
        if allows_opp_immediate_win(board, col, player):
            rejected += 1
        else:
            safe.append(col)
    if stats is not None:
        stats.safe_rejections += rejected
        if not safe and rejected:
            stats.safe_fallbacks += 1
    return safe if safe else cols


def minimax(board, depth, alpha, beta, maximizing, player, tt, stats, rng, shuffle_ties, avoid_giveaway=True):
    stats.nodes += 1
    valid_cols = get_valid_cols(board)
    opponent = 3 - player

    if not valid_cols:
        return 0, None

    key = (board_key(board), depth, maximizing, player, avoid_giveaway)
    cached = tt.get(key)
    if cached is not None:
        stats.tt_hits += 1
        return cached

    if depth == 0:
        result = (score_board(board, player), None)
        tt[key] = result
        return result

    cutoff = False

    if maximizing:
        candidate_cols = order_moves(valid_cols, rng, shuffle_ties)
        if avoid_giveaway:
            candidate_cols = filter_safe_cols(board, candidate_cols, player, stats)

        best_score = -math.inf
        best_cols = []
        for col in candidate_cols:
            row = drop_piece(board, col, player)
            if row == -1:
                continue
            if is_winning_move(board, row, col, player):
                undo_piece(board, row, col)
                result = (100000 + depth, col)
                tt[key] = result
                return result
            score, _ = minimax(board, depth - 1, alpha, beta, False, player, tt, stats, rng, shuffle_ties, avoid_giveaway)
            undo_piece(board, row, col)

            if score > best_score:
                best_score = score
                best_cols = [col]
            elif score == best_score:
                best_cols.append(col)

            alpha = max(alpha, best_score)
            if alpha >= beta:
                stats.cutoffs += 1
                cutoff = True
                break

        best_col = rng.choice(best_cols) if (shuffle_ties and len(best_cols) > 1) else best_cols[0]
        result = (best_score, best_col)
    else:
        candidate_cols = order_moves(valid_cols, rng, shuffle_ties)
        if avoid_giveaway:
            candidate_cols = filter_safe_cols(board, candidate_cols, opponent, stats)

        best_score = math.inf
        best_cols = []
        for col in candidate_cols:
            row = drop_piece(board, col, opponent)
            if row == -1:
                continue
            if is_winning_move(board, row, col, opponent):
                undo_piece(board, row, col)
                result = (-100000 - depth, col)
                tt[key] = result
                return result
            score, _ = minimax(board, depth - 1, alpha, beta, True, player, tt, stats, rng, shuffle_ties, avoid_giveaway)
            undo_piece(board, row, col)

            if score < best_score:
                best_score = score
                best_cols = [col]
            elif score == best_score:
                best_cols.append(col)

            beta = min(beta, best_score)
            if alpha >= beta:
                stats.cutoffs += 1
                cutoff = True
                break

        best_col = rng.choice(best_cols) if (shuffle_ties and len(best_cols) > 1) else best_cols[0]
        result = (best_score, best_col)

    if not cutoff:
        tt[key] = result
    return result


def tactical_move(board, player, rng=None):
    valid_cols = get_valid_cols(board)

    winning = []
    for col in valid_cols:
        row = drop_piece(board, col, player)
        if row != -1 and is_winning_move(board, row, col, player):
            winning.append(col)
        if row != -1:
            undo_piece(board, row, col)
    if winning:
        return rng.choice(winning) if rng and len(winning) > 1 else winning[0]

    opp = 3 - player
    blocking = []
    for col in valid_cols:
        row = drop_piece(board, col, opp)
        if row != -1 and is_winning_move(board, row, col, opp):
            blocking.append(col)
        if row != -1:
            undo_piece(board, row, col)
    if blocking:
        return rng.choice(blocking) if rng and len(blocking) > 1 else blocking[0]

    return None


def pick_move(board, player, depth, rng, shuffle_ties=True, avoid_giveaway=True):
    valid_cols = get_valid_cols(board)
    forced = tactical_move(board, player, rng)
    if forced is not None:
        return forced, {
            "nodes": 0,
            "tt_hits": 0,
            "cutoffs": 0,
            "tt_size": 0,
            "safe_rejections": 0,
            "safe_fallbacks": 0,
        }

    tt = {}
    stats = SearchStats()
    _, best_col = minimax(
        board,
        depth,
        -math.inf,
        math.inf,
        True,
        player,
        tt,
        stats,
        rng,
        shuffle_ties,
        avoid_giveaway,
    )

    ordered = order_moves(valid_cols, rng, shuffle_ties)
    if avoid_giveaway:
        safe_valid = filter_safe_cols(board, ordered, player, stats)
    else:
        safe_valid = ordered

    if best_col is None or best_col not in safe_valid:
        best_col = safe_valid[0] if safe_valid else rng.choice(valid_cols)

    return best_col, {
        "nodes": stats.nodes,
        "tt_hits": stats.tt_hits,
        "cutoffs": stats.cutoffs,
        "tt_size": len(tt),
        "safe_rejections": stats.safe_rejections,
        "safe_fallbacks": stats.safe_fallbacks,
    }


def play_game(depth=4, opening_random_plies=2, shuffle_ties=True, seed=None, avoid_giveaway=True):
    rng = random.Random(seed if seed is not None else (time.time_ns() ^ os.getpid()))
    board = make_board()
    history = []
    player = 1
    winner = None
    winning_line = []

    total_nodes = 0
    total_tt_hits = 0
    total_cutoffs = 0
    max_tt_size = 0
    total_safe_rejections = 0
    total_safe_fallbacks = 0

    ply = 0
    while True:
        valid = get_valid_cols(board)
        if not valid:
            status = "nulle"
            break

        forced = tactical_move(board, player, rng)
        if forced is not None:
            col = forced
        elif ply < opening_random_plies:
            near_center = order_moves(valid, rng, shuffle_ties=True)[: min(len(valid), 5)]
            if avoid_giveaway:
                near_center = filter_safe_cols(board, near_center, player)
            col = rng.choice(near_center)
        else:
            col, move_stats = pick_move(board, player, depth, rng, shuffle_ties=shuffle_ties, avoid_giveaway=avoid_giveaway)
            total_nodes += move_stats["nodes"]
            total_tt_hits += move_stats["tt_hits"]
            total_cutoffs += move_stats["cutoffs"]
            max_tt_size = max(max_tt_size, move_stats["tt_size"])
            total_safe_rejections += move_stats["safe_rejections"]
            total_safe_fallbacks += move_stats["safe_fallbacks"]

        row = drop_piece(board, col, player)
        if row == -1:
            valid.remove(col)
            if not valid:
                status = "nulle"
                break
            valid_choices = filter_safe_cols(board, valid, player) if avoid_giveaway else valid
            col = rng.choice(valid_choices)
            row = drop_piece(board, col, player)

        history.append((row, col, player))

        if is_winning_move(board, row, col, player):
            winner = player
            winning_line = get_winning_line(board, row, col, player)
            status = "terminee"
            break

        if is_board_full(board):
            status = "nulle"
            break

        player = 3 - player
        ply += 1

    sig_raw = "".join(str(c + 1) for (_, c, _) in history)
    sig_mirror = "".join(str(COLS - c) for (_, c, _) in history)
    signature = min(sig_raw, sig_mirror)

    cols_seq = [c + 1 for (_, c, _) in history]
    move_hash = hashlib.sha256("".join(str(x) for x in cols_seq).encode()).hexdigest()

    winning_line_str = None
    if winning_line:
        winning_line_str = str([[r, c] for r, c in winning_line])

    return {
        "board": board,
        "history": history,
        "signature": signature,
        "move_hash": move_hash,
        "winner": winner,
        "status": status,
        "winning_line": winning_line_str,
        "cols_seq": cols_seq,
        "search_nodes": total_nodes,
        "tt_hits": total_tt_hits,
        "cutoffs": total_cutoffs,
        "max_tt_size": max_tt_size,
        "safe_rejections": total_safe_rejections,
        "safe_fallbacks": total_safe_fallbacks,
    }


def get_conn():
    return psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG)


def ensure_columns():
    migrations = [
        "ALTER TABLE partie ADD COLUMN IF NOT EXISTS data_source TEXT;",
        "ALTER TABLE partie ADD COLUMN IF NOT EXISTS move_hash TEXT;",
        "ALTER TABLE partie ADD COLUMN IF NOT EXISTS bga_table_id TEXT;",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_partie_move_hash ON partie(move_hash) WHERE move_hash IS NOT NULL;",
        "CREATE INDEX IF NOT EXISTS idx_partie_signature ON partie(signature);",
    ]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for sql in migrations:
                cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


def signature_exists(cur, sig):
    cur.execute("SELECT 1 FROM partie WHERE signature=%s LIMIT 1;", (sig,))
    return cur.fetchone() is not None


def move_hash_exists(cur, mh):
    cur.execute("SELECT 1 FROM partie WHERE move_hash=%s LIMIT 1;", (mh,))
    return cur.fetchone() is not None


def board_to_text(board):
    return "\n".join("".join(str(x) for x in row) for row in board)


def insert_game(game_data):
    sig = game_data["signature"]
    mh = game_data["move_hash"]
    winner = game_data["winner"]
    status = game_data["status"]
    history = game_data["history"]
    winning_line = game_data["winning_line"]

    joueur_depart = "1"
    joueur_gagnant = str(winner) if winner else None

    timing = {"duplicate_s": 0.0, "insert_s": 0.0}
    query_count = 0

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            t_dup = time.perf_counter()
            sig_exists = signature_exists(cur, sig)
            query_count += 1
            if sig_exists:
                timing["duplicate_s"] = time.perf_counter() - t_dup
                return None, "duplicate_signature", timing, query_count

            hash_exists = move_hash_exists(cur, mh)
            query_count += 1
            timing["duplicate_s"] = time.perf_counter() - t_dup
            if hash_exists:
                return None, "duplicate_hash", timing, query_count

            t_ins = time.perf_counter()
            cur.execute(
                """
                INSERT INTO partie
                    (mode, type_partie, status, joueur_depart, joueur_gagnant,
                     ligne_gagnante, signature, rows, cols, nb_colonnes,
                     confiance, data_source, move_hash)
                VALUES
                    (%s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s,
                     %s, %s, %s)
                RETURNING id_partie;
                """,
                (
                    "ai_vs_ai",
                    "classique",
                    status,
                    joueur_depart,
                    joueur_gagnant,
                    winning_line,
                    sig,
                    ROWS,
                    COLS,
                    COLS,
                    3,
                    "minimax_engine_fast_safe",
                    mh,
                ),
            )
            query_count += 1
            id_partie = cur.fetchone()["id_partie"]

            board = make_board()
            prev_id = None
            for move_num, (_, col, player) in enumerate(history, start=1):
                drop_piece(board, col, player)
                plateau_text = board_to_text(board)
                cur.execute(
                    """
                    INSERT INTO situation
                        (id_partie, numero_coup, plateau, joueur, precedent, suivant)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id_situation;
                    """,
                    (id_partie, move_num, plateau_text, str(player), prev_id, None),
                )
                query_count += 1
                sit_id = cur.fetchone()["id_situation"]

                if prev_id is not None:
                    cur.execute(
                        "UPDATE situation SET suivant=%s WHERE id_situation=%s;",
                        (sit_id, prev_id),
                    )
                    query_count += 1
                prev_id = sit_id

            conn.commit()
            timing["insert_s"] = time.perf_counter() - t_ins
            return id_partie, "inserted", timing, query_count
    except Exception as e:
        conn.rollback()
        return None, f"error: {e}", timing, query_count
    finally:
        conn.close()


def worker_play_and_insert(depth, opening_random_plies, shuffle_ties, seed_base, avoid_giveaway):
    worker_seed = seed_base ^ time.time_ns() ^ os.getpid()

    t0 = time.perf_counter()
    game = play_game(
        depth=depth,
        opening_random_plies=opening_random_plies,
        shuffle_ties=shuffle_ties,
        seed=worker_seed,
        avoid_giveaway=avoid_giveaway,
    )
    play_elapsed = time.perf_counter() - t0

    id_partie, result, db_timing, query_count = insert_game(game)
    total_elapsed = play_elapsed + db_timing["duplicate_s"] + db_timing["insert_s"]

    winner_label = f"Player {game['winner']} wins" if game["winner"] else "Draw"

    return {
        "id_partie": id_partie,
        "result": result,
        "winner": winner_label,
        "moves": len(game["history"]),
        "signature": game["signature"],
        "play_s": round(play_elapsed, 4),
        "dup_s": round(db_timing["duplicate_s"], 4),
        "insert_s": round(db_timing["insert_s"], 4),
        "total_s": round(total_elapsed, 4),
        "queries": query_count,
        "nodes": game["search_nodes"],
        "tt_hits": game["tt_hits"],
        "cutoffs": game["cutoffs"],
        "max_tt_size": game["max_tt_size"],
        "safe_rejections": game["safe_rejections"],
        "safe_fallbacks": game["safe_fallbacks"],
    }


def format_sig(sig, preview):
    if preview == 0 or preview >= len(sig):
        return sig
    return f"{sig[:preview]}..."


def per_hour(avg_seconds, workers):
    if avg_seconds <= 0:
        return float("inf")
    return workers * 3600.0 / avg_seconds


def main():
    parser = argparse.ArgumentParser(description="Fast Connect 4 Minimax generator")
    parser.add_argument("--games", type=int, default=100, help="Number of games to generate")
    parser.add_argument("--depth", type=int, default=4, help="Minimax search depth")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers")
    parser.add_argument("--opening-random-plies", type=int, default=2, help="Random opening plies before minimax")
    parser.add_argument("--sig-preview", type=int, default=20, help="0 to print full signature")
    parser.add_argument("--no-shuffle-ties", action="store_true", help="Disable random tie-breaking between equal moves")
    parser.add_argument("--disable-safe-filter", action="store_true", help="Allow moves that may give opponent an immediate winning reply")
    args = parser.parse_args()

    avoid_giveaway = not args.disable_safe_filter

    print("=" * 80)
    print(f"Connect 4 ({ROWS}x{COLS}) — Fast Minimax Generator")
    print(f"Target games          : {args.games}")
    print(f"Minimax depth         : {args.depth}")
    print(f"Workers               : {args.workers}")
    print(f"Random opening plies  : {args.opening_random_plies}")
    print(f"Shuffle equal moves   : {not args.no_shuffle_ties}")
    print(f"Safe anti-blunder     : {avoid_giveaway}")
    print("=" * 80)

    print("\n[DB] Ensuring schema columns exist...")
    ensure_columns()
    print("[DB] Schema ready.\n")

    processed = inserted = duplicates = errors = 0
    play_times = []
    dup_times = []
    insert_times = []
    total_times = []
    query_counts = []
    node_counts = []
    tt_hits_counts = []
    cutoffs_counts = []
    tt_sizes = []
    safe_rejections = []
    safe_fallbacks = []

    wall_start = time.perf_counter()
    seed_base = time.time_ns() ^ os.getpid()

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(
                worker_play_and_insert,
                args.depth,
                args.opening_random_plies,
                not args.no_shuffle_ties,
                seed_base + i * 9973,
                avoid_giveaway,
            )
            for i in range(args.games)
        ]

        for idx, fut in enumerate(as_completed(futures), start=1):
            res = fut.result()
            processed += 1
            play_times.append(res["play_s"])
            dup_times.append(res["dup_s"])
            total_times.append(res["total_s"])
            query_counts.append(res["queries"])
            node_counts.append(res["nodes"])
            tt_hits_counts.append(res["tt_hits"])
            cutoffs_counts.append(res["cutoffs"])
            tt_sizes.append(res["max_tt_size"])
            safe_rejections.append(res["safe_rejections"])
            safe_fallbacks.append(res["safe_fallbacks"])

            if res["result"] == "inserted":
                inserted += 1
                insert_times.append(res["insert_s"])
                print(
                    f"✅ Game #{idx:5d} | id={res['id_partie']} | {res['winner']:<15} | "
                    f"{res['moves']:2d} moves | sig: {format_sig(res['signature'], args.sig_preview)} | "
                    f"play={res['play_s']:.3f}s | dup={res['dup_s']:.3f}s | insert={res['insert_s']:.3f}s | "
                    f"total={res['total_s']:.3f}s | q={res['queries']} | nodes={res['nodes']} | "
                    f"tt_hits={res['tt_hits']} | safe_rej={res['safe_rejections']}"
                )
            elif str(res["result"]).startswith("duplicate"):
                duplicates += 1
                print(f"⚠️  Duplicate skipped ({res['result']}) | sig: {format_sig(res['signature'], args.sig_preview)}")
            else:
                errors += 1
                print(f"❌ Error on game #{idx}: {res['result']}")

    wall_elapsed = time.perf_counter() - wall_start

    avg_play = sum(play_times) / len(play_times) if play_times else 0.0
    avg_dup = sum(dup_times) / len(dup_times) if dup_times else 0.0
    avg_ins = sum(insert_times) / len(insert_times) if insert_times else 0.0
    avg_total = sum(total_times) / len(total_times) if total_times else 0.0
    avg_queries = sum(query_counts) / len(query_counts) if query_counts else 0.0

    print("\n" + "=" * 80)
    print(f"Wall-clock run time             : {wall_elapsed:.2f}s")
    print(f"Processed tasks                 : {processed}")
    print(f"Inserted                        : {inserted}")
    print(f"Duplicates                      : {duplicates}")
    print(f"Errors                          : {errors}")
    print("-" * 80)
    print(f"Average play time / game        : {avg_play:.4f}s")
    print(f"Average duplicate-check time    : {avg_dup:.4f}s")
    print(f"Average insert time / inserted  : {avg_ins:.4f}s")
    print(f"Average end-to-end / game       : {avg_total:.4f}s")
    print(f"Average SQL queries / game      : {avg_queries:.2f}")
    print(f"Total search nodes              : {sum(node_counts)}")
    print(f"Total TT hits                   : {sum(tt_hits_counts)}")
    print(f"Total alpha-beta cutoffs        : {sum(cutoffs_counts)}")
    print(f"Max TT size seen                : {max(tt_sizes) if tt_sizes else 0}")
    print(f"Total safe-move rejections      : {sum(safe_rejections)}")
    print(f"Safe-filter hard fallbacks      : {sum(safe_fallbacks)}")
    print("-" * 80)
    print(f"Estimated inserted/hour (play only)    : {per_hour(avg_play, args.workers):.1f}")
    print(f"Estimated inserted/hour (play+dup)     : {per_hour(avg_play + avg_dup, args.workers):.1f}")
    print(f"Estimated inserted/hour (end-to-end)   : {per_hour(avg_total, args.workers):.1f}")
    print(f"Observed throughput by wall-clock      : {(inserted * 3600.0 / wall_elapsed) if wall_elapsed > 0 else 0.0:.1f}")
    print("=" * 80)


if __name__ == "__main__":
    main()
