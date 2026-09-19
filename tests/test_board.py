"""Board model tests."""

from __future__ import annotations

import pytest

from jev_mine_sweeping.game.board import (
    FLAG,
    HIDDEN,
    MINE,
    Board,
    format_cell,
    parse_cell,
)


def test_cell_reference_roundtrip():
    assert format_cell((3, 4)) == "r3c4"
    assert parse_cell("r3c4") == (3, 4)
    assert parse_cell(" R12C0 ") == (12, 0)
    with pytest.raises(ValueError):
        parse_cell("3,4")


def test_neighbours_are_clipped_to_the_board():
    board = Board.empty(3, 3, 1)
    assert len(board.neighbours((1, 1))) == 8
    assert len(board.neighbours((0, 0))) == 3
    assert (0, 1) in board.neighbours((0, 0))
    assert (-1, 0) not in board.neighbours((0, 0))


def test_selectors_and_counters():
    board = Board.from_grid(
        [
            [HIDDEN, HIDDEN, HIDDEN],
            [HIDDEN, 1, FLAG],
            [0, 0, HIDDEN],
        ],
        total_mines=2,
    )
    assert len(board.hidden_cells()) == 5
    assert board.flagged_cells() == [(1, 2)]
    assert board.flags_used == 1
    assert board.mines_remaining == 1
    assert (1, 2) in board.neighbours((1, 1))


def test_frontier_excludes_blind_cells():
    board = Board.from_grid(
        [
            [HIDDEN, HIDDEN, HIDDEN],
            [HIDDEN, 1, HIDDEN],
            [0, 0, HIDDEN],
        ],
        total_mines=2,
    )
    frontier = set(board.frontier_cells())
    assert (0, 1) in frontier  # touches the 1
    assert (0, 2) in frontier
    assert (0, 0) in frontier
    # (2, 2) only touches revealed zeros? no: (1,1)=1 is a diagonal neighbour
    assert (1, 0) in frontier
    blind = set(board.unconstrained_hidden_cells())
    assert not (blind & frontier)


def test_local_counts():
    board = Board.from_grid(
        [
            [FLAG, HIDDEN, HIDDEN],
            [HIDDEN, 1, 0],
            [0, 0, 0],
        ],
        total_mines=1,
    )
    flagged, hidden, revealed = board.local_counts((1, 1))
    assert (flagged, hidden, revealed) == (1, 3, 4)


def test_symbol_rows_are_single_characters():
    board = Board.from_grid(
        [
            [HIDDEN, FLAG, MINE],
            [0, 5, 3],
        ],
        total_mines=1,
    )
    assert board.to_symbol_rows() == ["?F*", "053"]


def test_inconsistencies_catch_impossible_readings():
    clean = Board.from_grid([[0, 1, HIDDEN], [0, 1, HIDDEN], [0, 0, 0]], total_mines=1)
    assert clean.inconsistencies() == []

    over_flagged = Board.from_grid(
        [
            [0, FLAG, 1],
            [0, FLAG, 0],
            [0, 0, 0],
        ],
        total_mines=2,
    )
    problems = over_flagged.inconsistencies()
    assert any("shows 1 but 2 flagged" in p for p in problems)

    impossible = Board.from_grid([[1, 0, 0], [0, 0, 0], [0, 0, 0]], total_mines=1)
    assert any("has no hidden neighbour" in p for p in impossible.inconsistencies())


def test_inconsistencies_flag_a_flag_budget_overrun():
    board = Board.from_grid(
        [
            [FLAG, FLAG, FLAG],
            [0, 0, 0],
            [0, 0, 0],
        ],
        total_mines=2,
    )
    assert any("only 2 mines exist" in p for p in board.inconsistencies())


def test_copy_is_independent():
    board = Board.from_grid([[HIDDEN, HIDDEN], [HIDDEN, 1]], total_mines=1)
    clone = board.copy()
    clone.set((0, 0), FLAG)
    assert board.get((0, 0)) == HIDDEN
