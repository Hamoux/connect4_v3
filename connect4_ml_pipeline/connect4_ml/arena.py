from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch

from model import Connect4PolicyValueNet


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Arena: model vs model for Connect4")
    p.add_argument("--model-a", required=True, help="Path to checkpoint A")
    p.add_argument("--model-b", required=True, help="Path to checkpoint B")
    p.add_argument("--games", type=int, default=200)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--batch-size", type=int, default=1, help="Reserved for future use; arena plays sequentially")
    p.add_argument("--log-every", type=int, default=20)
    p.add_argument("--explore-plies", type=int, default=0, help="Use stochastic sampling for first N plies")
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--temperature", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--save-json", default="", help="Optional path to save match details JSON")
    return p.parse_args()


class LoadedModel:
    def __init__(self, path: str, device: str):
        self.path = str(path)
        ckpt = torch.load(path, map_location="cpu")
        self.rows = int(ckpt.get("rows", 9))
        self.cols = int(ckpt.get("cols", 9))
        channels = int(ckpt.get("channels", 64))
        blocks = int(ckpt.get("blocks", 4))
        self.model = Connect4PolicyValueNet(rows=self.rows, cols=self.cols, channels=channels, num_blocks=blocks)
        self.model.load_state_dict(ckpt["model_state"])
        self.model.to(device)
        self.model.eval()
        self.device = device
        self.name = Path(path).stem



def board_to_channels(board: np.ndarray, player_to_move: int) -> np.ndarray:
    own = (board == player_to_move).astype(np.float32)
    opp = (board == (3 - player_to_move)).astype(np.float32)
    return np.stack([own, opp], axis=0)



def valid_cols(board: np.ndarray) -> List[int]:
    return [c for c in range(board.shape[1]) if board[0, c] == 0]



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



def choose_move(
    loaded: LoadedModel,
    board: np.ndarray,
    player: int,
    ply: int,
    rng: np.random.Generator,
    explore_plies: int,
    top_k: int,
    temperature: float,
) -> int:
    x = torch.from_numpy(board_to_channels(board, player)).unsqueeze(0).to(loaded.device)
    with torch.no_grad():
        policy_logits, _ = loaded.model(x)
    logits = policy_logits[0].detach().cpu().numpy().astype(np.float64)

    valid = valid_cols(board)
    if not valid:
        return 0

    invalid = np.ones(board.shape[1], dtype=bool)
    invalid[valid] = False
    logits[invalid] = -1e18

    if ply < explore_plies:
        k = max(1, min(top_k, len(valid)))
        top_idx = np.argsort(logits)[-k:][::-1]
        top_vals = logits[top_idx]
        temp = max(1e-6, float(temperature))
        probs = np.exp((top_vals - top_vals.max()) / temp)
        probs /= probs.sum()
        return int(rng.choice(top_idx, p=probs))

    return int(np.argmax(logits))



def play_one_game(
    model_r: LoadedModel,
    model_y: LoadedModel,
    rng: np.random.Generator,
    explore_plies: int,
    top_k: int,
    temperature: float,
) -> Dict[str, object]:
    if model_r.rows != model_y.rows or model_r.cols != model_y.cols:
        raise ValueError("Both checkpoints must use the same board size")

    board = np.zeros((model_r.rows, model_r.cols), dtype=np.int8)
    player = 1  # 1=R, 2=Y
    moves: List[int] = []
    ply = 0

    while True:
        valid = valid_cols(board)
        if not valid:
            return {"winner": 0, "moves": moves, "length": len(moves)}

        loaded = model_r if player == 1 else model_y
        col = choose_move(loaded, board, player, ply, rng, explore_plies, top_k, temperature)
        if col not in valid:
            col = valid[0]

        row = drop_piece(board, col, player)
        moves.append(int(col))

        if winning_line_exists(board, row, col, player):
            return {"winner": int(player), "moves": moves, "length": len(moves)}

        player = 3 - player
        ply += 1



def main() -> None:
    args = parse_args()
    rng = np.random.default_rng(args.seed)

    model_a = LoadedModel(args.model_a, args.device)
    model_b = LoadedModel(args.model_b, args.device)

    if model_a.rows != model_b.rows or model_a.cols != model_b.cols:
        raise ValueError(f"Board size mismatch: A={model_a.rows}x{model_a.cols}, B={model_b.rows}x{model_b.cols}")

    print(
        f"[INFO] Arena start | games={args.games} | device={args.device} | board={model_a.rows}x{model_a.cols} | "
        f"explore_plies={args.explore_plies} | top_k={args.top_k} | temperature={args.temperature}"
    )
    print(f"[INFO] Model A: {args.model_a}")
    print(f"[INFO] Model B: {args.model_b}")

    a_wins = 0
    b_wins = 0
    draws = 0
    total_len = 0
    match_details: List[Dict[str, object]] = []
    start = time.time()

    for i in range(args.games):
        # Alternate starting side fairly.
        if i % 2 == 0:
            result = play_one_game(model_a, model_b, rng, args.explore_plies, args.top_k, args.temperature)
            winner = result["winner"]
            if winner == 1:
                a_wins += 1
                winner_name = "A"
            elif winner == 2:
                b_wins += 1
                winner_name = "B"
            else:
                draws += 1
                winner_name = "D"
            start_side = "A_as_R"
        else:
            result = play_one_game(model_b, model_a, rng, args.explore_plies, args.top_k, args.temperature)
            winner = result["winner"]
            if winner == 1:
                b_wins += 1
                winner_name = "B"
            elif winner == 2:
                a_wins += 1
                winner_name = "A"
            else:
                draws += 1
                winner_name = "D"
            start_side = "B_as_R"

        total_len += int(result["length"])
        match_details.append({
            "game_index": i + 1,
            "start_side": start_side,
            "winner": winner_name,
            "length": int(result["length"]),
            "moves": result["moves"],
        })

        if (i + 1) % max(1, args.log_every) == 0 or (i + 1) == args.games:
            elapsed = max(1e-9, time.time() - start)
            gps = (i + 1) / elapsed
            avg_len = total_len / (i + 1)
            remaining = args.games - (i + 1)
            eta = remaining / max(1e-9, gps)
            eta_m, eta_s = divmod(int(eta), 60)
            print(
                f"[ARENA] games={i+1}/{args.games} | A/B/D={a_wins}/{b_wins}/{draws} | "
                f"A_wr={a_wins/(i+1):.3f} | B_wr={b_wins/(i+1):.3f} | avg_len={avg_len:.1f} | "
                f"games/s={gps:.2f} | eta={eta_m:02d}m{eta_s:02d}s"
            )

    final = {
        "games": args.games,
        "model_a": args.model_a,
        "model_b": args.model_b,
        "a_wins": a_wins,
        "b_wins": b_wins,
        "draws": draws,
        "a_win_rate": a_wins / args.games,
        "b_win_rate": b_wins / args.games,
        "draw_rate": draws / args.games,
        "avg_game_length": total_len / args.games,
        "board": {"rows": model_a.rows, "cols": model_a.cols},
        "explore_plies": args.explore_plies,
        "top_k": args.top_k,
        "temperature": args.temperature,
    }

    print("[RESULT] " + json.dumps(final, indent=2))

    if args.save_json:
        payload = {"summary": final, "matches": match_details}
        out = Path(args.save_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[OK] Saved arena report: {out}")


if __name__ == "__main__":
    main()
