"""The decision loop.

Two decision modes:

* **atomic** (default) — the code derives the *facts* (constraint deductions,
  per-cell code risk, the shortlist worth considering) and hands them to Jev in
  the state; one request asks a Choice over the shortlist, one ``is_mine`` Noul
  per shortlisted cell, and a danger Score. The move is then composed from Jev's
  *own* answers:

  1. ``noul`` — a cell the model rates at or below ``safe_threshold`` is opened.
     This is the model's calibrated per-cell judgment doing the work, instead of
     asking it to classify 480 near-identical options in one shot.
  2. ``jev`` — the model's own flag call, on a cell it rates at or above
     ``mine_threshold``.
  3. ``jev`` — the model's open call, when its own Noul agrees it is among the
     safest (within ``choice_tolerance`` of the safest cell). On a blind board,
     where every cell carries the same risk, this is what decides.
  4. ``noul_flag`` — every open is a gamble and the model names a likely mine:
     flag it instead of opening. Fires only when the best open is riskier than
     ``flag_preference``, so a comfortable move is never passed up for a flag.
  5. ``noul_guess`` — nothing is safe and nothing is clearly a mine: open the
     least risky cell the model named.

* **choice** — one Choice over the candidates, played verbatim, with the optional
  cross-checks (``safety_override``, ``confidence_floor``). This is the
  pre-optimisation behaviour, kept so the two can be compared on the same board.

Two more sources exist in both modes: ``solver`` (the constraint solver is
allowed to execute what it proves — ``solver_mode: prefilter`` / ``--guarded``)
and ``fallback`` (the code risk estimate takes over when the API fails).

Everything the model decided, and every overrule, is recorded in the run log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..config import AgentConfig, JevConfig
from ..game.board import HIDDEN, Board, Cell, format_cell
from ..game.solver import (
    Deduction,
    best_guess,
    rank_guesses,
    solve,
)
from ..game.state_builder import (
    build_state,
    estimate_tokens,
    select_candidates,
)
from ..jev.client import JevClient, JevError, JevResponse
from ..jev.questions import (
    RISK_ID,
    build_decision_questions,
    parse_action_id,
    select_focus,
)


@dataclass
class Decision:
    """One move, plus the full audit trail of how it was reached."""

    action: str  # "open" | "flag"
    cell: Cell
    source: str  # noul | jev | noul_flag | noul_guess | solver | override | fallback
    confidence: float = 1.0
    note: str = ""
    risk: float | None = None
    risks: dict[str, float] = field(default_factory=dict)
    danger: float | None = None
    state: dict[str, Any] | None = None
    questions: dict[str, Any] | None = None
    response: JevResponse | None = None
    solver: Deduction | None = None
    latency_ms: float = 0.0

    def as_record(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "cell": format_cell(self.cell),
            "source": self.source,
            "confidence": round(self.confidence, 4),
            "risk": None if self.risk is None else round(self.risk, 4),
            "danger": self.danger,
            "note": self.note,
            "latency_ms": round(self.latency_ms, 1),
        }

    def describe(self) -> str:
        risk = "?" if self.risk is None else f"{self.risk:.2f}"
        return (
            f"{self.action} {format_cell(self.cell)} "
            f"[{self.source}] p_mine={risk} conf={self.confidence:.2f} {self.note}".rstrip()
        )


class JevMinesweeperAgent:
    """Turns a board reading into one move."""

    def __init__(
        self,
        client: JevClient,
        agent_cfg: AgentConfig,
        jev_cfg: JevConfig | None = None,
    ) -> None:
        self.client = client
        self.cfg = agent_cfg
        self.jev_cfg = jev_cfg or JevConfig()
        self.last_state: dict[str, Any] | None = None
        self.last_questions: dict[str, Any] | None = None
        self.api_calls = 0
        self.solver_moves = 0

    # ------------------------------------------------------------- candidates
    def candidate_cells(self, board: Board) -> list[Cell]:
        """Cells the model is allowed to choose from."""
        return select_candidates(board, self.cfg.action_space)

    @property
    def atomic(self) -> bool:
        return self.cfg.decision_mode != "choice"

    # ----------------------------------------------------------------- decide
    def decide(
        self,
        board: Board,
        *,
        history: Sequence[dict[str, Any]] = (),
        step: int | None = None,
    ) -> Decision | None:
        if board.status != "in_progress" or not board.hidden_cells():
            return None

        # The constraint rules are always run: in atomic mode their conclusions
        # are *facts* handed to the model, and in prefilter mode they also
        # execute the moves they can prove.
        deduction = Deduction()
        try:
            deduction = solve(board, global_rule=self.cfg.solver_global_rule)
        except Exception:  # a contradictory board must not stop the game
            deduction = Deduction()

        if self.cfg.solver_mode != "off":
            if deduction.safe:
                cell = self._best_safe_cell(board, deduction.safe)
                self.solver_moves += 1
                return Decision(
                    "open",
                    cell,
                    "solver",
                    note=deduction.notes[0] if deduction.notes else "proved safe by constraint rules",
                    risk=0.0,
                    solver=deduction,
                )
            if deduction.mines:
                cell = deduction.mines[0]
                self.solver_moves += 1
                return Decision(
                    "flag",
                    cell,
                    "solver",
                    note=deduction.notes[0] if deduction.notes else "proved a mine by constraint rules",
                    risk=1.0,
                    solver=deduction,
                )

        candidates = self.candidate_cells(board)
        if not candidates:
            return None

        focus = (
            select_focus(board, candidates, self.cfg.focus_cells, deduction)
            if self.atomic
            else None
        )
        action_space = focus if focus else candidates

        state = build_state(
            board,
            action_space,
            history=history,
            solver_result=deduction,
            solver_mode=self.cfg.solver_mode,
            decision_mode=self.cfg.decision_mode,
            max_frontier_entries=self.cfg.max_frontier_entries,
            step=step,
        )
        questions = build_decision_questions(
            board,
            action_space,
            max_options=self.jev_cfg.max_choice_options,
            max_nouls=self.jev_cfg.max_noul_questions,
            ask_danger=self.jev_cfg.ask_danger_score,
            # The mine counter is a hard budget: once every mine is flagged, no
            # further flag can be right, so the option is not even offered.
            include_flag=board.mines_remaining > 0,
            focus=focus,
            deductions=deduction,
        )
        self.last_state = state
        self.last_questions = questions
        self.api_calls += 1

        budget_note = ""
        tokens = estimate_tokens(state) + estimate_tokens(questions)
        if tokens > 24000:
            budget_note = f" | WARNING: request is ~{tokens} tokens of a 32000 budget"

        try:
            response = self.client.ask(state, questions)
        except JevError as exc:
            guess = best_guess(board, action_space)
            if guess is None:
                return None
            cell, risk = guess
            return Decision(
                "open",
                cell,
                "fallback",
                note=f"Jev unavailable ({exc}); used code risk estimate",
                risk=risk,
                state=state,
                questions=questions,
                solver=deduction,
            )

        compose = self._compose_atomic if self.atomic else self._compose_choice
        decision = compose(board, action_space, response, deduction, state, questions)
        if budget_note:
            decision.note = f"{decision.note}{budget_note}"
        return decision

    # ------------------------------------------------------- atomic composition
    def _compose_atomic(
        self,
        board: Board,
        focus: Sequence[Cell],
        response: JevResponse,
        deduction: Deduction,
        state: dict[str, Any],
        questions: dict[str, Any],
    ) -> Decision:
        """Compose the move from Jev's per-cell probabilities and its Choice."""
        option, confidence, _ = response.choice("next_action")
        risks = response.noul_map("is_mine__")
        danger, _, _ = response.score(RISK_ID)

        decision = Decision(
            action="open",
            cell=focus[0],
            source="noul",
            confidence=confidence,
            risks=risks,
            danger=danger,
            state=state,
            questions=questions,
            response=response,
            solver=deduction,
            latency_ms=response.latency_ms,
            note=f"model={response.model} tokens={response.usage.get('input_tokens', '?')}",
        )

        ranked = self._ranked(board, focus, risks)
        if not ranked:
            return self._fallback(
                board, focus, risks, decision, "no per-cell probability came back"
            )
        safest, safest_p = ranked[0]
        riskiest, riskiest_p = ranked[-1]

        parsed = parse_action_id(option) if option else None
        choice_action, choice_cell = parsed if parsed else (None, None)
        choice_p = risks.get(format_cell(choice_cell)) if choice_cell else None
        choice_legal = (
            choice_cell is not None
            and choice_cell in set(focus)
            and board.get(choice_cell) == HIDDEN
            and (
                choice_action == "open"
                or (choice_action == "flag" and board.mines_remaining > 0)
            )
        )

        # 1. a cell the model rates clearly safe
        if safest_p <= self.cfg.safe_threshold:
            decision.action, decision.cell, decision.risk = "open", safest, safest_p
            decision.source = "noul"
            decision.note = (
                f"{decision.note} | safest cell {format_cell(safest)} "
                f"p_mine={safest_p:.2f} <= {self.cfg.safe_threshold:.2f}"
            )
            return decision

        # 2a. the model's own flag call, on a cell it rates a likely mine
        if (
            choice_legal
            and choice_cell is not None
            and choice_action == "flag"
            and choice_p is not None
            and choice_p >= self.cfg.mine_threshold
        ):
            decision.action, decision.cell, decision.risk = "flag", choice_cell, choice_p
            decision.source = "jev"
            decision.note = (
                f"{decision.note} | model flagged {format_cell(choice_cell)} "
                f"(p_mine={choice_p:.2f} >= {self.cfg.mine_threshold:.2f})"
            )
            return decision

        # Is flagging a probable mine better than gambling? Only when no
        # comfortable open exists — a 10%-risk open is always worth taking.
        prefer_flag = (
            board.mines_remaining > 0
            and riskiest_p >= self.cfg.mine_threshold
            and safest_p > self.cfg.flag_preference
        )

        # 3. the model's own open call, when its risk agrees it is among the safest
        tolerance = min(safest_p + self.cfg.choice_tolerance, self.cfg.mine_threshold)
        if (
            choice_legal
            and choice_cell is not None
            and choice_action == "open"
            and choice_p is not None
            and choice_p <= tolerance
            and not prefer_flag
        ):
            decision.action, decision.cell, decision.risk = "open", choice_cell, choice_p
            decision.source = "jev"
            decision.note = (
                f"{decision.note} | model chose {format_cell(choice_cell)} "
                f"(p_mine={choice_p:.2f}, safest {safest_p:.2f})"
            )
            return decision

        # 4. every open is a gamble, but the model names a likely mine: flag it
        if prefer_flag:
            decision.action, decision.cell, decision.risk = "flag", riskiest, riskiest_p
            decision.source = "noul_flag"
            note = (
                f"{decision.note} | no safe move; {format_cell(riskiest)} reads as a mine "
                f"(p_mine={riskiest_p:.2f})"
            )
            if choice_cell is not None and not (choice_action == "flag" and choice_cell == riskiest):
                note += f" | model's Choice {option} overruled by its own Noul"
            decision.note = note
            return decision

        # 5. a guess is unavoidable: take the least risky cell the model named
        decision.action, decision.cell, decision.risk = "open", safest, safest_p
        decision.source = "noul_guess"
        note = (
            f"{decision.note} | forced guess: least risky is {format_cell(safest)} "
            f"(p_mine={safest_p:.2f})"
        )
        if choice_cell is not None and choice_cell != safest:
            shown = "?" if choice_p is None else f"{choice_p:.2f}"
            note += f" | model's Choice {option} (p_mine={shown})"
        decision.note = note
        return decision

    # ------------------------------------------------------- choice composition
    def _compose_choice(
        self,
        board: Board,
        candidates: Sequence[Cell],
        response: JevResponse,
        deduction: Deduction,
        state: dict[str, Any],
        questions: dict[str, Any],
    ) -> Decision:
        """Play Jev's Choice verbatim, with the optional cross-checks."""
        option, confidence, _ = response.choice("next_action")
        risks = response.noul_map("is_mine__")
        danger, _, _ = response.score(RISK_ID)

        parsed = parse_action_id(option) if option else None
        decision = Decision(
            action="open",
            cell=candidates[0],
            source="jev",
            confidence=confidence,
            risks=risks,
            danger=danger,
            state=state,
            questions=questions,
            response=response,
            solver=deduction,
            latency_ms=response.latency_ms,
            note=f"model={response.model} tokens={response.usage.get('input_tokens', '?')}",
        )

        if parsed is None:
            return self._fallback(board, candidates, risks, decision, "Choice returned no usable option")
        action, cell = parsed
        if cell not in candidates:
            return self._fallback(
                board, candidates, risks, decision, f"Choice named {format_cell(cell)}, not a legal candidate"
            )
        if action == "open" and board.get(cell) != HIDDEN:
            return self._fallback(
                board, candidates, risks, decision, f"{format_cell(cell)} is not hidden"
            )
        if action == "flag" and board.mines_remaining <= 0:
            return self._fallback(
                board,
                candidates,
                risks,
                decision,
                "every mine is already flagged, so no further flag can be correct",
            )

        decision.action, decision.cell = action, cell
        decision.risk = risks.get(format_cell(cell))

        # --- confidence gate -------------------------------------------------
        if confidence < self.cfg.confidence_floor:
            return self._fallback(
                board,
                candidates,
                risks,
                decision,
                f"Choice confidence {confidence:.2f} < floor {self.cfg.confidence_floor:.2f}",
            )

        # --- safety override -------------------------------------------------
        if self.cfg.safety_override:
            override = self._safety_override(board, candidates, action, cell, risks)
            if override is not None:
                o_action, o_cell, reason = override
                decision.action, decision.cell = o_action, o_cell
                decision.source = "override"
                decision.risk = risks.get(format_cell(o_cell))
                decision.note = f"{decision.note} | {reason}"
        return decision

    def _safety_override(
        self,
        board: Board,
        candidates: Sequence[Cell],
        action: str,
        cell: Cell,
        risks: dict[str, float],
    ) -> tuple[str, Cell, str] | None:
        """Refuse to open a likely mine, or to burn a move on a safe flag."""
        if not risks:
            return None
        chosen_risk = risks.get(format_cell(cell))

        if action == "open" and chosen_risk is not None and chosen_risk > self.cfg.override_threshold:
            safest = self._safest(board, candidates, risks)
            if safest is not None and safest[1] <= self.cfg.override_threshold:
                return (
                    "open",
                    safest[0],
                    f"override: {format_cell(cell)} p_mine={chosen_risk:.2f} > "
                    f"{self.cfg.override_threshold:.2f}; opened safer {format_cell(safest[0])} "
                    f"(p_mine={safest[1]:.2f})",
                )
            # Nothing is comfortably safe. Flagging only makes sense for a cell the
            # model itself calls a mine — a flag on a coin flip just burns a move
            # and a slot of the mine budget.
            riskiest = self._riskiest(candidates, risks)
            if (
                riskiest is not None
                and board.mines_remaining > 0
                and riskiest[1] >= self.cfg.mine_threshold
            ):
                return (
                    "flag",
                    riskiest[0],
                    f"override: every candidate looks risky, but {format_cell(riskiest[0])} "
                    f"reads as a mine (p_mine={riskiest[1]:.2f}); flagged it instead of "
                    f"opening {format_cell(cell)}",
                )
            if safest is not None:
                return (
                    "open",
                    safest[0],
                    f"override: a guess is unavoidable; took the least risky open "
                    f"({format_cell(safest[0])}, p_mine={safest[1]:.2f}) instead of "
                    f"{format_cell(cell)} (p_mine={chosen_risk:.2f})",
                )

        if action == "flag" and chosen_risk is not None and chosen_risk < self.cfg.mine_threshold:
            safest = self._safest(board, candidates, risks)
            if safest is not None and safest[1] < self.cfg.mine_threshold:
                return (
                    "open",
                    safest[0],
                    f"override: {format_cell(cell)} p_mine={chosen_risk:.2f} looks safe, so a flag "
                    f"would waste a move; opened {format_cell(safest[0])} instead",
                )
        return None

    # --------------------------------------------------------------- fallbacks
    def _fallback(
        self,
        board: Board,
        candidates: Sequence[Cell],
        risks: dict[str, float],
        decision: Decision,
        reason: str,
    ) -> Decision:
        safest = self._safest(board, candidates, risks)
        if safest is not None:
            decision.action, decision.cell = "open", safest[0]
            decision.risk = safest[1]
        else:
            guess = best_guess(board, candidates)
            if guess is None:
                return decision
            decision.action, decision.cell = "open", guess[0]
            decision.risk = guess[1]
        decision.source = "fallback"
        decision.note = f"{decision.note} | fallback: {reason}".strip(" |")
        return decision

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _best_safe_cell(board: Board, safe: Sequence[Cell]) -> Cell:
        """Among proved-safe cells, open the one that reveals the most information."""
        def information(cell: Cell) -> tuple[int, tuple[int, int]]:
            hidden_neighbours = sum(1 for n in board.neighbours(cell) if board.get(n) == HIDDEN)
            return (-hidden_neighbours, cell)

        return min(safe, key=information)

    @staticmethod
    def _ranked(
        board: Board, candidates: Sequence[Cell], risks: dict[str, float]
    ) -> list[tuple[Cell, float]]:
        """Hidden candidates with a Jev probability, least risky first."""
        scored = [
            (cell, risks[format_cell(cell)])
            for cell in candidates
            if format_cell(cell) in risks and board.get(cell) == HIDDEN
        ]
        return sorted(scored, key=lambda item: (item[1], item[0]))

    @staticmethod
    def _safest(
        board: Board, candidates: Sequence[Cell], risks: dict[str, float]
    ) -> tuple[Cell, float] | None:
        scored = [
            (cell, risks[format_cell(cell)])
            for cell in candidates
            if format_cell(cell) in risks and board.get(cell) == HIDDEN
        ]
        if not scored:
            return None
        return min(scored, key=lambda item: (item[1], item[0]))

    @staticmethod
    def _riskiest(candidates: Sequence[Cell], risks: dict[str, float]) -> tuple[Cell, float] | None:
        scored = [
            (cell, risks[format_cell(cell)])
            for cell in candidates
            if format_cell(cell) in risks
        ]
        if not scored:
            return None
        return max(scored, key=lambda item: (item[1], item[0]))


def risk_table(board: Board, candidates: Sequence[Cell]) -> list[tuple[str, float]]:
    """Code-level risk ranking, handy for debugging and for the dry-run output."""
    return [(format_cell(cell), round(risk, 3)) for cell, risk in rank_guesses(board, candidates)]
