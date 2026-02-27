class Connect4Game:
    def __init__(self, rows=8, cols=9, starting_player="R", win_len=4):
        self.rows = rows
        self.cols = cols
        self.starting_player = starting_player
        self.win_len = win_len
        self.reset()
        
    def reset(self):
        self.current_player = self.starting_player
        self.board = [[0 for _ in range(self.cols)] for _ in range(self.rows)]
        self.game_over = False
        self.result = None
        self.history = []
        self.future = []

    def set_params(self, rows, cols, starting_player, win_len=None):
        self.rows = rows
        self.cols = cols
        self.starting_player = starting_player
        if win_len is not None:
            self.win_len = win_len
        self.reset()


    def valid_columns(self):
        return [c for c in range(self.cols) if self.board[0][c] == 0]

    def next_open_row(self, col):
        for r in range(self.rows - 1, -1, -1):
            if self.board[r][col] == 0:
                return r
        return None

    def is_draw(self):
        return all(self.board[0][c] != 0 for c in range(self.cols))

    def drop(self, col):
        if self.game_over:
            return False, None
        if col < 0 or col >= self.cols:
            return False, None

        r = self.next_open_row(col)
        if r is None:
            return False, None

        p = self.current_player
        self.board[r][col] = p
        self.history.append((r, col, p))
        self.future.clear()

        winning = self.check_win(r, col)
        if winning:
            self.game_over = True
            self.result = "Rouge" if p == "R" else "Jaune"
            return True, winning

        if self.is_draw():
            self.game_over = True
            self.result = "Match nul"
            return True, None

        self.current_player = "J" if p == "R" else "R"
        return True, None

    def undo(self):
        if not self.history:
            return False
        r, c, p = self.history.pop()
        self.board[r][c] = 0
        self.future.append((r, c, p))
        self.current_player = p
        self.game_over = False
        self.result = None
        return True

    def redo(self):
        if not self.future:
            return False
        r, c, p = self.future.pop()
        self.board[r][c] = p
        self.history.append((r, c, p))
        self.current_player = "J" if p == "R" else "R"
        self.game_over = False
        self.result = None
        return True

    def winner_on_board(self, board):
        """
        Retourne 'R' ou 'J' si un joueur a 4 alignés sur 'board', sinon None.
        """
        directions = [(0, 1), (1, 0), (1, 1), (1, -1)]  # horiz, vert, diag \, diag /
        for r in range(self.rows):
            for c in range(self.cols):
                p = board[r][c]
                if p == 0:
                    continue

                for dr, dc in directions:
                    cnt = 1
                    rr, cc = r + dr, c + dc

                    while 0 <= rr < self.rows and 0 <= cc < self.cols and board[rr][cc] == p:
                        cnt += 1
                        if cnt >= 4:
                            return p
                        rr += dr
                        cc += dc

        return None


    def check_win(self, row, col):
        """
        Après avoir posé un pion en (row, col), retourne la liste des 4 cases gagnantes
        [(r,c), ...] si victoire, sinon None.
        """
        p = self.board[row][col]
        if p == 0:
            return None

        directions = [(0, 1), (1, 0), (1, 1), (1, -1)]

        for dr, dc in directions:
            cells = [(row, col)]

            # Avancer dans le sens + (dr, dc)
            r, c = row + dr, col + dc
            while 0 <= r < self.rows and 0 <= c < self.cols and self.board[r][c] == p:
                cells.append((r, c))
                r += dr
                c += dc

            # Avancer dans le sens - (dr, dc)
            r, c = row - dr, col - dc
            while 0 <= r < self.rows and 0 <= c < self.cols and self.board[r][c] == p:
                cells.insert(0, (r, c))
                r -= dr
                c -= dc

            # Si on a au moins 4 cases alignées, on retourne une fenêtre de 4 qui contient (row,col)
            if len(cells) >= 4:
                idx = cells.index((row, col))
                start = max(0, idx - 3)
                end = start + 4

                if end > len(cells):
                    end = len(cells)
                    start = end - 4

                return cells[start:end]

        return None