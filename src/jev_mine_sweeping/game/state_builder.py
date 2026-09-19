"""Build the *state* Jev evaluates: an object-centric, English-only snapshot.

Two rules drive the shape of this payload:

* **English only.** Jev's primary training language is English; the docs state
  other languages (including CJK) are accepted but less accurate. Nothing that
  reaches the model is translated into Chinese — comments and logs are Chinese
  where it helps the human reader, the model payload is not.
* **Facts in code, judgment in the model.** The board, the constraint
  arithmetic, the deductions the rules can prove and the code-side risk estimate
  are all computed here and handed over as typed facts; Jev is asked only for
  the judgment that arithmetic cannot make. A model that has to re-derive
  "exactly one mine among these three cells" from a 480-cell grid will get it
  wrong; a model that is *told* it can weigh it.

Everything returned by :func:`build_state` is JSON-serialisable and fits well
inside Jev's 32k-token budget (a 9x9 board plus ~25 candidate entries is roughly
2-3k tokens).
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .board import Board, Cell, format_cell
from .solver import Constraint, Deduction, build_constraints, rank_guesses

GOAL = (
    "Clear a Minesweeper board: open every cell that is not a mine. "
    "Opening a mine loses the game immediately. Flagging a cell only marks it; "
    "flags are optional and do not end the game. You win when no hidden cell is left."
)

RULES = (
    "Each revealed number is the count of mines among that cell's up to 8 neighbours. "
    "`?` is hidden, `F` is flagged, `*` is a mine already visible (game lost). "
    "Coordinates are zero-based r<row>c<col>."
)


def _number_facts(board: Board, cell: Cell) -> dict[str, Any]:
    """Local arithmetic for one revealed number cell."""
    flagged, hidden, revealed = board.local_counts(cell)
    value = board.get(cell)
    return {
        "cell": format_cell(cell),
        "value": value,
        "flagged_neighbours": flagged,
        "hidden_neighbours": hidden,
        "revealed_neighbours": revealed,
        "mines_still_needed": max(value - flagged, 0),
    }


def _candidate_entry(
    board: Board,
    cell: Cell,
    *,
    max_numbers: int = 4,
    code_risk: float | None = None,
    status: str = "unknown",
) -> dict[str, Any]:
    flagged, hidden, _ = board.local_counts(cell)
    numbers = [
        _number_facts(board, n)
        for n in board.neighbours(cell)
        if 0 <= board.get(n) <= 6
    ]
    numbers.sort(key=lambda item: item["mines_still_needed"])
    entry: dict[str, Any] = {
        "cell": format_cell(cell),
        "status": status,
        "flagged_neighbours": flagged,
        "hidden_neighbours": hidden,
        "adjacent_numbers": numbers[:max_numbers],
        "blind": not numbers,
    }
    if code_risk is not None:
        # A fact, not a decision: the code's own rough mine density for this cell.
        entry["code_risk"] = round(code_risk, 2)
    return entry


def _constraint_facts(constraints: Sequence[Constraint], limit: int = 30) -> list[str]:
    out: list[str] = []
    for con in sorted(constraints, key=lambda c: (c.required, len(c.cells)))[:limit]:
        names = ", ".join(format_cell(c) for c in sorted(con.cells))
        out.append(f"{con.source}: exactly {con.required} mine(s) among [{names}]")
    return out


def _deduction_facts(deduction: Deduction | None) -> dict[str, Any]:
    safe = [format_cell(cell) for cell in (deduction.safe if deduction else [])]
    mines = [format_cell(cell) for cell in (deduction.mines if deduction else [])]
    return {
        "proved_safe_cells": safe[:24],
        "proved_mine_cells": mines[:24],
        "note": (
            "Derived in code from `mine_constraints`. These are facts, not moves: a "
            "proved-safe cell cannot be a mine, a proved-mine cell must not be opened. "
            "What to play next is still your decision."
        ),
    }


def build_state(
    board: Board,
    candidates: Sequence[Cell],
    *,
    total_mines: int | None = None,
    history: Sequence[dict[str, Any]] = (),
    solver_result: Deduction | None = None,
    solver_mode: str = "off",
    decision_mode: str = "atomic",
    max_frontier_entries: int = 24,
    step: int | None = None,
) -> dict[str, Any]:
    """Assemble the JSON object sent to Jev as ``state``."""
    total = board.total_mines if total_mines is None else total_mines
    candidates = list(candidates)
    deduction = solver_result if solver_result is not None else Deduction()

    risks = {
        cell: risk for cell, risk in rank_guesses(board, candidates)
    } if candidates else {}
    proved_safe = set(deduction.safe)
    proved_mines = set(deduction.mines)

    def status_of(cell: Cell) -> str:
        if cell in proved_safe:
            return "proved_safe"
        if cell in proved_mines:
            return "proved_mine"
        return "unknown"

    state: dict[str, Any] = {
        "goal": GOAL,
        "rules": RULES,
        "position": {
            "rows": board.rows,
            "cols": board.cols,
            "total_mines": total,
            "mines_flagged": board.flags_used,
            "mines_not_yet_flagged": board.mines_remaining,
            "hidden_cells": board.hidden_count,
            "revealed_cells": board.rows * board.cols - board.hidden_count - board.flags_used,
            "moves_played": board.moves,
            "game_status": board.status,
        },
        "board_text": board.to_symbol_rows(),
        "board_legend": RULES,
        "candidate_cells": [
            _candidate_entry(
                board,
                cell,
                code_risk=risks.get(cell),
                status=status_of(cell),
            )
            for cell in candidates[:max_frontier_entries]
        ],
    }
    if len(candidates) > max_frontier_entries:
        state["candidate_cells_truncated"] = (
            f"{len(candidates) - max_frontier_entries} further candidate cell(s) omitted"
        )
    state["action_space_note"] = (
        "You may only act on the cells listed in `candidate_cells`; every question in "
        "this request refers to one of them."
    )

    blind = [format_cell(c) for c in board.unconstrained_hidden_cells()]
    state["blind_hidden_cells"] = blind[:20]
    if len(blind) > 20:
        state["blind_hidden_cells"].append(f"... {len(blind) - 20} more")

    constraints = deduction.constraints if deduction.constraints else build_constraints(board)
    state["mine_constraints"] = _constraint_facts(constraints)
    state["derived_facts"] = _deduction_facts(deduction)
    state["deterministic_layer"] = {
        "mode": decision_mode,
        "note": (
            "The constraint rules above are executed in code before your answer; the "
            "cells they prove are reported in `derived_facts`. Everything left is "
            "genuinely uncertain, so it needs your judgment."
            if solver_mode != "off"
            else "No deterministic pre-filtering is active; every move is your call. "
            "`derived_facts` still tells you what the rules can prove."
        ),
    }

    if history:
        state["recent_actions"] = list(history)[-6:]

    if step is not None:
        state["step"] = step
    return state


def estimate_tokens(state: dict[str, Any]) -> int:
    """Rough token estimate (4 characters per token) for the request guard."""
    import json

    return len(json.dumps(state, ensure_ascii=False)) // 4 + 1


def select_candidates(board: Board, action_space: str = "frontier") -> list[Cell]:
    """The cells a decision may choose from.

    ``frontier`` restricts the choice to hidden cells that touch a revealed
    number — the only place where a judgment is informed rather than blind. When
    there is no frontier (the opening move, or a fully blind corner) the whole
    hidden set is offered instead, because something has to be opened.
    """
    if action_space == "all_hidden":
        return board.hidden_cells()
    cells = board.frontier_cells()
    return cells or board.hidden_cells()


def build_reading_check_state(board: Board, problems: Iterable[str]) -> dict[str, Any]:
    """State for auditing a *code-level* reading problem (used by tests/debugging)."""
    return {
        "goal": (
            "A board was read by code. Your job is to judge how trustworthy that "
            "reading is, not to play."
        ),
        "rules": RULES,
        "board_text": board.to_symbol_rows(),
        "position": {
            "rows": board.rows,
            "cols": board.cols,
            "total_mines": board.total_mines,
            "mines_flagged": board.flags_used,
            "hidden_cells": board.hidden_count,
        },
        "code_checks": {
            "consistent": not list(problems),
            "problems": list(problems)[:10],
        },
    }
