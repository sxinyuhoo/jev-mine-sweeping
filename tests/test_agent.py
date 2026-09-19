"""Decision composition tests.

Two decision modes are covered:

* ``atomic`` (default) — the move is composed from Jev's per-cell Noul
  probabilities plus its Choice, and the code only applies two thresholds.
* ``choice`` — the pre-optimisation behaviour, kept for comparison.

The stub client lets every branch be tested without a network call.
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from jev_mine_sweeping.agent.jev_agent import JevMinesweeperAgent
from jev_mine_sweeping.config import AgentConfig, JevConfig
from jev_mine_sweeping.game.board import FLAG, HIDDEN, Board
from jev_mine_sweeping.jev.client import JevError, JevResponse

AMBIGUOUS = [[HIDDEN] * 3, [HIDDEN, 1, HIDDEN], [HIDDEN] * 3]
FRONTIER = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1), (2, 2)]


def ambiguous_board() -> Board:
    """A position with no provable move: one "1" facing eight hidden cells."""
    return Board.from_grid(AMBIGUOUS, total_mines=1)


class StubClient:
    name = "stub"

    def __init__(self, answers: dict[str, Any] | None = None, error: Exception | None = None):
        self.answers = answers or {}
        self.error = error
        self.calls: list[tuple[Any, Mapping[str, Any]]] = []

    def ask(self, state, questions, model=None) -> JevResponse:
        self.calls.append((state, questions))
        if self.error:
            raise self.error
        return JevResponse(
            answers=self.answers,
            model="stub",
            usage={"input_tokens": 123, "output_tokens": 4},
            latency_ms=12.0,
            request={"state": state, "questions": dict(questions)},
        )


def choice(option: str, confidence: float = 0.9) -> dict[str, Any]:
    return {"type": "choice", "choice": option, "confidence": confidence, "probabilities": {}}


def noul(value: float) -> dict[str, Any]:
    return {"type": "noul", "noul": value}


def mine_nouls(values: Mapping[tuple[int, int], float], default: float | None = None) -> dict[str, Any]:
    """``is_mine__rXcY`` answers for every frontier cell."""
    answers: dict[str, Any] = {}
    for cell in FRONTIER:
        value = values.get(cell, default)
        if value is not None:
            answers[f"is_mine__r{cell[0]}c{cell[1]}"] = noul(value)
    return answers


def agent(client, **overrides) -> JevMinesweeperAgent:
    return JevMinesweeperAgent(client, AgentConfig(**overrides), JevConfig())


# ------------------------------------------------------------------ atomic mode
def test_atomic_opens_the_cell_the_model_rates_clearly_safe():
    """A cell the model itself rates safe wins over its own holistic pick."""
    answers = mine_nouls({(0, 0): 0.02, (2, 2): 0.45}, default=0.4)
    answers["next_action"] = choice("open_r2c2")
    decision = agent(StubClient(answers)).decide(ambiguous_board())
    assert decision is not None
    assert (decision.action, decision.cell) == ("open", (0, 0))
    assert decision.source == "noul"
    assert decision.risk == pytest.approx(0.02)


def test_atomic_honours_the_choice_when_every_risk_is_equal():
    """On a blind/flat board the model's holistic pick is what decides."""
    answers = mine_nouls({}, default=0.125)
    answers["next_action"] = choice("open_r0c0")
    decision = agent(StubClient(answers)).decide(ambiguous_board())
    assert decision is not None
    assert decision.source == "jev"
    assert (decision.action, decision.cell) == ("open", (0, 0))


def test_atomic_refuses_a_flag_the_model_itself_rates_as_safe():
    """The observed failure mode: a wasted flag on a cell the model rated 40%."""
    answers = mine_nouls({(0, 0): 0.40, (0, 1): 0.20}, default=0.30)
    answers["next_action"] = choice("flag_r0c0", confidence=0.12)
    decision = agent(StubClient(answers)).decide(ambiguous_board())
    assert decision is not None
    assert decision.action == "open", "a flag on a non-mine burns a move"
    assert decision.source == "noul_guess"
    assert decision.cell == (0, 1)


def test_atomic_honours_a_flag_on_a_cell_the_model_rates_as_a_mine():
    answers = mine_nouls({(0, 0): 0.80}, default=0.30)
    answers["next_action"] = choice("flag_r0c0")
    decision = agent(StubClient(answers)).decide(ambiguous_board())
    assert decision is not None
    assert (decision.action, decision.cell) == ("flag", (0, 0))
    assert decision.source == "jev"


def test_atomic_flags_the_riskiest_cell_when_nothing_is_safe():
    answers = mine_nouls({(1, 2): 0.75}, default=0.35)
    answers["next_action"] = choice("open_r0c0")
    decision = agent(StubClient(answers)).decide(ambiguous_board())
    assert decision is not None
    assert (decision.action, decision.cell) == ("flag", (1, 2))
    assert decision.source == "noul_flag"
    assert "overruled" in decision.note


def test_atomic_forced_guess_takes_the_least_risky_cell():
    answers = mine_nouls({(0, 0): 0.40, (0, 1): 0.30}, default=0.35)
    answers["next_action"] = choice("open_r0c0")
    decision = agent(StubClient(answers)).decide(ambiguous_board())
    assert decision is not None
    assert (decision.action, decision.cell) == ("open", (0, 1))
    assert decision.source == "noul_guess"
    assert decision.risk == pytest.approx(0.30)


def test_atomic_never_flags_when_the_mine_budget_is_spent():
    board = Board.from_grid(
        [[FLAG, HIDDEN, HIDDEN], [HIDDEN, 1, HIDDEN], [HIDDEN, HIDDEN, HIDDEN]],
        total_mines=1,
    )
    answers = mine_nouls({(1, 2): 0.9}, default=0.4)
    answers["next_action"] = choice("flag_r1c2")
    decision = agent(StubClient(answers)).decide(board)
    assert decision is not None
    assert decision.action == "open"


def test_atomic_asks_only_about_the_shortlist():
    answers = mine_nouls({}, default=0.2)
    answers["next_action"] = choice("open_r0c0")
    client = StubClient(answers)
    decision = agent(client, focus_cells=6).decide(ambiguous_board())
    assert decision is not None
    _, questions = client.calls[0]
    nouls = [key for key in questions if key.startswith("is_mine__")]
    options = questions["next_action"]["criteria"]
    assert len(nouls) <= 6
    assert len(options) <= 12, "two options per shortlisted cell"


def test_atomic_state_carries_the_derived_facts():
    answers = mine_nouls({}, default=0.2)
    answers["next_action"] = choice("open_r0c0")
    client = StubClient(answers)
    agent(client).decide(ambiguous_board())
    state, questions = client.calls[0]
    assert state["derived_facts"]["note"]
    assert "proved_safe_cells" in state["derived_facts"]
    assert state["deterministic_layer"]["mode"] == "atomic"
    assert all("code_risk" in entry for entry in state["candidate_cells"])
    assert all("status" in entry for entry in state["candidate_cells"])
    assert "action_space_note" in state


def test_atomic_without_any_noul_answer_falls_back_to_the_code_estimate():
    client = StubClient({"next_action": choice("open_r0c0")})
    decision = agent(client).decide(ambiguous_board())
    assert decision is not None
    assert decision.source == "fallback"
    assert decision.cell in FRONTIER


def test_atomic_defaults_are_pure_jev():
    a = agent(StubClient())
    assert a.cfg.decision_mode == "atomic"
    assert a.cfg.solver_mode == "off"
    assert a.cfg.safety_override is False
    assert a.cfg.confidence_floor == 0.0


# ------------------------------------------------------------------ choice mode
def test_solver_mode_executes_proved_moves_without_an_api_call():
    board = Board.from_grid(
        [[FLAG, HIDDEN, HIDDEN], [HIDDEN, 1, HIDDEN], [HIDDEN, HIDDEN, HIDDEN]],
        total_mines=1,
    )
    client = StubClient()
    decision = agent(client, solver_mode="prefilter").decide(board)
    assert decision is not None
    assert decision.source == "solver"
    assert decision.action == "open"
    assert client.calls == []


def test_choice_mode_is_honoured_when_it_looks_safe():
    client = StubClient(
        {
            "next_action": choice("open_r0c0"),
            "is_mine__r0c0": noul(0.10),
            "is_mine__r0c1": noul(0.15),
            "danger": {"type": "score", "score": 1.0, "confidence": 0.7, "probabilities": {}},
        }
    )
    decision = agent(client, decision_mode="choice").decide(ambiguous_board())
    assert decision is not None
    assert decision.source == "jev"
    assert (decision.action, decision.cell) == ("open", (0, 0))
    assert decision.risk == pytest.approx(0.10)
    assert decision.danger == pytest.approx(1.0)
    assert decision.state is not None and decision.questions is not None


def test_choice_mode_override_refuses_to_open_a_cell_jev_calls_a_mine():
    client = StubClient(
        {
            "next_action": choice("open_r0c0"),
            "is_mine__r0c0": noul(0.95),
            "is_mine__r0c1": noul(0.05),
        }
    )
    decision = agent(client, decision_mode="choice", safety_override=True).decide(ambiguous_board())
    assert decision is not None
    assert decision.source == "override"
    assert (decision.action, decision.cell) == ("open", (0, 1))
    assert "override" in decision.note


def test_choice_mode_override_flags_when_every_candidate_looks_risky():
    answers = {"next_action": choice("open_r0c0")}
    answers.update({f"is_mine__r{r}c{c}": noul(0.7) for r in range(3) for c in range(3)})
    decision = agent(
        StubClient(answers), decision_mode="choice", safety_override=True
    ).decide(ambiguous_board())
    assert decision is not None
    assert decision.source == "override"
    assert decision.action == "flag"


def test_choice_mode_override_turns_a_wasted_flag_into_an_open():
    client = StubClient(
        {
            "next_action": choice("flag_r0c0"),
            "is_mine__r0c0": noul(0.10),
            "is_mine__r0c1": noul(0.02),
        }
    )
    decision = agent(client, decision_mode="choice", safety_override=True).decide(ambiguous_board())
    assert decision is not None
    assert decision.source == "override"
    assert (decision.action, decision.cell) == ("open", (0, 1))


def test_choice_mode_override_is_off_by_default():
    client = StubClient({"next_action": choice("open_r0c0"), "is_mine__r0c0": noul(0.95)})
    decision = agent(client, decision_mode="choice").decide(ambiguous_board())
    assert decision is not None
    assert decision.source == "jev"
    assert decision.cell == (0, 0)


def test_choice_mode_low_confidence_falls_back_to_the_code_estimate():
    client = StubClient(
        {"next_action": choice("open_r0c0", confidence=0.1), "is_mine__r0c0": noul(0.2)}
    )
    decision = agent(client, decision_mode="choice", confidence_floor=0.4).decide(ambiguous_board())
    assert decision is not None
    assert decision.source == "fallback"
    assert "confidence" in decision.note


def test_choice_mode_illegal_option_falls_back():
    client = StubClient({"next_action": choice("open_r8c8")})
    decision = agent(client, decision_mode="choice").decide(ambiguous_board())
    assert decision is not None
    assert decision.source == "fallback"
    assert decision.cell in ambiguous_board().hidden_cells()


def test_choice_mode_missing_option_falls_back():
    client = StubClient({"next_action": {"type": "choice", "confidence": 0.9}})
    decision = agent(client, decision_mode="choice").decide(ambiguous_board())
    assert decision is not None
    assert decision.source == "fallback"


# ------------------------------------------------------------------- both modes
def test_api_failure_falls_back_instead_of_crashing():
    client = StubClient(error=JevError("boom"))
    decision = agent(client).decide(ambiguous_board())
    assert decision is not None
    assert decision.source == "fallback"
    assert "Jev unavailable" in decision.note


def test_no_solver_mode_lets_jev_decide_every_move():
    board = Board.from_grid(
        [[FLAG, HIDDEN, HIDDEN], [HIDDEN, 1, HIDDEN], [HIDDEN, HIDDEN, HIDDEN]],
        total_mines=1,
    )
    client = StubClient({"next_action": choice("open_r2c2"), "is_mine__r2c2": noul(0.1)})
    decision = agent(client, solver_mode="off").decide(board)
    assert decision is not None
    assert decision.cell == (2, 2)
    assert client.calls, "solver_mode=off must ask Jev for every move"


def test_action_space_all_hidden_offers_every_hidden_cell():
    board = ambiguous_board()
    assert len(agent(StubClient(), action_space="all_hidden").candidate_cells(board)) == 8


def test_decide_returns_none_on_a_finished_board():
    board = Board.from_grid([[0, 0], [0, 0]], total_mines=0)
    assert agent(StubClient()).decide(board) is None


def test_guard_mode_restores_the_solver_and_the_cross_check():
    assert AgentConfig().solver_mode == "off"
    guarded = AgentConfig(solver_mode="prefilter", safety_override=True, confidence_floor=0.4)
    assert guarded.solver_mode == "prefilter"
