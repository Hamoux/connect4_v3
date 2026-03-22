"""Hachage des séquences de coups pour déduplication."""

from __future__ import annotations

import hashlib
from typing import Iterable


def move_sequence_hash(cols_1_indexed: Iterable[int]) -> str:
    """
    Empreinte stable SHA-256 de la séquence de colonnes (1..cols).
    Ex: [1,5,5,3] -> hex digest.
    """
    s = "".join(str(int(c)) for c in cols_1_indexed)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def canonical_signature_from_cols(cols_1_indexed: list[int], nb_cols: int) -> str:
    """Signature canonique min(moves, mirror(moves)) pour symétrie miroir."""
    s = "".join(str(int(c)) for c in cols_1_indexed)
    m = "".join(str(int(nb_cols + 1 - int(c))) for c in cols_1_indexed)
    return s if s <= m else m
