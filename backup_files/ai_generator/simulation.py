"""
Simulation d'une partie Connect 4 (9x9) : deux joueurs Minimax indépendants.

FIXES:
- Ne vide plus le cache (clear_cache) à chaque coup → énorme gain de vitesse
- Utilise MinimaxAI directement sans passer par MinimaxMoveSelector
- Cache (transposition table) partagé sur toute la partie pour chaque joueur
"""

from __future__ import annotations

import random
from typing import Any

from ai import MinimaxAI
from game import Connect4Game


def _best_col(engine: MinimaxAI, board: list, player: str, depth: int, cols: int) -> int | None:
    """Choisit la meilleure colonne via Minimax (alpha-beta)."""
    legal = [c for c in range(cols) if board[0][c] == 0]
    if not legal:
        return None

    best_col = None
    best_score = -10**9

    for col in engine.ordered_valid_cols(board, player, True):
        r = engine.next_open_row(board, col)
        if r is None:
            continue
        board[r][col] = player
        score = engine.minimax(board, depth - 1, -10**9, 10**9, False, player)
        board[r][col] = 0
        if score > best_score:
            best_score = score
            best_col = col

    return best_col


def simulate_game(
    seed: int | None = None,
    rows: int = 9,
    cols: int = 9,
    depth_r: int = 4,
    depth_j: int = 4,
    epsilon: float = 0.0,
    starting_player: str | None = None,
) -> dict[str, Any]:
    """
    Simule une partie complète Minimax vs Minimax.

    epsilon: probabilité de jouer un coup aléatoire (pour diversifier les données).
             0.0 = pur Minimax, 0.1 = 10% aléatoire.

    Retourne un dict avec:
      - moves: liste de coups au format bga_import
      - winner: "R" / "J" / "D" (draw)
      - board: plateau final
      - signature: séquence de colonnes (str)
      - starting_player: "R" ou "J"
    """
    if seed is not None:
        random.seed(seed)

    sp = starting_player or random.choice(["R", "J"])
    game = Connect4Game(rows=rows, cols=cols, starting_player=sp)

    # Deux moteurs séparés avec leurs propres transposition tables
    # NE PAS vider le cache entre les coups — c'est ce qui rendait tout lent
    engine_r = MinimaxAI(rows, cols)
    engine_j = MinimaxAI(rows, cols)

    moves: list[dict[str, Any]] = []
    move_id = 0

    while not game.game_over:
        p = game.current_player
        engine = engine_r if p == "R" else engine_j

        legal = [c for c in range(cols) if game.board[0][c] == 0]
        if not legal:
            break

        # Copie du plateau pour Minimax (ne pas toucher game.board directement)
        board_copy = [row[:] for row in game.board]

        if random.random() < epsilon:
            col = random.choice(legal)
        else:
            depth = depth_r if p == "R" else depth_j
            col = _best_col(engine, board_copy, p, depth, cols)
            if col is None or col not in legal:
                col = legal[len(legal) // 2]  # centre si echec

        ok, _wl = game.drop(col)
        if not ok:
            break

        move_id += 1
        moves.append({
            "move_id": move_id,
            "col": col + 1,   # 1-indexed comme BGA
            "color": p,
            "player_id": f"sim_{p}",
        })

    # Résultat
    winner = None
    if game.result == "Rouge":
        winner = "R"
    elif game.result == "Jaune":
        winner = "J"
    elif game.result == "Match nul":
        winner = "D"

    sig = "".join(str(m["col"]) for m in moves)

    return {
        "moves": moves,
        "winner": winner,
        "board": game.board,
        "signature": sig,
        "starting_player": sp,
    }


def import_simulation_to_db(result: dict[str, Any], confiance: int = 3) -> int | None:
    """
    Importe le résultat dans PostgreSQL via bga_import.
    Retourne None si doublon, sinon l'id_partie.
    """
    from bga_import import import_bga_moves
    from db.deduplication import exists_signature
    from db.insert import attach_hashes_after_import
    from db.models import migrate
    from utils.hashing import canonical_signature_from_cols

    migrate()
    moves = result["moves"]
    cols_seq = [int(m["col"]) for m in sorted(moves, key=lambda m: int(m.get("move_id", 0)))]
    sig = canonical_signature_from_cols(cols_seq, 9)

    if exists_signature(sig):
        return None  # doublon, on skip silencieusement

    pid = import_bga_moves(
        moves,
        rows=9,
        cols=9,
        confiance=confiance,
        mode="AI_VS_AI",
        type_partie="HUMAIN",
    )
    if pid is not None:
        attach_hashes_after_import(pid, moves, 9, bga_table_id=None, data_source="AI_VS_AI")
    return pid
