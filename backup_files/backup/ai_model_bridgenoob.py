from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import importlib.util

import numpy as np
import torch


class MLModelAI:
    def __init__(self):
        self.model = None
        self.device = "cpu"
        self.checkpoint_path: Optional[Path] = None
        self.model_module_path: Optional[Path] = None
        self.rows: Optional[int] = None
        self.cols: Optional[int] = None

    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    def unload(self) -> None:
        self.model = None
        self.checkpoint_path = None
        self.model_module_path = None
        self.rows = None
        self.cols = None

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

        # 1) next to checkpoint
        candidates.append(ckpt_path.with_name("model.py"))

        # 2) parent folders of checkpoint (best_model.pt is usually in runs/...)
        for parent in ckpt_path.parents:
            candidates.append(parent / "model.py")

        # 3) common project-relative locations when GUI is launched from connect4_v3
        cwd = Path.cwd()
        candidates.extend([
            cwd / "model.py",
            cwd / "connect4_ml_pipeline" / "connect4_ml" / "model.py",
            cwd / "connect4_ml" / "model.py",
        ])

        # de-duplicate while preserving order
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
                raise FileNotFoundError(
                    "model.py not found automatically. Paths checked:\n" + looked
                )
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
    def _valid_cols(board_np: np.ndarray) -> List[int]:
        return [c for c in range(board_np.shape[1]) if board_np[0, c] == 0]

    def choose_move(self, board: List[List[object]], current_player: str) -> int:
        if self.model is None:
            raise RuntimeError("ML model is not loaded")

        board_np = self._board_to_np(board)
        if self.rows is not None and board_np.shape[0] != self.rows:
            raise RuntimeError(f"Checkpoint expects rows={self.rows}, got {board_np.shape[0]}")
        if self.cols is not None and board_np.shape[1] != self.cols:
            raise RuntimeError(f"Checkpoint expects cols={self.cols}, got {board_np.shape[1]}")

        player_num = 1 if current_player == "R" else 2
        x = torch.from_numpy(self._board_to_channels(board_np, player_num)).unsqueeze(0).to(self.device)

        with torch.no_grad():
            policy_logits, _ = self.model(x)

        logits = policy_logits[0].detach().cpu().numpy().astype(np.float64)
        valid = self._valid_cols(board_np)
        if not valid:
            return 0

        invalid_mask = np.ones(board_np.shape[1], dtype=bool)
        invalid_mask[valid] = False
        logits[invalid_mask] = -1e18

        return int(np.argmax(logits))
