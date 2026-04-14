from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import torch

from model import Connect4PolicyValueNet

LABEL_LOSS = 0
LABEL_DRAW = 1
LABEL_WIN = 2


@dataclass
class PositionRecord:
    x: np.ndarray
    policy: int
    player_to_move: int
    valid_mask: np.ndarray


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate self-play dataset for Connect4 model")
    p.add_argument("--model", required=True, help="Path to best_model.pt")
    p.add_argument("--games", type=int, default=1000, help="Number of self-play games")
    p.add_argument("--rows", type=int, default=9)
    p.add_argument("--cols", type=int, default=9)
    p.add_argument("--connect-n", type=int, default=4)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--output-dir", default="./selfplay_data")
    p.add_argument("--save-prefix", default="selfplay")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--top-k", type=int, default=3, help="Sample among top-k legal moves during exploration")
    p.add_argument("--temperature", type=float, default=0.3, help="Sampling temperature during exploration")
    p.add_argument("--explore-plies", type=int, default=4, help="Explore for the first N plies, then greedy")
    p.add_argument("--log-every", type=int, default=50)
    p.add_argument("--save-games-jsonl", action="store_true", help="Also save played games as JSONL")
    return p.parse_args()


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def board_to_channels(board: np.ndarray, player_to_move: int) -> np.ndarray:
    own = (board == player_to_move).astype(np.float32)
    opp = (board == (3 - player_to_move)).astype(np.float32)
    return np.stack([own, opp], axis=0)


def valid_cols(board: np.ndarray) -> List[int]:
    return [c for c in range(board.shape[1]) if board[0, c] == 0]


def valid_mask(board: np.ndarray) -> np.ndarray:
    mask = np.zeros(board.shape[1], dtype=np.float32)
    for c in valid_cols(board):
        mask[c] = 1.0
    return mask


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


def immediate_winning_move(board: np.ndarray, player: int, connect_n: int) -> Optional[int]:
    for c in valid_cols(board):
        tmp = board.copy()
        r = drop_piece(tmp, c, player)
        if r >= 0 and winning_line_exists(tmp, r, c, player, connect_n):
            return c
    return None


def softmax_sample(logits: np.ndarray, legal_cols: List[int], top_k: int, temperature: float) -> int:
    legal_logits = np.array([logits[c] for c in legal_cols], dtype=np.float64)

    if temperature <= 0:
        return legal_cols[int(np.argmax(legal_logits))]

    order = np.argsort(-legal_logits)
    k = max(1, min(top_k, len(legal_cols)))
    chosen_idx = order[:k]
    chosen_cols = [legal_cols[i] for i in chosen_idx]
    chosen_logits = legal_logits[chosen_idx] / temperature
    chosen_logits = chosen_logits - np.max(chosen_logits)
    probs = np.exp(chosen_logits)
    probs = probs / probs.sum()
    picked = np.random.choice(len(chosen_cols), p=probs)
    return int(chosen_cols[int(picked)])


def choose_model_move(
    model: Connect4PolicyValueNet,
    board: np.ndarray,
    player: int,
    device: str,
    ply: int,
    explore_plies: int,
    top_k: int,
    temperature: float,
    connect_n: int,
) -> int:
    legal = valid_cols(board)
    if not legal:
        raise RuntimeError("No legal moves")

    # Tactical hard guards to avoid many stupid self-play blunders.
    win_now = immediate_winning_move(board, player, connect_n)
    if win_now is not None:
        return win_now

    opp = 3 - player
    block_now = immediate_winning_move(board, opp, connect_n)
    if block_now is not None:
        return block_now

    x = torch.from_numpy(board_to_channels(board, player)).unsqueeze(0).to(device)
    with torch.no_grad():
        policy_logits, _ = model(x)
    logits = policy_logits[0].detach().cpu().numpy().astype(np.float64)

    for c in range(board.shape[1]):
        if c not in legal:
            logits[c] = -1e18

    if ply < explore_plies:
        return softmax_sample(logits, legal, top_k=top_k, temperature=temperature)
    return int(np.argmax(logits))


def value_label_from_outcome(winner: int, player_to_move: int) -> int:
    if winner == 0:
        return LABEL_DRAW
    if winner == player_to_move:
        return LABEL_WIN
    return LABEL_LOSS


def play_one_game(
    model: Connect4PolicyValueNet,
    rows: int,
    cols: int,
    connect_n: int,
    device: str,
    top_k: int,
    temperature: float,
    explore_plies: int,
    starting_player: Optional[int] = None,
) -> tuple[list[PositionRecord], dict]:
    board = np.zeros((rows, cols), dtype=np.int8)
    player = starting_player if starting_player in (1, 2) else random.choice([1, 2])
    records: list[PositionRecord] = []
    move_sequence: list[int] = []
    winner = 0
    ply = 0

    while True:
        legal = valid_cols(board)
        if not legal:
            winner = 0
            break

        x = board_to_channels(board, player)
        mask = valid_mask(board)
        move = choose_model_move(
            model=model,
            board=board,
            player=player,
            device=device,
            ply=ply,
            explore_plies=explore_plies,
            top_k=top_k,
            temperature=temperature,
            connect_n=connect_n,
        )
        records.append(PositionRecord(x=x, policy=move, player_to_move=player, valid_mask=mask))
        row = drop_piece(board, move, player)
        if row < 0:
            raise RuntimeError(f"Illegal self-play move generated: col={move}")
        move_sequence.append(move + 1)  # 1-based for readability / DB-style signatures

        if winning_line_exists(board, row, move, player, connect_n):
            winner = player
            break

        if not valid_cols(board):
            winner = 0
            break

        player = 3 - player
        ply += 1

    result_records = []
    for rec in records:
        result_records.append(
            {
                "x": rec.x,
                "policy": rec.policy,
                "value": value_label_from_outcome(winner, rec.player_to_move),
                "valid_mask": rec.valid_mask,
            }
        )

    game_info = {
        "winner": winner,
        "moves": move_sequence,
        "plies": len(move_sequence),
        "starting_player": records[0].player_to_move if records else starting_player,
    }
    return result_records, game_info


def save_npz(output_dir: str, prefix: str, xs, policies, values, masks) -> str:
    path = os.path.join(output_dir, f"{prefix}.npz")
    np.savez_compressed(
        path,
        x=np.stack(xs).astype(np.float32),
        policy=np.asarray(policies, dtype=np.int64),
        value=np.asarray(values, dtype=np.int64),
        valid_mask=np.stack(masks).astype(np.float32),
    )
    return path


def main() -> None:
    args = parse_args()
    set_seed(args.seed)
    os.makedirs(args.output_dir, exist_ok=True)

    ckpt = torch.load(args.model, map_location="cpu")
    model = Connect4PolicyValueNet(
        rows=args.rows,
        cols=args.cols,
        channels=ckpt.get("channels", 64),
        num_blocks=ckpt.get("blocks", 4),
    )
    model.load_state_dict(ckpt["model_state"])
    model.to(args.device)
    model.eval()

    xs: list[np.ndarray] = []
    policies: list[int] = []
    values: list[int] = []
    masks: list[np.ndarray] = []
    games_jsonl = []

    wins_r = 0
    wins_y = 0
    draws = 0
    total_plies = 0
    start = time.time()

    print(
        f"[INFO] Self-play start | games={args.games} | device={args.device} | "
        f"explore_plies={args.explore_plies} | top_k={args.top_k} | temperature={args.temperature}"
    )

    for game_idx in range(1, args.games + 1):
        records, game_info = play_one_game(
            model=model,
            rows=args.rows,
            cols=args.cols,
            connect_n=args.connect_n,
            device=args.device,
            top_k=args.top_k,
            temperature=args.temperature,
            explore_plies=args.explore_plies,
            starting_player=1 if game_idx % 2 == 1 else 2,
        )

        for rec in records:
            xs.append(rec["x"])
            policies.append(rec["policy"])
            values.append(rec["value"])
            masks.append(rec["valid_mask"])

        winner = game_info["winner"]
        if winner == 1:
            wins_r += 1
        elif winner == 2:
            wins_y += 1
        else:
            draws += 1

        total_plies += game_info["plies"]
        if args.save_games_jsonl:
            games_jsonl.append(game_info)

        if game_idx % max(1, args.log_every) == 0 or game_idx == args.games:
            elapsed = max(1e-9, time.time() - start)
            gps = game_idx / elapsed
            sps = len(xs) / elapsed
            avg_len = total_plies / game_idx
            remain_games = args.games - game_idx
            eta_sec = remain_games / max(1e-9, gps)
            eta_min = int(eta_sec // 60)
            eta_s = int(eta_sec % 60)
            print(
                f"[SELFPLAY] games={game_idx}/{args.games} | samples={len(xs)} | "
                f"R/Y/D={wins_r}/{wins_y}/{draws} | avg_len={avg_len:.1f} | "
                f"games/s={gps:.2f} | samples/s={sps:.1f} | eta={eta_min}m{eta_s:02d}s"
            )

    if not xs:
        raise RuntimeError("No self-play samples generated")

    npz_path = save_npz(args.output_dir, args.save_prefix, xs, policies, values, masks)

    meta = {
        "model": os.path.abspath(args.model),
        "games": args.games,
        "rows": args.rows,
        "cols": args.cols,
        "connect_n": args.connect_n,
        "samples": len(xs),
        "wins_r": wins_r,
        "wins_y": wins_y,
        "draws": draws,
        "avg_game_len": total_plies / max(1, args.games),
        "seed": args.seed,
        "top_k": args.top_k,
        "temperature": args.temperature,
        "explore_plies": args.explore_plies,
        "label_map": {"loss": LABEL_LOSS, "draw": LABEL_DRAW, "win": LABEL_WIN},
    }
    meta_path = os.path.join(args.output_dir, f"{args.save_prefix}_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    if args.save_games_jsonl:
        jsonl_path = os.path.join(args.output_dir, f"{args.save_prefix}_games.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for item in games_jsonl:
                f.write(json.dumps(item) + "\n")
        print(f"[OK] Saved games JSONL: {jsonl_path}")

    print(f"[OK] Saved self-play dataset: {npz_path}")
    print(f"[OK] Saved metadata: {meta_path}")
    print(
        f"[DONE] games={args.games} | samples={len(xs)} | R/Y/D={wins_r}/{wins_y}/{draws} | "
        f"avg_len={total_plies / max(1, args.games):.2f}"
    )


if __name__ == "__main__":
    main()
