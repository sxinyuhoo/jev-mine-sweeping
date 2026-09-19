"""State builder and question builder tests.

The most important assertion in this file is the language rule: everything that
reaches Jev must be English, because Jev's primary training language is English
and CJK input is documented as less accurate.
"""

from __future__ import annotations

import json
import re

from jev_mine_sweeping.config import AgentConfig
from jev_mine_sweeping.game.board import HIDDEN, Board
from jev_mine_sweeping.game.solver import solve
from jev_mine_sweeping.game.state_builder import (
    build_state,
    estimate_tokens,
    select_candidates,
)
from jev_mine_sweeping.jev.questions import (
    RISK_ID,
    build_decision_questions,
    is_mine_id,
    parse_action_id,
    select_focus,
)

CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


def midgame_board() -> Board:
    return Board.from_grid(
        [
            [HIDDEN, HIDDEN, HIDDEN, HIDDEN, HIDDEN],
            [HIDDEN, 1, 2, 1, HIDDEN],
            [0, 0, 1, HIDDEN, HIDDEN],
            [0, 0, 0, 0, 0],
        ],
        total_mines=4,
        moves=7,
    )


def test_state_is_json_serialisable_and_english_only():
    board = midgame_board()
    candidates = select_candidates(board)
    state = build_state(board, candidates, solver_result=solve(board), history=[])
    payload = json.dumps(state, ensure_ascii=False)
    assert CJK.search(payload) is None, "the model payload must stay English"
    assert len(state["board_text"]) == board.rows
    assert state["position"]["hidden_cells"] == board.hidden_count
    assert state["position"]["total_mines"] == 4


def test_state_lists_candidate_arithmetic_and_constraints():
    board = midgame_board()
    candidates = select_candidates(board)
    state = build_state(board, candidates, solver_result=solve(board))
    assert state["candidate_cells"], "a mid-game position must offer candidates"
    entry = state["candidate_cells"][0]
    assert entry["cell"].startswith("r")
    assert "adjacent_numbers" in entry
    assert state["mine_constraints"], "revealed numbers must produce constraints"
    assert state["deterministic_layer"]["mode"] == "atomic"
    assert "proved_safe_cells" in state["derived_facts"]
    assert state["candidate_cells"][0]["status"] in ("unknown", "proved_safe", "proved_mine")
    assert "code_risk" in state["candidate_cells"][0]


def test_state_stays_inside_the_token_budget():
    board = Board.empty(9, 9, 10)
    candidates = select_candidates(board)
    state = build_state(board, candidates, solver_result=solve(board))
    assert estimate_tokens(state) < 8000


def test_blind_cells_are_reported_separately():
    board = Board.from_grid(
        [
            [HIDDEN, HIDDEN, HIDDEN, HIDDEN],
            [HIDDEN, 1, HIDDEN, HIDDEN],
            [HIDDEN, HIDDEN, HIDDEN, HIDDEN],
            [0, 0, 0, 0],
        ],
        total_mines=1,
    )
    state = build_state(board, select_candidates(board))
    # (1,3) touches only hidden cells, so no number can constrain it
    assert "(1, 3)" not in state["blind_hidden_cells"]
    assert "r1c3" in state["blind_hidden_cells"]
    assert state["candidate_cells"], "frontier cells still need to be described"
    assert "r1c3" not in [entry["cell"] for entry in state["candidate_cells"]]


def test_decision_questions_cover_every_candidate_and_parse_back():
    board = midgame_board()
    candidates = select_candidates(board)
    questions = build_decision_questions(board, candidates, max_nouls=40)

    assert questions["next_action"]["type"] == "choice"
    assert questions[RISK_ID]["type"] == "score"
    for cell in candidates:
        assert is_mine_id(cell) in questions
        assert questions[is_mine_id(cell)]["type"] == "noul"

    options = questions["next_action"]["criteria"]
    assert len(options) == 2 * len(candidates)
    for option in options:
        parsed = parse_action_id(option)
        assert parsed is not None, option
        action, cell = parsed
        assert action in ("open", "flag")
        assert cell in candidates
        assert options[option], "every option needs a description"


def test_choice_options_are_capped():
    board = Board.empty(9, 9, 10)
    candidates = board.hidden_cells()
    questions = build_decision_questions(board, candidates, max_options=20)
    assert len(questions["next_action"]["criteria"]) <= 20


def test_option_descriptions_do_not_leak_into_the_question_id():
    board = midgame_board()
    candidates = select_candidates(board)
    questions = build_decision_questions(board, candidates)
    # question ids are not sent to the model, so instructions must be complete
    assert "candidate_cells" in questions["next_action"]["instructions"]
    assert "board_text" in questions["next_action"]["instructions"]


def test_noul_instructions_carry_the_local_arithmetic():
    """Jev is a model, not a calculator: every is_mine question repeats the facts."""
    board = midgame_board()
    candidates = select_candidates(board)
    questions = build_decision_questions(board, candidates)
    for cell in candidates:
        instructions = questions[is_mine_id(cell)]["instructions"]
        assert "Local arithmetic" in instructions
        assert "derived_facts" in instructions


def test_focus_shortlist_puts_proved_safe_cells_first():
    board = midgame_board()
    candidates = select_candidates(board)
    deduction = solve(board)
    focus = select_focus(board, candidates, 6, deduction)
    assert focus, "the shortlist must not be empty"
    assert len(focus) <= 6
    head_size = min(len(deduction.safe), max(6 // 2, 1))
    assert set(deduction.safe[:head_size]) <= set(focus)
    assert not deduction.safe or focus[0] in set(deduction.safe)


def test_focus_shortlist_spreads_over_a_blind_board():
    board = Board.empty(16, 30, 99)
    focus = select_focus(board, select_candidates(board, "all_hidden"), 24)
    assert len(focus) == 24
    assert len({cell[0] for cell in focus}) >= 8


def test_agent_config_defaults_are_the_documented_ones():
    """Defaults are "every move is Jev's decision" — the project measures the model."""
    cfg = AgentConfig()
    assert cfg.decision_mode == "atomic"
    assert cfg.solver_mode == "off"
    assert cfg.safety_override is False
    assert cfg.confidence_floor == 0.0
    assert cfg.action_space == "frontier"
    assert cfg.solver_global_rule == "full"
