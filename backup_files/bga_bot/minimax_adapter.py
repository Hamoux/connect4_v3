"""
Adaptateur Minimax — réutilise la classe MinimaxAI de ai.py sans la modifier.
Même stratégie que le serveur Webapp : gain/blocage immédiats puis minimax.
"""

from __future__ import annotations

from ai import MinimaxAI


def check_win(board: list[list], rows: int, cols: int, r: int, c: int, player) -> bool:
    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
    for dr, dc in directions:
        count = 1
        rr, cc = r + dr, c + dc
        while 0 <= rr < rows and 0 <= cc < cols and board[rr][cc] == player:
            count += 1
            rr += dr
            cc += dc
        rr, cc = r - dr, c - dc
        while 0 <= rr < rows and 0 <= cc < cols and board[rr][cc] == player:
            count += 1
            rr -= dr
            cc -= dc
        if count >= 4:
            return True
    return False


class MinimaxMoveSelector:
    def __init__(self, rows: int, cols: int, depth: int = 6):
        self.rows = rows
        self.cols = cols
        self.depth = depth
        self.engine = MinimaxAI(rows, cols)

    def clear_cache(self) -> None:
        self.engine.clear_cache()

    def immediate_win_or_block(self, board, player):
        opponent = "J" if player == "R" else "R"
        valid = self.engine.valid_cols(board)
        for col in valid:
            r = self.engine.next_open_row(board, col)
            if r is None:
                continue
            board[r][col] = player
            ok = check_win(board, self.rows, self.cols, r, col, player)
            board[r][col] = 0
            if ok:
                return col
        for col in valid:
            r = self.engine.next_open_row(board, col)
            if r is None:
                continue
            board[r][col] = opponent
            ok = check_win(board, self.rows, self.cols, r, col, opponent)
            board[r][col] = 0
            if ok:
                return col
        return None

    def best_col(self, board, ai_player: str) -> int | None:
        valid = self.engine.valid_cols(board)
        if not valid:
            return None

        obvious = self.immediate_win_or_block(board, ai_player)
        if obvious is not None:
            return obvious

        best_score = -10**18
        best_col = valid[0]
        depth = max(1, self.depth)

        for col in self.engine.ordered_valid_cols(board, ai_player, maximizing=True):
            r = self.engine.next_open_row(board, col)
            if r is None:
                continue
            board[r][col] = ai_player
            score = self.engine.minimax(
                board, depth - 1, -10**18, 10**18, False, ai_player
            )
            board[r][col] = 0
            if score > best_score:
                best_score = score
                best_col = col
        return best_col
