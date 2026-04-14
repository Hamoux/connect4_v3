from __future__ import annotations

import argparse
import json
import os

import torch
from torch.utils.data import DataLoader

from dataset import Connect4NPZDataset
from model import Connect4PolicyValueNet
from train import evaluate


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate trained Connect4 model")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--batch-size", type=int, default=512)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    return p.parse_args()



def main() -> None:
    args = parse_args()
    ckpt = torch.load(args.model, map_location="cpu")
    model = Connect4PolicyValueNet(channels=ckpt["channels"], num_blocks=ckpt["blocks"])
    model.load_state_dict(ckpt["model_state"])
    model.to(args.device)

    test_ds = Connect4NPZDataset(os.path.join(args.data_dir, "test.npz"))
    test_loader = DataLoader(test_ds, batch_size=args.batch_size, shuffle=False, num_workers=0)
    metrics = evaluate(model, test_loader, args.device)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
