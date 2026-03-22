"""
Insertion unifiée : import BGA, métadonnées, et enregistrement post-import.
"""

from __future__ import annotations

import os

import psycopg2
from psycopg2.extras import RealDictCursor

from db.db import canonical_signature_from_moves
from db.deduplication import exists_bga_table_id, exists_move_hash, exists_signature
from db.models import migrate
from utils.hashing import move_sequence_hash


def _conn():
    host = os.getenv("PGHOST", "localhost")
    port = int(os.getenv("PGPORT", "5432"))
    dbname = os.getenv("PGDATABASE", "Connect4DB")
    user = os.getenv("PGUSER", "postgres")
    password = os.getenv("PGPASSWORD")
    return psycopg2.connect(
        host=host, port=port, dbname=dbname, user=user, password=password,
        cursor_factory=RealDictCursor,
    )


def update_partie_metadata(
    id_partie: int,
    *,
    bga_table_id: str | None = None,
    data_source: str | None = None,
    move_hash: str | None = None,
) -> None:
    """Met à jour les colonnes optionnelles si présentes."""
    parts = []
    vals = []
    if bga_table_id is not None:
        parts.append("bga_table_id = %s")
        vals.append(bga_table_id)
    if data_source is not None:
        parts.append("data_source = %s")
        vals.append(data_source)
    if move_hash is not None:
        parts.append("move_hash = %s")
        vals.append(move_hash)
    if not parts:
        return
    vals.append(id_partie)
    sql = f"UPDATE partie SET {', '.join(parts)} WHERE id_partie = %s;"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(vals))
        conn.commit()
    finally:
        conn.close()


def should_skip_import(
    *,
    signature: str | None,
    bga_table_id: str | None = None,
    cols: int = 9,
    moves_for_hash: list[dict] | None = None,
) -> bool:
    """
    Retourne True si la partie existe déjà (signature, id BGA, ou hash des coups).
    """
    migrate()
    if bga_table_id and exists_bga_table_id(str(bga_table_id)):
        return True
    if signature and exists_signature(signature):
        return True
    if moves_for_hash:
        cols_seq = [int(m["col"]) for m in sorted(moves_for_hash, key=lambda m: int(m.get("move_id", 0)))]
        mh = move_sequence_hash(cols_seq)
        if exists_move_hash(mh):
            return True
    return False


def attach_hashes_after_import(
    id_partie: int,
    moves: list[dict],
    cols: int,
    *,
    bga_table_id: str | None = None,
    data_source: str = "BGA",
) -> None:
    """Calcule hash + signature canonique et met à jour la ligne partie."""
    migrate()
    moves = sorted(moves, key=lambda m: int(m.get("move_id", 0)))
    cols_seq = [int(m["col"]) for m in moves]
    mh = move_sequence_hash(cols_seq)
    sig = canonical_signature_from_moves(
        [{"col": c} for c in cols_seq],
        cols,
    )
    update_partie_metadata(
        id_partie,
        bga_table_id=bga_table_id,
        data_source=data_source,
        move_hash=mh,
    )
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE partie SET signature = COALESCE(signature, %s), move_hash = %s, data_source = COALESCE(data_source, %s) WHERE id_partie = %s;",
                (sig, mh, data_source, id_partie),
            )
        conn.commit()
    finally:
        conn.close()
