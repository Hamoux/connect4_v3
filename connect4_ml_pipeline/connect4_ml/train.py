from __future__ import annotations

import argparse
import json
import os
from typing import Dict

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from dataset import Connect4NPZDataset
from model import Connect4PolicyValueNet


LABELS = ["loss", "draw", "win"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train Connect4 policy+value model")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--weight-decay", type=float, default=1e-4)
    p.add_argument("--channels", type=int, default=64)
    p.add_argument("--blocks", type=int, default=4)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--output-dir", default="./runs/connect4_v1")
    p.add_argument("--policy-loss-weight", type=float, default=1.0)
    p.add_argument("--value-loss-weight", type=float, default=1.0)
    return p.parse_args()


@torch.no_grad()
def evaluate(model, loader, device: str) -> Dict[str, float]:
    model.eval()
    total = 0
    total_loss = 0.0
    policy_correct = 0
    top3_correct = 0
    value_correct = 0

    for batch in loader:
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

    train_ds = Connect4NPZDataset(os.path.join(args.data_dir, "train.npz"))
    val_ds = Connect4NPZDataset(os.path.join(args.data_dir, "val.npz"))

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, num_workers=0)

    device = args.device
    model = Connect4PolicyValueNet(channels=args.channels, num_blocks=args.blocks).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)

    best_val = float("inf")
    history = []

    for epoch in range(1, args.epochs + 1):
        model.train()
        running_loss = 0.0
        total = 0

        for batch in train_loader:
            x = batch["x"].to(device)
            y_policy = batch["policy"].to(device)
            y_value = batch["value"].to(device)
            valid_mask = batch["valid_mask"].to(device)

            optimizer.zero_grad(set_to_none=True)
            policy_logits, value_logits = model(x)
            policy_logits = policy_logits.masked_fill(valid_mask <= 0, -1e9)
            p_loss = F.cross_entropy(policy_logits, y_policy)
            v_loss = F.cross_entropy(value_logits, y_value)
            loss = args.policy_loss_weight * p_loss + args.value_loss_weight * v_loss
            loss.backward()
            optimizer.step()

            n = x.size(0)
            total += n
            running_loss += loss.item() * n

        train_loss = running_loss / total
        val_metrics = evaluate(model, val_loader, device)
        record = {
            "epoch": epoch,
            "train_loss": train_loss,
            **{f"val_{k}": v for k, v in val_metrics.items()},
        }
        history.append(record)

        print(
            f"Epoch {epoch:02d} | train_loss={train_loss:.4f} | "
            f"val_loss={val_metrics['loss']:.4f} | "
            f"policy_acc={val_metrics['policy_acc']:.4%} | "
            f"top3={val_metrics['policy_top3']:.4%} | "
            f"value_acc={val_metrics['value_acc']:.4%}"
        )

        if val_metrics["loss"] < best_val:
            best_val = val_metrics["loss"]
            torch.save(
                {
                    "model_state": model.state_dict(),
                    "channels": args.channels,
                    "blocks": args.blocks,
                },
                os.path.join(args.output_dir, "best_model.pt"),
            )

    with open(os.path.join(args.output_dir, "history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    print(f"[OK] Model saved to {os.path.join(args.output_dir, 'best_model.pt')}")
    print(f"[OK] History saved to {os.path.join(args.output_dir, 'history.json')}")


if __name__ == "__main__":
    main()
