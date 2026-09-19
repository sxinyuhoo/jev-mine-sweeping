"""Board presets, config I/O and the action-space caps."""

from __future__ import annotations

import pytest

from jev_mine_sweeping.config import (
    DIFFICULTIES,
    AgentConfig,
    Config,
    GameConfig,
    JevConfig,
    difficulty,
    load_config,
)
from jev_mine_sweeping.game.board import HIDDEN, Board
from jev_mine_sweeping.game.state_builder import select_candidates
from jev_mine_sweeping.jev.questions import build_action_choice, parse_action_id, select_focus


# --------------------------------------------------------------------- presets
def test_difficulty_presets_match_the_classic_buttons():
    assert difficulty("困难") == (16, 16, 40)
    assert difficulty("hard") == (16, 16, 40)
    assert difficulty("专家") == (16, 30, 99)
    assert difficulty("expert") == (16, 30, 99)
    assert difficulty("简单") == (9, 9, 10)
    assert difficulty(" Expert ") == (16, 30, 99)
    with pytest.raises(ValueError):
        difficulty("impossible")
    assert {"困难", "专家"} <= set(DIFFICULTIES)


# ------------------------------------------------------- action space on 16x30
def test_option_list_is_capped_and_spread_on_a_blind_large_board():
    board = Board.empty(16, 30, 99)
    candidates = select_candidates(board, "all_hidden")
    assert len(candidates) == 480
    question = build_action_choice(board, candidates, max_options=240)
    options = question["criteria"]
    assert len(options) <= 240
    cells = [parse_action_id(option) for option in options]
    assert all(parsed is not None for parsed in cells)
    rows = {parsed[1][0] for parsed in cells if parsed}
    assert len(rows) >= 8, f"blind sampling should spread over the board, got rows {sorted(rows)}"


def test_focused_option_list_stays_small_and_spread():
    """The default question offers a shortlist the model can actually weigh."""
    board = Board.empty(16, 30, 99)
    candidates = select_candidates(board, "all_hidden")
    focus = select_focus(board, candidates, 24)
    question = build_action_choice(board, candidates, max_options=60, focus=focus)
    cells = [parse_action_id(option) for option in question["criteria"] if option]
    assert 0 < len(cells) <= 48, "focus_cells=24 means at most 24 cells x 2 actions"
    rows = {parsed[1][0] for parsed in cells if parsed}
    assert len(rows) >= 8, f"the shortlist should spread over the board, got rows {sorted(rows)}"


# ------------------------------------------------------------------- config I/O
def test_unquoted_yaml_off_is_read_as_the_string_off(tmp_path):
    """YAML 1.1 turns a bare `off` into False, which must not silently enable the solver."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "jev:\n  backend: mock\n"
        "agent:\n  solver_mode: off\n  decision_mode: choice\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.agent.solver_mode == "off"
    assert isinstance(cfg.agent.solver_mode, str)
    assert cfg.agent.decision_mode == "choice"


def test_unknown_config_keys_are_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("agent:\n  not_a_real_key: 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_defaults_are_pure_jev_on_an_expert_board():
    cfg = Config(
        jev=JevConfig(),
        game=GameConfig(rows=16, cols=30, mines=99),
        agent=AgentConfig(),
    )
    assert cfg.agent.decision_mode == "atomic"
    assert cfg.agent.solver_mode == "off"
    assert cfg.agent.safety_override is False
    assert cfg.agent.confidence_floor == 0.0
    assert cfg.game.cols == 30
    assert HIDDEN == 7
