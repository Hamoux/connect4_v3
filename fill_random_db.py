# fill_random_db.py
import random
from psycopg2 import IntegrityError

from game import Connect4Game
from db.db import (
    create_partie, insert_situation, update_links,
    finish_partie, delete_partie,
    canonical_signature_from_history,
)

def board_to_text(board):
    # même format que ton db.py (0/R/J)
    return "\n".join("".join(str(x) for x in row) for row in board)

def winning_line_to_text(winning_line):
    # format simple en texte, lisible + compact
    # ex: "(7,3);(6,4);(5,5);(4,6)"
    if not winning_line:
        return None
    return ";".join(f"({r},{c})" for (r, c) in winning_line)

def play_one_random_game(rows=9, cols=9, starting_player="R", confiance=1):
    g = Connect4Game(rows=rows, cols=cols, starting_player=starting_player)

    # 1) créer la partie (avec rows/cols pour éviter les defaults en DB)
    pid = create_partie(
        mode="batch_random",
        type_partie="IA_VS_IA",
        status="EN_COURS",
        joueur_depart=starting_player,
        rows=rows,
        cols=cols,
        nb_colonnes=cols,
        confiance=confiance
    )

    last_sid = None
    winning_line = None

    try:
        # 2) jouer jusqu’à fin
        while not g.game_over:
            valid = g.valid_columns()
            if not valid:
                break

            col = random.choice(valid)
            ok, wl = g.drop(col)
            if not ok:
                break

            if wl:
                winning_line = wl  # on garde la ligne gagnante

            num = len(g.history)
            plateau = board_to_text(g.board)
            joueur = g.history[-1][2]  # "R" ou "J"

            sid = insert_situation(
                id_partie=pid,
                numero_coup=num,
                plateau=plateau,
                joueur=joueur,
                precedent=last_sid,
                suivant=None
            )

            if last_sid is not None:
                update_links(last_sid, sid)
            last_sid = sid

        # 3) signature canonique
        sig = canonical_signature_from_history(g.history, cols)

        # 4) statut + gagnant
        if g.result == "Rouge":
            gagnant = "R"
            status = "TERMINEE"
        elif g.result == "Jaune":
            gagnant = "J"
            status = "TERMINEE"
        elif g.result == "Match nul":
            gagnant = "D"
            status = "NULLE"   # ou "TERMINEE" selon ton choix
        else:
            gagnant = None
            status = "EN_COURS"

        # 5) ligne gagnante (texte) si victoire
        lg_txt = winning_line_to_text(winning_line) if status == "TERMINEE" else None

        finish_partie(
            id_partie=pid,
            status=status,
            joueur_gagnant=gagnant,
            ligne_gagnante=lg_txt,
            signature=sig
        )

        return True

    except IntegrityError:
        # doublon signature (unique) ou autre contrainte -> on supprime la partie créée
        try:
            delete_partie(pid)
        except Exception:
            pass
        return False

def fill(n=500, rows=9, cols=9, starting_player="R", confiance=1):
    ok = 0
    tries = 0
    while ok < n:
        tries += 1
        if play_one_random_game(rows=rows, cols=cols, starting_player=starting_player, confiance=confiance):
            ok += 1
            if ok % 50 == 0 or ok == 1:
                print(f"✅ {ok}/{n} parties insérées (essais={tries})")

    print(f"🎉 Terminé: {ok} parties insérées (essais totaux={tries})")

if __name__ == "__main__":
    fill(n=500, rows=9, cols=9, starting_player="R", confiance=1)
