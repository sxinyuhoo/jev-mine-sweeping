"""Local simulator tests (the offline stand-in for the desktop app)."""

from __future__ import annotations

from jev_mine_sweeping.game.board import FLAG, HIDDEN, MINE
from jev_mine_sweeping.game.local_game import LocalGame


def test_first_click_is_always_safe_and_reveals_something():
    for seed in range(25):
        game = LocalGame(9, 9, 10, seed=seed)
        result = game.open((4, 4))
        assert not result.hit_mine
        assert result.revealed
        assert game.status == "in_progress"


def test_flood_fill_opens_the_whole_zero_region():
    game = LocalGame(9, 9, 10, seed=3)
    game.open((4, 4))
    board = game.observed
    for cell in board.revealed_cells():
        if board.get(cell) == 0:
            for neighbour in board.neighbours(cell):
                assert board.get(neighbour) != HIDDEN


def test_hitting_a_mine_loses_the_game():
    game = LocalGame(9, 9, 10, seed=11)
    game.open((4, 4))
    mine = next(iter(game.mine_cells))
    result = game.open(mine)
    assert result.hit_mine
    assert game.status == "lost"
    assert game.observed.status == "lost"


def test_flag_toggles_and_updates_the_counter():
    game = LocalGame(9, 9, 10, seed=5)
    assert game.observed.mines_remaining == 10
    assert game.toggle_flag((0, 0))
    assert game.observed.get((0, 0)) == FLAG
    assert game.observed.mines_remaining == 9
    assert game.toggle_flag((0, 0))
    assert game.observed.get((0, 0)) == HIDDEN
    assert game.observed.mines_remaining == 10


def test_winning_when_every_cell_is_resolved():
    game = LocalGame(5, 5, 3, seed=2)
    game.open((2, 2))
    # Reveal every safe cell directly through the truth grid.
    for r in range(5):
        for c in range(5):
            if (r, c) not in game.mine_cells:
                game.open((r, c))
    assert game.status == "won"


def test_debug_text_marks_mines_in_the_truth_grid():
    game = LocalGame(4, 4, 2, seed=1)
    game.open((0, 0))
    assert "*" in game.debug_text()
    assert MINE in game.truth[0] or any(MINE in row for row in game.truth)
