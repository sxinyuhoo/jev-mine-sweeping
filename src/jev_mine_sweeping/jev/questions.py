"""Jev question builders — Choice / Noul / Score, per the TypeSafe primitives.

Design notes (these are deliberate, not incidental):

* **One request per decision.** The three question types are mixed in a single
  call because questions are evaluated in parallel and in isolation: adding them
  barely changes latency, while splitting them into separate calls costs roughly
  an order of magnitude more (TypeSafe's own measurement: 13 questions in one
  call was 11.5x cheaper and 9.6x faster than 13 calls).
* **Ask about the cells we might act on, and nothing else.** A 16x30 board has
  480 cells; a Choice over all of them spends thousands of tokens to make the
  model pick between near-identical options, and dilutes its attention. The
  option list is therefore a *shortlist* (:func:`select_focus`) built from the
  facts code already derived — proved-safe cells first, then the safest and the
  riskiest cells — and the per-cell Nouls cover exactly that shortlist.
* **Atomic questions carry the arithmetic.** Jev is a model, not a calculator:
  each ``is_mine`` question repeats the local number arithmetic so the judgment
  is made from facts, not from scanning a 480-cell grid.
* **Choice = the holistic pick; Noul = the calibrated risk; Score = telemetry.**
  ``next_action`` says what the model would do; the per-cell ``is_mine`` Nouls
  say how risky each candidate is, and those probabilities are what ranks the
  moves in ``atomic`` decision mode.
* **Option names are sent to the model; question ids are not.** Every option
  therefore carries a description with the local arithmetic, and every
  ``instructions`` string is self-contained.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..game.board import HIDDEN, Board, Cell, format_cell
from ..game.solver import Deduction, build_constraints, rank_guesses

OPEN_PREFIX = "open_"
FLAG_PREFIX = "flag_"
IS_MINE_PREFIX = "is_mine__"
RISK_ID = "danger"


def action_id(action: str, cell: Cell) -> str:
    return f"{OPEN_PREFIX if action == 'open' else FLAG_PREFIX}{format_cell(cell)}"


def parse_action_id(option: str) -> tuple[str, Cell] | None:
    """``"open_r3c4"`` -> ``("open", (3, 4))``; ``None`` when malformed."""
    from ..game.board import parse_cell

    text = option.strip()
    for prefix, action in ((OPEN_PREFIX, "open"), (FLAG_PREFIX, "flag")):
        if text.startswith(prefix):
            try:
                return action, parse_cell(text[len(prefix) :])
            except ValueError:
                return None
    return None


def is_mine_id(cell: Cell) -> str:
    return f"{IS_MINE_PREFIX}{format_cell(cell)}"


def _local_summary(board: Board, cell: Cell) -> str:
    """Compact arithmetic shown in an option description."""
    parts = []
    for n in board.neighbours(cell):
        value = board.get(n)
        if 0 <= value <= 6:
            flagged, hidden, _ = board.local_counts(n)
            needed = max(value - flagged, 0)
            parts.append(f"{format_cell(n)}={value} ({flagged}F/{hidden}?/{needed} left)")
    if not parts:
        return "no adjacent number, blind guess"
    return "; ".join(parts[:3])


def _spread_sample(cells: list[Cell], limit: int) -> list[Cell]:
    """Evenly spaced subset, for boards with no constraints to rank by.

    On the opening move every cell has the same risk estimate, and truncating
    row-major would silently restrict the model to the top rows — so the sample
    is spread over the whole board instead.
    """
    if len(cells) <= limit:
        return cells
    step = len(cells) / limit
    return [cells[int(i * step)] for i in range(limit)]


def select_focus(
    board: Board,
    candidates: Sequence[Cell],
    size: int,
    deductions: Deduction | None = None,
) -> list[Cell]:
    """The shortlist the model is offered as its action space.

    Order of inclusion:

    1. cells the constraint rules *proved* safe — they are the best moves, and
       the model is told so in the option description;
    2. the safest unknown cells by the code risk estimate;
    3. a few of the riskiest ones, so flagging is a real option.

    A blind board (no constraints yet) has nothing to rank by, so the shortlist
    is spread over the whole board instead.
    """
    size = max(int(size), 2)
    hidden = [cell for cell in candidates if board.get(cell) == HIDDEN]
    if not build_constraints(board):
        return _spread_sample(list(candidates), size)

    proved_safe = [
        cell for cell in (deductions.safe if deductions else []) if board.get(cell) == HIDDEN
    ]
    ranked = [cell for cell, _ in rank_guesses(board, hidden)]
    head = proved_safe[: max(size // 2, 1)]
    rest = [cell for cell in ranked if cell not in head]
    budget = max(size - len(head), 1)
    n_risky = max(1, budget // 4) if len(rest) > budget else 0
    safest = rest[: budget - n_risky]
    riskiest = [cell for cell in reversed(rest) if cell not in safest][:n_risky]
    return head + safest + riskiest


def build_action_choice(
    board: Board,
    candidates: Sequence[Cell],
    *,
    max_options: int = 60,
    include_flag: bool = True,
    focus: Sequence[Cell] | None = None,
    deductions: Deduction | None = None,
) -> dict[str, Any]:
    """The ``next_action`` Choice question.

    Options cover both actions on every cell of the shortlist (``focus`` when
    given, otherwise a truncated candidate list). Option descriptions carry the
    local arithmetic *and* the code-side status, so the model's holistic pick is
    made with the derived facts in hand.
    """
    per_cell = 2 if include_flag else 1
    cell_budget = max(max_options // per_cell, 1)
    if focus is not None:
        cells = list(focus)[:cell_budget]
    elif build_constraints(board):
        ranked = [cell for cell, _ in rank_guesses(board, candidates)]
        cells = ranked[:cell_budget]
    else:
        cells = _spread_sample(list(candidates), cell_budget)

    proved_safe = set(deductions.safe) if deductions else set()
    proved_mines = set(deductions.mines) if deductions else set()

    criteria: dict[str, str] = {}
    for cell in cells:
        summary = _local_summary(board, cell)
        name = format_cell(cell)
        if cell in proved_safe:
            status = "The constraint rules prove this cell is safe"
        elif cell in proved_mines:
            status = "The constraint rules prove this cell is a mine"
        else:
            status = "Unknown cell"
        criteria[action_id("open", cell)] = (
            f"Open {name} (left click). {summary}. {status}. "
            "Chosen when the cell is safe or is the least risky guess."
        )
        if include_flag:
            criteria[action_id("flag", cell)] = (
                f"Flag {name} (right click). {summary}. {status}. "
                "Chosen when this cell is a mine and flagging helps later deductions."
            )
    return {
        "type": "choice",
        "instructions": (
            "Given `board_text`, `derived_facts`, `mine_constraints` and the local "
            "arithmetic in each option, which single action should be played next? "
            "The cells you may act on are exactly the ones in `candidate_cells`; "
            "pick exactly one option id. Never open a cell you believe is a mine; "
            "flag it instead. Never flag a cell you believe is safe."
        ),
        "criteria": criteria,
    }


def build_mine_nouls(board: Board, candidates: Sequence[Cell], *, limit: int = 40) -> dict[str, Any]:
    """One ``is_mine`` Noul per candidate cell (independent, asked in parallel)."""
    questions: dict[str, Any] = {}
    for cell in candidates[:limit]:
        name = format_cell(cell)
        questions[is_mine_id(cell)] = {
            "type": "noul",
            "instructions": (
                f"Using `board_text`, `derived_facts` and the local arithmetic below, "
                f"is the hidden cell {name} a mine? High probability means opening it "
                f"would end the game. Local arithmetic: {_local_summary(board, cell)}."
            ),
            "criteria": {
                "true": f"{name} is one of the mines still on the board",
                "false": f"{name} is safe to open",
            },
        }
    return questions


def build_danger_score(board: Board) -> dict[str, Any]:
    """A single Score describing how risky the position is (telemetry)."""
    return {
        RISK_ID: {
            "type": "score",
            "instructions": "How dangerous is the current position for the player?",
            "criteria": [
                "Safe: a certain move exists, no guess is needed",
                "Manageable: a guess is needed but the odds are clearly favourable",
                "Risky: a guess is needed with roughly even odds or worse",
            ],
        }
    }


def build_decision_questions(
    board: Board,
    candidates: Sequence[Cell],
    *,
    max_options: int = 60,
    max_nouls: int = 40,
    ask_danger: bool = True,
    include_flag: bool = True,
    focus: Sequence[Cell] | None = None,
    deductions: Deduction | None = None,
) -> dict[str, Any]:
    """The full question set for one decision, sent as a single request.

    When ``focus`` is given (atomic mode) the Nouls cover exactly those cells —
    the ones the code could act on. Asking about cells that cannot be played
    costs tokens and adds no signal.
    """
    questions: dict[str, Any] = {
        "next_action": build_action_choice(
            board,
            candidates,
            max_options=max_options,
            include_flag=include_flag,
            focus=focus,
            deductions=deductions,
        )
    }
    noul_cells = list(focus) if focus is not None else list(candidates)
    questions.update(build_mine_nouls(board, noul_cells, limit=max_nouls))
    if ask_danger:
        questions.update(build_danger_score(board))
    return questions
