"""Command line interface.

    jev-ms jev-check     verify the API key / connectivity with one tiny request
    jev-ms state-demo    print the exact JSON that would be sent to Jev (no key needed)
    jev-ms serve         serve the HTML game whose 自动操作 asks Jev every move
    jev-ms sim           play offline episodes (LocalGame) and report win rate

``python -m jev_mine_sweeping ...`` works the same way.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from . import __version__
from .config import Config, describe, difficulty, load_config
from .agent.jev_agent import risk_table
from .game.board import format_cell
from .game.local_game import LocalGame
from .game.solver import best_guess, solve
from .game.state_builder import build_state, estimate_tokens, select_candidates
from .jev.client import JevError, build_client
from .jev.questions import build_decision_questions, parse_action_id, select_focus
from .runner import RunLogger, SimRunner, summarize

# --------------------------------------------------------------------------- utils


def _print_json(payload: Any, title: str | None = None) -> None:
    if title:
        print(f"--- {title} ---")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _config_path(args: argparse.Namespace) -> str | None:
    """``--config`` is optional and may be suppressed by the shared-option parser."""
    return getattr(args, "config", None)


def _apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    if getattr(args, "backend", None):
        cfg.jev.backend = args.backend
    if getattr(args, "model", None):
        cfg.jev.model = args.model
    if getattr(args, "log_dir", None):
        cfg.run.log_dir = args.log_dir
    if getattr(args, "decision_mode", None):
        cfg.agent.decision_mode = args.decision_mode
    if getattr(args, "focus", None):
        cfg.agent.focus_cells = args.focus
    if getattr(args, "pure_choice", False):
        # The pre-optimisation behaviour: one big Choice, played verbatim.
        cfg.agent.decision_mode = "choice"
        cfg.agent.safety_override = False
        cfg.agent.confidence_floor = 0.0
    if getattr(args, "guarded", False):
        # The "let code help" mode: constraint solver first, plus the cross-checks.
        cfg.agent.solver_mode = "prefilter"
        cfg.agent.safety_override = True
        cfg.agent.confidence_floor = 0.4
    return cfg


def _make_logger(cfg: Config, args: argparse.Namespace, prefix: str) -> RunLogger | None:
    if getattr(args, "no_log", False):
        return None
    return RunLogger(cfg.log_dir(), prefix=prefix)


def _resolve_board_size(args: argparse.Namespace, cfg: Config) -> Config:
    if getattr(args, "difficulty", None):
        rows, cols, mines = difficulty(args.difficulty)
        cfg.game.rows, cfg.game.cols, cfg.game.mines = rows, cols, mines
    if getattr(args, "grid", None):
        text = args.grid.lower().replace("×", "x")
        rows, _, cols = text.partition("x")
        cfg.game.rows = int(rows)
        cfg.game.cols = int(cols or rows)
    if getattr(args, "mines", None):
        cfg.game.mines = args.mines
    return cfg


# ------------------------------------------------------------------------ commands


def cmd_jev_check(args: argparse.Namespace) -> int:
    cfg = _apply_overrides(load_config(_config_path(args)), args)
    print(describe(cfg))
    if cfg.jev.backend != "mock" and not cfg.jev.has_api_key:
        print(
            "\nNo usable API key yet. Fill `jev.api_key` in config/config.yaml "
            "or export TYPESAFE_API_KEY, then re-run."
        )
        return 2
    client = build_client(cfg.jev)
    state = "This is a connectivity check for the jev-mine-sweeping project."
    questions = {
        "is_test": {
            "type": "noul",
            "instructions": "Does `state` say that this is a connectivity check?",
            "criteria": {
                "true": "The text mentions a connectivity check",
                "false": "The text does not mention a connectivity check",
            },
        }
    }
    try:
        response = client.ask(state, questions)
    except JevError as exc:
        print(f"\nFAILED: {exc}")
        return 1
    print(f"\nOK  backend={getattr(client, 'name', '?')}  model={response.model}  "
          f"latency={response.latency_ms:.0f}ms  usage={response.usage}")
    _print_json(response.answers, "answers")
    return 0


def _demo_board(cfg: Config, seed: int, warmup: int) -> tuple[LocalGame, list[dict[str, Any]]]:
    """Play ``warmup`` moves to reach a realistic mid-game position.

    Advancement uses the constraint solver where it can, and the code risk
    estimate otherwise — this is only about *building a position* to show the
    payload, not about how the real agent decides (which is Jev, every move).
    """
    game = LocalGame(cfg.game.rows, cfg.game.cols, cfg.game.mines, seed=seed)
    history: list[dict[str, Any]] = []

    opening = (cfg.game.rows // 2, cfg.game.cols // 2)
    result = game.open(opening)
    history.append(
        {
            "step": 1,
            "action": "open",
            "cell": format_cell(opening),
            "source": "opening move",
            "result": f"revealed {len(result.revealed)} cell(s)",
        }
    )

    for _ in range(max(warmup - 1, 0)):
        if game.status != "in_progress":
            break
        board = game.observed
        deduction = solve(board, global_rule=cfg.agent.solver_global_rule)
        if deduction.mines:
            game.toggle_flag(deduction.mines[0])
            history.append(
                {
                    "step": len(history) + 1,
                    "action": "flag",
                    "cell": format_cell(deduction.mines[0]),
                    "source": "solver (proved a mine)",
                }
            )
            continue
        if deduction.safe:
            cell = deduction.safe[0]
            source = "solver (proved safe)"
        else:
            guess = best_guess(board)
            if guess is None:
                break
            cell, source = guess[0], "code risk estimate"
        result = game.open(cell)
        history.append(
            {
                "step": len(history) + 1,
                "action": "open",
                "cell": format_cell(cell),
                "source": source,
                "result": "hit a mine" if result.hit_mine else f"revealed {len(result.revealed)} cell(s)",
            }
        )
    return game, history


def cmd_state_demo(args: argparse.Namespace) -> int:
    cfg = _apply_overrides(load_config(_config_path(args)), args)
    cfg = _resolve_board_size(args, cfg)
    game, history = _demo_board(cfg, args.seed, args.warmup)
    board = game.observed
    candidates = select_candidates(board, cfg.agent.action_space)
    deduction = solve(board, global_rule=cfg.agent.solver_global_rule)
    # Atomic mode asks about a shortlist, not every candidate: the demo must show
    # what the agent would really send, not an idealised payload.
    atomic = cfg.agent.decision_mode != "choice"
    focus = select_focus(board, candidates, cfg.agent.focus_cells, deduction) if atomic else None
    action_space = focus if focus else candidates
    state = build_state(
        board,
        action_space,
        history=history,
        solver_result=deduction,
        solver_mode=cfg.agent.solver_mode,
        decision_mode=cfg.agent.decision_mode,
        max_frontier_entries=cfg.agent.max_frontier_entries,
    )
    questions = build_decision_questions(
        board,
        action_space,
        max_options=cfg.jev.max_choice_options,
        max_nouls=cfg.jev.max_noul_questions,
        ask_danger=cfg.jev.ask_danger_score,
        include_flag=board.mines_remaining > 0,
        focus=focus,
        deductions=deduction,
    )

    print(f"seed={args.seed}  warmup={args.warmup}  status={game.status}  "
          f"hidden={board.hidden_count}  flags={board.flags_used}  "
          f"frontier={len(candidates)}  decision_mode={cfg.agent.decision_mode}  "
          f"offered={len(action_space)}")
    print(f"estimated tokens: state={estimate_tokens(state)} "
          f"questions={estimate_tokens(questions)} (budget 32000)")
    if game.status != "in_progress":
        print(f"  ! the warmup finished this game ({game.status}) — use a bigger board "
              f"or a smaller --warmup to see a mid-game payload")
    print("\nboard as read:")
    print(board.to_debug_text())
    print("\ncode-level risk estimate for the candidate cells (safest first):")
    for name, risk in risk_table(board, candidates)[:12]:
        print(f"  {name}  p_mine={risk:.3f}")
    if deduction.safe or deduction.mines:
        print("\nconstraint rules already prove (handed to Jev as facts):")
        print(f"  proved safe : {[format_cell(c) for c in deduction.safe] or 'none'}")
        print(f"  proved mines: {[format_cell(c) for c in deduction.mines] or 'none'}")
    _print_json(state, "state sent to Jev")
    _print_json(questions, "questions sent to Jev")

    if args.dry_run_client:
        client = build_client(cfg.jev) if cfg.jev.backend != "http" or cfg.jev.has_api_key else None
        if client is None:
            print("\n(no API key; skipping the live call)")
            return 0
        response = client.ask(state, questions)
        _print_json(response.answers, f"live answers ({response.model}, {response.latency_ms:.0f}ms)")
        option, confidence, probabilities = response.choice("next_action")
        parsed = parse_action_id(option) if option else None
        print(f"\nChoice -> {option} (confidence {confidence:.2f})")
        if parsed:
            print(f"  parsed as: {parsed[0]} {format_cell(parsed[1])}")
        print(f"  probabilities: {json.dumps(probabilities, ensure_ascii=False)}")
        risks = response.noul_map("is_mine__")
        if risks:
            ranked = sorted(risks.items(), key=lambda item: item[1])
            print("  per-cell p(mine), safest first: "
                  + ", ".join(f"{name}={value:.2f}" for name, value in ranked[:8]))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = _apply_overrides(load_config(_config_path(args)), args)
    cfg = _resolve_board_size(args, cfg)
    if cfg.jev.backend != "mock":
        cfg = _apply_overrides(load_config(_config_path(args), require_key=True), args)
        cfg = _resolve_board_size(args, cfg)
    client = build_client(cfg.jev)
    print(describe(cfg))
    from .web.server import serve

    serve(cfg, client, host=args.host, port=args.port, open_browser=args.open)
    return 0


def cmd_sim(args: argparse.Namespace) -> int:
    cfg = _apply_overrides(load_config(_config_path(args)), args)
    cfg = _resolve_board_size(args, cfg)
    if not args.solver_only and cfg.jev.backend != "mock":
        cfg = _apply_overrides(load_config(_config_path(args), require_key=True), args)
        cfg = _resolve_board_size(args, cfg)
    if cfg.jev.backend in ("http", "sdk") and not args.solver_only and args.episodes > 20 and not args.yes:
        print(
            f"About to run {args.episodes} episodes against the paid API. "
            "Re-run with --yes to confirm, or use --backend mock / --solver-only."
        )
        return 2
    client = None if args.solver_only else build_client(cfg.jev)
    logger = _make_logger(cfg, args, prefix="sim")
    runner = SimRunner(cfg, client, logger)
    print(describe(cfg))
    print(f"mode: {'solver-only baseline' if client is None else f'Jev ({cfg.jev.backend})'}\n")
    results = []
    for index in range(1, args.episodes + 1):
        seed = None if args.seed is None else args.seed + index - 1
        result = runner.play_episode(index, seed=seed)
        results.append(result)
        print(
            f"  episode {index:>3}: {result.status:<10} steps={result.steps:<4} "
            f"solver={result.solver_moves:<4} uncertain={result.uncertain_moves:<3} "
            f"api={result.api_calls:<3} {result.duration_s:5.1f}s"
        )
    summary = summarize(results)
    print("\n--- summary ---")
    for key, value in summary.items():
        print(f"  {key:<16} {value}")
    if logger:
        print(f"  log              {logger.path}")
        logger.close()
    return 0


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    # Shared options, accepted both before and after the subcommand. SUPPRESS
    # keeps the subparser copy from overwriting a value the main parser already
    # read (the classic argparse parents= gotcha).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS,
                        help="path to config.yaml (default: config/config.yaml)")
    common.add_argument("--backend", choices=["http", "sdk", "mock"], default=argparse.SUPPRESS,
                        help="override jev.backend")
    common.add_argument("--model", default=argparse.SUPPRESS, help="override jev.model")
    common.add_argument("--log-dir", default=argparse.SUPPRESS, help="override run.log_dir")
    common.add_argument("--no-log", action="store_true", default=argparse.SUPPRESS,
                        help="do not write a JSONL run log")
    common.add_argument("--decision-mode", choices=["atomic", "choice"], default=argparse.SUPPRESS,
                        help="atomic (default): facts + focused questions, Jev's probabilities "
                             "rank the moves | choice: one big Choice, played verbatim")
    common.add_argument("--focus", type=int, default=argparse.SUPPRESS,
                        help="how many cells the Choice question offers (default 24)")
    common.add_argument("--pure-choice", action="store_true", default=argparse.SUPPRESS,
                        help="shorthand for --decision-mode choice --no-override")
    common.add_argument("--difficulty", default=argparse.SUPPRESS,
                        help="board preset: 困难/hard (16x16, 40) | 专家/expert (16x30, 99) | "
                             "简单/beginner (9x9, 10)")
    common.add_argument("--grid", default=argparse.SUPPRESS,
                        help="board size, e.g. 16x30 (overrides --difficulty)")
    common.add_argument("--mines", type=int, default=argparse.SUPPRESS,
                        help="mine count (overrides --difficulty)")

    parser = argparse.ArgumentParser(
        prog="jev-ms",
        description="Play Minesweeper with TypeSafe Jev: the HTML game, or offline episodes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"jev-mine-sweeping {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("jev-check", parents=[common], help="verify the API key with one tiny request")
    p.set_defaults(func=cmd_jev_check)

    p = sub.add_parser("state-demo", parents=[common],
                       help="print the state/questions sent to Jev (no key needed)")
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--warmup", type=int, default=12,
                   help="moves to play before printing the payload (builds a mid-game position)")
    p.add_argument("--live", dest="dry_run_client", action="store_true",
                   help="also make the live call and print the answers")
    p.set_defaults(func=cmd_state_demo)

    p = sub.add_parser("serve", parents=[common],
                       help="serve the HTML game whose 自动操作 asks Jev every move")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open the page in the default browser")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("sim", parents=[common], help="offline episodes against the local simulator")
    p.add_argument("--episodes", type=int, default=20)
    p.add_argument("--seed", type=int, default=None, help="first RNG seed; increments per episode")
    p.add_argument("--solver-only", action="store_true", help="code-only baseline, no Jev calls")
    p.add_argument("--yes", action="store_true", help="confirm a large paid run")
    p.add_argument("--guarded", action="store_true", default=argparse.SUPPRESS,
                   help="let code help: constraint solver first, plus the safety override")
    p.set_defaults(func=cmd_sim)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    except JevError as exc:
        print(f"Jev error: {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
