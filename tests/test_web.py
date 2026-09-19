"""Web mode tests: the /decide contract and the HTTP layer.

The browser owns the environment, so these tests post a board the way the page
does and check the decision payload the UI depends on — including the pieces the
visualisation needs (option probabilities, per-cell Noul answers, danger score).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from jev_mine_sweeping.config import AgentConfig, Config, GameConfig, JevConfig, RunConfig
from jev_mine_sweeping.game.board import FLAG, HIDDEN
from jev_mine_sweeping.jev.client import MockJevClient
from jev_mine_sweeping.web.server import JevWebApp, build_handler


def make_app(**agent_overrides) -> JevWebApp:
    cfg = Config(
        jev=JevConfig(backend="mock"),
        game=GameConfig(rows=9, cols=9, mines=10),
        agent=AgentConfig(max_steps=200, **agent_overrides),
        run=RunConfig(),
    )
    return JevWebApp(cfg, MockJevClient())


def empty_board_payload(rows=9, cols=9, mines=10, **extra):
    payload = {
        "rows": rows,
        "cols": cols,
        "mines": mines,
        "cells": [[HIDDEN] * cols for _ in range(rows)],
        "moves": 0,
        "status": "in_progress",
        "step": 0,
        "history": [],
    }
    payload.update(extra)
    return payload


JEV_SOURCES = ("jev", "noul", "noul_flag", "noul_guess", "fallback", "override")


def test_decide_returns_a_legal_action_with_visualisation_data():
    app = make_app()
    response = app.decide(empty_board_payload())

    assert response["source"] in JEV_SOURCES
    assert response["action"] in ("open", "flag")
    row, col = response["cell"]
    assert 0 <= row < 9 and 0 <= col < 9
    assert response["cell_name"] == f"r{row}c{col}"
    assert response["model"] == "mock-heuristic"
    assert response["backend"] == "mock"
    assert response["decision_mode"] == "atomic"

    # everything the page draws
    assert response["options"], "the option list drives the probability panel"
    assert all(
        {"option", "probability", "chosen", "jev_risk", "status"} <= set(o)
        for o in response["options"]
    )
    assert sum(1 for o in response["options"] if o["chosen"]) <= 1
    assert response["nouls"], "per-cell mine probabilities drive the heat overlay"
    assert response["tokens"]["state"] > 0 and response["tokens"]["questions"] > 0
    assert response["candidates"] > 0
    assert len(response["board_rows"]) == 9
    assert "solver_view" in response
    assert "solved" in response["solver_view"], "the page picks its own wording from this flag"
    assert response["action_space"], "the shortlist Jev was offered"
    assert "proved_safe_cells" in response["derived_facts"]


def test_default_mode_asks_jev_even_when_a_move_is_provable():
    """The whole point of the web mode: the model decides, not the constraint solver."""
    app = make_app()  # solver_mode="off" by default
    cells = [[HIDDEN] * 9 for _ in range(9)]
    cells[4][4] = 1
    cells[4][3] = FLAG  # the "1" is satisfied -> everything else around it is provably safe
    payload = empty_board_payload(cells=cells, moves=5)
    response = app.decide(payload)
    assert response["source"] in JEV_SOURCES
    assert response["source"] != "solver", "the default mode never lets code take the move"
    assert response["solver_view"]["proved_safe"], "the solver *can* prove moves here"
    assert response["solver_view"]["solved"] is True, "the UI needs the boolean, not the prose"
    assert response["solver_view"]["note"].startswith("the constraint rules can prove")
    assert "proved_safe_cells" in response["derived_facts"]


def test_guarded_mode_lets_the_solver_take_the_move():
    app = make_app(solver_mode="prefilter")
    cells = [[HIDDEN] * 9 for _ in range(9)]
    cells[4][4] = 1
    cells[4][3] = FLAG
    response = app.decide(empty_board_payload(cells=cells, moves=5))
    assert response["source"] == "solver"
    assert response["action"] == "open"


def test_finished_board_gets_no_decision():
    app = make_app()
    response = app.decide(empty_board_payload(status="won"))
    assert response["action"] is None
    assert "won" in response["note"]


def test_wrong_board_shape_is_rejected():
    app = make_app()
    payload = empty_board_payload()
    payload["cells"] = payload["cells"][:3]
    with pytest.raises(ValueError):
        app.decide(payload)


def test_stats_accumulate_across_decisions():
    app = make_app()
    for _ in range(3):
        app.decide(empty_board_payload())
    stats = app.stats.as_dict()
    assert stats["decisions"] == 3
    assert sum(stats["by_source"].values()) == 3
    assert stats["avg_latency_ms"] >= 0


# ------------------------------------------------------------------ http layer
@pytest.fixture()
def live_server():
    app = make_app()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(app))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    try:
        yield f"http://{host}:{port}", app
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_layer_serves_the_page_and_answers_decide(live_server):
    base, _app = live_server

    page = urllib.request.urlopen(f"{base}/", timeout=10).read().decode("utf-8")
    assert "Jev 扫雷" in page and "/app.js" in page

    script = urllib.request.urlopen(f"{base}/app.js", timeout=10).read().decode("utf-8")
    assert "askJev" in script

    cfg = json.loads(urllib.request.urlopen(f"{base}/config", timeout=10).read())
    assert cfg["backend"] == "mock"
    assert cfg["board"] == {"rows": 9, "cols": 9, "mines": 10}

    request = urllib.request.Request(
        f"{base}/decide",
        data=json.dumps(empty_board_payload()).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    response = json.loads(urllib.request.urlopen(request, timeout=30).read())
    assert response["action"] in ("open", "flag")
    assert response["stats"]["decisions"] == 1

    stats = json.loads(urllib.request.urlopen(f"{base}/stats", timeout=10).read())
    assert stats["decisions"] == 1


def test_http_layer_rejects_a_bad_body(live_server):
    base, _app = live_server
    request = urllib.request.Request(
        f"{base}/decide",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    assert excinfo.value.code == 400
