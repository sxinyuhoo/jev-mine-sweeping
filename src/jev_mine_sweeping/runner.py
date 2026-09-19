"""Episode runner and run logging.

:class:`SimRunner` plays the offline :class:`LocalGame`, which is how the
pipeline (and the solver) gets exercised at scale without a GUI or an API bill.
The HTML game drives the same agent through ``jev_mine_sweeping.web``.

Every decision is appended to a JSONL file, so a run can be replayed, scored
and audited after the fact.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .agent.jev_agent import Decision, JevMinesweeperAgent
from .config import Config
from .game.board import Board, format_cell
from .game.local_game import LocalGame
from .game.solver import best_guess, solve
from .jev.client import JevClient


@dataclass
class EpisodeResult:
    episode: int
    status: str
    steps: int
    api_calls: int
    solver_moves: int
    uncertain_moves: int
    overrides: int
    fallbacks: int
    duration_s: float
    stalled: bool = False
    log_path: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def won(self) -> bool:
        return self.status == "won"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class RunLogger:
    """Append-only JSONL log for one run (one file per process invocation)."""

    def __init__(self, directory: Path, prefix: str = "run") -> None:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"{prefix}-{stamp}.jsonl"
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, record: dict[str, Any]) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "RunLogger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def summarize(results: Sequence[EpisodeResult]) -> dict[str, Any]:
    total = len(results)
    wins = sum(1 for r in results if r.won)
    losses = sum(1 for r in results if r.status == "lost")
    other = total - wins - losses
    return {
        "episodes": total,
        "wins": wins,
        "losses": losses,
        "unfinished": other,
        "win_rate": round(wins / total, 4) if total else 0.0,
        "avg_steps": round(sum(r.steps for r in results) / total, 2) if total else 0.0,
        "api_calls": sum(r.api_calls for r in results),
        "solver_moves": sum(r.solver_moves for r in results),
        "uncertain_moves": sum(r.uncertain_moves for r in results),
        "overrides": sum(r.overrides for r in results),
        "fallbacks": sum(r.fallbacks for r in results),
        "avg_seconds": round(sum(r.duration_s for r in results) / total, 2) if total else 0.0,
    }


# --------------------------------------------------------------------------- sim
class SimRunner:
    """Offline episodes against :class:`LocalGame`.

    ``client=None`` runs the code-only baseline (solver plus the risk-estimate
    fallback), which is the honest yardstick for whatever Jev adds.
    """

    def __init__(self, cfg: Config, client: JevClient | None = None, logger: RunLogger | None = None) -> None:
        self.cfg = cfg
        self.logger = logger
        self.agent = (
            JevMinesweeperAgent(client, cfg.agent, cfg.jev) if client is not None else None
        )

    def play_episode(self, episode: int, seed: int | None = None) -> EpisodeResult:
        game = LocalGame(
            self.cfg.game.rows, self.cfg.game.cols, self.cfg.game.mines, seed=seed
        )
        started = time.perf_counter()
        calls_before = self.agent.api_calls if self.agent else 0
        history: list[dict[str, Any]] = []
        steps = solver_moves = uncertain = overrides = fallbacks = 0
        notes: list[str] = []

        while game.status == "in_progress" and steps < self.cfg.agent.max_steps:
            board = game.observed
            decision = self._decide(board, history, steps)
            if decision is None:
                break
            steps += 1
            if decision.source == "solver":
                solver_moves += 1
            else:
                uncertain += 1
            overrides += decision.source == "override"
            fallbacks += decision.source == "fallback"

            if decision.action == "open":
                result = game.open(decision.cell)
                outcome = (
                    "hit a mine"
                    if result.hit_mine
                    else f"revealed {len(result.revealed)} cell(s)"
                )
            else:
                game.toggle_flag(decision.cell)
                outcome = "flag toggled"

            history.append(
                {
                    "step": steps,
                    "action": decision.action,
                    "cell": format_cell(decision.cell),
                    "source": decision.source,
                    "result": outcome,
                }
            )
            self._log(episode, steps, board, decision, outcome)

        duration = time.perf_counter() - started
        status = game.status
        if status == "in_progress":
            status = "blocked"  # the agent could not find a legal move
        result = EpisodeResult(
            episode=episode,
            status=status,
            steps=steps,
            api_calls=(self.agent.api_calls - calls_before) if self.agent else 0,
            solver_moves=solver_moves,
            uncertain_moves=uncertain,
            overrides=overrides,
            fallbacks=fallbacks,
            duration_s=duration,
            log_path=str(self.logger.path) if self.logger else None,
            notes=notes,
        )
        if self.logger:
            self.logger.write({"event": "episode_end", **result.as_dict(), "seed": seed})
        return result

    def _decide(self, board: Board, history: Sequence[dict[str, Any]], step: int) -> Decision | None:
        if self.agent is not None:
            return self.agent.decide(board, history=history, step=step)
        # code-only baseline
        deduction = solve(board, global_rule=self.cfg.agent.solver_global_rule)
        if deduction.safe:
            return Decision("open", deduction.safe[0], "solver", note="proved safe")
        if deduction.mines:
            return Decision("flag", deduction.mines[0], "solver", note="proved a mine")
        guess = best_guess(board)
        if guess is None:
            return None
        return Decision("open", guess[0], "fallback", risk=guess[1], note="code risk estimate")

    def _log(
        self, episode: int, step: int, board: Board, decision: Decision, outcome: str
    ) -> None:
        if not self.logger:
            return
        record = {
            "event": "decision",
            "episode": episode,
            "step": step,
            "board": board.to_symbol_rows(),
            "decision": decision.as_record(),
            "outcome": outcome,
            "solver": (
                {
                    "safe": [format_cell(c) for c in decision.solver.safe],
                    "mines": [format_cell(c) for c in decision.solver.mines],
                    "constraints": len(decision.solver.constraints),
                }
                if decision.solver
                else None
            ),
        }
        if decision.state is not None:
            record["state"] = decision.state
        if decision.questions is not None:
            record["questions"] = decision.questions
        if decision.response is not None:
            record["jev"] = {
                "model": decision.response.model,
                "usage": decision.response.usage,
                "answers": decision.response.answers,
            }
        self.logger.write(record)
