"""
Lance plusieurs simulations en parallèle (multiprocessing).
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import random
from typing import Callable

from ai_generator.simulation import import_simulation_to_db, simulate_game
from db.models import migrate
from utils.config import load_config

log = logging.getLogger(__name__)


def _worker(args: tuple) -> tuple[bool, str]:
    """args = (worker_id, games, depth_r, depth_j, epsilon, seed_base)"""
    wid, n_games, dr, dj, eps, seed_base = args
    migrate()
    ok_n = 0
    for i in range(n_games):
        seed = seed_base + wid * 100_000 + i
        try:
            res = simulate_game(
                seed=seed,
                depth_r=dr,
                depth_j=dj,
                epsilon=eps,
            )
            pid = import_simulation_to_db(res)
            if pid is not None:
                ok_n += 1
        except Exception as e:
            return False, f"worker {wid} erreur: {e}"
    return True, f"worker {wid}: {ok_n}/{n_games} importées"


def run_parallel(
    total_games: int | None = None,
    workers: int | None = None,
    depth: int | None = None,
    epsilon: float | None = None,
    progress: Callable[[str], None] | None = None,
) -> None:
    cfg = load_config()
    total = total_games or (cfg.ai_parallel_workers * cfg.ai_games_per_worker)
    w = workers or cfg.ai_parallel_workers
    d = depth or cfg.ai_default_depth
    eps = epsilon if epsilon is not None else cfg.ai_epsilon_random

    migrate()

    base = total // w
    rem = total % w
    chunks = [base + (1 if i < rem else 0) for i in range(w)]

    seed_base = random.randint(1, 2**30)

    tasks = []
    for wid in range(w):
        if chunks[wid] <= 0:
            continue
        tasks.append((wid, chunks[wid], d, d, eps, seed_base))

    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=min(w, len(tasks))) as pool:
        for res in pool.imap_unordered(_worker, tasks):
            ok, msg = res
            log.info(msg)
            if progress:
                progress(msg)
            if not ok:
                log.error(msg)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_parallel()
