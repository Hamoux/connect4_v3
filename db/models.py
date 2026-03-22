"""
Schéma et migrations légères pour le pipeline (colonnes optionnelles, index).
"""

from __future__ import annotations

import os

import psycopg2


def get_conn():
    host = os.getenv("PGHOST", "localhost")
    port = int(os.getenv("PGPORT", "5432"))
    dbname = os.getenv("PGDATABASE", "Connect4DB")
    user = os.getenv("PGUSER", "postgres")
    password = os.getenv("PGPASSWORD")
    return psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password)


MIGRATIONS = [
    "ALTER TABLE partie ADD COLUMN IF NOT EXISTS bga_table_id TEXT;",
    "ALTER TABLE partie ADD COLUMN IF NOT EXISTS data_source TEXT;",
    "ALTER TABLE partie ADD COLUMN IF NOT EXISTS move_hash TEXT;",
    # Unicité logique : id BGA ou hash de coups
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_partie_bga_table_id ON partie(bga_table_id) WHERE bga_table_id IS NOT NULL;",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_partie_move_hash ON partie(move_hash) WHERE move_hash IS NOT NULL;",
]


def migrate() -> None:
    """Applique les migrations idempotentes."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for sql in MIGRATIONS:
                cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
    print("Migrations appliquées.")
