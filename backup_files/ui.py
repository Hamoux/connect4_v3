# ui.py
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
from pathlib import Path
import random
import os

from game import Connect4Game
from ai import MinimaxAI
from db.db import (
    canonical_signature_from_history, create_partie, insert_situation, update_links,
    finish_partie, delete_partie, board_to_text, moves_signature,
    update_partie_signature
)
from psycopg2 import IntegrityError



# Optional: load a partie directly from DB into the current UI
try:
    from explorer_tool import pick_partie_id_dialog, load_partie_for_play
except Exception:
    pick_partie_id_dialog = None
    load_partie_for_play = None
try:
    from explorer_tool import open_explorer_from_ui
except Exception:
    open_explorer_from_ui = None
# ============================================================
# CONSTANTES UI / GEOMETRIE
# ============================================================
DEFAULT_ROWS = 9
DEFAULT_COLS = 9
DEFAULT_STARTING_PLAYER = "R"

CELL = 60
PADDING = 12
HOLE_R = 22
TOP_BAR = 32
BOTTOM_BAR = 32

BOARD_BG = "#7a4bb3"
HOLE_COLOR = "#e6e6e6"

SAVE_DIR = "saves"
os.makedirs(SAVE_DIR, exist_ok=True)


class Connect4UI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Puissance 4")
        self.resizable(False, False)

        # ---- config ----
        self.rows = DEFAULT_ROWS
        self.cols = DEFAULT_COLS
        self.starting_player = DEFAULT_STARTING_PLAYER
        self.load_config("config.json")

        # ---- Game + AI ----
        self.game = Connect4Game(self.rows, self.cols, self.starting_player)
        self.ai = MinimaxAI(self.rows, self.cols)

        self.db_enabled = True
        self.db_partie_id = None
        self.db_last_situation_id = None

        # ---- état UI / IA ----
        self.game_id = 0
        self.paused = False
        self.ai_scheduled = False

        # progressive minimax (affichage des scores)
        self.ai_thinking_job = None
        self.ai_iter_depth = 0
        self.ai_iter_cols = []
        self.ai_iter_col_idx = 0
        self.ai_scores = [None for _ in range(self.cols)]

        self._build_ui()
        self.after(0, self.new_game)   # ✅ au lieu de self.new_game()

    # ============================================================
    # CONFIG
    # ============================================================
    def load_config(self, filename: str):
        path = Path(filename)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = int(data.get("rows", self.rows))
            cols = int(data.get("cols", self.cols))
            sp = data.get("starting_player", self.starting_player)

            if rows < 4 or cols < 4:
                raise ValueError("rows/cols trop petits (min 4).")
            if sp not in ("R", "J"):
                raise ValueError("starting_player doit être 'R' ou 'J'.")

            self.rows, self.cols, self.starting_player = rows, cols, sp
        except Exception as e:
            print("Config invalide -> valeurs par défaut :", e)

    # ============================================================
    # UI BUILD
    # ============================================================
    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.grid(row=0, column=0)

        # Canvas (plateau)
        self.canvas = tk.Canvas(
            root,
            width=self.cols * CELL + 2 * PADDING,
            height=self.rows * CELL + 2 * PADDING + TOP_BAR + BOTTOM_BAR,
            bg=BOARD_BG,
            highlightthickness=0
        )
        self.canvas.grid(row=0, column=0, padx=(0, 12))
        self.canvas.bind("<Button-1>", self.on_click)

        # Panel droite
        panel = ttk.Frame(root, width=260)
        panel.grid(row=0, column=1, sticky="ns")
        panel.columnconfigure(0, weight=1)

        ttk.Label(panel, text="À qui de jouer", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 2)
        )
        self.status_var = tk.StringVar(value="Rouge")
        ttk.Label(panel, textvariable=self.status_var).grid(
            row=1, column=0, sticky="w", pady=(0, 8)
        )

        ttk.Label(panel, text="Partie", font=("Segoe UI", 10, "bold")).grid(
            row=2, column=0, sticky="w", pady=(0, 2)
        )
        self.game_id_var = tk.StringVar(value="Partie #0")
        ttk.Label(panel, textvariable=self.game_id_var).grid(
            row=3, column=0, sticky="w", pady=(0, 10)
        )

        ttk.Separator(panel).grid(row=4, column=0, sticky="ew", pady=10)

        # Mode
        ttk.Label(panel, text="Mode de jeu", font=("Segoe UI", 10, "bold")).grid(
            row=5, column=0, sticky="w", pady=(0, 5)
        )
        self.modes = ["0 joueur (IA vs IA)", "1 joueur (vs IA)", "2 joueurs"]
        self.mode_var = tk.StringVar(value="2 joueurs")
        self.mode_combo = ttk.Combobox(panel, values=self.modes, textvariable=self.mode_var, state="readonly")
        self.mode_combo.current(2)
        self.mode_combo.grid(row=6, column=0, sticky="ew", pady=(0, 10))
        
        self.mode_combo.bind("<<ComboboxSelected>>", self.on_mode_change)

        # IA
        ttk.Label(panel, text="IA (robot)", font=("Segoe UI", 10, "bold")).grid(
            row=7, column=0, sticky="w", pady=(0, 5)
        )
        self.ai_types = ["Aléatoire", "Mini-max"]
        self.ai_type_var = tk.StringVar(value="Aléatoire")
        self.ai_type_combo = ttk.Combobox(panel, values=self.ai_types, textvariable=self.ai_type_var, state="readonly")
        self.ai_type_combo.current(0)
        self.ai_type_combo.grid(row=8, column=0, sticky="ew", pady=(0, 6))
        self.ai_type_combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_ai_controls())

        depth_row = ttk.Frame(panel)
        depth_row.grid(row=9, column=0, sticky="ew", pady=(0, 10))
        depth_row.columnconfigure(1, weight=1)

        ttk.Label(depth_row, text="Profondeur").grid(row=0, column=0, sticky="w")
        self.ai_depth_var = tk.IntVar(value=4)
        self.ai_depth_spin = ttk.Spinbox(depth_row, from_=1, to=10, textvariable=self.ai_depth_var, width=5)
        self.ai_depth_spin.grid(row=0, column=1, sticky="e")

        self.thinking_var = tk.StringVar(value="")
        ttk.Label(panel, textvariable=self.thinking_var).grid(row=10, column=0, sticky="w", pady=(0, 6))

        self._sync_ai_controls()

        # Boutons
        ttk.Button(panel, text="Nouvelle partie", command=self.new_game).grid(row=11, column=0, sticky="ew")
        ttk.Button(panel, text="Pause", command=self.pause_game).grid(row=12, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(panel, text="Reprendre", command=self.resume_game).grid(row=13, column=0, sticky="ew")

        ttk.Button(panel, text="Retour (Undo)", command=self.undo_move).grid(row=14, column=0, sticky="ew", pady=(10, 6))
        ttk.Button(panel, text="Reretourner (Redo)", command=self.redo_move).grid(row=15, column=0, sticky="ew")

        ttk.Button(panel, text="Sauvegarder", command=self.save_game).grid(row=16, column=0, sticky="ew", pady=(10, 6))
        ttk.Button(panel, text="Charger", command=self.load_game).grid(row=17, column=0, sticky="ew")

        # Charger depuis la DB (optionnel)
        if pick_partie_id_dialog is not None and load_partie_for_play is not None:
            ttk.Button(panel, text="Charger (DB)", command=self.load_game_from_db).grid(
                row=18, column=0, sticky="ew", pady=(6, 0)
            )

        ttk.Button(panel, text="Paramétrage", command=self.open_settings).grid(row=19, column=0, sticky="ew", pady=(10, 0))
        if open_explorer_from_ui is not None:
            ttk.Button(panel, text="Explorer DB", command=lambda: open_explorer_from_ui(self)).grid(
                row=19, column=0, sticky="ew", pady=(10, 0)
            )

    def _sync_ai_controls(self):
        is_mm = self.ai_type_var.get() == "Mini-max"
        self.ai_depth_spin.config(state=("normal" if is_mm else "disabled"))

    # ============================================================
    # MODE
    # ============================================================
    def get_mode(self) -> int:
        val = self.mode_var.get()
        if val.startswith("0"):
            return 0
        if val.startswith("1"):
            return 1
        return 2

    # ============================================================
    # SCHEDULING IA
    # ============================================================
    def _cancel_ai_thinking(self):
        if self.ai_thinking_job is not None:
            try:
                self.after_cancel(self.ai_thinking_job)
            except Exception:
                pass
            self.ai_thinking_job = None
        self.thinking_var.set("")

    def schedule_ai_if_needed(self):
        if self.game.game_over or self.paused:
            return

        mode = self.get_mode()
        if mode == 2:
            return
        # mode 1: IA = "J"
        if mode == 1 and self.game.current_player != "J":
            return

        if self.ai_scheduled:
            return

        self.ai_scheduled = True
        self.after(120, self.ai_move)

    def ai_move(self):
        self.ai_scheduled = False
        if self.game.game_over or self.paused:
            return

        self.ai_scores = [None for _ in range(self.cols)]
        self.thinking_var.set("")
        self.draw_column_numbers()

        if self.ai_type_var.get() == "Mini-max":
            self.ai_move_minimax_progressive()
        else:
            self.ai_move_random()

    def ai_move_random(self):
        valid = self.game.valid_columns()
        if not valid:
            self.game.game_over = True
            self.game.result = "Match nul"
            self.status_var.set("Match nul")
            return
        col = random.choice(valid)
        self.play_move(col)

    # ============================================================
    # MINIMAX PROGRESSIF (scores sous colonnes)
    # ============================================================
    @staticmethod
    def _copy_board(board):
        return [row[:] for row in board]

    def ai_move_minimax_progressive(self):
        self._cancel_ai_thinking()

        self.ai_iter_depth = 1
        self.ai_iter_cols = self.game.valid_columns()
        self.ai_iter_col_idx = 0
        max_depth = int(self.ai_depth_var.get())

        if not self.ai_iter_cols:
            self.game.game_over = True
            self.game.result = "Match nul"
            self.status_var.set("Match nul")
            return

        self.ai_scores = [None for _ in range(self.cols)]
        for c in self.ai_iter_cols:
            self.ai_scores[c] = 0

        self.thinking_var.set(f"Réflexion ...")
        self.draw_column_numbers()

        self.ai_thinking_job = self.after(10, lambda: self._mm_step_depth(max_depth))

    def _mm_step_depth(self, max_depth):
        if self.game.game_over or self.paused:
            self.ai_thinking_job = None
            return

        if self.ai_iter_col_idx >= len(self.ai_iter_cols):
            if self.ai_iter_depth >= max_depth:
                self.ai_thinking_job = None
                col = self._mm_pick_best_move()
                self.thinking_var.set("")
                self.play_move(col)
                return

            self.ai_iter_depth += 1
            self.ai_iter_col_idx = 0
            self.thinking_var.set(f"Réflexion ...")
            self.draw_column_numbers()
            self.ai_thinking_job = self.after(10, lambda: self._mm_step_depth(max_depth))
            return

        col = self.ai_iter_cols[self.ai_iter_col_idx]
        self.ai_iter_col_idx += 1

        ai_player = self.game.current_player
        tmp = self._copy_board(self.game.board)

        r = self.ai.next_open_row(tmp, col)
        if r is None:
            score = -10**9
        else:
            tmp[r][col] = ai_player
            score = self.ai.minimax(
                tmp,
                depth=self.ai_iter_depth - 1,
                alpha=-10**9,
                beta=10**9,
                maximizing=False,
                ai_player=ai_player
            )

        self.ai_scores[col] = score
        self.draw_column_numbers()
        self.ai_thinking_job = self.after(45, lambda: self._mm_step_depth(max_depth))

    def _mm_pick_best_move(self):
        valid = self.game.valid_columns()
        if not valid:
            return 0

        best_score = -10**9
        best_cols = []
        for c in valid:
            sc = self.ai_scores[c]
            if sc is None:
                continue
            if sc > best_score:
                best_score = sc
                best_cols = [c]
            elif sc == best_score:
                best_cols.append(c)

        if not best_cols:
            return random.choice(valid)

        center = self.cols // 2
        best_cols.sort(key=lambda x: abs(x - center))
        return best_cols[0]

    # ============================================================
    # PLAY MOVE
    # ============================================================
    def play_move(self, col):
        self._cancel_ai_thinking()

        ok, winning = self.game.drop(col)
        if not ok:
            return

        # ✅ Créer la partie DB SEULEMENT au 1er coup
        if self.db_enabled and self.db_partie_id is None:
            mode = self.get_mode()
            if mode == 0:
                type_partie = "IA_VS_IA"
            elif mode == 1:
                type_partie = "HUMAIN_VS_IA"
            else:
                type_partie = "HUMAIN_VS_HUMAIN"

            mode_txt = self.mode_var.get()

            try:
                # confiance : 1 = aléatoire, 2 = minimax (0 = exprès perdre si tu l’ajoutes plus tard)
                confiance = 2 if self.ai_type_var.get() == "Mini-max" else 1

                self.db_partie_id = create_partie(
                    mode=mode_txt,
                    type_partie=type_partie,
                    status="EN_COURS",
                    joueur_depart=self.game.starting_player,
                    nb_colonnes=self.cols,
                    confiance=confiance
                )

                self.db_last_situation_id = None

                # Mettre tout de suite la signature canonique du 1er coup
                sig0 = canonical_signature_from_history(self.game.history, self.cols)
                update_partie_signature(self.db_partie_id, sig0)

            except IntegrityError:
                # Cas rare: si ton create_partie / update_signature déclenche un conflit
                # (ex: signature unique déjà prise). On stoppe l'enregistrement DB.
                try:
                    if self.db_partie_id is not None:
                        delete_partie(self.db_partie_id)
                except Exception:
                    pass
                self.db_partie_id = None
                self.db_last_situation_id = None

        # ✅ DB: sauvegarder la situation après chaque coup
        if self.db_enabled and self.db_partie_id is not None:
            num = len(self.game.history)  # 1er coup => 1
            plateau_txt = board_to_text(self.game.board)
            joueur = self.game.history[-1][2]  # "R" ou "J"

            new_id = insert_situation(
                id_partie=self.db_partie_id,
                numero_coup=num,
                plateau=plateau_txt,
                joueur=joueur,
                precedent=self.db_last_situation_id,
                suivant=None
            )
            if self.db_last_situation_id is not None:
                update_links(self.db_last_situation_id, new_id)
            self.db_last_situation_id = new_id

            # ✅ DB: update signature canonique après chaque coup
            if not self.game.game_over:
                try:
                    sig = canonical_signature_from_history(self.game.history, self.cols)
                    update_partie_signature(self.db_partie_id, sig)
                except IntegrityError:
                    # La partie (ou sa symétrie) existe déjà -> on supprime la partie courante
                    try:
                        delete_partie(self.db_partie_id)
                    except Exception:
                        pass
                    self.db_partie_id = None
                    self.db_last_situation_id = None

        self.ai_scores = [None for _ in range(self.cols)]
        self.thinking_var.set("")
        self.draw_from_game()

        # ✅ FIN DE PARTIE -> update table partie
        if self.game.game_over:
            if self.game.result == "Match nul":
                self.status_var.set("Match nul")
            else:
                self.status_var.set(f"Vainqueur : {self.game.result}")

            if winning:
                self.highlight_winner(winning)
            self.draw_column_numbers()

            # ✅ DB: clôturer la partie
            if self.db_enabled and self.db_partie_id is not None:
                sig = canonical_signature_from_history(self.game.history, self.cols)

                if self.game.result == "Match nul":
                    finish_partie(
                        self.db_partie_id,
                        status="NULLE",
                        signature=sig
                    )
                else:
                    gagnant = "R" if self.game.result == "Rouge" else "J"
                    lg = ";".join(f"({r},{c})" for (r, c) in (winning or []))

                    try:
                        finish_partie(
                            self.db_partie_id,
                            status="TERMINEE",
                            joueur_gagnant=gagnant,
                            ligne_gagnante=lg,
                            signature=sig
                        )
                    except IntegrityError:
                        # si signature UNIQUE doublon -> on supprime la partie
                        delete_partie(self.db_partie_id)
                        self.db_partie_id = None
                        self.db_last_situation_id = None

            return

        self.status_var.set("Rouge" if self.game.current_player == "R" else "Jaune")
        self.schedule_ai_if_needed()

    # ============================================================
    # DRAW
    # ============================================================
    def draw_from_game(self):
        self.canvas.config(
            width=self.cols * CELL + 2 * PADDING,
            height=self.rows * CELL + 2 * PADDING + TOP_BAR + BOTTOM_BAR
        )
        self.canvas.delete("all")

        board_top = PADDING + TOP_BAR

        # trous
        for r in range(self.rows):
            for c in range(self.cols):
                cx = PADDING + c * CELL + CELL // 2
                cy = board_top + r * CELL + CELL // 2
                x0, y0 = cx - HOLE_R, cy - HOLE_R
                x1, y1 = cx + HOLE_R, cy + HOLE_R
                self.canvas.create_oval(
                    x0, y0, x1, y1,
                    fill=HOLE_COLOR,
                    outline="#cfcfcf",
                    width=2
                )

        # pions
        for r in range(self.rows):
            for c in range(self.cols):
                p = self.game.board[r][c]
                if p != 0:
                    self._draw_token(r, c, p)

        self.draw_column_numbers()

    def _draw_token(self, row, col, player):
        board_top = PADDING + TOP_BAR
        cx = PADDING + col * CELL + CELL // 2
        cy = board_top + row * CELL + CELL // 2
        x0, y0 = cx - HOLE_R, cy - HOLE_R
        x1, y1 = cx + HOLE_R, cy + HOLE_R
        color = "red" if player == "R" else "yellow"
        self.canvas.create_oval(x0, y0, x1, y1, fill=color, outline="#333", width=2)

    def highlight_winner(self, cells):
        board_top = PADDING + TOP_BAR
        for r, c in cells:
            cx = PADDING + c * CELL + CELL // 2
            cy = board_top + r * CELL + CELL // 2
            x0, y0 = cx - HOLE_R, cy - HOLE_R
            x1, y1 = cx + HOLE_R, cy + HOLE_R
            self.canvas.create_oval(x0, y0, x1, y1, outline="#00ff00", width=4)

    def draw_column_numbers(self):
        board_top = PADDING + TOP_BAR
        board_bottom = board_top + self.rows * CELL

        box_h = TOP_BAR - 8
        y1_top = PADDING + 4
        y0_top = y1_top + box_h

        y0_bottom = board_bottom + 4
        y1_bottom = y0_bottom + box_h

        for c in range(self.cols):
            x0 = PADDING + c * CELL + 4
            x1 = PADDING + (c + 1) * CELL - 4

            label_top = str(c + 1)
            score = self.ai_scores[c] if hasattr(self, "ai_scores") else None
            if score is None:
                label_bottom = str(c + 1)
            else:
                label_bottom = f"{score:+d}" if isinstance(score, int) else str(score)

            # Haut
            self.canvas.create_rectangle(x0, y1_top, x1, y0_top, fill="#e9e9e9", outline="#bdbdbd", width=1)
            self.canvas.create_text((x0 + x1) / 2, (y1_top + y0_top) / 2,
                                    text=label_top, fill="#222", font=("Segoe UI", 10, "bold"))
            # Bas
            self.canvas.create_rectangle(x0, y0_bottom, x1, y1_bottom, fill="#e9e9e9", outline="#bdbdbd", width=1)
            self.canvas.create_text((x0 + x1) / 2, (y0_bottom + y1_bottom) / 2,
                                    text=label_bottom, fill="#222", font=("Segoe UI", 9, "bold"))

    # ============================================================
    # EVENTS
    # ============================================================
    def on_click(self, event):
        if self.game.game_over or self.paused:
            return

        mode = self.get_mode()
        if mode == 0:
            return
        if mode == 1 and self.game.current_player == "J":
            return

        x, y = event.x, event.y
        board_top = PADDING + TOP_BAR
        board_bottom = board_top + self.rows * CELL

        if x < PADDING or x > PADDING + self.cols * CELL:
            return
        if y < board_top or y > board_bottom:
            return

        col = int((x - PADDING) // CELL)
        self.play_move(col)

    # ============================================================
    # BUTTONS
    # ============================================================
    def new_game(self):
        self._cancel_ai_thinking()
        self.game_id += 1
        self.game_id_var.set(f"Partie #{self.game_id}")

        self.game.reset()
        self.ai.clear_cache()

        # ✅ ne PAS créer de partie en DB ici
        # (sinon tu crées des lignes avec signature NULL/vides, et ça casse la mutualisation)
        if self.db_enabled:
            self.db_partie_id = None
            self.db_last_situation_id = None

        self.ai_scores = [None for _ in range(self.cols)]
        self.paused = False
        self.status_var.set("Rouge" if self.game.current_player == "R" else "Jaune")
        self.draw_from_game()
        self.schedule_ai_if_needed()


    def pause_game(self):
        if self.game.game_over:
            return
        self._cancel_ai_thinking()
        self.paused = True
        self.status_var.set("⏸ Pause")

    def resume_game(self):
        if self.game.game_over:
            return
        self.paused = False
        self.status_var.set("Rouge" if self.game.current_player == "R" else "Jaune")
        self.schedule_ai_if_needed()

    def undo_move(self):
        if self.paused:
            return
        self._cancel_ai_thinking()
        if self.game.undo():
            self.ai.clear_cache()
            self.ai_scores = [None for _ in range(self.cols)]
            self.status_var.set("Rouge" if self.game.current_player == "R" else "Jaune")
            self.draw_from_game()

    def redo_move(self):
        if self.paused:
            return
        self._cancel_ai_thinking()
        if self.game.redo():
            self.ai.clear_cache()
            self.ai_scores = [None for _ in range(self.cols)]
            self.status_var.set("Rouge" if self.game.current_player == "R" else "Jaune")
            self.draw_from_game()
            self.schedule_ai_if_needed()

    # ============================================================
    # SETTINGS
    # ============================================================
    def open_settings(self):
        win = tk.Toplevel(self)
        win.title("Paramétrage")

        rows_var = tk.IntVar(value=self.rows)
        cols_var = tk.IntVar(value=self.cols)
        sp_var = tk.StringVar(value=self.starting_player)

        ttk.Label(win, text="Lignes (min 4)").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        ttk.Entry(win, textvariable=rows_var).grid(row=0, column=1, padx=10, pady=5)

        ttk.Label(win, text="Colonnes (min 4)").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        ttk.Entry(win, textvariable=cols_var).grid(row=1, column=1, padx=10, pady=5)

        ttk.Label(win, text="Couleur de départ").grid(row=2, column=0, sticky="w", padx=10, pady=5)
        ttk.Radiobutton(win, text="Rouge", variable=sp_var, value="R").grid(row=2, column=1, sticky="w")
        ttk.Radiobutton(win, text="Jaune", variable=sp_var, value="J").grid(row=3, column=1, sticky="w")

        def save_cfg():
            r = rows_var.get()
            c = cols_var.get()
            sp = sp_var.get()

            if r < 4 or c < 4:
                messagebox.showerror("Erreur", "Minimum 4x4")
                return
            if sp not in ("R", "J"):
                messagebox.showerror("Erreur", "Starting player invalide")
                return

            cfg = {"rows": r, "cols": c, "starting_player": sp}
            Path("config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

            self.rows, self.cols, self.starting_player = r, c, sp

            self.game.set_params(r, c, sp)
            self.ai.reset_params(r, c)

            self.ai_scores = [None for _ in range(self.cols)]
            self.paused = False
            self.status_var.set("Rouge" if self.game.current_player == "R" else "Jaune")
            self.draw_from_game()
            self.schedule_ai_if_needed()
            win.destroy()

        ttk.Button(win, text="Enregistrer", command=save_cfg).grid(row=4, column=0, columnspan=2, pady=10)

    # ============================================================
    # SAVE / LOAD
    # ============================================================
    def save_game(self):
        if self.paused:
            messagebox.showinfo("Info", "Reprends la partie avant de sauvegarder.")
            return

        data = {
            "game_id": self.game_id,
            "rows": self.rows,
            "cols": self.cols,
            "starting_player": self.starting_player,
            "current_player": self.game.current_player,
            "board": self.game.board,
            "history": self.game.history,
            "future": self.game.future,
            "game_over": self.game.game_over,
            "result": self.game.result,
            "mode": self.mode_var.get(),
            "ai_type": self.ai_type_var.get(),
            "ai_depth": int(self.ai_depth_var.get()),
        }

        default_name = f"partie_{self.game_id}.json"
        path = filedialog.asksaveasfilename(
            initialdir=SAVE_DIR,
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("Sauvegarde Puissance4", "*.json")]
        )
        if not path:
            return

        try:
            Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
            messagebox.showinfo("Sauvegarde", "Partie sauvegardée ✅")
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible de sauvegarder.\n{e}")

    def load_game(self):
        if self.paused:
            messagebox.showinfo("Info", "Reprends la partie avant de charger.")
            return

        self._cancel_ai_thinking()

        path = filedialog.askopenfilename(
            initialdir=SAVE_DIR,
            filetypes=[("Sauvegarde Puissance4", "*.json")]
        )
        if not path:
            return

        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            messagebox.showerror("Erreur", "Fichier invalide.")
            return

        required = ["rows", "cols", "starting_player", "current_player", "board"]
        for k in required:
            if k not in data:
                messagebox.showerror("Erreur", f"Fichier incomplet (manque {k}).")
                return

        self.rows = int(data["rows"])
        self.cols = int(data["cols"])
        self.starting_player = data["starting_player"]

        self.game.set_params(self.rows, self.cols, self.starting_player)
        self.ai.reset_params(self.rows, self.cols)

        self.game.current_player = data["current_player"]
        self.game.board = data["board"]
        self.game.history = data.get("history", [])
        self.game.future = data.get("future", [])
        self.game.game_over = bool(data.get("game_over", False))
        self.game.result = data.get("result", None)

        self.game_id = int(data.get("game_id", self.game_id))
        self.game_id_var.set(f"Partie #{self.game_id}")

        mode_txt = data.get("mode", "2 joueurs")
        if mode_txt in self.modes:
            self.mode_var.set(mode_txt)

        ai_type = data.get("ai_type", "Aléatoire")
        if ai_type in self.ai_types:
            self.ai_type_var.set(ai_type)

        self.ai_depth_var.set(int(data.get("ai_depth", self.ai_depth_var.get())))
        self._sync_ai_controls()

        self.paused = False
        self.ai_scores = [None for _ in range(self.cols)]
        self.draw_from_game()

        if self.game.game_over and self.game.result:
            self.status_var.set(f"Fin : {self.game.result}")
        else:
            self.status_var.set("Rouge" if self.game.current_player == "R" else "Jaune")

        self.schedule_ai_if_needed()

    def load_game_from_db(self):
        """Charge une partie existante depuis PostgreSQL et la remet dans l'UI.

        - Préserve le mode/IA sélectionnés
        - Reprend la partie sur le même id_partie (les nouveaux coups seront insérés à la suite)
        """

        if pick_partie_id_dialog is None or load_partie_for_play is None:
            messagebox.showerror("Erreur", "Le module explorer_tool n'est pas disponible.")
            return

        if self.paused:
            messagebox.showinfo("Info", "Reprends la partie avant de charger.")
            return

        self._cancel_ai_thinking()

        try:
            partie_id = pick_partie_id_dialog(self)
        except Exception as e:
            messagebox.showerror("DB", f"Impossible d'ouvrir le sélecteur de partie.\n{e}")
            return

        if not partie_id:
            return

        try:
            payload = load_partie_for_play(int(partie_id))
        except Exception as e:
            messagebox.showerror("DB", f"Impossible de charger la partie #{partie_id}.\n{e}")
            return

        # Apply parameters
        self.rows = int(payload["rows"])
        self.cols = int(payload["cols"])
        self.starting_player = payload.get("starting_player") or self.starting_player

        self.game.set_params(self.rows, self.cols, self.starting_player)
        self.ai.reset_params(self.rows, self.cols)

        # Load game state
        self.game.board = payload.get("board")
        self.game.history = payload.get("history", [])
        self.game.future = payload.get("future", [])
        self.game.current_player = payload.get("current_player") or self.game.current_player
        self.game.game_over = bool(payload.get("game_over", False))
        self.game.result = payload.get("result", None)

        # DB continuation
        self.db_enabled = True
        self.db_partie_id = int(partie_id)
        self.db_last_situation_id = payload.get("last_situation_id")

        # UI refresh
        self.game_id = int(partie_id)
        self.game_id_var.set(f"Partie #{self.game_id}")

        self.paused = False
        self.ai_scores = [None for _ in range(self.cols)]
        self.draw_from_game()

        if self.game.game_over and self.game.result:
            self.status_var.set(f"Fin : {self.game.result}")
        else:
            self.status_var.set("Rouge" if self.game.current_player == "R" else "Jaune")

        self.schedule_ai_if_needed()

    def on_mode_change(self, _e=None):
        # si une partie a déjà commencé, on prévient
        if self.game.history and not self.game.game_over:
            if not messagebox.askyesno("Changer le mode", "Changer le mode démarre une nouvelle partie. Continuer ?"):
                return
        self.new_game()

