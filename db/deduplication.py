"""Vérifications anti-doublons (signature, hash, id BGA)."""

from __future__ import annotations

import os

import psycopg2
from psycopg2.extras import RealDictCursor


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


def exists_signature(signature: str) -> bool:
    if not signature:
        return False
    sql = "SELECT 1 FROM partie WHERE signature = %s LIMIT 1;"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (signature,))
        return cur.fetchone() is not None


def exists_bga_table_id(bga_table_id: str) -> bool:
    if not bga_table_id:
        return False
    sql = "SELECT 1 FROM partie WHERE bga_table_id = %s LIMIT 1;"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (str(bga_table_id),))
        return cur.fetchone() is not None


def exists_move_hash(move_hash: str) -> bool:
    if not move_hash:
        return False
    sql = "SELECT 1 FROM partie WHERE move_hash = %s LIMIT 1;"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (move_hash,))
        return cur.fetchone() is not None


def get_partie_id_by_signature(signature: str) -> int | None:
    sql = "SELECT id_partie FROM partie WHERE signature = %s LIMIT 1;"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (signature,))
        row = cur.fetchone()
        return int(row["id_partie"]) if row else None
