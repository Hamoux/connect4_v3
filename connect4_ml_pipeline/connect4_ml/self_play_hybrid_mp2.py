from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import sys
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import torch

# Make connect4_v3 root importable so ai.py can be found when this script sits in
# connect4_v3/connect4_ml_pipeline/connect4_ml
_THIS = Path(__file__).resolve()
for candidate in [_THIS.parent.parent.parent, _THIS.parent.parent, _THIS.parent]:
    if (candidate / 'ai.py').exists():
        sys.path.insert(0, str(candidate))
        break

try:
    from ai import MinimaxAI
except Exception:
    # Fallback minimal copy if ai.py still isn't importable.
    class MinimaxAI:
        def __init__(self, rows, cols):
            self.rows = rows
            self.cols = cols
            self.tt = {}
        def clear_cache(self):
            self.tt.clear()
        def valid_cols(self, board):
            return [c for c in range(self.cols) if board[0][c] == 0]
        def next_open_row(self, board, col):
            for r in range(self.rows - 1, -1, -1):
                if board[r][col] == 0:
                    return r
            return None
        def winner_on_board(self, board):
            directions = [(0,1),(1,0),(1,1),(1,-1)]
            for r in range(self.rows):
                for c in range(self.cols):
                    p = board[r][c]
                    if p == 0:
                        continue
                    for dr, dc in directions:
                        cnt = 1
                        rr, cc = r+dr, c+dc
                        while 0 <= rr < self.rows and 0 <= cc < self.cols and board[rr][cc] == p:
                            cnt += 1
                            if cnt >= 4:
                                return p
                            rr += dr
                            cc += dc
            return None
        def ordered_valid_cols(self, board, ai_player, maximizing):
            valid = self.valid_cols(board)
            if not valid:
                return []
            opp = 'J' if ai_player == 'R' else 'R'
            player_to_play = ai_player if maximizing else opp
            center = self.cols // 2
            def move_score(col):
                score = -abs(col-center) * 10
                r = self.next_open_row(board, col)
                if r is None:
                    return -10**9
                board[r][col] = player_to_play
                w = self.winner_on_board(board)
                board[r][col] = 0
                if w == player_to_play:
                    score += 10**6
                return score
            valid.sort(key=move_score, reverse=True)
            return valid
        def heuristic(self, board, ai_player):
            opp = 'J' if ai_player == 'R' else 'R'
            def score_window(w):
                ai = w.count(ai_player); op = w.count(opp); empty = w.count(0)
                if ai > 0 and op > 0: return 0
                if ai == 4: return 100000
                if op == 4: return -100000
                if ai == 3 and empty == 1: return 80
                if ai == 2 and empty == 2: return 10
                if op == 3 and empty == 1: return -90
                if op == 2 and empty == 2: return -12
                return 0
            score = 0
            center = self.cols // 2
            score += 6 * [board[r][center] for r in range(self.rows)].count(ai_player)
            for r in range(self.rows):
                for c in range(self.cols - 3): score += score_window([board[r][c+i] for i in range(4)])
            for c in range(self.cols):
                for r in range(self.rows - 3): score += score_window([board[r+i][c] for i in range(4)])
            for r in range(self.rows - 3):
                for c in range(self.cols - 3): score += score_window([board[r+i][c+i] for i in range(4)])
            for r in range(3, self.rows):
                for c in range(self.cols - 3): score += score_window([board[r-i][c+i] for i in range(4)])
            return score
        def board_key(self, board, maximizing, ai_player):
            return (ai_player, maximizing, tuple(map(tuple, board)))
        def minimax(self, board, depth, alpha, beta, maximizing, ai_player):
            winner = self.winner_on_board(board)
            opp = 'J' if ai_player == 'R' else 'R'
            if winner == ai_player: return 10**7 + depth
            if winner == opp: return -10**7 - depth
            valid = self.valid_cols(board)
            if depth == 0 or not valid: return self.heuristic(board, ai_player)
            key = self.board_key(board, maximizing, ai_player)
            cached = self.tt.get(key)
            if cached is not None:
                cd, cs = cached
                if cd >= depth: return cs
            if maximizing:
                value = -10**9
                for col in self.ordered_valid_cols(board, ai_player, True):
                    r = self.next_open_row(board, col)
                    if r is None: continue
                    board[r][col] = ai_player
                    value = max(value, self.minimax(board, depth-1, alpha, beta, False, ai_player))
                    board[r][col] = 0
                    alpha = max(alpha, value)
                    if alpha >= beta: break
            else:
                value = 10**9
                for col in self.ordered_valid_cols(board, ai_player, False):
                    r = self.next_open_row(board, col)
                    if r is None: continue
                    board[r][col] = opp
                    value = min(value, self.minimax(board, depth-1, alpha, beta, True, ai_player))
                    board[r][col] = 0
                    beta = min(beta, value)
                    if alpha >= beta: break
            self.tt[key] = (depth, value)
            return value

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
    reason: str

_WORKER = {}

def _play_one_game_star(args):
    return play_one_game(*args)

def parse_args():
    p = argparse.ArgumentParser(description='Generate hybrid self-play dataset with optional multiprocessing')
    p.add_argument('--model', required=True)
    p.add_argument('--games', type=int, default=1000)
    p.add_argument('--rows', type=int, default=9)
    p.add_argument('--cols', type=int, default=9)
    p.add_argument('--connect-n', type=int, default=4)
    p.add_argument('--device', default='cpu')
    p.add_argument('--output-dir', default='./selfplay_data')
    p.add_argument('--save-prefix', default='selfplay_hybrid')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--top-k', type=int, default=3)
    p.add_argument('--temperature', type=float, default=1.0)
    p.add_argument('--explore-plies', type=int, default=8)
    p.add_argument('--minimax-depth', type=int, default=7)
    p.add_argument('--log-every', type=int, default=25)
    p.add_argument('--save-games-jsonl', action='store_true')
    p.add_argument('--workers', type=int, default=1, help='Number of worker processes. 1 = single process')
    p.add_argument('--torch-threads', type=int, default=1, help='Torch CPU threads per process. Use 1 with multiprocessing to avoid oversubscription.')
    p.add_argument('--append', action='store_true', help='Append to existing JSONL only. NPZ is always rewritten.')
    return p.parse_args()

def board_to_channels(board, player_to_move):
    own = (board == player_to_move).astype(np.float32)
    opp = (board == (3 - player_to_move)).astype(np.float32)
    return np.stack([own, opp], axis=0)

def valid_cols_np(board):
    return np.flatnonzero(board[0] == 0).tolist()

def valid_mask(board):
    return (board[0] == 0).astype(np.float32, copy=False)

def drop_piece_np(board, col, player):
    for r in range(board.shape[0]-1, -1, -1):
        if board[r, col] == 0:
            board[r, col] = player
            return r
    return -1

def winning_line_exists(board, row, col, player, connect_n=4):
    rows, cols = board.shape
    for dr, dc in ((0,1),(1,0),(1,1),(1,-1)):
        count = 1
        for sign in (1,-1):
            r, c = row + sign*dr, col + sign*dc
            while 0 <= r < rows and 0 <= c < cols and board[r, c] == player:
                count += 1
                r += sign*dr
                c += sign*dc
        if count >= connect_n:
            return True
    return False

def immediate_winning_move(board, player, connect_n):
    for c in valid_cols_np(board):
        tmp = board.copy()
        r = drop_piece_np(tmp, c, player)
        if r >= 0 and winning_line_exists(tmp, r, c, player, connect_n):
            return c
    return None

def board_np_to_mm(board):
    mapper = {0: 0, 1: 'R', 2: 'J'}
    return [[mapper[int(v)] for v in row] for row in board]

def softmax_sample(logits, legal_cols, top_k, temperature):
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

def score_all_minimax_moves(minimax_ai, board_np, current_player_num, depth):
    current_player = 'R' if current_player_num == 1 else 'J'
    mm_board = board_np_to_mm(board_np)
    valid = minimax_ai.valid_cols(mm_board)
    scores = {}
    ordered = minimax_ai.ordered_valid_cols(mm_board, current_player, True)
    for col in ordered:
        r = minimax_ai.next_open_row(mm_board, col)
        if r is None:
            continue
        mm_board[r][col] = current_player
        score = minimax_ai.minimax(mm_board, depth-1, -10**18, 10**18, False, current_player)
        mm_board[r][col] = 0
        scores[col] = int(score)
    return valid, scores

def choose_hybrid_move(model, minimax_ai, board_np, player_num, device, ply, explore_plies, top_k, temperature, connect_n, minimax_depth):
    legal = valid_cols_np(board_np)
    legal_set = set(legal)
    win_now = immediate_winning_move(board_np, player_num, connect_n)
    if win_now is not None:
        return MoveDecision(win_now, 'win_now')
    opp = 3 - player_num
    block_now = immediate_winning_move(board_np, opp, connect_n)
    if block_now is not None:
        return MoveDecision(block_now, 'block_now')
    x = torch.from_numpy(board_to_channels(board_np, player_num)).unsqueeze(0).to(device)
    inference_ctx = torch.inference_mode if hasattr(torch, 'inference_mode') else torch.no_grad
    with inference_ctx():
        policy_logits, _ = model(x)
    logits = policy_logits[0].detach().cpu().numpy().astype(np.float64)
    for c in range(board_np.shape[1]):
        if c not in legal_set:
            logits[c] = -1e18
    ml_move = int(np.argmax(logits))
    valid, mm_scores = score_all_minimax_moves(minimax_ai, board_np, player_num, minimax_depth)
    best_mm_move = max(valid, key=lambda c: mm_scores[c])
    ml_score = mm_scores.get(ml_move, -10**18)
    winning_moves = [c for c in valid if mm_scores[c] >= FORCED_WIN_SCORE]
    safe_moves = [c for c in valid if mm_scores[c] > FORCED_LOSS_SCORE]
    if winning_moves and ml_move not in winning_moves:
        return MoveDecision(max(winning_moves, key=lambda c: mm_scores[c]), 'override_forced_win')
    if ml_score <= FORCED_LOSS_SCORE and safe_moves:
        return MoveDecision(max(safe_moves, key=lambda c: mm_scores[c]), 'override_forced_loss')
    if ply < explore_plies:
        pool = safe_moves if safe_moves else valid
        sampled = softmax_sample(logits, pool, top_k, temperature)
        if mm_scores.get(sampled, ml_score) > FORCED_LOSS_SCORE or not safe_moves:
            return MoveDecision(sampled, 'safe_explore')
    return MoveDecision(ml_move, 'keep_ml')

def value_label_from_outcome(winner, player_to_move):
    if winner == 0: return LABEL_DRAW
    if winner == player_to_move: return LABEL_WIN
    return LABEL_LOSS

def init_worker(model_path, rows, cols, device, seed, torch_threads):
    global _WORKER
    if torch_threads is not None and torch_threads > 0:
        torch.set_num_threads(torch_threads)
        if hasattr(torch, 'set_num_interop_threads'):
            torch.set_num_interop_threads(max(1, min(torch_threads, 2)))
    random.seed(seed + os.getpid())
    np.random.seed(seed + os.getpid())
    torch.manual_seed(seed + os.getpid())
    ckpt = torch.load(model_path, map_location='cpu')
    channels = ckpt.get('channels', 64)
    blocks = ckpt.get('blocks', 6)
    model = Connect4PolicyValueNet(rows=rows, cols=cols, channels=channels, num_blocks=blocks)
    model.load_state_dict(ckpt['model_state'])
    model.eval().to(device)
    minimax_ai = MinimaxAI(rows, cols)
    _WORKER = {'model': model, 'minimax_ai': minimax_ai, 'device': device}

def play_one_game(game_idx, rows, cols, connect_n, explore_plies, top_k, temperature, minimax_depth):
    model = _WORKER['model']
    minimax_ai = _WORKER['minimax_ai']
    device = _WORKER['device']
    board = np.zeros((rows, cols), dtype=np.int8)
    current_player = 1 if game_idx % 2 == 0 else 2
    ply = 0
    positions = []
    moves = []
    reasons = {'win_now':0,'block_now':0,'override_forced_win':0,'override_forced_loss':0,'safe_explore':0,'keep_ml':0}
    winner = 0
    while True:
        legal = valid_cols_np(board)
        if not legal:
            winner = 0
            break
        x = board_to_channels(board, current_player)
        positions.append((x, current_player, valid_mask(board)))
        decision = choose_hybrid_move(model, minimax_ai, board.copy(), current_player, device, ply, explore_plies, top_k, temperature, connect_n, minimax_depth)
        reasons[decision.reason] = reasons.get(decision.reason, 0) + 1
        col = decision.move
        moves.append(col)
        r = drop_piece_np(board, col, current_player)
        if winning_line_exists(board, r, col, current_player, connect_n):
            winner = current_player
            break
        current_player = 3 - current_player
        ply += 1
    xs, policies, values, masks = [], [], [], []
    for x, p, vm in positions:
        xs.append(x)
        policies.append(moves[len(xs)-1])
        values.append(value_label_from_outcome(winner, p))
        masks.append(vm)
    return {
        'x': np.asarray(xs, dtype=np.float32),
        'policy': np.asarray(policies, dtype=np.int64),
        'value': np.asarray(values, dtype=np.int64),
        'valid_mask': np.asarray(masks, dtype=np.float32),
        'winner': winner,
        'num_moves': len(moves),
        'moves': moves,
        'reasons': reasons,
    }

def main():
    args = parse_args()
    os.makedirs(args.output_dir, exist_ok=True)
    out_npz = os.path.join(args.output_dir, f'{args.save_prefix}.npz')
    out_meta = os.path.join(args.output_dir, f'{args.save_prefix}_meta.json')
    out_jsonl = os.path.join(args.output_dir, f'{args.save_prefix}_games.jsonl')
    if args.save_games_jsonl and not args.append and os.path.exists(out_jsonl):
        os.remove(out_jsonl)
    print(f'[INFO] Starting hybrid self-play | games={args.games} | workers={args.workers} | device={args.device} | minimax_depth={args.minimax_depth} | torch_threads={args.torch_threads}')
    start = time.time()
    xs_all=[]; pol_all=[]; val_all=[]; mask_all=[]
    wins_r = wins_j = draws = total_moves = 0
    total_reasons = {'win_now':0,'block_now':0,'override_forced_win':0,'override_forced_loss':0,'safe_explore':0,'keep_ml':0}
    if args.workers <= 1:
        init_worker(args.model, args.rows, args.cols, args.device, args.seed, args.torch_threads)
        results_iter = (play_one_game(i, args.rows, args.cols, args.connect_n, args.explore_plies, args.top_k, args.temperature, args.minimax_depth) for i in range(args.games))
    else:
        ctx = mp.get_context('spawn')
        pool = ctx.Pool(
            processes=args.workers,
            initializer=init_worker,
            initargs=(args.model, args.rows, args.cols, args.device, args.seed, args.torch_threads),
        )
        tasks = ((i, args.rows, args.cols, args.connect_n, args.explore_plies, args.top_k, args.temperature, args.minimax_depth) for i in range(args.games))
        chunksize = max(1, min(16, args.games // max(args.workers * 4, 1)))
        results_iter = pool.imap_unordered(_play_one_game_star, tasks, chunksize=chunksize)
    jsonl_handle = None
    if args.save_games_jsonl:
        jsonl_mode = 'a' if args.append else 'w'
        jsonl_handle = open(out_jsonl, jsonl_mode, encoding='utf-8')
    total_samples = 0
    try:
        for i, res in enumerate(results_iter, start=1):
            if res['x'].size:
                xs_all.append(res['x']); pol_all.append(res['policy']); val_all.append(res['value']); mask_all.append(res['valid_mask'])
                total_samples += int(res['x'].shape[0])
            if res['winner'] == 1: wins_r += 1
            elif res['winner'] == 2: wins_j += 1
            else: draws += 1
            total_moves += res['num_moves']
            for k, v in res['reasons'].items(): total_reasons[k] = total_reasons.get(k, 0) + v
            if jsonl_handle is not None:
                jsonl_handle.write(json.dumps({
                    'game_index': i-1,
                    'winner': res['winner'],
                    'num_moves': res['num_moves'],
                    'moves': res['moves']
                }) + '\n')
            if i % args.log_every == 0 or i == args.games:
                elapsed = max(time.time() - start, 1e-9)
                gps = i / elapsed
                sps = total_samples / elapsed if total_samples else 0.0
                remaining = args.games - i
                eta = remaining / gps if gps > 0 else 0.0
                h = int(eta // 3600); m = int((eta % 3600)//60); s = int(eta % 60)
                avg_len = total_moves / i if i else 0.0
                print(f"[SELFPLAY-HYBRID] games={i}/{args.games} | samples={total_samples} | R/J/D={wins_r}/{wins_j}/{draws} | avg_len={avg_len:.1f} | keep_ml={total_reasons.get('keep_ml',0)} | explore={total_reasons.get('safe_explore',0)} | owin={total_reasons.get('override_forced_win',0)} | oloss={total_reasons.get('override_forced_loss',0)} | games/s={gps:.2f} | samples/s={sps:.1f} | eta={h:02d}:{m:02d}:{s:02d}")
    finally:
        if jsonl_handle is not None:
            jsonl_handle.close()
        if args.workers > 1:
            pool.close(); pool.join()
    X = np.concatenate(xs_all, axis=0) if xs_all else np.empty((0,2,args.rows,args.cols), dtype=np.float32)
    P = np.concatenate(pol_all, axis=0) if pol_all else np.empty((0,), dtype=np.int64)
    V = np.concatenate(val_all, axis=0) if val_all else np.empty((0,), dtype=np.int64)
    M = np.concatenate(mask_all, axis=0) if mask_all else np.empty((0,args.cols), dtype=np.float32)
    np.savez_compressed(out_npz, x=X, policy=P, value=V, valid_mask=M)
    meta = {
        'games': args.games,
        'samples': int(X.shape[0]),
        'rows': args.rows,
        'cols': args.cols,
        'wins_r': wins_r,
        'wins_j': wins_j,
        'draws': draws,
        'avg_len': (total_moves / args.games) if args.games else 0.0,
        'reasons': total_reasons,
        'workers': args.workers,
    }
    with open(out_meta, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)
    print(f'[OK] Saved dataset: {out_npz} | samples={X.shape[0]}')
    print(f'[OK] Saved meta: {out_meta}')
    if args.save_games_jsonl:
        print(f'[OK] Saved games JSONL: {out_jsonl}')

if __name__ == '__main__':
    main()
