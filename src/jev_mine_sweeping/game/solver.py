"""Deterministic Minesweeper deduction — the part that must never be guessed.

Jev's own guidance is *atomic questions, composed in code*: arithmetic and hard
constraints stay in code, judgment goes to the model. Minesweeper splits cleanly
along that line, so this module owns everything that is provably true:

* **single point** — a number whose flags already match it clears its remaining
  hidden neighbours; a number whose flags plus hidden neighbours equal it makes
  all of those neighbours mines;
* **subset (1-2-1 style patterns)** — for two constraints ``A ⊂ B``, a matching
  requirement clears ``B \\ A``, and a requirement gap equal to ``|B \\ A|``
  mines it;
* **global count** — when every remaining mine is accounted for, every hidden
  cell is safe, and vice versa.

The closure is computed on a scratch copy where *deduced safe* cells are marked
:data:`SAFE_UNKNOWN` (they stop counting as hidden, but we do not invent a number
for them) and *deduced mines* are marked as flags, so chains of deductions fall
out of one pass. Nothing here talks to the screen or to Jev.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .board import FLAG, HIDDEN, Cell, format_cell

#: Scratch marker for "known safe, true number not yet observed".
SAFE_UNKNOWN = -1


@dataclass(frozen=True)
class Constraint:
    """``required`` of ``cells`` are mines, as asserted by one number cell."""

    cells: frozenset[Cell]
    required: int
    source: str

    def __str__(self) -> str:  # pragma: no cover - debug helper
        names = ", ".join(format_cell(c) for c in sorted(self.cells))
        return f"[{names}] has {self.required} mine(s) ({self.source})"


@dataclass
class Deduction:
    """Everything the deterministic layer proved about the current reading."""

    safe: list[Cell] = field(default_factory=list)
    mines: list[Cell] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    iterations: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def solved(self) -> bool:
        return bool(self.safe or self.mines)

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"{len(self.safe)} safe / {len(self.mines)} mine(s) "
            f"from {len(self.constraints)} constraint(s) in {self.iterations} pass(es)"
        )


def _value_counts(board, cell: Cell) -> tuple[int, int, int]:
    """``(flagged, hidden, unknown_safe)`` neighbour counts on a scratch board."""
    flagged = hidden = safe_unknown = 0
    for n in board.neighbours(cell):
        v = board.get(n)
        if v == FLAG:
            flagged += 1
        elif v == HIDDEN:
            hidden += 1
        elif v == SAFE_UNKNOWN:
            safe_unknown += 1
    return flagged, hidden, safe_unknown


def build_constraints(board) -> list[Constraint]:
    """Turn every revealed number into a constraint over its hidden neighbours."""
    constraints: list[Constraint] = []
    for cell in board.all_cells():
        value = board.get(cell)
        if value < 0 or value > 6:
            continue
        flagged, hidden, _ = _value_counts(board, cell)
        if hidden == 0:
            continue
        needed = value - flagged
        if needed < 0:
            needed = 0  # over-flagged reading; the consistency check reports it
        neighbours = frozenset(
            n for n in board.neighbours(cell) if board.get(n) == HIDDEN
        )
        constraints.append(Constraint(neighbours, needed, f"{format_cell(cell)}={value}"))
    return constraints


def _deduce_once(
    constraints: Sequence[Constraint], board, global_rule: str = "full"
) -> tuple[set[Cell], set[Cell], list[str]]:
    safe: set[Cell] = set()
    mines: set[Cell] = set()
    notes: list[str] = []

    for con in constraints:
        if con.required <= 0:
            safe |= set(con.cells)
            notes.append(f"{con.source} leaves no mine among its hidden neighbours")
        elif con.required >= len(con.cells):
            mines |= set(con.cells)
            notes.append(f"{con.source} accounts for all its hidden neighbours")

    # Subset rule over constraint pairs.
    ordered = sorted(constraints, key=lambda c: len(c.cells))
    for i, small in enumerate(ordered):
        for big in ordered[i + 1 :]:
            if small.cells == big.cells:
                continue
            if not small.cells <= big.cells:
                continue
            rest = big.cells - small.cells
            gap = big.required - small.required
            if gap == 0:
                safe |= set(rest)
                notes.append(
                    f"{small.source} and {big.source} share the same mine count -> "
                    f"{len(rest)} cell(s) outside {small.source} are safe"
                )
            elif gap == len(rest):
                mines |= set(rest)
                notes.append(
                    f"{small.source} and {big.source} differ by exactly the "
                    f"{len(rest)} cell(s) outside {small.source} -> those are mines"
                )

    # Global mine-count rule, evaluated on the scratch board.
    #
    # The budget must account for the mines *this pass* just proved: they are not
    # written to the scratch board until the caller applies them, so counting only
    # the existing flags would overstate the remaining budget and let the rule
    # claim cells that are actually safe. (This is not hypothetical — it produced
    # an unsound "all hidden are mines" verdict on seed 40 of the offline sim.)
    hidden_cells = {c for c in board.all_cells() if board.get(c) == HIDDEN}
    flags_on_scratch = sum(1 for c in board.all_cells() if board.get(c) == FLAG)
    remaining_mines = board.total_mines - flags_on_scratch - len(mines)
    hidden_cells -= safe | mines
    if hidden_cells and global_rule != "off":
        if remaining_mines <= 0:
            safe |= hidden_cells
            notes.append("every mine is already accounted for -> all remaining hidden cells are safe")
        elif remaining_mines >= len(hidden_cells) and global_rule == "full":
            mines |= hidden_cells
            notes.append("remaining hidden cells exactly match the mine budget -> all are mines")

    return safe - mines, mines, notes


def solve(board, max_iterations: int = 50, global_rule: str = "full") -> Deduction:
    """Run the deduction closure on a scratch copy of ``board``.

    Never mutates ``board``. Cells reported as safe/mine are *proved*, so the
    caller may act on them without asking the model.

    ``global_rule`` controls the mine-counter inference, which is only sound when
    every placed flag really is a mine:

    ``full``       both directions (default);
    ``safe_only``  only "no mines left -> everything hidden is safe";
    ``off``        never infer from the mine counter.

    Because the agent only flags solver-proved mines or cells Jev rates above
    ``agent.mine_threshold``, ``full`` is the right default; switch to
    ``safe_only`` if you turn the safety override off and let the model flag
    freely.
    """
    scratch = board.copy()
    result = Deduction()
    # The constraints of the *reading* are what the state shows to the model, so
    # they are captured before any deduction mutates the scratch board.
    result.constraints = build_constraints(scratch)
    all_notes: list[str] = []

    for iteration in range(1, max_iterations + 1):
        constraints = build_constraints(scratch)
        safe, mines, notes = _deduce_once(constraints, scratch, global_rule)
        safe = {c for c in safe if scratch.get(c) == HIDDEN}
        mines = {c for c in mines if scratch.get(c) == HIDDEN}
        if not safe and not mines:
            result.iterations = iteration
            break
        for cell in safe:
            scratch.set(cell, SAFE_UNKNOWN)
            if cell not in result.safe:
                result.safe.append(cell)
        for cell in mines:
            scratch.set(cell, FLAG)
            if cell not in result.mines:
                result.mines.append(cell)
        all_notes.extend(notes)
    else:  # pragma: no cover - defensive
        result.iterations = max_iterations

    result.notes = all_notes[:20]
    result.safe.sort()
    result.mines.sort()
    return result


def local_mine_density(board, cell: Cell) -> float | None:
    """Estimate ``P(mine)`` for a hidden cell from its adjacent numbers.

    A weighted average of the constraints that cover the cell; ``None`` when no
    revealed number touches it (a blind cell, judged by global density instead).
    """
    constraints = build_constraints(board)
    covering = [c for c in constraints if cell in c.cells]
    if not covering:
        return None
    weight = sum(len(c.cells) for c in covering)
    if weight == 0:
        return None
    return sum(c.required for c in covering) / weight


def global_mine_density(board) -> float:
    hidden = board.hidden_count
    if hidden == 0:
        return 0.0
    return board.mines_remaining / hidden


def rank_guesses(board, candidates: Sequence[Cell]) -> list[tuple[Cell, float]]:
    """Order candidate cells by estimated mine risk (safest first)."""
    density = global_mine_density(board)
    scored: list[tuple[Cell, float]] = []
    for cell in candidates:
        local = local_mine_density(board, cell)
        scored.append((cell, density if local is None else local))
    scored.sort(key=lambda item: (item[1], item[0]))
    return scored


def best_guess(board, candidates: Sequence[Cell] | None = None) -> tuple[Cell, float] | None:
    """Code-level fallback: the safest-looking hidden cell to open."""
    if candidates is None:
        candidates = board.hidden_cells()
    if not candidates:
        return None
    if not build_constraints(board):
        # Nothing to reason about yet (opening move): take the centre.
        centre = (board.rows // 2, board.cols // 2)
        if centre in candidates:
            return centre, global_mine_density(board)
    return rank_guesses(board, candidates)[0]
