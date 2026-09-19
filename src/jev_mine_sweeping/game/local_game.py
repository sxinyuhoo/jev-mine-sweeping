"""Offline Minesweeper simulator (refactor of the original ``MineSweeper`` class).

It plays by the same rules as the desktop app and exposes an observed
:class:`~jev_mine_sweeping.game.board.Board`, so the whole agent pipeline —
solver, state builder, Jev questions, decision composition — can be exercised
without a GUI, without clicking anything and without spending API calls.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .board import FLAG, HIDDEN, MINE, Board, Cell


@dataclass
class OpenResult:
    """Outcome of one left click."""

    cell: Cell
    revealed: list[Cell] = field(default_factory=list)
    hit_mine: bool = False
    already_open: bool = False
    status: str = "in_progress"


class LocalGame:
    """A single Minesweeper episode with hidden ground truth."""

    def __init__(
        self,
        rows: int = 9,
        cols: int = 9,
        mines: int = 10,
        seed: int | None = None,
        safe_first_click: bool = True,
    ) -> None:
        self.rows = rows
        self.cols = cols
        self.mines = mines
        self.rng = random.Random(seed)
        self.safe_first_click = safe_first_click
        self.reset()

    # ------------------------------------------------------------------ setup
    def reset(self) -> Board:
        self.truth: list[list[int]] = [[0] * self.cols for _ in range(self.rows)]
        self.board = Board.empty(self.rows, self.cols, self.mines)
        self.mine_cells: set[Cell] = set()
        self.placed = False
        self.status = "in_progress"
        return self.board

    def _place_mines(self, first: Cell) -> None:
        forbidden = {first}
        if self.safe_first_click:
            forbidden |= set(self.board.neighbours(first))
        free = [
            (r, c)
            for r in range(self.rows)
            for c in range(self.cols)
            if (r, c) not in forbidden
        ]
        chosen = self.rng.sample(free, self.mines)
        self.mine_cells = set(chosen)
        for r in range(self.rows):
            for c in range(self.cols):
                if (r, c) in self.mine_cells:
                    self.truth[r][c] = MINE
                else:
                    self.truth[r][c] = sum(
                        1 for n in self.board.neighbours((r, c)) if n in self.mine_cells
                    )
        self.placed = True

    # ------------------------------------------------------------------ moves
    def open(self, cell: Cell) -> OpenResult:
        if self.status != "in_progress":
            return OpenResult(cell, status=self.status)
        if not self.board.in_bounds(cell):
            raise ValueError(f"cell out of bounds: {cell}")
        value = self.board.get(cell)
        if value == FLAG:
            return OpenResult(cell, already_open=True, status=self.status)
        if value != HIDDEN:
            return OpenResult(cell, already_open=True, status=self.status)

        if not self.placed:
            self._place_mines(cell)

        if self.truth[cell[0]][cell[1]] == MINE:
            self.board.set(cell, MINE)
            self.board.status = "lost"
            self.status = "lost"
            self.board.moves += 1
            return OpenResult(cell, [cell], hit_mine=True, status=self.status)

        revealed: list[Cell] = []
        self._flood(cell, revealed)
        self.board.moves += 1
        self._check_win()
        return OpenResult(cell, revealed, status=self.status)

    def _flood(self, start: Cell, revealed: list[Cell]) -> None:
        stack = [start]
        seen: set[Cell] = set()
        while stack:
            cell = stack.pop()
            if cell in seen:
                continue
            seen.add(cell)
            if self.board.get(cell) in (FLAG,):
                continue
            if self.board.get(cell) != HIDDEN:
                continue
            value = self.truth[cell[0]][cell[1]]
            self.board.set(cell, value)
            revealed.append(cell)
            if value == 0:
                stack.extend(self.board.neighbours(cell))

    def toggle_flag(self, cell: Cell) -> bool:
        if self.status != "in_progress":
            return False
        value = self.board.get(cell)
        if value == HIDDEN:
            self.board.set(cell, FLAG)
        elif value == FLAG:
            self.board.set(cell, HIDDEN)
        else:
            return False
        self.board.moves += 1
        self._check_win()
        return True

    def _check_win(self) -> None:
        if self.status != "in_progress":
            return
        # Classic rule: you win once every non-mine cell is revealed; mines left
        # unflagged do not block the win (the app shows the banner anyway).
        revealed = sum(
            1
            for r in range(self.rows)
            for c in range(self.cols)
            if self.board.cells[r][c] not in (HIDDEN, FLAG)
        )
        if revealed >= self.rows * self.cols - self.mines:
            self.status = "won"
            self.board.status = "won"

    # ------------------------------------------------------------------ views
    @property
    def observed(self) -> Board:
        return self.board

    @property
    def mines_left(self) -> int:
        return self.board.mines_remaining

    def debug_text(self) -> str:
        lines = [self.board.to_debug_text(), "", "truth:"]
        symbols = {
            MINE: "*",
            **{v: str(v) for v in range(0, 7)},
        }
        for r in range(self.rows):
            lines.append("    " + " ".join(f"{symbols[self.truth[r][c]]:>2}" for c in range(self.cols)))
        return "\n".join(lines)
