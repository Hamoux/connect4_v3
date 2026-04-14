from __future__ import annotations

import numpy as np
import torch
from torch.utils.data import Dataset


class Connect4NPZDataset(Dataset):
    def __init__(self, npz_path: str):
        data = np.load(npz_path)
        self.x = data["x"].astype(np.float32)
        self.policy = data["policy"].astype(np.int64)
        self.value = data["value"].astype(np.int64)
        self.valid_mask = data["valid_mask"].astype(np.float32)

    def __len__(self) -> int:
        return int(self.x.shape[0])

    def __getitem__(self, idx: int):
        return {
            "x": torch.from_numpy(self.x[idx]),
            "policy": torch.tensor(self.policy[idx], dtype=torch.long),
            "value": torch.tensor(self.value[idx], dtype=torch.long),
            "valid_mask": torch.from_numpy(self.valid_mask[idx]),
        }
