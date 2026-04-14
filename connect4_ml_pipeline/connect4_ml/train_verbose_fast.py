from __future__ import annotations

import argparse
import json
import math
import os
import time
from typing import Dict

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from dataset import Connect4NPZDataset
from model import Connect4PolicyValueNet


LABELS = ["loss", "draw", "win"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train Connect4 policy+value model (verbose + faster)")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--batch-size", type=int, default=512)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--weight-decay", type=float, default=1e-4)
    p.add_argument("--channels", type=int, default=64)
    p.add_argument("--blocks", type=int, default=4)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--output-dir", default="./runs/connect4_v1")
    p.add_argument("--policy-loss-weight", type=float, default=1.0)
    p.add_argument("--value-loss-weight", type=float, default=1.0)
    p.add_argument("--num-workers", type=int, default=4)
    p.add_argument("--log-every", type=int, default=200)
    p.add_argument("--amp", action="store_true", help="Use mixed precision on CUDA")
    p.add_argument("--compile", action="store_true", help="Use torch.compile when available")
    return p.parse_args()


def format_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:d}h{m:02d}m{s:02d}s"
    if m > 0:
        return f"{m:d}m{s:02d}s"
    return f"{s:d}s"


@torch.no_grad()
def evaluate(model, loader, device: str, use_amp: bool = False) -> Dict[str, float]:
    model.eval()
    total = 0
    total_loss = 0.0
    policy_correct = 0
    top3_correct = 0
    value_correct = 0

    amp_enabled = use_amp and device.startswith("cuda")
    autocast_device = "cuda" if device.startswith("cuda") else "cpu"

    for batch in loader:
        x = batch["x"].to(device, non_blocking=True)
        y_policy = batch["policy"].to(device, non_blocking=True)
        y_value = batch["value"].to(device, non_blocking=True)
        valid_mask = batch["valid_mask"].to(device, non_blocking=True)

        with torch.autocast(device_type=autocast_device, enabled=amp_enabled):
            policy_logits, value_logits = model(x)
            policy_logits = policy_logits.masked_fill(valid_mask <= 0, -1e9)
            p_loss = F.cross_entropy(policy_logits, y_policy)
            v_loss = F.cross_entropy(value_logits, y_value)
            loss = p_loss + v_loss

        n = x.size(0)
        total += n
        total_loss += loss.item() * n
        policy_pred = policy_logits.argmax(dim=1)
        policy_correct += (policy_pred == y_policy).sum().item()

        top3 = torch.topk(policy_logits, k=min(3, policy_logits.shape[1]), dim=1).indices
        top3_correct += (top3 == y_policy.unsqueeze(1)).any(dim=1).sum().item()

        value_pred = value_logits.argmax(dim=1)
        value_correct += (value_pred == y_value).sum().item()

    return {
        "loss": total_loss / total,
        "policy_acc": policy_correct / total,
        "policy_top3": top3_correct / total,
        "value_acc": value_correct / total,
    }


def main() -> None:
    args = parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    print("[INFO] Loading datasets...")
    train_ds = Connect4NPZDataset(os.path.join(args.data_dir, "train.npz"))
    val_ds = Connect4NPZDataset(os.path.join(args.data_dir, "val.npz"))
    print(f"[INFO] Train samples: {len(train_ds):,}")
    print(f"[INFO] Val samples  : {len(val_ds):,}")

    pin_memory = args.device.startswith("cuda")
    persistent_workers = args.num_workers > 0

    train_loader = DataLoader(
        train_ds,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=args.num_workers,
        pin_memory=pin_memory,
        persistent_workers=persistent_workers,
        prefetch_factor=2 if args.num_workers > 0 else None,
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=pin_memory,
        persistent_workers=persistent_workers,
        prefetch_factor=2 if args.num_workers > 0 else None,
    )

    device = args.device
    print(f"[INFO] Device: {device}")
    model = Connect4PolicyValueNet(channels=args.channels, num_blocks=args.blocks).to(device)

    if device.startswith("cuda"):
        model = model.to(memory_format=torch.channels_last)
        torch.backends.cudnn.benchmark = True

    if args.compile and hasattr(torch, "compile"):
        print("[INFO] Compiling model...")
        model = torch.compile(model)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scaler = torch.amp.GradScaler("cuda", enabled=args.amp and device.startswith("cuda"))

    best_val = float("inf")
    history = []
    train_batches = len(train_loader)
    global_start = time.time()

    print(
        f"[INFO] Starting training | epochs={args.epochs} | batch_size={args.batch_size} | "
        f"num_workers={args.num_workers} | amp={args.amp and device.startswith('cuda')} | "
        f"batches/epoch={train_batches:,}"
    )

    for epoch in range(1, args.epochs + 1):
        epoch_start = time.time()
        model.train()
        running_loss = 0.0
        running_p_loss = 0.0
        running_v_loss = 0.0
        total = 0

        for step, batch in enumerate(train_loader, start=1):
            x = batch["x"].to(device, non_blocking=True)
            y_policy = batch["policy"].to(device, non_blocking=True)
            y_value = batch["value"].to(device, non_blocking=True)
            valid_mask = batch["valid_mask"].to(device, non_blocking=True)

            if device.startswith("cuda"):
                x = x.contiguous(memory_format=torch.channels_last)

            optimizer.zero_grad(set_to_none=True)

            with torch.autocast(device_type="cuda" if device.startswith("cuda") else "cpu", enabled=args.amp and device.startswith("cuda")):
                policy_logits, value_logits = model(x)
                policy_logits = policy_logits.masked_fill(valid_mask <= 0, -1e9)
                p_loss = F.cross_entropy(policy_logits, y_policy)
                v_loss = F.cross_entropy(value_logits, y_value)
                loss = args.policy_loss_weight * p_loss + args.value_loss_weight * v_loss

            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()

            n = x.size(0)
            total += n
            running_loss += loss.item() * n
            running_p_loss += p_loss.item() * n
            running_v_loss += v_loss.item() * n

            if step % args.log_every == 0 or step == train_batches:
                elapsed = time.time() - epoch_start
                samples_per_sec = total / max(elapsed, 1e-9)
                batches_per_sec = step / max(elapsed, 1e-9)
                eta = (train_batches - step) / max(batches_per_sec, 1e-9)
                avg_loss = running_loss / total
                avg_p = running_p_loss / total
                avg_v = running_v_loss / total
                print(
                    f"[TRAIN] epoch={epoch}/{args.epochs} step={step:,}/{train_batches:,} "
                    f"samples={total:,}/{len(train_ds):,} "
                    f"loss={avg_loss:.4f} p_loss={avg_p:.4f} v_loss={avg_v:.4f} "
                    f"speed={samples_per_sec:,.0f} samp/s eta={format_seconds(eta)}",
                    flush=True,
                )

        train_loss = running_loss / total
        val_metrics = evaluate(model, val_loader, device, use_amp=args.amp)
        epoch_time = time.time() - epoch_start
        record = {
            "epoch": epoch,
            "train_loss": train_loss,
            **{f"val_{k}": v for k, v in val_metrics.items()},
            "epoch_seconds": epoch_time,
        }
        history.append(record)

        print(
            f"[EPOCH END] {epoch:02d}/{args.epochs} | train_loss={train_loss:.4f} | "
            f"val_loss={val_metrics['loss']:.4f} | policy_acc={val_metrics['policy_acc']:.4%} | "
            f"top3={val_metrics['policy_top3']:.4%} | value_acc={val_metrics['value_acc']:.4%} | "
            f"time={format_seconds(epoch_time)}",
            flush=True,
        )

        if val_metrics["loss"] < best_val:
            best_val = val_metrics["loss"]
            save_path = os.path.join(args.output_dir, "best_model.pt")
            torch.save(
                {
                    "model_state": model.state_dict(),
                    "channels": args.channels,
                    "blocks": args.blocks,
                },
                save_path,
            )
            print(f"[CHECKPOINT] New best model saved to {save_path} (val_loss={best_val:.4f})", flush=True)

    with open(os.path.join(args.output_dir, "history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    total_time = time.time() - global_start
    print(f"[OK] Model saved to {os.path.join(args.output_dir, 'best_model.pt')}")
    print(f"[OK] History saved to {os.path.join(args.output_dir, 'history.json')}")
    print(f"[OK] Total training time: {format_seconds(total_time)}")


if __name__ == "__main__":
    main()
