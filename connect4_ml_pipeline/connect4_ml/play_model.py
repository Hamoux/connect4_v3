from __future__ import annotations

import argparse
from typing import List

import numpy as np
import torch

from model import Connect4PolicyValueNet


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Play with trained Connect4 model")
    p.add_argument("--model", required=True)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    return p.parse_args()



def print_board(board: np.ndarray) -> None:
    symbols = {0: ".", 1: "R", 2: "Y"}
    print()
    for r in range(board.shape[0]):
        print(" ".join(symbols[int(x)] for x in board[r]))
    print("1 2 3 4 5 6 7 8 9")
    print()



def drop_piece(board: np.ndarray, col: int, player: int) -> int:
    for r in range(board.shape[0] - 1, -1, -1):
        if board[r, col] == 0:
            board[r, col] = player
            return r
    return -1



def board_to_channels(board: np.ndarray, player_to_move: int) -> np.ndarray:
    own = (board == player_to_move).astype(np.float32)
    opp = (board == (3 - player_to_move)).astype(np.float32)
    return np.stack([own, opp], axis=0)



def valid_cols(board: np.ndarray) -> List[int]:
    return [c for c in range(board.shape[1]) if board[0, c] == 0]



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



def choose_model_move(model, board: np.ndarray, player: int, device: str) -> int:
    x = torch.from_numpy(board_to_channels(board, player)).unsqueeze(0).to(device)
    with torch.no_grad():
        policy_logits, value_logits = model(x)
    logits = policy_logits[0].detach().cpu().numpy()
    for c in range(board.shape[1]):
        if board[0, c] != 0:
            logits[c] = -1e9
    return int(np.argmax(logits))



def main() -> None:
    args = parse_args()
    ckpt = torch.load(args.model, map_location="cpu")
    model = Connect4PolicyValueNet(channels=ckpt["channels"], num_blocks=ckpt["blocks"])
    model.load_state_dict(ckpt["model_state"])
    model.to(args.device)
    model.eval()

    board = np.zeros((9, 9), dtype=np.int8)
    human = 1  # R
    ai = 2     # Y
    player = 1

    while True:
        print_board(board)
        if not valid_cols(board):
            print("Draw.")
            break

        if player == human:
            valid = valid_cols(board)
            raw = input(f"Your move {valid} (1-9): ").strip()
            try:
                col = int(raw) - 1
            except Exception:
                print("Invalid input")
                continue
            if col not in valid:
                print("Illegal move")
                continue
        else:
            col = choose_model_move(model, board, ai, args.device)
            print(f"Model plays: {col + 1}")

        row = drop_piece(board, col, player)
        if winning_line_exists(board, row, col, player):
            print_board(board)
            print("You win!" if player == human else "Model wins!")
            break

        player = 3 - player


if __name__ == "__main__":
    main()
