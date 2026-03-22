"""
Simulation d'une partie Connect 4 (9x9) : deux joueurs Minimax indépendants
(moteurs et tables de transposition séparés pour éviter les effets de bord).
"""

from __future__ import annotations

import random
from typing import Any

from ai import MinimaxAI
from bga_bot.minimax_adapter import MinimaxMoveSelector
from game import Connect4Game


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
    Retourne un dict avec moves (format import BGA), winner R/J/D, board final, signature.
    epsilon: probabilité de jouer un coup légal aléatoire (diversité).
    """
    if seed is not None:
        random.seed(seed)

    sp = starting_player or random.choice(["R", "J"])
    game = Connect4Game(rows=rows, cols=cols, starting_player=sp)
    sel_r = MinimaxMoveSelector(rows, cols, depth=depth_r)
    sel_j = MinimaxMoveSelector(rows, cols, depth=depth_j)

    moves: list[dict[str, Any]] = []
    move_id = 0

    while not game.game_over:
        p = game.current_player
        board_copy = [row[:] for row in game.board]
        sel = sel_r if p == "R" else sel_j

        legal = [c for c in range(cols) if game.board[0][c] == 0]
        if not legal:
            break

        if random.random() < epsilon:
            col = random.choice(legal)
        else:
            col = sel.best_col(board_copy, p)
            if col is None or col not in legal:
                col = legal[0]

        ok, _wl = game.drop(col)
        move_id += 1
        if not ok:
            break
        moves.append(
            {
                "move_id": move_id,
                "col": col + 1,
                "color": p,
                "player_id": "sim_R" if p == "R" else "sim_J",
            }
        )
        sel_r.engine.clear_cache()
        sel_j.engine.clear_cache()

    winner = None
    if game.result == "Rouge":
        winner = "R"
    elif game.result == "Jaune":
        winner = "J"
    elif game.result == "Match nul":
        winner = "D"

    sig = "".join(str(int(m["col"])) for m in moves)
    return {
        "moves": moves,
        "winner": winner,
        "board": game.board,
        "signature": sig,
        "starting_player": sp,
    }


def import_simulation_to_db(result: dict[str, Any], confiance: int = 3) -> int | None:
    """Importe le résultat dans PostgreSQL (réutilise bga_import). Retourne None si doublon."""
    from bga_import import import_bga_moves
    from db.deduplication import exists_signature
    from db.insert import attach_hashes_after_import
    from db.models import migrate
    from utils.hashing import canonical_signature_from_cols

    migrate()
    moves = result["moves"]
    cols = 9
    cols_seq = [int(m["col"]) for m in sorted(moves, key=lambda m: int(m.get("move_id", 0)))]
    sig = canonical_signature_from_cols(cols_seq, cols)
    if exists_signature(sig):
        return None
    pid = import_bga_moves(
        moves,
        rows=9,
        cols=9,
        confiance=confiance,
        mode="AI_VS_AI",
        type_partie="HUMAIN",
    )
    attach_hashes_after_import(pid, moves, cols, bga_table_id=None, data_source="AI_VS_AI")
    return pid
