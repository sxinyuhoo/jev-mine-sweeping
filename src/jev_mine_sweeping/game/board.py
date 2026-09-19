"""Board data model and the cell vocabulary shared by every layer.

Cell vocabulary (kept from the original project so the template images and the
screen parser stay compatible):

======  =========================================================
value   meaning
======  =========================================================
0-6     revealed cell showing how many of its 8 neighbours are mines
7       hidden (not yet opened, no flag)
8       flagged by the player
9       mine visible on screen (i.e. the game is lost)
======  =========================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

HIDDEN = 7
FLAG = 8
MINE = 9

Cell = tuple[int, int]

#: Symbols used when a board is rendered as text for the model.
SYMBOL_HIDDEN = "?"
SYMBOL_FLAG = "F"
SYMBOL_MINE = "*"


@dataclass
class Board:
    """The *observed* board: what the player can see on screen.

    This is deliberately separate from :mod:`jev_mine_sweeping.game.local_game`,
    which owns the hidden ground truth used by the offline simulator.
    """

    rows: int
    cols: int
    cells: list[list[int]]
    total_mines: int
    moves: int = 0
    status: str = "in_progress"  # in_progress | won | lost
    #: Per-cell template match score, filled in by the screen parser.
    scores: list[list[float]] = field(default_factory=list)

    # ---------------------------------------------------------------- helpers
    @classmethod
    def empty(cls, rows: int, cols: int, total_mines: int) -> "Board":
        return cls(rows, cols, [[HIDDEN] * cols for _ in range(rows)], total_mines)

    @classmethod
    def from_grid(cls, grid: Sequence[Sequence[int]], total_mines: int, **kwargs) -> "Board":
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        return cls(rows, cols, [list(r) for r in grid], total_mines, **kwargs)

    def copy(self) -> "Board":
        return Board(
            self.rows,
            self.cols,
            [row[:] for row in self.cells],
            self.total_mines,
            self.moves,
            self.status,
            [row[:] for row in self.scores],
        )

    def in_bounds(self, cell: Cell) -> bool:
        r, c = cell
        return 0 <= r < self.rows and 0 <= c < self.cols

    def get(self, cell: Cell) -> int:
        return self.cells[cell[0]][cell[1]]

    def set(self, cell: Cell, value: int) -> None:
        self.cells[cell[0]][cell[1]] = value

    def all_cells(self) -> Iterable[Cell]:
        for r in range(self.rows):
            for c in range(self.cols):
                yield (r, c)

    def neighbours(self, cell: Cell) -> list[Cell]:
        r, c = cell
        out: list[Cell] = []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                rr, cc = r + dr, c + dc
                if 0 <= rr < self.rows and 0 <= cc < self.cols:
                    out.append((rr, cc))
        return out

    # ------------------------------------------------------------- selectors
    def cells_with(self, *values: int) -> list[Cell]:
        wanted = set(values)
        return [cell for cell in self.all_cells() if self.get(cell) in wanted]

    def hidden_cells(self) -> list[Cell]:
        return self.cells_with(HIDDEN)

    def flagged_cells(self) -> list[Cell]:
        return self.cells_with(FLAG)

    def revealed_cells(self) -> list[Cell]:
        return self.cells_with(*range(0, 7))

    def mine_cells(self) -> list[Cell]:
        return self.cells_with(MINE)

    def frontier_cells(self) -> list[Cell]:
        """Hidden cells that touch at least one revealed number.

        These are the only cells where a deduction or a meaningful judgment can
        be made; the rest of the hidden board is a blind guess.
        """
        out = []
        for cell in self.hidden_cells():
            if any(self.get(n) in range(0, 7) for n in self.neighbours(cell)):
                out.append(cell)
        return out

    def unconstrained_hidden_cells(self) -> list[Cell]:
        frontier = set(self.frontier_cells())
        return [cell for cell in self.hidden_cells() if cell not in frontier]

    # ---------------------------------------------------------------- counters
    @property
    def flags_used(self) -> int:
        return len(self.flagged_cells())

    @property
    def mines_remaining(self) -> int:
        """Mines the player has not flagged yet (never negative in a valid game)."""
        return max(self.total_mines - self.flags_used, 0)

    @property
    def hidden_count(self) -> int:
        return len(self.hidden_cells())

    def local_counts(self, cell: Cell) -> tuple[int, int, int]:
        """Return ``(flagged, hidden, revealed)`` neighbour counts of ``cell``."""
        flagged = hidden = revealed = 0
        for n in self.neighbours(cell):
            v = self.get(n)
            if v == FLAG:
                flagged += 1
            elif v == HIDDEN:
                hidden += 1
            else:
                revealed += 1
        return flagged, hidden, revealed

    # ------------------------------------------------------------ consistency
    def inconsistencies(self) -> list[str]:
        """Cheap code-level sanity check on a board reading.

        Detects readings that cannot describe a real Minesweeper position, which
        in practice almost always means the screen parser misread a cell. Used to
        decide whether to ask Jev to re-examine the reading.
        """
        problems: list[str] = []
        for cell in self.revealed_cells():
            value = self.get(cell)
            flagged, hidden, _ = self.local_counts(cell)
            if value == 0 and flagged:
                problems.append(
                    f"{format_cell(cell)} shows 0 but has {flagged} flagged neighbour(s)"
                )
            if flagged > value:
                problems.append(
                    f"{format_cell(cell)} shows {value} but {flagged} flagged neighbour(s)"
                )
            if hidden == 0 and flagged != value:
                problems.append(
                    f"{format_cell(cell)} shows {value}, has no hidden neighbour, "
                    f"but {flagged} flag(s)"
                )
        if self.flags_used > self.total_mines:
            problems.append(f"{self.flags_used} flags placed but only {self.total_mines} mines exist")
        return problems

    # -------------------------------------------------------------- rendering
    def to_symbol_rows(self) -> list[str]:
        """Rows of single-character symbols, used in the state sent to Jev."""
        rows = []
        for r in range(self.rows):
            chars = []
            for c in range(self.cols):
                v = self.cells[r][c]
                if v == HIDDEN:
                    chars.append(SYMBOL_HIDDEN)
                elif v == FLAG:
                    chars.append(SYMBOL_FLAG)
                elif v == MINE:
                    chars.append(SYMBOL_MINE)
                else:
                    chars.append(str(v))
            rows.append("".join(chars))
        return rows

    def to_debug_text(self) -> str:
        header = "    " + " ".join(f"{c:>2}" for c in range(self.cols))
        lines = [header]
        for r, row in enumerate(self.to_symbol_rows()):
            lines.append(f"{r:>3} " + "  ".join(row))
        return "\n".join(lines)


def format_cell(cell: Cell) -> str:
    """``(3, 4)`` -> ``r3c4`` (zero-based, matches the state sent to Jev)."""
    return f"r{cell[0]}c{cell[1]}"


def parse_cell(text: str) -> Cell:
    """``r3c4`` -> ``(3, 4)``. Raises ``ValueError`` on anything else."""
    cleaned = text.strip().lower().replace(" ", "")
    if not cleaned.startswith("r") or "c" not in cleaned:
        raise ValueError(f"not a cell reference: {text!r}")
    row_part, _, col_part = cleaned[1:].partition("c")
    return int(row_part), int(col_part)
