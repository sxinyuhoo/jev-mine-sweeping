"""Deterministic solver tests — every assertion here is a proved deduction."""

from __future__ import annotations

from jev_mine_sweeping.game.board import FLAG, HIDDEN, Board
from jev_mine_sweeping.game.solver import (
    best_guess,
    build_constraints,
    global_mine_density,
    rank_guesses,
    solve,
)


def test_single_point_safe_when_flags_satisfy_the_number():
    board = Board.from_grid(
        [
            [FLAG, HIDDEN, HIDDEN],
            [HIDDEN, 1, HIDDEN],
            [HIDDEN, HIDDEN, HIDDEN],
        ],
        total_mines=1,
    )
    deduction = solve(board, global_rule="off")
    assert deduction.safe
    assert set(deduction.safe) == set(board.hidden_cells())
    assert deduction.mines == []


def test_single_point_mine_when_hidden_neighbours_match_the_number():
    board = Board.from_grid(
        [
            [0, 0, 0],
            [0, 1, HIDDEN],
            [0, 0, 0],
        ],
        total_mines=1,
    )
    deduction = solve(board, global_rule="off")
    assert deduction.mines == [(1, 2)]
    assert deduction.safe == []


def test_subset_rule_solves_a_one_two_one_pattern():
    # row1: 1 2 1 facing a hidden row above; the "1" on the left is a subset of
    # the "2", so the third cell above the "2" must be a mine.
    board = Board.from_grid(
        [
            [HIDDEN, HIDDEN, HIDDEN, HIDDEN],
            [1, 2, 1, HIDDEN],
        ],
        total_mines=2,
    )
    deduction = solve(board, global_rule="off")
    assert set(deduction.mines) == {(0, 0), (0, 2)}
    assert set(deduction.safe) == {(0, 1), (0, 3), (1, 3)}
    assert any("outside" in note for note in deduction.notes)


def test_global_rule_clears_everything_once_the_mines_are_flagged():
    # No revealed number at all, so only the mine counter can say anything.
    board = Board.from_grid(
        [
            [FLAG, FLAG, HIDDEN, HIDDEN],
            [HIDDEN, HIDDEN, HIDDEN, HIDDEN],
            [HIDDEN, HIDDEN, HIDDEN, HIDDEN],
        ],
        total_mines=2,
    )
    deduction = solve(board)
    assert set(deduction.safe) == {(0, 2), (0, 3), (1, 0), (1, 1), (1, 2), (1, 3),
                                   (2, 0), (2, 1), (2, 2), (2, 3)}
    assert deduction.mines == []


def test_global_rule_can_be_disabled():
    board = Board.from_grid(
        [
            [FLAG, FLAG, HIDDEN, HIDDEN],
            [HIDDEN, HIDDEN, HIDDEN, HIDDEN],
            [HIDDEN, HIDDEN, HIDDEN, HIDDEN],
        ],
        total_mines=2,
    )
    deduction = solve(board, global_rule="off")
    assert deduction.safe == []
    assert deduction.mines == []


def test_safe_only_global_rule_never_claims_mines():
    board = Board.from_grid(
        [
            [HIDDEN, HIDDEN, HIDDEN],
            [HIDDEN, HIDDEN, HIDDEN],
            [HIDDEN, HIDDEN, HIDDEN],
        ],
        total_mines=9,
    )
    assert solve(board, global_rule="full").mines
    assert solve(board, global_rule="safe_only").mines == []
    assert solve(board, global_rule="off").mines == []


def test_solve_does_not_mutate_the_input():
    board = Board.from_grid(
        [
            [0, 0, 0],
            [0, 1, HIDDEN],
            [0, 0, 0],
        ],
        total_mines=1,
    )
    before = board.to_symbol_rows()
    solve(board)
    assert board.to_symbol_rows() == before


def test_constraints_carry_the_remaining_mine_budget():
    board = Board.from_grid(
        [
            [FLAG, HIDDEN],
            [1, HIDDEN],
        ],
        total_mines=1,
    )
    constraints = build_constraints(board)
    assert len(constraints) == 1
    assert constraints[0].required == 0
    assert constraints[0].cells == frozenset({(0, 1), (1, 1)})


def test_rank_guesses_prefers_the_less_dense_cell():
    board = Board.from_grid(
        [
            [HIDDEN, HIDDEN, HIDDEN, HIDDEN],
            [1, 1, 1, 0],
            [0, 0, 0, 0],
        ],
        total_mines=3,
    )
    ranked = rank_guesses(board, board.hidden_cells())
    # (0, 3) is touched by the "0" only, so it is the safest cell on the board.
    assert ranked[0][0] == (0, 3)


def test_best_guess_opens_the_centre_on_an_empty_board():
    board = Board.empty(9, 9, 10)
    cell, risk = best_guess(board)
    assert cell == (4, 4)
    assert 0 < risk < 1
    assert global_mine_density(board) == 10 / 81
