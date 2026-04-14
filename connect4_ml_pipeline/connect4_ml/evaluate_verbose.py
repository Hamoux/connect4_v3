from __future__ import annotations

import argparse
import json
import os
import time
from typing import Dict

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from dataset import Connect4NPZDataset
from model import Connect4PolicyValueNet


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate trained Connect4 model with progress logs")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--batch-size", type=int, default=512)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--num-workers", type=int, default=0)
    p.add_argument("--log-every", type=int, default=50)
    return p.parse_args()


def format_eta(seconds: float) -> str:
    if seconds < 0 or seconds == float("inf"):
        return "unknown"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"


@torch.no_grad()
def evaluate_verbose(model, loader, device: str, log_every: int = 50) -> Dict[str, float]:
    model.eval()
    total_samples = len(loader.dataset)
    total_batches = len(loader)

    seen = 0
    total_loss = 0.0
    policy_correct = 0
    top3_correct = 0
    value_correct = 0

    start = time.perf_counter()
    last_log = start

    print(
        f"[INFO] Starting evaluation | device={device} | batches={total_batches:,} | samples={total_samples:,}",
        flush=True,
    )

    for step, batch in enumerate(loader, start=1):
        x = batch["x"].to(device)
        y_policy = batch["policy"].to(device)
        y_value = batch["value"].to(device)
        valid_mask = batch["valid_mask"].to(device)

        policy_logits, value_logits = model(x)
        policy_logits = policy_logits.masked_fill(valid_mask <= 0, -1e9)
        p_loss = F.cross_entropy(policy_logits, y_policy)
        v_loss = F.cross_entropy(value_logits, y_value)
        loss = p_loss + v_loss

        n = x.size(0)
        seen += n
        total_loss += loss.item() * n

        policy_pred = policy_logits.argmax(dim=1)
        policy_correct += (policy_pred == y_policy).sum().item()

        top3 = torch.topk(policy_logits, k=min(3, policy_logits.shape[1]), dim=1).indices
        top3_correct += (top3 == y_policy.unsqueeze(1)).any(dim=1).sum().item()

        value_pred = value_logits.argmax(dim=1)
        value_correct += (value_pred == y_value).sum().item()

        should_log = step == 1 or step % max(1, log_every) == 0 or step == total_batches
        if should_log:
            now = time.perf_counter()
            elapsed = now - start
            speed = seen / elapsed if elapsed > 0 else 0.0
            remaining = max(0, total_samples - seen)
            eta = remaining / speed if speed > 0 else float("inf")
            avg_loss = total_loss / seen
            print(
                f"[EVAL] step={step:,}/{total_batches:,} "
                f"samples={seen:,}/{total_samples:,} "
                f"loss={avg_loss:.4f} "
                f"policy_acc={policy_correct / seen:.4%} "
                f"top3={top3_correct / seen:.4%} "
                f"value_acc={value_correct / seen:.4%} "
                f"speed={speed:,.0f} samp/s "
                f"eta={format_eta(eta)}",
                flush=True,
            )
            last_log = now

    return {
        "loss": total_loss / seen,
        "policy_acc": policy_correct / seen,
        "policy_top3": top3_correct / seen,
        "value_acc": value_correct / seen,
    }


def main() -> None:
    args = parse_args()
    if not os.path.exists(args.model):
        raise FileNotFoundError(f"Model file not found: {args.model}")

    print(f"[INFO] Loading checkpoint: {args.model}", flush=True)
    ckpt = torch.load(args.model, map_location="cpu")
    model = Connect4PolicyValueNet(channels=ckpt["channels"], num_blocks=ckpt["blocks"])
    model.load_state_dict(ckpt["model_state"])
    model.to(args.device)

    test_path = os.path.join(args.data_dir, "test.npz")
    if not os.path.exists(test_path):
        raise FileNotFoundError(f"Test dataset not found: {test_path}")

    print(f"[INFO] Loading test dataset: {test_path}", flush=True)
    test_ds = Connect4NPZDataset(test_path)
    print(f"[INFO] Test samples: {len(test_ds):,}", flush=True)

    test_loader = DataLoader(
        test_ds,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=(args.device.startswith("cuda")),
    )

    metrics = evaluate_verbose(model, test_loader, args.device, log_every=args.log_every)
    print("[RESULT] Final metrics:", flush=True)
    print(json.dumps(metrics, indent=2), flush=True)


if __name__ == "__main__":
    main()
