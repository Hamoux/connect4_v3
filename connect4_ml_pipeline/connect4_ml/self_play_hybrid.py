from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import torch

from ai import MinimaxAI
from model import Connect4PolicyValueNet

LABEL_LOSS = 0
LABEL_DRAW = 1
LABEL_WIN = 2
FORCED_WIN_SCORE = 9_000_000
FORCED_LOSS_SCORE = -9_000_000


@dataclass
class PositionRecord:
    x: np.ndarray
    policy: int
    player_to_move: int
    valid_mask: np.ndarray


@dataclass
class MoveDecision:
    move: int
    ml_move: int
    reason: str
    mm_best_move: int
    mm_best_score: int
    ml_score: int


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate hybrid self-play dataset for Connect4 model")
    p.add_argument("--model", required=True, help="Path to best_model.pt")
    p.add_argument("--games", type=int, default=1000, help="Number of hybrid self-play games")
    p.add_argument("--rows", type=int, default=9)
    p.add_argument("--cols", type=int, default=9)
    p.add_argument("--connect-n", type=int, default=4)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--output-dir", default="./selfplay_data")
    p.add_argument("--save-prefix", default="selfplay_hybrid")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--top-k", type=int, default=3, help="Sample among top-k safe legal moves during exploration")
    p.add_argument("--temperature", type=float, default=1.0, help="Sampling temperature during exploration")
    p.add_argument("--explore-plies", type=int, default=8, help="Explore for the first N plies, then guarded greedy")
    p.add_argument("--minimax-depth", type=int, default=7, help="Minimax depth used as tactical guard")
    p.add_argument("--log-every", type=int, default=25)
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


def valid_cols_np(board: np.ndarray) -> List[int]:
    return [c for c in range(board.shape[1]) if board[0, c] == 0]


def valid_mask(board: np.ndarray) -> np.ndarray:
    mask = np.zeros(board.shape[1], dtype=np.float32)
    for c in valid_cols_np(board):
        mask[c] = 1.0
    return mask


def drop_piece_np(board: np.ndarray, col: int, player: int) -> int:
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
    for c in valid_cols_np(board):
        tmp = board.copy()
        r = drop_piece_np(tmp, c, player)
        if r >= 0 and winning_line_exists(tmp, r, c, player, connect_n):
            return c
    return None


def board_np_to_mm(board: np.ndarray) -> List[List[object]]:
    out: List[List[object]] = []
    for r in range(board.shape[0]):
        row: List[object] = []
        for c in range(board.shape[1]):
            v = int(board[r, c])
            if v == 1:
                row.append("R")
            elif v == 2:
                row.append("J")
            else:
                row.append(0)
        out.append(row)
    return out


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


def score_all_minimax_moves(
    minimax_ai: MinimaxAI,
    board_np: np.ndarray,
    current_player_num: int,
    depth: int,
) -> Tuple[List[int], dict[int, int]]:
    current_player = "R" if current_player_num == 1 else "J"
    mm_board = board_np_to_mm(board_np)
    minimax_ai.clear_cache()
    valid = minimax_ai.valid_cols(mm_board)
    if not valid:
        return [], {}

    scores: dict[int, int] = {}
    ordered = minimax_ai.ordered_valid_cols(mm_board, current_player, True)
    for col in ordered:
        r = minimax_ai.next_open_row(mm_board, col)
        if r is None:
            continue
        mm_board[r][col] = current_player
        score = minimax_ai.minimax(
            mm_board,
            depth - 1,
            -10**18,
            10**18,
            False,
            current_player,
        )
        mm_board[r][col] = 0
        scores[col] = int(score)
    return valid, scores


def choose_hybrid_move(
    model: Connect4PolicyValueNet,
    minimax_ai: MinimaxAI,
    board_np: np.ndarray,
    player_num: int,
    device: str,
    ply: int,
    explore_plies: int,
    top_k: int,
    temperature: float,
    connect_n: int,
    minimax_depth: int,
) -> MoveDecision:
    legal = valid_cols_np(board_np)
    if not legal:
        raise RuntimeError("No legal moves")

    # Hard tactical guards first.
    win_now = immediate_winning_move(board_np, player_num, connect_n)
    if win_now is not None:
        return MoveDecision(win_now, win_now, "win_now", win_now, FORCED_WIN_SCORE, FORCED_WIN_SCORE)

    opp = 3 - player_num
    block_now = immediate_winning_move(board_np, opp, connect_n)
    if block_now is not None:
        return MoveDecision(block_now, block_now, "block_now", block_now, 0, 0)

    x = torch.from_numpy(board_to_channels(board_np, player_num)).unsqueeze(0).to(device)
    with torch.no_grad():
        policy_logits, _ = model(x)
    logits = policy_logits[0].detach().cpu().numpy().astype(np.float64)
    for c in range(board_np.shape[1]):
        if c not in legal:
            logits[c] = -1e18

    ml_move = int(np.argmax(logits))
    valid, mm_scores = score_all_minimax_moves(minimax_ai, board_np, player_num, minimax_depth)
    if not valid:
        return MoveDecision(ml_move, ml_move, "ml_no_mm", ml_move, 0, 0)

    best_mm_move = max(valid, key=lambda c: mm_scores[c])
    best_mm_score = mm_scores[best_mm_move]
    ml_score = mm_scores.get(ml_move, -10**18)
    winning_moves = [c for c in valid if mm_scores[c] >= FORCED_WIN_SCORE]
    safe_moves = [c for c in valid if mm_scores[c] > FORCED_LOSS_SCORE]

    if winning_moves and ml_move not in winning_moves:
        chosen = max(winning_moves, key=lambda c: mm_scores[c])
        return MoveDecision(chosen, ml_move, "override_forced_win", best_mm_move, best_mm_score, ml_score)

    if ml_score <= FORCED_LOSS_SCORE and safe_moves:
        chosen = max(safe_moves, key=lambda c: mm_scores[c])
        return MoveDecision(chosen, ml_move, "override_forced_loss", best_mm_move, best_mm_score, ml_score)

    # Controlled exploration, but only among minimax-safe moves when possible.
    if ply < explore_plies:
        explore_pool = safe_moves if safe_moves else valid
        sampled = softmax_sample(logits, explore_pool, top_k=top_k, temperature=temperature)
        sampled_score = mm_scores.get(sampled, ml_score)
        # Avoid sampling an obviously losing move if a safe move exists.
        if sampled_score > FORCED_LOSS_SCORE or not safe_moves:
            return MoveDecision(sampled, ml_move, "safe_explore", best_mm_move, best_mm_score, ml_score)

    return MoveDecision(ml_move, ml_move, "keep_ml", best_mm_move, best_mm_score, ml_score)


def value_label_from_outcome(winner: int, player_to_move: int) -> int:
    if winner == 0:
        return LABEL_DRAW
    if winner == player_to_move:
        return LABEL_WIN
    return LABEL_LOSS


def play_one_game(
    model: Connect4PolicyValueNet,
    minimax_ai: MinimaxAI,
    rows: int,
    cols: int,
    connect_n: int,
    device: str,
    top_k: int,
    temperature: float,
    explore_plies: int,
    minimax_depth: int,
    starting_player: Optional[int] = None,
) -> tuple[list[dict], dict, dict]:
    board = np.zeros((rows, cols), dtype=np.int8)
    player = starting_player if starting_player in (1, 2) else random.choice([1, 2])
    records: list[PositionRecord] = []
    move_sequence: list[int] = []
    decision_reasons: list[str] = []
    winner = 0
    ply = 0
    stats = {
        "keep_ml": 0,
        "safe_explore": 0,
        "override_forced_win": 0,
        "override_forced_loss": 0,
        "win_now": 0,
        "block_now": 0,
    }

    while True:
        legal = valid_cols_np(board)
        if not legal:
            winner = 0
            break

        x = board_to_channels(board, player)
        mask = valid_mask(board)
        decision = choose_hybrid_move(
            model=model,
            minimax_ai=minimax_ai,
            board_np=board,
            player_num=player,
            device=device,
            ply=ply,
            explore_plies=explore_plies,
            top_k=top_k,
            temperature=temperature,
            connect_n=connect_n,
            minimax_depth=minimax_depth,
        )
        stats[decision.reason] = stats.get(decision.reason, 0) + 1
        records.append(PositionRecord(x=x, policy=decision.move, player_to_move=player, valid_mask=mask))
        row = drop_piece_np(board, decision.move, player)
        if row < 0:
            raise RuntimeError(f"Illegal self-play move generated: col={decision.move}")
        move_sequence.append(decision.move + 1)
        decision_reasons.append(decision.reason)

        if winning_line_exists(board, row, decision.move, player, connect_n):
            winner = player
            break

        if not valid_cols_np(board):
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
        "decision_reasons": decision_reasons,
    }
    return result_records, game_info, stats


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


def fmt_eta(seconds: float) -> str:
    if seconds <= 0 or math.isinf(seconds) or math.isnan(seconds):
        return "0s"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def main() -> None:
    args = parse_args()
    set_seed(args.seed)
    os.makedirs(args.output_dir, exist_ok=True)

    ckpt = torch.load(args.model, map_location="cpu")
    channels = int(ckpt.get("channels", 64))
    blocks = int(ckpt.get("blocks", 4))
    rows = int(ckpt.get("rows", args.rows))
    cols = int(ckpt.get("cols", args.cols))

    if rows != args.rows or cols != args.cols:
        print(f"[WARN] Checkpoint board size is {rows}x{cols}; overriding CLI size {args.rows}x{args.cols}")

    model = Connect4PolicyValueNet(rows=rows, cols=cols, channels=channels, num_blocks=blocks)
    model.load_state_dict(ckpt["model_state"])
    model.to(args.device)
    model.eval()

    minimax_ai = MinimaxAI(rows, cols)

    print(
        f"[INFO] Hybrid self-play start | games={args.games} | device={args.device} | "
        f"board={rows}x{cols} | minimax_depth={args.minimax_depth} | "
        f"explore_plies={args.explore_plies} | top_k={args.top_k} | temperature={args.temperature}"
    )

    xs: list[np.ndarray] = []
    policies: list[int] = []
    values: list[int] = []
    masks: list[np.ndarray] = []
    game_rows: list[str] = []

    wins_r = 0
    wins_y = 0
    draws = 0
    total_plies = 0
    aggregate_stats = {
        "keep_ml": 0,
        "safe_explore": 0,
        "override_forced_win": 0,
        "override_forced_loss": 0,
        "win_now": 0,
        "block_now": 0,
    }

    t0 = time.time()
    for g in range(1, args.games + 1):
        starting_player = 1 if g % 2 == 1 else 2
        recs, game_info, game_stats = play_one_game(
            model=model,
            minimax_ai=minimax_ai,
            rows=rows,
            cols=cols,
            connect_n=args.connect_n,
            device=args.device,
            top_k=args.top_k,
            temperature=args.temperature,
            explore_plies=args.explore_plies,
            minimax_depth=args.minimax_depth,
            starting_player=starting_player,
        )

        for rec in recs:
            xs.append(rec["x"])
            policies.append(rec["policy"])
            values.append(rec["value"])
            masks.append(rec["valid_mask"])

        winner = int(game_info["winner"])
        if winner == 1:
            wins_r += 1
        elif winner == 2:
            wins_y += 1
        else:
            draws += 1
        total_plies += int(game_info["plies"])

        for k, v in game_stats.items():
            aggregate_stats[k] = aggregate_stats.get(k, 0) + int(v)

        if args.save_games_jsonl:
            game_rows.append(json.dumps(game_info, ensure_ascii=False))

        if g % args.log_every == 0 or g == args.games:
            elapsed = max(1e-9, time.time() - t0)
            games_per_sec = g / elapsed
            samples_per_sec = len(xs) / elapsed
            eta = (args.games - g) / games_per_sec if games_per_sec > 0 else math.inf
            avg_len = total_plies / max(1, g)
            print(
                f"[SELFPLAY-HYBRID] games={g}/{args.games} | samples={len(xs)} | "
                f"R/J/D={wins_r}/{wins_y}/{draws} | avg_len={avg_len:.2f} | "
                f"keep_ml={aggregate_stats['keep_ml']} | explore={aggregate_stats['safe_explore']} | "
                f"owin={aggregate_stats['override_forced_win']} | oloss={aggregate_stats['override_forced_loss']} | "
                f"games/s={games_per_sec:.2f} | samples/s={samples_per_sec:.1f} | eta={fmt_eta(eta)}"
            )

    npz_path = save_npz(args.output_dir, args.save_prefix, xs, policies, values, masks)
    meta = {
        "games": args.games,
        "samples": len(xs),
        "rows": rows,
        "cols": cols,
        "connect_n": args.connect_n,
        "device": args.device,
        "model": os.path.abspath(args.model),
        "minimax_depth": args.minimax_depth,
        "explore_plies": args.explore_plies,
        "top_k": args.top_k,
        "temperature": args.temperature,
        "wins_r": wins_r,
        "wins_y": wins_y,
        "draws": draws,
        "avg_game_len": total_plies / max(1, args.games),
        "decision_stats": aggregate_stats,
    }
    meta_path = os.path.join(args.output_dir, f"{args.save_prefix}_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    games_jsonl_path = None
    if args.save_games_jsonl:
        games_jsonl_path = os.path.join(args.output_dir, f"{args.save_prefix}_games.jsonl")
        with open(games_jsonl_path, "w", encoding="utf-8") as f:
            for row in game_rows:
                f.write(row + "\n")

    print(f"[OK] Saved hybrid self-play dataset: {npz_path}")
    print(f"[OK] Saved hybrid self-play metadata: {meta_path}")
    if games_jsonl_path is not None:
        print(f"[OK] Saved hybrid played games JSONL: {games_jsonl_path}")
    print(
        f"[SUMMARY] samples={len(xs)} | R/J/D={wins_r}/{wins_y}/{draws} | "
        f"avg_len={meta['avg_game_len']:.2f} | decision_stats={aggregate_stats}"
    )


if __name__ == "__main__":
    main()
