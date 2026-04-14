import argparse
import json
from pathlib import Path
from typing import Dict, Tuple

import numpy as np


REQUIRED_KEYS = ["x", "policy", "value", "valid_mask"]


def load_npz(path: Path) -> Dict[str, np.ndarray]:
    data = np.load(path)
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(f"{path} is missing keys: {missing}")
    out = {k: data[k] for k in REQUIRED_KEYS}
    n = len(out["x"])
    for k, v in out.items():
        if len(v) != n:
            raise ValueError(f"{path} has inconsistent length for key '{k}': {len(v)} != {n}")
    return out


def normalize_weights(w1: float, w2: float) -> Tuple[float, float]:
    if w1 < 0 or w2 < 0:
        raise ValueError("Weights must be >= 0")
    s = w1 + w2
    if s <= 0:
        raise ValueError("At least one weight must be > 0")
    return w1 / s, w2 / s


def sample_count(total: int, ratio: float) -> int:
    return int(round(total * ratio))


def sample_indices(n_available: int, n_take: int, rng: np.random.Generator, replace: bool) -> np.ndarray:
    if n_take <= 0:
        return np.empty((0,), dtype=np.int64)
    if not replace and n_take > n_available:
        raise ValueError(
            f"Cannot sample {n_take} items without replacement from dataset of size {n_available}. "
            f"Either reduce total size/weight or use --allow-replacement."
        )
    return rng.choice(n_available, size=n_take, replace=replace)


def subset(data: Dict[str, np.ndarray], idx: np.ndarray) -> Dict[str, np.ndarray]:
    return {k: v[idx] for k, v in data.items()}


def concat_parts(parts):
    return {k: np.concatenate([p[k] for p in parts], axis=0) for k in REQUIRED_KEYS}


def shuffle_in_unison(data: Dict[str, np.ndarray], rng: np.random.Generator) -> Dict[str, np.ndarray]:
    n = len(data["x"])
    order = rng.permutation(n)
    return {k: v[order] for k, v in data.items()}


def save_npz(path: Path, data: Dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **data)


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge two .npz datasets with configurable percentages and total sample count.")
    ap.add_argument("--dataset-a", required=True, help="Path to first dataset (.npz), e.g. historical train.npz")
    ap.add_argument("--dataset-b", required=True, help="Path to second dataset (.npz), e.g. self-play .npz")
    ap.add_argument("--weight-a", type=float, required=True, help="Relative weight or percentage for dataset A, e.g. 80")
    ap.add_argument("--weight-b", type=float, required=True, help="Relative weight or percentage for dataset B, e.g. 20")
    ap.add_argument("--total-samples", type=int, required=True, help="Total number of merged samples to output")
    ap.add_argument("--output", required=True, help="Output .npz path")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--allow-replacement", action="store_true", help="Allow sampling with replacement if requested samples exceed dataset size")
    ap.add_argument("--shuffle", action="store_true", default=True, help="Shuffle merged output (default: on)")
    ap.add_argument("--no-shuffle", dest="shuffle", action="store_false", help="Disable output shuffle")
    ap.add_argument("--save-meta", action="store_true", help="Also save a JSON metadata file next to output")
    args = ap.parse_args()

    out_path = Path(args.output)
    ds_a_path = Path(args.dataset_a)
    ds_b_path = Path(args.dataset_b)

    if args.total_samples <= 0:
        raise ValueError("--total-samples must be > 0")

    rng = np.random.default_rng(args.seed)

    print(f"[INFO] Loading dataset A: {ds_a_path}")
    a = load_npz(ds_a_path)
    print(f"[INFO] Loading dataset B: {ds_b_path}")
    b = load_npz(ds_b_path)

    n_a = len(a["x"])
    n_b = len(b["x"])
    print(f"[INFO] Dataset A samples: {n_a:,}")
    print(f"[INFO] Dataset B samples: {n_b:,}")

    ra, rb = normalize_weights(args.weight_a, args.weight_b)
    take_a = sample_count(args.total_samples, ra)
    take_b = args.total_samples - take_a

    print(
        f"[INFO] Merge plan | total={args.total_samples:,} | "
        f"A={take_a:,} ({ra:.2%}) | B={take_b:,} ({rb:.2%}) | "
        f"replacement={args.allow_replacement}"
    )

    idx_a = sample_indices(n_a, take_a, rng, args.allow_replacement)
    idx_b = sample_indices(n_b, take_b, rng, args.allow_replacement)

    part_a = subset(a, idx_a)
    part_b = subset(b, idx_b)
    merged = concat_parts([part_a, part_b])

    if args.shuffle:
        print("[INFO] Shuffling merged dataset...")
        merged = shuffle_in_unison(merged, rng)

    save_npz(out_path, merged)
    final_n = len(merged["x"])
    print(f"[OK] Saved merged dataset: {out_path} | samples={final_n:,}")

    if args.save_meta:
        meta = {
            "dataset_a": str(ds_a_path),
            "dataset_b": str(ds_b_path),
            "dataset_a_available": n_a,
            "dataset_b_available": n_b,
            "weight_a_input": args.weight_a,
            "weight_b_input": args.weight_b,
            "weight_a_normalized": ra,
            "weight_b_normalized": rb,
            "take_a": take_a,
            "take_b": take_b,
            "total_samples": final_n,
            "seed": args.seed,
            "allow_replacement": args.allow_replacement,
            "shuffle": args.shuffle,
        }
        meta_path = out_path.with_suffix(out_path.suffix + ".meta.json")
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"[OK] Saved metadata: {meta_path}")


if __name__ == "__main__":
    main()
