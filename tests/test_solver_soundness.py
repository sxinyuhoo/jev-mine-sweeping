"""Soundness property test: every "proved" deduction must match the hidden truth.

The solver drives a real simulated game and each deduction is checked against the
mine layout the player cannot see. This is what caught the mine-budget bug where
a pass's newly proved mines were not subtracted from the remaining budget, making
the global rule claim safe cells as mines.
"""

from __future__ import annotations

from jev_mine_sweeping.game.local_game import LocalGame
from jev_mine_sweeping.game.solver import solve


def test_every_deduction_agrees_with_the_hidden_truth():
    seeds = range(1, 61)
    stalls = 0
    for seed in seeds:
        game = LocalGame(9, 9, 10, seed=seed)
        game.open((4, 4))
        for _ in range(300):
            if game.status != "in_progress":
                break
            board = game.observed
            deduction = solve(board)
            assert all(cell not in game.mine_cells for cell in deduction.safe), (
                f"seed {seed}: solver called a mine safe: "
                f"{[c for c in deduction.safe if c in game.mine_cells]}"
            )
            assert all(cell in game.mine_cells for cell in deduction.mines), (
                f"seed {seed}: solver called a safe cell a mine: "
                f"{[c for c in deduction.mines if c not in game.mine_cells]}"
            )
            if deduction.mines:
                game.toggle_flag(deduction.mines[0])
                continue
            if deduction.safe:
                game.open(deduction.safe[0])
                continue
            stalls += 1
            break
        assert game.observed.flags_used <= 10, f"seed {seed}: over-flagged"
    assert stalls > 0, "some seeds must require a guess, otherwise the test proves nothing"


def test_most_boards_are_solvable_without_a_single_guess():
    """Documents the property the architecture leans on.

    Measured 159/200 (79.5%) on seeds 1..200 when this test was written; the band
    below is deliberately loose so the assertion is about the order of magnitude,
    not about a frozen number.
    """
    seeds = range(1, 101)
    solved_without_guessing = 0
    for seed in seeds:
        game = LocalGame(9, 9, 10, seed=seed)
        game.open((4, 4))
        for _ in range(300):
            if game.status != "in_progress":
                break
            deduction = solve(game.observed)
            if deduction.mines:
                game.toggle_flag(deduction.mines[0])
            elif deduction.safe:
                game.open(deduction.safe[0])
            else:
                break  # a guess would be needed here
        if game.status == "won":
            solved_without_guessing += 1
    ratio = solved_without_guessing / len(list(seeds))
    assert 0.6 <= ratio <= 0.95, f"no-guess solve rate moved: {ratio:.2f}"
