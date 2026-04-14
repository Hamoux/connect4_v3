from __future__ import annotations

import argparse
import csv
import json
import os
import random
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor

LABEL_LOSS = 0
LABEL_DRAW = 1
LABEL_WIN = 2


@dataclass
class Example:
    x: np.ndarray
    policy: int
    value: int
    valid_mask: np.ndarray


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract ML dataset from PostgreSQL partie table")
    p.add_argument("--host", default=os.getenv("PGHOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.getenv("PGPORT", "5432")))
    p.add_argument("--dbname", default=os.getenv("PGDATABASE", "Connect4DB"))
    p.add_argument("--user", default=os.getenv("PGUSER", "postgres"))
    p.add_argument("--password", default=os.getenv("PGPASSWORD", ""))
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
    p.add_argument(
        "--bad-games-csv",
        default=None,
        help="Optional CSV path to store skipped/corrupted games. Default: <output-dir>/bad_games.csv",
    )
    p.add_argument(
        "--allow-post-win-moves",
        action="store_true",
        help="Do not reject games that continue after a winning move was already reached",
    )
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


def normalize_status(status: Optional[str]) -> str:
    return (status or "").strip().lower()


def normalize_color(color: Optional[str]) -> Optional[str]:
    if color is None:
        return None
    c = color.strip().upper()
    return c or None


def decode_signature(sig: str, cols: int) -> List[int]:
    sig = (sig or "").strip()
    if not sig:
        raise ValueError("Empty signature")

    # Format A: comma-separated moves, e.g. "3,7,6,6,5"
    if "," in sig:
        parts = [p.strip() for p in sig.split(",") if p.strip()]
        if not parts:
            raise ValueError("Empty comma-separated signature")
        moves: List[int] = []
        for token in parts:
            if not token.isdigit():
                raise ValueError(f"Invalid token in comma-separated signature: {token!r}")
            col_1_based = int(token)
            if not (1 <= col_1_based <= cols):
                raise ValueError(f"Column {col_1_based} out of range for {cols} cols")
            moves.append(col_1_based - 1)
        return moves

    # Format B: compact digits, e.g. "376654..."
    moves = []
    for ch in sig:
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


def board_to_channels(board: np.ndarray, player_to_move: int) -> np.ndarray:
    own = (board == player_to_move).astype(np.float32)
    opp = (board == (3 - player_to_move)).astype(np.float32)
    return np.stack([own, opp], axis=0)


def valid_moves_mask(board: np.ndarray) -> np.ndarray:
    return (board[0] == 0).astype(np.float32)


def outcome_label_from_side_to_move(final_status: str, final_winner_color: Optional[str], player_to_move: int) -> int:
    status = normalize_status(final_status)
    winner_color = normalize_color(final_winner_color)

    if status in {"nulle", "draw"}:
        return LABEL_DRAW
    if winner_color is None:
        return LABEL_DRAW

    winner_player = 1 if winner_color == "R" else 2
    return LABEL_WIN if winner_player == player_to_move else LABEL_LOSS


def validate_final_winner(status: str, winner_color: Optional[str], observed_winner_player: Optional[int]) -> None:
    status_n = normalize_status(status)
    winner_color_n = normalize_color(winner_color)

    if status_n in {"nulle", "draw"}:
        if observed_winner_player is not None:
            raise ValueError("Game marked as draw but a winning line was reconstructed")
        return

    if winner_color_n is None:
        if observed_winner_player is not None:
            raise ValueError("Winner missing in DB but reconstructed board has a winner")
        return

    expected_winner_player = 1 if winner_color_n == "R" else 2
    if observed_winner_player is None:
        raise ValueError("DB says there is a winner but no winning line was reconstructed")
    if observed_winner_player != expected_winner_player:
        raise ValueError(
            f"Winner mismatch: DB winner={winner_color_n} but reconstructed winner player={observed_winner_player}"
        )


def build_examples_from_game(
    signature: str,
    joueur_depart: str,
    joueur_gagnant: Optional[str],
    status: str,
    rows: int,
    cols: int,
    min_moves: int,
    allow_post_win_moves: bool,
) -> List[Example]:
    moves = decode_signature(signature, cols)
    if len(moves) < min_moves:
        return []

    start_color = normalize_color(joueur_depart)
    if start_color not in {"R", "Y"}:
        raise ValueError(f"Invalid joueur_depart: {joueur_depart!r}")

    board = np.zeros((rows, cols), dtype=np.int8)
    current_player = 1 if start_color == "R" else 2
    examples: List[Example] = []
    observed_winner_player: Optional[int] = None

    for ply_index, next_col in enumerate(moves):
        if observed_winner_player is not None and not allow_post_win_moves:
            raise ValueError(f"Game contains moves after a winning move at ply={ply_index}")

        x = board_to_channels(board, current_player)
        valid = valid_moves_mask(board)
        if valid[next_col] <= 0:
            raise ValueError(f"Illegal move before drop: column {next_col + 1} is already full")

        value = outcome_label_from_side_to_move(status, joueur_gagnant, current_player)
        examples.append(Example(x=x, policy=next_col, value=value, valid_mask=valid))

        played_row = drop_piece(board, next_col, current_player)
        if played_row == -1:
            raise ValueError(f"Illegal reconstructed move in signature {signature}: col={next_col + 1}")

        if winning_line_exists(board, played_row, next_col, current_player):
            observed_winner_player = current_player

        current_player = 3 - current_player

    validate_final_winner(status, joueur_gagnant, observed_winner_player)
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


def materialize_split(
    games: Sequence[dict],
    min_moves: int,
    allow_post_win_moves: bool,
    bad_game_records: List[dict],
    split_name: str,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int, int]:
    xs: List[np.ndarray] = []
    ps: List[int] = []
    vs: List[int] = []
    masks: List[np.ndarray] = []
    kept_games = 0
    skipped_games = 0

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
                allow_post_win_moves=allow_post_win_moves,
            )
        except Exception as exc:
            skipped_games += 1
            bad_game_records.append(
                {
                    "split": split_name,
                    "id_partie": g.get("id_partie"),
                    "mode": g.get("mode"),
                    "status": g.get("status"),
                    "joueur_depart": g.get("joueur_depart"),
                    "joueur_gagnant": g.get("joueur_gagnant"),
                    "rows": g.get("rows"),
                    "cols": g.get("cols"),
                    "signature": g.get("signature"),
                    "error": str(exc),
                }
            )
            print(f"[WARN] Skip game id={g['id_partie']} due to reconstruction error: {exc}")
            continue

        if not examples:
            continue

        kept_games += 1
        for ex in examples:
            xs.append(ex.x)
            ps.append(ex.policy)
            vs.append(ex.value)
            masks.append(ex.valid_mask)

    if not xs:
        raise RuntimeError(f"No examples built for split={split_name}")

    x_arr = np.stack(xs).astype(np.float32)
    p_arr = np.asarray(ps, dtype=np.int64)
    v_arr = np.asarray(vs, dtype=np.int64)
    m_arr = np.stack(masks).astype(np.float32)
    return x_arr, p_arr, v_arr, m_arr, kept_games, skipped_games


def save_split(output_dir: str, name: str, x: np.ndarray, p: np.ndarray, v: np.ndarray, m: np.ndarray):
    path = os.path.join(output_dir, f"{name}.npz")
    np.savez_compressed(path, x=x, policy=p, value=v, valid_mask=m)
    print(f"[OK] Saved {name}: {path} | samples={len(x)}")


def save_bad_games_csv(path: str, rows: List[dict]) -> None:
    fieldnames = [
        "split",
        "id_partie",
        "mode",
        "status",
        "joueur_depart",
        "joueur_gagnant",
        "rows",
        "cols",
        "signature",
        "error",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[OK] Saved bad games audit: {path} | rows={len(rows)}")


def main() -> None:
    args = parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    bad_games_csv = args.bad_games_csv or os.path.join(args.output_dir, "bad_games.csv")
    bad_game_records: List[dict] = []

    games = fetch_games(args)
    if not games:
        raise RuntimeError("No games found with current filters")

    train_games, val_games, test_games = split_games(games, args.seed, args.train_ratio, args.val_ratio)

    print(f"Games fetched: {len(games)}")
    print(f"Train games : {len(train_games)}")
    print(f"Val games   : {len(val_games)}")
    print(f"Test games  : {len(test_games)}")

    train = materialize_split(train_games, args.min_moves, args.allow_post_win_moves, bad_game_records, "train")
    val = materialize_split(val_games, args.min_moves, args.allow_post_win_moves, bad_game_records, "val")
    test = materialize_split(test_games, args.min_moves, args.allow_post_win_moves, bad_game_records, "test")

    save_split(args.output_dir, "train", train[0], train[1], train[2], train[3])
    save_split(args.output_dir, "val", val[0], val[1], val[2], val[3])
    save_split(args.output_dir, "test", test[0], test[1], test[2], test[3])

    save_bad_games_csv(bad_games_csv, bad_game_records)

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
        "games_kept_train": train[4],
        "games_kept_val": val[4],
        "games_kept_test": test[4],
        "games_skipped_train": train[5],
        "games_skipped_val": val[5],
        "games_skipped_test": test[5],
        "samples_train": int(train[0].shape[0]),
        "samples_val": int(val[0].shape[0]),
        "samples_test": int(test[0].shape[0]),
        "label_map": {"loss": LABEL_LOSS, "draw": LABEL_DRAW, "win": LABEL_WIN},
        "bad_games_csv": bad_games_csv,
    }
    with open(os.path.join(args.output_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"[OK] Saved metadata: {os.path.join(args.output_dir, 'meta.json')}")


if __name__ == "__main__":
    main()
