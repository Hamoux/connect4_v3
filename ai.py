class MinimaxAI:
    TERMINAL_WIN_SCORE = 10_000_000
    HEURISTIC_THREAT_PENALTY = 2_500
    HEURISTIC_THREAT_BONUS = 600

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

    def board_key(self, board, maximizing, ai_player, depth):
        return (ai_player, maximizing, depth, tuple(map(tuple, board)))

    def opponent(self, player):
        return "J" if player == "R" else "R"

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

    def ordered_valid_cols(self, board, ai_player=None, maximizing=True):
        center = self.cols // 2
        valid = self.valid_cols(board)
        # Deterministic ordering for search and tie cases: prefer center, then leftmost.
        return sorted(valid, key=lambda c: (abs(c - center), c))

    def _windows(self, board):
        for r in range(self.rows):
            for c in range(self.cols - 3):
                yield [board[r][c + i] for i in range(4)]
        for c in range(self.cols):
            for r in range(self.rows - 3):
                yield [board[r + i][c] for i in range(4)]
        for r in range(self.rows - 3):
            for c in range(self.cols - 3):
                yield [board[r + i][c + i] for i in range(4)]
        for r in range(3, self.rows):
            for c in range(self.cols - 3):
                yield [board[r - i][c + i] for i in range(4)]

    def count_immediate_wins(self, board, player):
        count = 0
        for col in self.valid_cols(board):
            r = self.next_open_row(board, col)
            if r is None:
                continue
            board[r][col] = player
            if self.winner_on_board(board) == player:
                count += 1
            board[r][col] = 0
        return count

    def heuristic(self, board, ai_player):
        opp = self.opponent(ai_player)

        def score_window(window):
            ai = window.count(ai_player)
            op = window.count(opp)
            empty = window.count(0)

            if ai and op:
                return 0
            if ai == 3 and empty == 1:
                return 90
            if ai == 2 and empty == 2:
                return 14
            if ai == 1 and empty == 3:
                return 2
            if op == 3 and empty == 1:
                return -120
            if op == 2 and empty == 2:
                return -18
            if op == 1 and empty == 3:
                return -2
            return 0

        score = 0
        center = self.cols // 2
        score += 8 * [board[r][center] for r in range(self.rows)].count(ai_player)
        score -= 8 * [board[r][center] for r in range(self.rows)].count(opp)

        for window in self._windows(board):
            score += score_window(window)

        ai_threats = self.count_immediate_wins(board, ai_player)
        opp_threats = self.count_immediate_wins(board, opp)
        score += ai_threats * self.HEURISTIC_THREAT_BONUS
        score -= opp_threats * self.HEURISTIC_THREAT_PENALTY

        return int(score)

    def minimax(self, board, depth, alpha, beta, maximizing, ai_player, eval_func=None):
        winner = self.winner_on_board(board)
        opp = self.opponent(ai_player)

        if winner == ai_player:
            return self.TERMINAL_WIN_SCORE + depth
        if winner == opp:
            return -self.TERMINAL_WIN_SCORE - depth

        valid = self.valid_cols(board)
        if depth == 0 or not valid:
            if not valid:
                return 0
            return (eval_func or self.heuristic)(board, ai_player)

        key = self.board_key(board, maximizing, ai_player, depth)
        cached = self.tt.get(key)
        if cached is not None:
            return cached

        if maximizing:
            value = -10**18
            for col in self.ordered_valid_cols(board, ai_player, True):
                r = self.next_open_row(board, col)
                if r is None:
                    continue
                board[r][col] = ai_player
                child = self.minimax(board, depth - 1, alpha, beta, False, ai_player, eval_func)
                board[r][col] = 0
                if child > value:
                    value = child
                alpha = max(alpha, value)
                if alpha >= beta:
                    break
        else:
            value = 10**18
            for col in self.ordered_valid_cols(board, ai_player, False):
                r = self.next_open_row(board, col)
                if r is None:
                    continue
                board[r][col] = opp
                child = self.minimax(board, depth - 1, alpha, beta, True, ai_player, eval_func)
                board[r][col] = 0
                if child < value:
                    value = child
                beta = min(beta, value)
                if alpha >= beta:
                    break

        self.tt[key] = value
        return value

    def evaluate_move_scores(self, board, ai_player, depth, eval_func=None):
        depth = max(0, int(depth))
        self.clear_cache()
        scores = {}
        for col in self.ordered_valid_cols(board, ai_player, True):
            r = self.next_open_row(board, col)
            if r is None:
                continue
            board[r][col] = ai_player
            score = self.minimax(board, max(0, depth - 1), -10**18, 10**18, False, ai_player, eval_func)
            board[r][col] = 0
            scores[col] = int(score)
        return scores

    def choose_best_move(self, board, ai_player, depth, eval_func=None):
        scores = self.evaluate_move_scores(board, ai_player, depth, eval_func)
        if not scores:
            return None, {}
        # Deterministic tie-break: leftmost column among best-scoring moves.
        best_score = max(scores.values())
        best_col = min(col for col, score in scores.items() if score == best_score)
        return best_col, scores

    def predict_winner(self, board, current_player, depth=6, eval_func=None):
        winner_now = self.winner_on_board(board)
        if winner_now:
            return {"winner": winner_now, "moves": 0, "certain": True}

        valid = self.valid_cols(board)
        if not valid:
            return {"winner": "draw", "moves": 0, "certain": True}

        opp = self.opponent(current_player)
        eval_depth = min(int(depth), 8)
        score = self.minimax([row[:] for row in board], eval_depth, -10**18, 10**18, True, current_player, eval_func)

        if score >= self.TERMINAL_WIN_SCORE:
            depth_remaining = score - self.TERMINAL_WIN_SCORE
            moves = max(1, eval_depth - depth_remaining)
            return {"winner": current_player, "moves": moves, "certain": True}

        if score <= -self.TERMINAL_WIN_SCORE:
            depth_remaining = (-score) - self.TERMINAL_WIN_SCORE
            moves = max(1, eval_depth - depth_remaining)
            return {"winner": opp, "moves": moves, "certain": True}

        cases_vides = sum(1 for r in range(self.rows) for c in range(self.cols) if board[r][c] == 0)
        if score > 250:
            return {"winner": current_player, "moves": max(3, cases_vides // 5), "certain": False}
        if score < -250:
            return {"winner": opp, "moves": max(3, cases_vides // 5), "certain": False}
        if cases_vides <= eval_depth:
            return {"winner": "draw", "moves": cases_vides, "certain": True}
        if abs(score) <= 20 and cases_vides <= eval_depth * 2:
            return {"winner": "draw", "moves": cases_vides, "certain": False}
        return {"winner": None, "moves": None, "certain": False}

    def _find_forced_win(self, board, player, current_mover, max_depth, depth):
        if depth > max_depth:
            return None

        winner = self.winner_on_board(board)
        if winner == player:
            return depth

        valid = self.valid_cols(board)
        if not valid:
            return None

        opp = self.opponent(player)

        if current_mover == player:
            for col in self.ordered_valid_cols(board, player, True):
                r = self.next_open_row(board, col)
                if r is None:
                    continue
                board[r][col] = player
                result = self._find_forced_win(board, player, opp, max_depth, depth + 1)
                board[r][col] = 0
                if result is not None:
                    return result
            return None

        worst = None
        for col in self.ordered_valid_cols(board, player, False):
            r = self.next_open_row(board, col)
            if r is None:
                continue
            board[r][col] = current_mover
            result = self._find_forced_win(board, player, player, max_depth, depth + 1)
            board[r][col] = 0
            if result is None:
                return None
            if worst is None or result > worst:
                worst = result
        return worst
