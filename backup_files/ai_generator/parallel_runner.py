"""
Lance plusieurs simulations en parallèle (multiprocessing).

FIXES vs version Cursor:
- migrate() appelé UNE SEULE FOIS dans le process principal, pas dans chaque worker
- Les workers ne font PAS d'import DB → ils retournent juste les résultats
- L'import DB se fait en batch dans le process principal (évite N connexions simultanées)
- Utilise 'fork' sur Linux/Mac (plus rapide), 'spawn' sur Windows (obligatoire)
- Contrôle du nombre de workers basé sur les CPU disponibles
- Affichage de progression en temps réel
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import random
import sys
import time
from typing import Any

from ai_generator.simulation import simulate_game

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Worker : simule N parties, retourne les résultats bruts (pas d'import DB)
# ─────────────────────────────────────────────
def _worker(args: tuple) -> list[dict[str, Any]]:
    """
    args = (worker_id, n_games, depth_r, depth_j, epsilon, seed_base)
    Retourne une liste de résultats de simulation (dicts).
    Pas de DB ici → évite les connexions simultanées et le overhead spawn.
    """
    wid, n_games, depth_r, depth_j, epsilon, seed_base = args
    results = []

    for i in range(n_games):
        seed = seed_base + wid * 100_000 + i
        try:
            res = simulate_game(
                seed=seed,
                depth_r=depth_r,
                depth_j=depth_j,
                epsilon=epsilon,
            )
            results.append(res)
        except Exception as e:
            log.warning(f"Worker {wid} game {i} error: {e}")

    return results


# ─────────────────────────────────────────────
# Import batch dans le process principal
# ─────────────────────────────────────────────
def _import_batch(results: list[dict[str, Any]], confiance: int = 3) -> tuple[int, int]:
    """Importe une liste de résultats en DB. Retourne (ok, skipped)."""
    from ai_generator.simulation import import_simulation_to_db
    ok = skipped = 0
    for res in results:
        try:
            pid = import_simulation_to_db(res, confiance=confiance)
            if pid is not None:
                ok += 1
            else:
                skipped += 1
        except Exception as e:
            log.warning(f"Import error: {e}")
            skipped += 1
    return ok, skipped


# ─────────────────────────────────────────────
# Runner principal
# ─────────────────────────────────────────────
def run_parallel(
    total_games: int = 500,
    workers: int | None = None,
    depth: int = 4,
    epsilon: float = 0.05,
    confiance: int = 3,
) -> None:
    """
    Lance `total_games` simulations en parallèle et les importe en DB.

    Args:
        total_games: nombre total de parties à générer
        workers: nombre de processus parallèles (défaut: nb CPUs - 1)
        depth: profondeur Minimax pour les deux joueurs
        epsilon: probabilité de coup aléatoire (0.0 = pur Minimax)
        confiance: niveau de confiance pour l'import DB
    """
    # Migrate une seule fois avant tout
    from db.models import migrate
    migrate()

    # Nombre de workers
    cpu_count = os.cpu_count() or 4
    w = workers or max(1, cpu_count - 1)
    w = min(w, total_games)  # pas plus de workers que de parties

    log.info(f"🚀 Démarrage: {total_games} parties | {w} workers | depth={depth} | epsilon={epsilon}")
    print(f"🚀 Démarrage: {total_games} parties | {w} workers | depth={depth} | epsilon={epsilon}")

    # Répartir les parties entre workers
    base = total_games // w
    rem = total_games % w
    chunks = [base + (1 if i < rem else 0) for i in range(w)]

    seed_base = random.randint(1, 2**30)
    tasks = [
        (wid, chunks[wid], depth, depth, epsilon, seed_base)
        for wid in range(w)
        if chunks[wid] > 0
    ]

    # Sur Windows on est forcé d'utiliser 'spawn', sur Linux 'fork' est bien plus rapide
    ctx_method = "spawn" if sys.platform == "win32" else "fork"
    ctx = mp.get_context(ctx_method)

    total_ok = 0
    total_skipped = 0
    t_start = time.time()

    with ctx.Pool(processes=len(tasks)) as pool:
        # imap_unordered → on traite les résultats dès qu'un worker finit
        for batch_results in pool.imap_unordered(_worker, tasks):
            ok, skipped = _import_batch(batch_results, confiance=confiance)
            total_ok += ok
            total_skipped += skipped
            elapsed = time.time() - t_start
            rate = total_ok / elapsed if elapsed > 0 else 0
            print(f"✅ {total_ok} importées | ⏭ {total_skipped} doublons | {rate:.1f} parties/s")

    elapsed = time.time() - t_start
    rate = total_ok / elapsed if elapsed > 0 else 0
    print(f"\n🏁 Terminé en {elapsed:.1f}s")
    print(f"   ✅ {total_ok} parties importées")
    print(f"   ⏭  {total_skipped} doublons ignorés")
    print(f"   ⚡ {rate:.1f} parties/seconde")


# ─────────────────────────────────────────────
# Point d'entrée CLI
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    parser = argparse.ArgumentParser(description="Générateur AI vs AI Connect 4")
    parser.add_argument("--games",   type=int,   default=500,  help="Nombre total de parties (défaut: 500)")
    parser.add_argument("--workers", type=int,   default=None, help="Nombre de workers (défaut: CPUs-1)")
    parser.add_argument("--depth",   type=int,   default=4,    help="Profondeur Minimax (défaut: 4)")
    parser.add_argument("--epsilon", type=float, default=0.05, help="Aléatoire 0.0-1.0 (défaut: 0.05)")
    args = parser.parse_args()

    run_parallel(
        total_games=args.games,
        workers=args.workers,
        depth=args.depth,
        epsilon=args.epsilon,
    )
