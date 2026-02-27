# explorer_tool.py
import os
import re
import json
import time
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import IntegrityError

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from bga_puppet import import_table_id_connect4

# ✅ Debug console (errors + logs)
DEBUG = True


# ============================================================
# DB helpers
# ============================================================

def get_conn():
    host = os.getenv("PGHOST", "localhost")
    port = int(os.getenv("PGPORT", "5432"))
    dbname = os.getenv("PGDATABASE", "Connect4DB")
    user = os.getenv("PGUSER", "postgres")
    password = os.getenv("PGPASSWORD", "Celina123")
    return psycopg2.connect(
        host=host, port=port, dbname=dbname, user=user, password=password,
        cursor_factory=RealDictCursor
    )

def q_all(sql, params=None):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()

def q_one(sql, params=None):
    rows = q_all(sql, params)
    return rows[0] if rows else None

def exec_sql(sql, params=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            conn.commit()


# ============================================================
# Helpers signature / plateau
# ============================================================

def sanitize_signature(sig: str) -> str:
    if not sig:
        return ""
    return re.sub(r"\D", "", sig)

def mirror_moves_signature(sig: str, cols: int) -> str:
    out = []
    for ch in sanitize_signature(sig):
        c = int(ch)
        out.append(str(cols + 1 - c))
    return "".join(out)

def canonical_signature(sig: str, cols: int) -> str:
    s = sanitize_signature(sig)
    ms = mirror_moves_signature(s, cols)
    return min(s, ms) if s and ms else s

def empty_board(rows, cols):
    return [[0 for _ in range(cols)] for _ in range(rows)]

def mirror_board(board):
    return [list(reversed(row)) for row in board]

def parse_board_text(plateau: str, rows: int, cols: int):
    if plateau is None:
        raise ValueError("plateau is NULL")

    s = plateau.strip("\n\r ")
    lines = s.splitlines()

    # format "rows lignes" de longueur cols (0/R/J)
    if len(lines) == rows and all(len(line) == cols for line in lines):
        board = empty_board(rows, cols)
        for r in range(rows):
            for c in range(cols):
                ch = lines[r][c]
                if ch in ("0", "."):
                    board[r][c] = 0
                elif ch in ("R", "J"):
                    board[r][c] = ch
                else:
                    board[r][c] = 0
        return board

    # fallback flatten
    flat = re.sub(r"[^0RJ\.]", "", s)
    if len(flat) == rows * cols:
        board = empty_board(rows, cols)
        i = 0
        for r in range(rows):
            for c in range(cols):
                ch = flat[i]; i += 1
                if ch in ("0", "."):
                    board[r][c] = 0
                elif ch in ("R", "J"):
                    board[r][c] = ch
                else:
                    board[r][c] = 0
        return board

    raise ValueError(
        f"plateau format inattendu: {len(lines)} lignes, "
        f"len0={len(lines[0]) if lines else 0}, rows={rows}, cols={cols}"
    )


# ============================================================
# Replay: IMPORTANT pour UI (history format = (row,col,player))
# ============================================================

def replay_from_signature(sig: str, rows: int, cols: int, starting_player: str):
    """
    Rejoue la signature et retourne:
    board, history [(r,c,p)], next_player
    """
    board = empty_board(rows, cols)
    player = starting_player
    hist = []

    for ch in sanitize_signature(sig):
        col = int(ch) - 1
        if col < 0 or col >= cols:
            raise ValueError(f"Coup invalide: colonne hors bornes {ch}")

        placed_row = None
        for r in range(rows - 1, -1, -1):
            if board[r][col] == 0:
                board[r][col] = player
                placed_row = r
                break

        if placed_row is None:
            raise ValueError(f"Coup invalide: colonne pleine {ch}")

        hist.append((placed_row, col, player))
        player = "J" if player == "R" else "R"

    return board, hist, player


# ============================================================
# UI: Explorer
# ============================================================

PADDING = 12
TOP_BAR = 20
BOTTOM_BAR = 26


class DBExplorer(tk.Toplevel):
    def __init__(self, master=None, config_path="config.json"):
        super().__init__(master)
        self.title("Explorateur DB - Connect4")
        self.geometry("1100x700")

        # config
        self.rows, self.cols, self.starting_player = 9, 9, "R"
        try:
            cfg = json.load(open(config_path, "r", encoding="utf-8"))
            self.rows = int(cfg.get("rows", self.rows))
            self.cols = int(cfg.get("cols", self.cols))
            self.starting_player = cfg.get("starting_player", self.starting_player)
        except Exception:
            pass

        self.current_partie_id = None
        self.situations = []
        self.current_idx = 0
        self.show_mirror = tk.BooleanVar(value=False)
        self._ignore_scale_callback = False
        self._last_canvas_size = (0, 0)

        self._build_ui()
        self.refresh_parties()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        root = ttk.Frame(self, padding=8)
        root.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=3)
        root.rowconfigure(0, weight=1)

        # LEFT
        left = ttk.Frame(root)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)

        ttk.Label(left, text="Parties", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")

        search_row = ttk.Frame(left)
        search_row.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        search_row.columnconfigure(0, weight=1)
        self.search_var = tk.StringVar()
        entry = ttk.Entry(search_row, textvariable=self.search_var)
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda _e: self.refresh_parties())
        ttk.Button(search_row, text="Rechercher", command=self.refresh_parties).grid(row=0, column=1, padx=(6, 0))

        # Treeview (avec mode)
        self.parties_tree = ttk.Treeview(left, columns=("id", "mode", "status", "type", "sig"), show="headings")
        for col, w in [("id", 60), ("mode", 80), ("status", 110), ("type", 130), ("sig", 220)]:
            self.parties_tree.heading(col, text=col)
            self.parties_tree.column(col, width=w, anchor="w")
        self.parties_tree.grid(row=2, column=0, sticky="nsew")
        self.parties_tree.bind("<<TreeviewSelect>>", self._on_select_partie)

        ttk.Separator(left).grid(row=3, column=0, sticky="ew", pady=8)
        ttk.Button(left, text="Importer une partie (.txt)", command=self.import_partie_from_filename).grid(
            row=4, column=0, sticky="ew"
        )

        # ---- Import BGA (Mission 4.1)
        ttk.Label(left, text="Importer depuis BGA", font=("Segoe UI", 10, "bold")).grid(
            row=5, column=0, sticky="w", pady=(10, 4)
        )

        bga_row = ttk.Frame(left)
        bga_row.grid(row=6, column=0, sticky="ew", pady=(0, 6))
        bga_row.columnconfigure(0, weight=1)

        self.bga_table_var = tk.StringVar()
        ttk.Entry(bga_row, textvariable=self.bga_table_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(bga_row, text="Importer", command=self.import_partie_from_bga).grid(row=0, column=1, padx=(6, 0))

        # RIGHT
        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        details = ttk.Frame(right)
        details.grid(row=0, column=0, sticky="ew")
        self.details_var = tk.StringVar(value="Sélectionne une partie à gauche.")
        ttk.Label(details, text="Détails", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(details, textvariable=self.details_var, justify="left").grid(row=1, column=0, sticky="w")

        toggles = ttk.Frame(right)
        toggles.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        ttk.Checkbutton(
            toggles,
            text="Vue symétrique (miroir)",
            variable=self.show_mirror,
            command=self._redraw_current
        ).pack(side="left")

        canvas_frame = ttk.Frame(right)
        canvas_frame.grid(row=2, column=0, sticky="nsew")
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(canvas_frame, bg="white")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        controls = ttk.Frame(right)
        controls.grid(row=3, column=0, sticky="ew", pady=(6, 0))

        self.step_label = ttk.Label(controls, text="coup: - / -")
        self.step_label.pack(side="left")

        nav = ttk.Frame(controls)
        nav.pack(side="right")
        ttk.Button(nav, text="|<", width=3, command=self.go_first).pack(side="left", padx=2)
        ttk.Button(nav, text="<", width=3, command=self.go_prev).pack(side="left", padx=2)
        ttk.Button(nav, text=">", width=3, command=self.go_next).pack(side="left", padx=2)
        ttk.Button(nav, text=">|", width=3, command=self.go_last).pack(side="left", padx=2)

        self.step_scale = ttk.Scale(right, orient="horizontal", from_=0, to=0, command=self._on_scale)
        self.step_scale.grid(row=4, column=0, sticky="ew", pady=(6, 0))

    def _on_canvas_resize(self, event):
        size = (event.width, event.height)
        if size == self._last_canvas_size:
            return
        self._last_canvas_size = size
        self._redraw_current()

    # ----------------- data
    def refresh_parties(self):
        for item in self.parties_tree.get_children():
            self.parties_tree.delete(item)

        term = (self.search_var.get() or "").strip()
        if term:
            rows = q_all(
                """
                SELECT id_partie, mode, status, type_partie, signature
                FROM partie
                WHERE CAST(id_partie AS TEXT) LIKE %s OR COALESCE(signature,'') ILIKE %s
                ORDER BY id_partie DESC
                LIMIT 200
                """,
                (f"%{term}%", f"%{term}%"),
            )
        else:
            rows = q_all(
                """
                SELECT id_partie, mode, status, type_partie, signature
                FROM partie
                ORDER BY id_partie DESC
                LIMIT 200
                """
            )

        for r in rows:
            self.parties_tree.insert(
                "", "end",
                values=(
                    r["id_partie"],
                    r.get("mode"),
                    r.get("status"),
                    r.get("type_partie"),
                    (r.get("signature") or "")[:30],
                )
            )

    def _on_select_partie(self, _evt=None):
        sel = self.parties_tree.selection()
        if not sel:
            return
        vals = self.parties_tree.item(sel[0], "values")
        if not vals:
            return
        try:
            pid = int(vals[0])
        except Exception:
            return
        try:
            self.load_partie(pid)
        except Exception as e:
            messagebox.showerror("Erreur", f"Chargement partie échoué:\n{e}")

    def load_partie(self, id_partie: int):
        self.current_partie_id = id_partie
        partie = q_one("SELECT * FROM partie WHERE id_partie=%s", (id_partie,))
        if not partie:
            raise ValueError(f"Partie {id_partie} introuvable")

        self.situations = q_all(
            """
            SELECT id_situation, numero_coup, plateau, joueur
            FROM situation
            WHERE id_partie=%s
            ORDER BY numero_coup ASC
            """,
            (id_partie,),
        )

        sig = partie.get("signature") or ""
        msig = mirror_moves_signature(sig, self.cols) if sig else ""
        self.details_var.set(
            f"id_partie: {id_partie}\n"
            f"mode: {partie.get('mode')}\n"
            f"status: {partie.get('status')}\n"
            f"type: {partie.get('type_partie')}\n"
            f"signature: {sig}\n"
            f"miroir: {msig}\n"
            f"nb coups: {len(self.situations)}"
        )

        max_idx = max(0, len(self.situations) - 1)
        self._ignore_scale_callback = True
        try:
            self.step_scale.configure(from_=0, to=max_idx)
            self.current_idx = 0
            self.step_scale.set(0)
        finally:
            self._ignore_scale_callback = False

        self._redraw_current()

    # ----------------- navigation
    def _on_scale(self, value):
        if self._ignore_scale_callback:
            return
        if not self.situations:
            return
        try:
            idx = int(float(value))
        except Exception:
            return
        idx = max(0, min(idx, len(self.situations) - 1))
        self.current_idx = idx
        self._redraw_current()

    def _set_scale(self, idx: int):
        self._ignore_scale_callback = True
        try:
            self.step_scale.set(idx)
        finally:
            self._ignore_scale_callback = False

    def go_first(self):
        if not self.situations:
            return
        self.current_idx = 0
        self._set_scale(0)
        self._redraw_current()

    def go_last(self):
        if not self.situations:
            return
        self.current_idx = len(self.situations) - 1
        self._set_scale(self.current_idx)
        self._redraw_current()

    def go_prev(self):
        if not self.situations:
            return
        self.current_idx = max(0, self.current_idx - 1)
        self._set_scale(self.current_idx)
        self._redraw_current()

    def go_next(self):
        if not self.situations:
            return
        self.current_idx = min(len(self.situations) - 1, self.current_idx + 1)
        self._set_scale(self.current_idx)
        self._redraw_current()

    # ----------------- drawing (responsive)
    def _redraw_current(self):
        if not self.situations:
            self.canvas.delete("all")
            self.step_label.config(text="coup: - / -")
            return

        st = self.situations[self.current_idx]
        board = parse_board_text(st.get("plateau"), self.rows, self.cols)
        if self.show_mirror.get():
            board = mirror_board(board)

        self._draw_board(board)
        self.step_label.config(
            text=f"coup: {st.get('numero_coup')} / {self.situations[-1].get('numero_coup')}"
        )

    def _draw_board(self, board):
        self.canvas.delete("all")

        avail_w = max(1, self.canvas.winfo_width())
        avail_h = max(1, self.canvas.winfo_height())

        usable_w = avail_w - 2 * PADDING
        usable_h = avail_h - 2 * PADDING - TOP_BAR - BOTTOM_BAR
        if usable_w <= 10 or usable_h <= 10:
            return

        cell = int(min(usable_w / self.cols, usable_h / self.rows))
        cell = max(18, cell)
        hole_r = max(6, int(cell * 0.38))

        board_top = PADDING + TOP_BAR

        # holes
        for r in range(self.rows):
            for c in range(self.cols):
                cx = PADDING + c * cell + cell // 2
                cy = board_top + r * cell + cell // 2
                x0, y0 = cx - hole_r, cy - hole_r
                x1, y1 = cx + hole_r, cy + hole_r
                self.canvas.create_oval(
                    x0, y0, x1, y1,
                    fill="#f4f4f4", outline="#cfcfcf", width=2
                )

        # tokens
        for r in range(self.rows):
            for c in range(self.cols):
                p = board[r][c]
                if p in ("R", "J"):
                    self._draw_token_scaled(r, c, p, cell, hole_r)

        # col numbers
        y = board_top + self.rows * cell + 12
        for c in range(self.cols):
            x = PADDING + c * cell + cell // 2
            self.canvas.create_text(x, y, text=str(c + 1), fill="#222", font=("Segoe UI", 10, "bold"))

    def _draw_token_scaled(self, row, col, player, cell, hole_r):
        board_top = PADDING + TOP_BAR
        cx = PADDING + col * cell + cell // 2
        cy = board_top + row * cell + cell // 2
        x0, y0 = cx - hole_r, cy - hole_r
        x1, y1 = cx + hole_r, cy + hole_r
        color = "red" if player == "R" else "yellow"
        self.canvas.create_oval(x0, y0, x1, y1, fill=color, outline="#333", width=2)

    # ----------------- import (.txt)
    def import_partie_from_filename(self):
        path = filedialog.askopenfilename(
            title="Choisir un fichier .txt",
            filetypes=[("Text", "*.txt"), ("All", "*.*")]
        )
        if not path:
            return

        base = os.path.basename(path)
        name, _ = os.path.splitext(base)
        sig_raw = sanitize_signature(name)
        if not sig_raw:
            messagebox.showwarning("Import", "Nom de fichier invalide: aucune colonne détectée.")
            return

        sig_can = canonical_signature(sig_raw, self.cols)
        existing = q_one("SELECT id_partie FROM partie WHERE signature=%s", (sig_can,))
        sig_mirror = mirror_moves_signature(sig_raw, self.cols)

        if existing:
            msg = (
                "Partie déjà en base ✅\n"
                f"ID: {existing['id_partie']}\n"
                f"canonique: {sig_can}\n"
                f"miroir: {sig_mirror}"
            )
            messagebox.showinfo("Import", msg)
            self.load_partie(existing["id_partie"])
            return

        try:
            partie = q_one(
                """
                INSERT INTO partie (mode, type_partie, status, joueur_depart, signature, nb_colonnes, confiance)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id_partie
                """,
                ("BGA", "HUMAIN", "EN_COURS", self.starting_player, sig_can, self.cols, 1),
            )
            id_partie = partie["id_partie"]

        except IntegrityError:
            existing = q_one("SELECT id_partie FROM partie WHERE signature=%s", (sig_can,))
            if existing:
                msg = (
                    "Partie déjà en base ✅ (conflit UNIQUE)\n"
                    f"ID: {existing['id_partie']}\n"
                    f"canonique: {sig_can}\n"
                    f"miroir: {sig_mirror}"
                )
                messagebox.showinfo("Import", msg)
                self.load_partie(existing["id_partie"])
                return
            raise

        # Insert situations step-by-step
        board2 = empty_board(self.rows, self.cols)
        player = self.starting_player
        prev_sid = None
        numero = 0

        for ch in sanitize_signature(sig_raw):
            col = int(ch) - 1

            placed_row = None
            for r in range(self.rows - 1, -1, -1):
                if board2[r][col] == 0:
                    board2[r][col] = player
                    placed_row = r
                    break
            if placed_row is None:
                break

            numero += 1
            plateau = "\n".join("".join(str(x) if x == 0 else x for x in row) for row in board2)

            sid = q_one(
                """
                INSERT INTO situation (id_partie, numero_coup, plateau, joueur, precedent, suivant)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id_situation
                """,
                (id_partie, numero, plateau, player, prev_sid, None),
            )["id_situation"]

            if prev_sid is not None:
                exec_sql("UPDATE situation SET suivant=%s WHERE id_situation=%s", (sid, prev_sid))

            prev_sid = sid
            player = "J" if player == "R" else "R"

        messagebox.showinfo("Import", f"Import OK. id_partie={id_partie}")
        self.refresh_parties()
        self.load_partie(id_partie)

    # ----------------- import BGA (Mission 4.1) — logs console ✅
    def _extract_table_id(self, s: str):
        s = (s or "").strip()
        m = re.search(r"table=(\d+)", s)
        if m:
            return m.group(1)
        if s.isdigit():
            return s
        return None

    def _make_bga_driver(self):
        opts = Options()
        opts.add_argument("--start-maximized")
        opts.add_argument("--disable-popup-blocking")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        return webdriver.Chrome(options=opts)

    def import_partie_from_bga(self):
        raw = self.bga_table_var.get()
        table_id = self._extract_table_id(raw)

        if not table_id:
            print("[BGA] ❌ Table id invalide:", raw)
            if not DEBUG:
                messagebox.showwarning("Import BGA", "Entre un numéro de table (ex: 808151221) ou une URL BGA.")
            return

        driver = None
        try:
            print("\n================= IMPORT BGA =================")
            print("[BGA] Table demandée:", table_id)

            driver = self._make_bga_driver()
            driver.set_page_load_timeout(60)

            # Login manuel : pas de popup, tout en console
            driver.get("https://boardgamearena.com/account")
            print("[BGA] 👉 Connecte-toi dans Chrome (avatar visible), puis appuie ENTER ici...")
            input()

            id_partie, preview = import_table_id_connect4(
                driver,
                table_id,
                rows=self.rows,
                cols=self.cols,
                confiance=3
            )

            print("[BGA] id_partie retourné:", id_partie)

            if id_partie is None:
                print("[BGA] ❌ Import impossible (moves=0 / pas Connect4 / archive indispo). Preview:")
                for ln in (preview or [])[:15]:
                    print("  -", ln)
                print("==============================================\n")
                if not DEBUG:
                    messagebox.showerror("Import BGA", "Import impossible. Voir console.")
                return

            print("[BGA] ✅ Import OK ! id_partie =", id_partie)
            print("==============================================\n")

            self.refresh_parties()
            self.load_partie(id_partie)

        except Exception:
            print("\n========== ERREUR IMPORT BGA ==========")
            traceback.print_exc()
            print("=======================================\n")
            if not DEBUG:
                messagebox.showerror("Import BGA", "Erreur import (voir console).")
        finally:
            try:
                if driver:
                    time.sleep(0.5)
                    driver.quit()
            except Exception:
                pass


# ============================================================
# Integration with ui.py
# ============================================================

def pick_partie_id_dialog(master=None, config_path="config.json"):
    dlg = DBExplorer(master=master, config_path=config_path)
    dlg.grab_set()

    selected = {"id": None}

    def select_current():
        sel = dlg.parties_tree.selection()
        if not sel:
            messagebox.showwarning("Charger", "Sélectionne une partie")
            return
        vals = dlg.parties_tree.item(sel[0], "values")
        try:
            selected["id"] = int(vals[0])
        except Exception:
            selected["id"] = None
        dlg.destroy()

    btn = ttk.Button(dlg, text="Charger cette partie pour jouer", command=select_current)
    btn.grid(row=1, column=0)

    dlg.wait_window()
    return selected["id"]


def load_partie_for_play(id_partie: int, config_path="config.json"):
    """
    ✅ LA fonction clé pour que ton UI puisse continuer une partie importée.
    Elle renvoie:
    rows, cols, starting_player, signature,
    board, history[(r,c,p)], future[],
    current_player, game_over, result,
    last_situation_id
    """
    rows, cols, starting_player = 9, 9, "R"
    try:
        cfg = json.load(open(config_path, "r", encoding="utf-8"))
        rows = int(cfg.get("rows", rows))
        cols = int(cfg.get("cols", cols))
        starting_player = cfg.get("starting_player", starting_player)
    except Exception:
        pass

    partie = q_one("SELECT * FROM partie WHERE id_partie=%s", (id_partie,))
    if not partie:
        raise ValueError(f"Partie {id_partie} introuvable")

    sig = partie.get("signature") or ""

    last_row = q_one(
        "SELECT id_situation, numero_coup FROM situation WHERE id_partie=%s ORDER BY numero_coup DESC LIMIT 1",
        (id_partie,),
    )
    last_situation_id = last_row["id_situation"] if last_row else None

    if sig:
        board, hist, next_player = replay_from_signature(sig, rows, cols, starting_player)
    else:
        last = q_one(
            "SELECT plateau FROM situation WHERE id_partie=%s ORDER BY numero_coup DESC LIMIT 1",
            (id_partie,)
        )
        board = parse_board_text(last["plateau"], rows, cols) if last else empty_board(rows, cols)
        hist = []
        next_player = starting_player

    return {
        "rows": rows,
        "cols": cols,
        "starting_player": starting_player,
        "signature": sig,
        "board": board,
        "history": hist,
        "future": [],
        "current_player": next_player,
        "game_over": False,
        "result": None,
        "last_situation_id": last_situation_id,
    }


def open_explorer_from_ui(master):
    DBExplorer(master=master)


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    DBExplorer(master=root)
    root.mainloop()