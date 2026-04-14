import argparse
import json
import os
import random
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor

LABEL_LOSS = 0
LABEL_DRAW = 1
LABEL_WIN = 2


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract ML dataset from PostgreSQL partie table")
    p.add_argument("--host", default=os.getenv("PGHOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.getenv("PGPORT", "5432")))
    p.add_argument("--dbname", default=os.getenv("PGDATABASE", "Connect4DB"))
    p.add_argument("--user", default=os.getenv("PGUSER", "postgres"))
    p.add_argument("--password", default=os.getenv("PGPASSWORD", "Celina123"))
    p.add_argument("--rows", type=int, default=9)
    p.add_argument("--cols", type=int, default=9)
    p.add_argument("--modes", default="minimax,bga", help="Comma-separated mode filter")
    p.add_argument("--statuses", default="terminee,nulle", help="Comma-separated status filter")
    p.add_argument("--min-moves", type=int, default=6)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--train-ratio", type=float, default=0.8)
    p.add_argument("--val-ratio", type=float, default=0.1)
    p.add_argument("--output-dir", default="./ml_dataset")
    p.add_argument("--dedupe-signatures", action="store_true", help="Drop duplicate signatures inside extraction set")
    return p.parse_args()


def db_connect(args: argparse.Namespace):
    return psycopg2.connect(
        host=args.host,
        port=args.port,
        dbname=args.dbname,
        user=args.user,
        password=args.password,
        cursor_factory=RealDictCursor,
    )


def decode_signature(sig: str, cols: int) -> List[int]:
    moves = []
    for ch in sig.strip():
        if not ch.isdigit():
            raise ValueError(f"Invalid signature character: {ch!r}")
        col_1_based = int(ch)
        if not (1 <= col_1_based <= cols):
            raise ValueError(f"Column {col_1_based} out of range for {cols} cols")
        moves.append(col_1_based - 1)
    return moves


def drop_piece(board: np.ndarray, col: int, player: int) -> int:
    for r in range(board.shape[0] - 1, -1, -1):
        if board[r, col] == 0:
            board[r, col] = player
            return r
    return -1


def winning_line_exists(board: np.ndarray, row: int, col: int, player: int, connect_n: int = 4) -> bool:
    rows, cols = board.shape
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        count = 1
        for sign in (1, -1):
            r, c = row + sign * dr, col + sign * dc
            while 0 <= r < rows and 0 <= c < cols and board[r, c] == player:
                count += 1
                r += sign * dr
                c += sign * dc
        if count >= connect_n:
            return True
    return False


@dataclass
class Example:
    x: np.ndarray  # (2, rows, cols)
    policy: int
    value: int
    valid_mask: np.ndarray  # (cols,)



def board_to_channels(board: np.ndarray, player_to_move: int) -> np.ndarray:
    own = (board == player_to_move).astype(np.float32)
    opp = (board == (3 - player_to_move)).astype(np.float32)
    return np.stack([own, opp], axis=0)


def valid_moves_mask(board: np.ndarray) -> np.ndarray:
    return (board[0] == 0).astype(np.float32)


def outcome_label_from_side_to_move(final_status: str, final_winner_color: Optional[str], player_to_move: int) -> int:
    if final_status == "nulle":
        return LABEL_DRAW
    if final_winner_color is None:
        return LABEL_DRAW
    winner_player = 1 if final_winner_color == "R" else 2
    return LABEL_WIN if winner_player == player_to_move else LABEL_LOSS



def build_examples_from_game(signature: str, joueur_depart: str, joueur_gagnant: Optional[str], status: str,
                             rows: int, cols: int, min_moves: int) -> List[Example]:
    moves = decode_signature(signature, cols)
    if len(moves) < min_moves:
        return []

    board = np.zeros((rows, cols), dtype=np.int8)
    current_player = 1 if joueur_depart == "R" else 2
    examples: List[Example] = []

    for next_col in moves:
        x = board_to_channels(board, current_player)
        valid = valid_moves_mask(board)
        value = outcome_label_from_side_to_move(status, joueur_gagnant, current_player)
        examples.append(Example(x=x, policy=next_col, value=value, valid_mask=valid))

        played_row = drop_piece(board, next_col, current_player)
        if played_row == -1:
            raise ValueError(f"Illegal reconstructed move in signature {signature}: col={next_col + 1}")
        current_player = 3 - current_player

    return examples



def fetch_games(args: argparse.Namespace) -> List[dict]:
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    statuses = [s.strip() for s in args.statuses.split(",") if s.strip()]
    sql = """
        SELECT id_partie, mode, status, joueur_depart, joueur_gagnant, signature, rows, cols, created_at
        FROM partie
        WHERE signature IS NOT NULL
          AND rows = %s
          AND cols = %s
          AND mode = ANY(%s)
          AND status = ANY(%s)
        ORDER BY id_partie ASC
    """
    if args.limit:
        sql += " LIMIT %s"

    conn = db_connect(args)
    try:
        with conn.cursor() as cur:
            params: List[object] = [args.rows, args.cols, modes, statuses]
            if args.limit:
                params.append(args.limit)
            cur.execute(sql, params)
            rows = list(cur.fetchall())
    finally:
        conn.close()

    if args.dedupe_signatures:
        seen = set()
        deduped = []
        for row in rows:
            sig = row["signature"]
            if sig in seen:
                continue
            seen.add(sig)
            deduped.append(row)
        rows = deduped
    return rows



def split_games(games: Sequence[dict], seed: int, train_ratio: float, val_ratio: float):
    games = list(games)
    rng = random.Random(seed)
    rng.shuffle(games)
    n = len(games)
    n_train = int(n * train_ratio)
    n_val = int(n * val_ratio)
    train_games = games[:n_train]
    val_games = games[n_train:n_train + n_val]
    test_games = games[n_train + n_val:]
    return train_games, val_games, test_games



def materialize_split(games: Sequence[dict], min_moves: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    xs: List[np.ndarray] = []
    ps: List[int] = []
    vs: List[int] = []
    masks: List[np.ndarray] = []

    for g in games:
        try:
            examples = build_examples_from_game(
                signature=g["signature"],
                joueur_depart=g["joueur_depart"],
                joueur_gagnant=g["joueur_gagnant"],
                status=g["status"],
                rows=g["rows"],
                cols=g["cols"],
                min_moves=min_moves,
            )
        except Exception as exc:
            print(f"[WARN] Skip game id={g['id_partie']} due to reconstruction error: {exc}")
            continue

        for ex in examples:
            xs.append(ex.x)
            ps.append(ex.policy)
            vs.append(ex.value)
            masks.append(ex.valid_mask)

    if not xs:
        raise RuntimeError("No examples built for this split")

    x_arr = np.stack(xs).astype(np.float32)
    p_arr = np.asarray(ps, dtype=np.int64)
    v_arr = np.asarray(vs, dtype=np.int64)
    m_arr = np.stack(masks).astype(np.float32)
    return x_arr, p_arr, v_arr, m_arr



def save_split(output_dir: str, name: str, x: np.ndarray, p: np.ndarray, v: np.ndarray, m: np.ndarray):
    path = os.path.join(output_dir, f"{name}.npz")
    np.savez_compressed(path, x=x, policy=p, value=v, valid_mask=m)
    print(f"[OK] Saved {name}: {path} | samples={len(x)}")



def main() -> None:
    args = parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    games = fetch_games(args)
    if not games:
        raise RuntimeError("No games found with current filters")

    train_games, val_games, test_games = split_games(games, args.seed, args.train_ratio, args.val_ratio)

    print(f"Games fetched: {len(games)}")
    print(f"Train games : {len(train_games)}")
    print(f"Val games   : {len(val_games)}")
    print(f"Test games  : {len(test_games)}")

    train = materialize_split(train_games, args.min_moves)
    val = materialize_split(val_games, args.min_moves)
    test = materialize_split(test_games, args.min_moves)

    save_split(args.output_dir, "train", *train)
    save_split(args.output_dir, "val", *val)
    save_split(args.output_dir, "test", *test)

    meta = {
        "rows": args.rows,
        "cols": args.cols,
        "modes": [m.strip() for m in args.modes.split(",") if m.strip()],
        "statuses": [s.strip() for s in args.statuses.split(",") if s.strip()],
        "min_moves": args.min_moves,
        "games_total": len(games),
        "games_train": len(train_games),
        "games_val": len(val_games),
        "games_test": len(test_games),
        "samples_train": int(train[0].shape[0]),
        "samples_val": int(val[0].shape[0]),
        "samples_test": int(test[0].shape[0]),
        "label_map": {"loss": LABEL_LOSS, "draw": LABEL_DRAW, "win": LABEL_WIN},
    }
    with open(os.path.join(args.output_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"[OK] Saved metadata: {os.path.join(args.output_dir, 'meta.json')}")


if __name__ == "__main__":
    main()
