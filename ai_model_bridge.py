from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple
import importlib.util

import numpy as np
import torch

from ai import MinimaxAI


class MLModelAI:
    """
    Hybrid guarded bridge for the GUI.

    Logic:
    - The ML model proposes a move.
    - Minimax depth N evaluates *all* legal moves.
    - If the ML move misses a forced win, or walks into a clearly losing line,
      the bridge overrides it with the best minimax move.
    - Otherwise the ML move is kept.

    This keeps the model's style when the move is "safe enough", but uses minimax
    as a tactical safety net.
    """

    FORCED_WIN_SCORE = 9_000_000
    FORCED_LOSS_SCORE = -9_000_000

    def __init__(self, minimax_depth: int = 7):
        self.model = None
        self.device = "cpu"
        self.checkpoint_path: Optional[Path] = None
        self.model_module_path: Optional[Path] = None
        self.rows: Optional[int] = None
        self.cols: Optional[int] = None
        self.minimax_depth = int(minimax_depth)
        self.minimax_ai: Optional[MinimaxAI] = None
        self.last_debug: str = ""

    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    def unload(self) -> None:
        self.model = None
        self.checkpoint_path = None
        self.model_module_path = None
        self.rows = None
        self.cols = None
        self.minimax_ai = None
        self.last_debug = ""

    def set_minimax_depth(self, depth: int) -> None:
        self.minimax_depth = int(depth)

    def _load_model_class(self, model_py_path: Path):
        spec = importlib.util.spec_from_file_location("connect4_ml_model_dynamic", str(model_py_path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load model module from: {model_py_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "Connect4PolicyValueNet"):
            raise RuntimeError("model.py does not expose Connect4PolicyValueNet")
        return module.Connect4PolicyValueNet

    def _candidate_model_paths(self, ckpt_path: Path) -> list[Path]:
        candidates: list[Path] = []
        candidates.append(ckpt_path.with_name("model.py"))
        for parent in ckpt_path.parents:
            candidates.append(parent / "model.py")
        cwd = Path.cwd()
        candidates.extend([
            cwd / "model.py",
            cwd / "connect4_ml_pipeline" / "connect4_ml" / "model.py",
            cwd / "connect4_ml" / "model.py",
        ])
        seen = set()
        unique: list[Path] = []
        for p in candidates:
            rp = str(p.resolve()) if p.exists() else str(p)
            if rp not in seen:
                seen.add(rp)
                unique.append(p)
        return unique

    def load(self, checkpoint_path: str, model_py_path: str | None = None, device: str = "cpu") -> None:
        ckpt_path = Path(checkpoint_path)
        if not ckpt_path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {ckpt_path}")

        if model_py_path is None:
            found = None
            searched = self._candidate_model_paths(ckpt_path)
            for candidate in searched:
                if candidate.exists():
                    found = candidate
                    break
            if found is None:
                looked = "\n".join(str(p) for p in searched)
                raise FileNotFoundError("model.py not found automatically. Paths checked:\n" + looked)
            model_path = found
        else:
            model_path = Path(model_py_path)
            if not model_path.exists():
                raise FileNotFoundError(f"model.py not found: {model_path}")

        Connect4PolicyValueNet = self._load_model_class(model_path)
        ckpt = torch.load(str(ckpt_path), map_location="cpu")

        rows = int(ckpt.get("rows", 9))
        cols = int(ckpt.get("cols", 9))
        channels = int(ckpt.get("channels", 64))
        blocks = int(ckpt.get("blocks", 4))

        model = Connect4PolicyValueNet(rows=rows, cols=cols, channels=channels, num_blocks=blocks)
        model.load_state_dict(ckpt["model_state"])
        model.to(device)
        model.eval()

        self.model = model
        self.device = device
        self.checkpoint_path = ckpt_path
        self.model_module_path = model_path
        self.rows = rows
        self.cols = cols
        self.minimax_ai = MinimaxAI(rows, cols)
        self.last_debug = f"Loaded guarded hybrid ML+Minimax(depth={self.minimax_depth}) from {ckpt_path.name}"

    @staticmethod
    def _board_to_np(board: List[List[object]]) -> np.ndarray:
        arr = np.zeros((len(board), len(board[0])), dtype=np.int8)
        for r in range(len(board)):
            for c in range(len(board[0])):
                v = board[r][c]
                if v == "R":
                    arr[r, c] = 1
                elif v in ("J", "Y"):
                    arr[r, c] = 2
        return arr

    @staticmethod
    def _board_to_channels(board_np: np.ndarray, player_to_move: int) -> np.ndarray:
        own = (board_np == player_to_move).astype(np.float32)
        opp = (board_np == (3 - player_to_move)).astype(np.float32)
        return np.stack([own, opp], axis=0)

    @staticmethod
    def _valid_cols_np(board_np: np.ndarray) -> List[int]:
        return [c for c in range(board_np.shape[1]) if board_np[0, c] == 0]

    @staticmethod
    def _to_minimax_board(board: List[List[object]]) -> List[List[object]]:
        out: List[List[object]] = []
        for row in board:
            out_row = []
            for v in row:
                if v in (0, "R", "J"):
                    out_row.append(v)
                elif v == "Y":
                    out_row.append("J")
                else:
                    out_row.append(0)
            out.append(out_row)
        return out

    def _choose_ml_move(self, board: List[List[object]], current_player: str) -> Tuple[int, np.ndarray]:
        board_np = self._board_to_np(board)
        player_num = 1 if current_player == "R" else 2
        x = torch.from_numpy(self._board_to_channels(board_np, player_num)).unsqueeze(0).to(self.device)
        with torch.no_grad():
            policy_logits, _ = self.model(x)
        logits = policy_logits[0].detach().cpu().numpy().astype(np.float64)
        valid = self._valid_cols_np(board_np)
        if not valid:
            return 0, logits
        invalid_mask = np.ones(board_np.shape[1], dtype=bool)
        invalid_mask[valid] = False
        logits[invalid_mask] = -1e18
        return int(np.argmax(logits)), logits

    def _score_all_minimax_moves(self, board: List[List[object]], current_player: str) -> Tuple[List[int], dict[int, int]]:
        if self.minimax_ai is None:
            raise RuntimeError("MinimaxAI not initialized")
        mm_board = self._to_minimax_board(board)
        valid = self.minimax_ai.valid_cols(mm_board)
        if not valid:
            return [], {}
        self.minimax_ai.clear_cache()
        scores: dict[int, int] = {}
        ordered = self.minimax_ai.ordered_valid_cols(mm_board, current_player, True)
        for col in ordered:
            r = self.minimax_ai.next_open_row(mm_board, col)
            if r is None:
                continue
            mm_board[r][col] = current_player
            score = self.minimax_ai.minimax(
                mm_board,
                self.minimax_depth - 1,
                -10**18,
                10**18,
                False,
                current_player,
            )
            mm_board[r][col] = 0
            scores[col] = int(score)
        return valid, scores

    def choose_move(self, board: List[List[object]], current_player: str) -> int:
        if self.model is None:
            raise RuntimeError("Hybrid ML model is not loaded")

        board_np = self._board_to_np(board)
        if self.rows is not None and board_np.shape[0] != self.rows:
            raise RuntimeError(f"Checkpoint expects rows={self.rows}, got {board_np.shape[0]}")
        if self.cols is not None and board_np.shape[1] != self.cols:
            raise RuntimeError(f"Checkpoint expects cols={self.cols}, got {board_np.shape[1]}")

        ml_move, logits = self._choose_ml_move(board, current_player)
        valid, mm_scores = self._score_all_minimax_moves(board, current_player)
        if not valid:
            self.last_debug = "No valid moves"
            return 0

        best_mm_move = max(valid, key=lambda c: mm_scores[c])
        best_mm_score = mm_scores[best_mm_move]
        ml_score = mm_scores.get(ml_move, -10**18)

        winning_moves = [c for c in valid if mm_scores[c] >= self.FORCED_WIN_SCORE]
        safe_moves = [c for c in valid if mm_scores[c] > self.FORCED_LOSS_SCORE]

        # Case 1: the model misses a forced win visible to minimax.
        if winning_moves and ml_move not in winning_moves:
            chosen = max(winning_moves, key=lambda c: mm_scores[c])
            self.last_debug = (
                f"Guarded hybrid: ML={ml_move+1} misses forced win, "
                f"MM chooses {chosen+1} depth={self.minimax_depth}"
            )
            return chosen

        # Case 2: the model chooses a move that is clearly losing within the search horizon,
        # while another non-losing move exists.
        if ml_score <= self.FORCED_LOSS_SCORE and safe_moves:
            chosen = max(safe_moves, key=lambda c: mm_scores[c])
            self.last_debug = (
                f"Guarded hybrid: ML={ml_move+1} loses in search horizon, "
                f"MM saves with {chosen+1} depth={self.minimax_depth}"
            )
            return chosen

        # Case 3: ML move is tactically safe enough -> keep the model move.
        # This lets the model keep its own style when minimax does not see a disaster.
        sorted_valid_by_logit = sorted(valid, key=lambda c: logits[c], reverse=True)
        ml_rank = sorted_valid_by_logit.index(ml_move) + 1 if ml_move in sorted_valid_by_logit else -1
        self.last_debug = (
            f"Guarded hybrid: keep ML {ml_move+1} | rank={ml_rank} | "
            f"ml_score={ml_score} | mm_best={best_mm_move+1}:{best_mm_score} | depth={self.minimax_depth}"
        )
        return ml_move
