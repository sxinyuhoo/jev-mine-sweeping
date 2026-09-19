"""End-to-end offline pipeline test: LocalGame -> solver -> Jev client -> move.

Runs the same code path ``jev-ms sim`` uses, with the mock backend, so the whole
loop is covered without a GUI and without an API key.
"""

from __future__ import annotations

from jev_mine_sweeping.config import AgentConfig, Config, GameConfig, JevConfig, RunConfig
from jev_mine_sweeping.jev.client import MockJevClient
from jev_mine_sweeping.runner import SimRunner, summarize


def make_config(**agent_overrides) -> Config:
    return Config(
        jev=JevConfig(backend="mock"),
        game=GameConfig(rows=9, cols=9, mines=10),
        agent=AgentConfig(max_steps=200, **agent_overrides),
        run=RunConfig(episodes=1),
    )


def test_solver_only_baseline_plays_real_games():
    runner = SimRunner(make_config(), client=None)
    results = [runner.play_episode(i, seed=i) for i in range(1, 26)]
    summary = summarize(results)
    assert summary["episodes"] == 25
    assert summary["wins"] >= 1, summary
    assert all(r.steps > 0 for r in results)
    assert summary["api_calls"] == 0


def test_mock_jev_pipeline_produces_decisions_and_finishes_games():
    """The mock is a weak heuristic stand-in, not a model.

    Pure-Jev play against it loses most 9x9 games (measured ~8% with the atomic
    composition, ~4% with the old one), so this asserts the *pipeline* — games
    finish, every move went through the client — not playing strength. Skill is
    what ``sim --backend http`` measures.
    """
    runner = SimRunner(make_config(), client=MockJevClient())
    results = [runner.play_episode(i, seed=i) for i in range(1, 26)]
    summary = summarize(results)
    assert summary["api_calls"] > 0, "the mock backend must be exercised"
    assert summary["uncertain_moves"] > 0
    assert summary["wins"] + summary["losses"] + summary["unfinished"] == 25
    assert all(r.steps > 0 for r in results)


def test_solver_first_mode_still_wins_with_the_mock():
    """The deterministic layer is intact: with the solver allowed to act, it wins."""
    runner = SimRunner(make_config(solver_mode="prefilter"), client=MockJevClient())
    results = [runner.play_episode(i, seed=i) for i in range(1, 26)]
    summary = summarize(results)
    assert summary["wins"] >= 15, summary
    assert summary["solver_moves"] > 0


def test_solver_mode_off_still_finishes_games():
    runner = SimRunner(make_config(solver_mode="off"), client=MockJevClient())
    results = [runner.play_episode(i, seed=i) for i in range(1, 11)]
    assert all(r.status in ("won", "lost") for r in results)
    assert all(r.solver_moves == 0 for r in results)


def test_run_logger_writes_one_record_per_decision(tmp_path):
    from jev_mine_sweeping.runner import RunLogger

    cfg = make_config()
    cfg.run.log_dir = str(tmp_path)
    with RunLogger(tmp_path, prefix="test") as logger:
        runner = SimRunner(cfg, client=MockJevClient(), logger=logger)
        result = runner.play_episode(1, seed=4)
        assert result.log_path == str(logger.path)
    lines = logger.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2  # at least one decision plus the episode_end record
    assert '"event": "decision"' in lines[0]
    assert '"event": "episode_end"' in lines[-1]
