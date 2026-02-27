class MinimaxAI:
    def __init__(self, rows, cols):
        self.rows = rows
        self.cols = cols
        self.tt = {}

    def reset_params(self, rows, cols):
        self.rows = rows
        self.cols = cols
        self.tt.clear()

    def clear_cache(self):
        self.tt.clear()

    def board_key(self, board, maximizing, ai_player):
        return (ai_player, maximizing, tuple(map(tuple, board)))

    def valid_cols(self, board):
        return [c for c in range(self.cols) if board[0][c] == 0]

    def next_open_row(self, board, col):
        for r in range(self.rows - 1, -1, -1):
            if board[r][col] == 0:
                return r
        return None

    def winner_on_board(self, board):
        directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
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

    def ordered_valid_cols(self, board, ai_player, maximizing):
        valid = self.valid_cols(board)
        if not valid:
            return []

        opp = "J" if ai_player == "R" else "R"
        player_to_play = ai_player if maximizing else opp
        center = self.cols // 2

        def move_score(col):
            score = -abs(col - center) * 10
            r = self.next_open_row(board, col)
            if r is None:
                return -10**9

            board[r][col] = player_to_play
            w = self.winner_on_board(board)
            board[r][col] = 0
            if w == player_to_play:
                score += 10**6
            return score

        valid.sort(key=move_score, reverse=True)
        return valid

    def heuristic(self, board, ai_player):
        opp = "J" if ai_player == "R" else "R"

        def score_window(w):
            ai = w.count(ai_player)
            op = w.count(opp)
            empty = w.count(0)
            if ai > 0 and op > 0:
                return 0
            if ai == 4:
                return 100000
            if op == 4:
                return -100000
            if ai == 3 and empty == 1:
                return 80
            if ai == 2 and empty == 2:
                return 10
            if op == 3 and empty == 1:
                return -90
            if op == 2 and empty == 2:
                return -12
            return 0

        score = 0
        center = self.cols // 2
        score += 6 * [board[r][center] for r in range(self.rows)].count(ai_player)

        for r in range(self.rows):
            for c in range(self.cols - 3):
                score += score_window([board[r][c+i] for i in range(4)])
        for c in range(self.cols):
            for r in range(self.rows - 3):
                score += score_window([board[r+i][c] for i in range(4)])
        for r in range(self.rows - 3):
            for c in range(self.cols - 3):
                score += score_window([board[r+i][c+i] for i in range(4)])
        for r in range(3, self.rows):
            for c in range(self.cols - 3):
                score += score_window([board[r-i][c+i] for i in range(4)])

        return score

    def minimax(self, board, depth, alpha, beta, maximizing, ai_player):
        winner = self.winner_on_board(board)
        opp = "J" if ai_player == "R" else "R"

        if winner == ai_player:
            return 10**7 + depth
        if winner == opp:
            return -10**7 - depth

        valid = self.valid_cols(board)
        if depth == 0 or not valid:
            return self.heuristic(board, ai_player)

        key = self.board_key(board, maximizing, ai_player)
        cached = self.tt.get(key)
        if cached is not None:
            cd, cs = cached
            if cd >= depth:
                return cs

        if maximizing:
            value = -10**9
            for col in self.ordered_valid_cols(board, ai_player, True):
                r = self.next_open_row(board, col)
                if r is None:
                    continue
                board[r][col] = ai_player
                value = max(value, self.minimax(board, depth-1, alpha, beta, False, ai_player))
                board[r][col] = 0
                alpha = max(alpha, value)
                if alpha >= beta:
                    break
        else:
            value = 10**9
            for col in self.ordered_valid_cols(board, ai_player, False):
                r = self.next_open_row(board, col)
                if r is None:
                    continue
                board[r][col] = opp
                value = min(value, self.minimax(board, depth-1, alpha, beta, True, ai_player))
                board[r][col] = 0
                beta = min(beta, value)
                if alpha >= beta:
                    break

        self.tt[key] = (depth, value)
        return value