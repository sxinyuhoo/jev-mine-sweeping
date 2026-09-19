"""Local HTTP server: serves the game page and answers ``POST /decide`` with Jev.

Standard library only (``http.server``) — the API surface is three endpoints, so
adding a web framework would buy nothing:

===========================  ==================================================
``GET  /``                   the game page (``web/index.html``)
``GET  /app.js`` ``/style.css``  static assets
``POST /decide``             one board -> one decision (+ visualisation payload)
``GET  /stats``              cumulative counters for the current session
===========================  ==================================================
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..config import Config, PROJECT_ROOT
from ..game.board import Board, format_cell
from ..game.solver import local_mine_density, solve
from ..game.state_builder import estimate_tokens
from ..jev.client import JevClient, JevError
from ..agent.jev_agent import JevMinesweeperAgent


def _find_web_root() -> Path:
    """Locate the page assets (``web/index.html`` next to the checkout).

    They live at ``<repo>/web/``, which is found for an editable install (the
    documented setup) and when running from a checkout. A non-editable
    ``pip install .`` copies only the package into site-packages, so the assets
    would be missing — :func:`serve` says so explicitly instead of 404-ing.
    """
    for candidate in (PROJECT_ROOT / "web", Path(__file__).resolve().parent / "static"):
        if (candidate / "index.html").is_file():
            return candidate
    return PROJECT_ROOT / "web"


WEB_ROOT = _find_web_root()
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


@dataclass
class SessionStats:
    """Cumulative counters, shown in the page's status bar."""

    decisions: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    by_source: dict[str, int] = field(default_factory=dict)

    def record(self, decision: dict[str, Any]) -> None:
        self.decisions += 1
        usage = decision.get("usage") or {}
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)
        self.latency_ms += float(decision.get("latency_ms") or 0.0)
        source = decision.get("source") or "?"
        self.by_source[source] = self.by_source.get(source, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "decisions": self.decisions,
            "errors": self.errors,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "avg_latency_ms": round(self.latency_ms / self.decisions, 1) if self.decisions else 0.0,
            "by_source": self.by_source,
        }


class JevWebApp:
    """Turns a posted board into a decision plus a visualisation payload."""

    def __init__(self, cfg: Config, client: JevClient) -> None:
        self.cfg = cfg
        self.client = client
        self.agent = JevMinesweeperAgent(client, cfg.agent, cfg.jev)
        self.stats = SessionStats()
        self.lock = threading.Lock()
        self.last_payload: dict[str, Any] | None = None
        self.last_response: dict[str, Any] | None = None

    # ------------------------------------------------------------------ helpers
    def backend_name(self) -> str:
        return getattr(self.client, "name", self.cfg.jev.backend)

    def board_from_payload(self, payload: dict[str, Any]) -> Board:
        rows = int(payload.get("rows") or self.cfg.game.rows)
        cols = int(payload.get("cols") or self.cfg.game.cols)
        mines = int(payload.get("mines") or self.cfg.game.mines)
        cells = payload.get("cells") or []
        if len(cells) != rows or any(len(row) != cols for row in cells):
            raise ValueError(
                f"cells must be {rows}x{cols}, got "
                f"{len(cells)}x{len(cells[0]) if cells else 0}"
            )
        board = Board(
            rows=rows,
            cols=cols,
            cells=[[int(v) for v in row] for row in cells],
            total_mines=mines,
            moves=int(payload.get("moves") or 0),
            status=str(payload.get("status") or "in_progress"),
        )
        return board

    @staticmethod
    def _option_view(
        board: Board,
        answer: dict[str, Any],
        nouls: dict[str, float],
        *,
        statuses: dict[str, str] | None = None,
        limit: int = 14,
    ):
        """Ranked option list for the UI: probability, mine risk, code estimate."""
        from ..jev.questions import parse_action_id

        statuses = statuses or {}
        probabilities = {
            key: float(value) for key, value in (answer.get("probabilities") or {}).items()
        }
        chosen = answer.get("choice")
        ranked = sorted(probabilities.items(), key=lambda item: -item[1])[:limit]
        rows = []
        for option, probability in ranked:
            parsed = parse_action_id(option)
            if parsed is None:
                continue
            action, cell = parsed
            name = format_cell(cell)
            code_risk = local_mine_density(board, cell)
            rows.append(
                {
                    "option": option,
                    "action": action,
                    "cell": name,
                    "status": statuses.get(name, "unknown"),
                    "probability": round(probability, 4),
                    "chosen": option == chosen,
                    "jev_risk": None if name not in nouls else round(nouls[name], 4),
                    "code_risk": None if code_risk is None else round(code_risk, 4),
                }
            )
        return rows

    # ------------------------------------------------------------------- decide
    def decide(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            board = self.board_from_payload(payload)
            history = list(payload.get("history") or [])[-6:]
            step = payload.get("step")

            if board.status != "in_progress" or not board.hidden_cells():
                return {
                    "action": None,
                    "source": "none",
                    "note": f"game is {board.status}",
                    "stats": self.stats.as_dict(),
                }

            try:
                decision = self.agent.decide(board, history=history, step=step)
            except JevError as exc:
                self.stats.errors += 1
                return {
                    "action": None,
                    "source": "error",
                    "note": f"Jev error: {exc}",
                    "stats": self.stats.as_dict(),
                }
            if decision is None:
                return {
                    "action": None,
                    "source": "none",
                    "note": "no decision available",
                    "stats": self.stats.as_dict(),
                }

            answers = decision.response.answers if decision.response else {}
            nouls = dict(decision.risks)
            state = decision.state or {}
            statuses = {
                entry["cell"]: entry.get("status", "unknown")
                for entry in state.get("candidate_cells", [])
            }
            response: dict[str, Any] = {
                "action": decision.action,
                "cell": list(decision.cell),
                "cell_name": format_cell(decision.cell),
                "source": decision.source,
                "confidence": round(decision.confidence, 4),
                "risk": None if decision.risk is None else round(decision.risk, 4),
                "danger": decision.danger,
                "note": decision.note,
                "latency_ms": round(decision.latency_ms, 1),
                "usage": (decision.response.usage if decision.response else {}) or {},
                "model": (decision.response.model if decision.response else self.backend_name()),
                "backend": self.backend_name(),
                "decision_mode": self.cfg.agent.decision_mode,
                "nouls": {name: round(value, 4) for name, value in nouls.items()},
                "options": self._option_view(
                    board, answers.get("next_action", {}), nouls, statuses=statuses
                ),
                "choice_answer": answers.get("next_action"),
                "danger_answer": answers.get("danger"),
                "derived_facts": state.get("derived_facts", {}),
                "action_space": [entry["cell"] for entry in state.get("candidate_cells", [])],
                "tokens": {
                    "state": estimate_tokens(decision.state or {}),
                    "questions": estimate_tokens(decision.questions or {}),
                },
                "candidates": len(self.agent.candidate_cells(board)),
                "board_rows": board.to_symbol_rows(),
            }
            # the code-only view, for the side-by-side comparison in the UI
            deduction = solve(board, global_rule=self.cfg.agent.solver_global_rule)
            response["solver_view"] = {
                "solved": bool(deduction.solved),
                "proved_safe": [format_cell(c) for c in deduction.safe],
                "proved_mines": [format_cell(c) for c in deduction.mines],
                "note": (
                    "the constraint rules can prove moves here; they are handed to "
                    "Jev as facts, and Jev still decides"
                    if deduction.solved
                    else "the constraint rules cannot prove anything here"
                ),
            }

            self.last_payload = payload
            self.last_response = response
            self.stats.record(response)
            response["stats"] = self.stats.as_dict()
            response["answered_at"] = time.time()
            return response


# ------------------------------------------------------------------------ http
def build_handler(app: JevWebApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "jev-mine-sweeping"
        protocol_version = "HTTP/1.1"

        # ---------------------------------------------------------- utilities
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON body: {exc}") from exc

        def _static(self, relative: str) -> None:
            candidate = (WEB_ROOT / relative.lstrip("/")).resolve()
            if not str(candidate).startswith(str(WEB_ROOT.resolve())) or not candidate.is_file():
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            self._send(
                200,
                candidate.read_bytes(),
                CONTENT_TYPES.get(candidate.suffix, "application/octet-stream"),
            )

        # ------------------------------------------------------------ routes
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._static("index.html")
            elif path == "/stats":
                self._send_json(200, app.stats.as_dict())
            elif path == "/config":
                self._send_json(
                    200,
                    {
                        "backend": app.backend_name(),
                        "model": app.cfg.jev.model,
                        "decision_mode": app.cfg.agent.decision_mode,
                        "solver_mode": app.cfg.agent.solver_mode,
                        "safety_override": app.cfg.agent.safety_override,
                        "thresholds": {
                            "safe": app.cfg.agent.safe_threshold,
                            "mine": app.cfg.agent.mine_threshold,
                            "flag_preference": app.cfg.agent.flag_preference,
                            "choice_tolerance": app.cfg.agent.choice_tolerance,
                        },
                        "board": {
                            "rows": app.cfg.game.rows,
                            "cols": app.cfg.game.cols,
                            "mines": app.cfg.game.mines,
                        },
                    },
                )
            else:
                self._static(path)

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            path = self.path.split("?", 1)[0]
            if path != "/decide":
                self._send_json(404, {"error": "unknown endpoint"})
                return
            try:
                payload = self._read_json()
                self._send_json(200, app.decide(payload))
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
            except Exception as exc:  # pragma: no cover - defensive
                self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server API
            """Quiet by default; decision calls are logged by the app itself."""
            return

    return Handler


def serve(
    cfg: Config,
    client: JevClient,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    """Run the game server until interrupted."""
    if not (WEB_ROOT / "index.html").is_file():
        print(f"! page assets not found at {WEB_ROOT}")
        print("  the HTML game needs the repository checkout: install it with")
        print("  `pip install -e .` from the repo root (or run `python -m jev_mine_sweeping serve`")
        print("  inside the checkout). `pip install .` alone does not ship web/.")
    app = JevWebApp(cfg, client)
    httpd = ThreadingHTTPServer((host, port), build_handler(app))
    url = f"http://{host}:{port}/"
    print(f"jev-mine-sweeping web game: {url}")
    print(f"  backend={app.backend_name()} model={cfg.jev.model} "
          f"decision_mode={cfg.agent.decision_mode} solver_mode={cfg.agent.solver_mode} "
          f"override={cfg.agent.safety_override}")
    print(f"  board={cfg.game.rows}x{cfg.game.cols} {cfg.game.mines} mines "
          f"(change it in the page's difficulty buttons)")
    print("  Ctrl+C to stop")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()
