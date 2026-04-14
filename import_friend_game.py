import argparse
import csv
import os
from typing import List, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

DB_CONFIG = {
    "host": os.getenv("PGHOST", "localhost"),
    "port": int(os.getenv("PGPORT", "5432")),
    "dbname": os.getenv("PGDATABASE", "Connect4DB"),
    "user": os.getenv("PGUSER", "postgres"),
    "password": os.getenv("PGPASSWORD", "Celina123"),
}


def get_conn():
    return psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG)


def make_board(rows: int, cols: int) -> List[List[int]]:
    return [[0] * cols for _ in range(rows)]


def drop_piece(board: List[List[int]], col_1_based: int, player: int) -> int:
    col = col_1_based - 1
    rows = len(board)
    for r in range(rows - 1, -1, -1):
        if board[r][col] == 0:
            board[r][col] = player
            return r
    return -1


def is_board_full(board: List[List[int]]) -> bool:
    return all(x != 0 for x in board[0])


def get_winning_line(
    board: List[List[int]], row: int, col_1_based: int, player: int, connect: int = 4
) -> List[Tuple[int, int]]:
    rows = len(board)
    cols = len(board[0])
    col = col_1_based - 1
    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]

    for dr, dc in directions:
        line = [(row, col)]

        r, c = row + dr, col + dc
        while 0 <= r < rows and 0 <= c < cols and board[r][c] == player:
            line.append((r, c))
            r += dr
            c += dc

        r, c = row - dr, col - dc
        while 0 <= r < rows and 0 <= c < cols and board[r][c] == player:
            line.append((r, c))
            r -= dr
            c -= dc

        if len(line) >= connect:
            return line

    return []


def is_winning_move(
    board: List[List[int]], row: int, col_1_based: int, player: int, connect: int = 4
) -> bool:
    return len(get_winning_line(board, row, col_1_based, player, connect)) >= connect


def parse_moves(raw: str, cols: int) -> List[int]:
    moves = [int(x.strip()) for x in raw.split(",") if x.strip()]
    if not moves:
        raise ValueError("original_sequence is empty")
    if not all(1 <= x <= cols for x in moves):
        raise ValueError(f"moves out of range 1..{cols}: {moves}")
    return moves


def signature_exists(cur, signature: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM partie
        WHERE signature = %s
        LIMIT 1;
        """,
        (signature,),
    )
    return cur.fetchone() is not None


def infer_mode(source_filename: str) -> str:
    value = (source_filename or "").strip().lower()
    if not value:
        return "import"
    if value.startswith("hybrid_minimax"):
        return "hybrid_minimax"
    if value.startswith("minimax"):
        return "minimax"
    if value.startswith("bga"):
        return "bga"
    if value.startswith("random"):
        return "random"
    return value.split("_")[0]


def replay_game_from_csv_row(row: dict):
    rows = int(row["rows"])
    cols = int(row["cols"])
    starting_color = (row.get("starting_color") or "R").strip().upper()
    confidence = row.get("confiance")

    moves = parse_moves(row["original_sequence"], cols)
    signature = (row.get("canonical_key") or "").strip()
    if not signature:
        raise ValueError("canonical_key is empty")

    board = make_board(rows, cols)

    start_player = 1 if starting_color == "R" else 2
    current_player = start_player

    winner = None
    winning_line = None
    status = "en_cours"

    for move_num, col in enumerate(moves, start=1):
        played_row = drop_piece(board, col, current_player)
        if played_row == -1:
            raise ValueError(f"illegal move at #{move_num}: column {col} full")

        if is_winning_move(board, played_row, col, current_player):
            winner = current_player
            winning_line = get_winning_line(board, played_row, col, current_player)
            status = "terminee"
            break

        if is_board_full(board):
            status = "nulle"
            break

        current_player = 3 - current_player

    # on peut aussi s'aligner sur le CSV si besoin, mais on garde le replay comme source de vérité
    joueur_gagnant = None
    if winner == 1:
        joueur_gagnant = "R"
    elif winner == 2:
        joueur_gagnant = "Y"

    ligne_gagnante = None
    if winning_line:
        ligne_gagnante = str([[r, c] for r, c in winning_line])

    conf = 1
    if confidence not in (None, "", "NULL"):
        try:
            conf = int(confidence)
        except Exception:
            conf = 1

    return {
        "mode": infer_mode(row.get("source_filename") or ""),
        "type_partie": "classique",
        "status": status,
        "joueur_depart": starting_color,
        "joueur_gagnant": joueur_gagnant,
        "ligne_gagnante": ligne_gagnante,
        "signature": signature,
        "rows": rows,
        "cols": cols,
        "confiance": conf,
        "nb_colonnes": cols,
    }


def insert_partie(game):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if signature_exists(cur, game["signature"]):
                conn.rollback()
                return None, "duplicate_signature"

            cur.execute(
                """
                INSERT INTO partie
                    (mode, type_partie, status, joueur_depart, joueur_gagnant,
                     ligne_gagnante, signature, rows, cols, confiance, nb_colonnes)
                VALUES
                    (%s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s, %s)
                RETURNING id_partie;
                """,
                (
                    game["mode"],
                    game["type_partie"],
                    game["status"],
                    game["joueur_depart"],
                    game["joueur_gagnant"],
                    game["ligne_gagnante"],
                    game["signature"],
                    game["rows"],
                    game["cols"],
                    game["confiance"],
                    game["nb_colonnes"],
                ),
            )
            new_id = cur.fetchone()["id_partie"]
            conn.commit()
            return new_id, "inserted"

    except Exception as e:
        conn.rollback()
        return None, f"error: {e}"
    finally:
        conn.close()


def import_csv(csv_path: str):
    inserted = 0
    duplicates = 0
    errors = 0

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)

        for idx, row in enumerate(reader, start=1):
            try:
                game = replay_game_from_csv_row(row)
                new_id, result = insert_partie(game)

                if result == "inserted":
                    inserted += 1
                    print(
                        f"✅ Row {idx} | friend_id={row.get('id')} -> id_partie={new_id} | "
                        f"mode={game['mode']} | start={game['joueur_depart']} | "
                        f"status={game['status']} | sig={game['signature']}"
                    )
                elif result == "duplicate_signature":
                    duplicates += 1
                    print(
                        f"⚠️ Row {idx} skipped | duplicate signature | "
                        f"friend_id={row.get('id')} | sig={game['signature']}"
                    )
                else:
                    errors += 1
                    print(f"❌ Row {idx} failed | friend_id={row.get('id')} | {result}")

            except Exception as e:
                errors += 1
                print(f"❌ Row {idx} parse/replay failed | friend_id={row.get('id')} | {e}")

    print("\n" + "=" * 80)
    print(f"Inserted            : {inserted}")
    print(f"Duplicate signature : {duplicates}")
    print(f"Errors              : {errors}")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Import friend CSV into local partie table only"
    )
    parser.add_argument("--csv", required=True, help="Path to CSV file")
    args = parser.parse_args()

    import_csv(args.csv)


if __name__ == "__main__":
    main()
