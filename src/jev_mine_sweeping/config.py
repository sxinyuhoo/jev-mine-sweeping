"""Configuration loading for jev-mine-sweeping.

Resolution order (first hit wins):

1. an explicit path passed on the command line (``--config``);
2. ``$JEV_MS_CONFIG``;
3. ``<project root>/config/config.yaml``;
4. built-in defaults.

Environment variables always override file values, so a key can be kept out of
the file entirely::

    export TYPESAFE_API_KEY=...
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
ENV_CONFIG_PATH = "JEV_MS_CONFIG"

#: Board presets matching the classic Minesweeper difficulty buttons.
#: 简单 9x9/10, 困难 16x16/40, 专家 16x30/99 (rows x cols).
DIFFICULTIES: dict[str, tuple[int, int, int]] = {
    "beginner": (9, 9, 10),
    "easy": (9, 9, 10),
    "简单": (9, 9, 10),
    "初级": (9, 9, 10),
    "intermediate": (16, 16, 40),
    "hard": (16, 16, 40),
    "困难": (16, 16, 40),
    "expert": (16, 30, 99),
    "专家": (16, 30, 99),
}


def difficulty(name: str) -> tuple[int, int, int]:
    """``"困难"`` -> ``(16, 16, 40)`` as ``(rows, cols, mines)``."""
    key = str(name).strip().lower()
    if key not in DIFFICULTIES:
        raise ValueError(
            f"unknown difficulty {name!r}; known: {sorted(set(DIFFICULTIES))}"
        )
    return DIFFICULTIES[key]

#: The API key may be supplied either in the config file or through the
#: environment (the official SDK uses the same variable name).
API_KEY_ENV = "TYPESAFE_API_KEY"


@dataclass
class JevConfig:
    api_key: str = ""
    model: str = "jev-latest"
    endpoint: str = "https://api.typesafe.ai/v1/systemone"
    backend: str = "http"  # http | sdk | mock
    timeout: float = 60.0
    max_retries: int = 3
    retry_backoff: float = 1.5
    #: One Noul per cell the code might act on. Asking about cells the
    #: constraint rules have already settled wastes tokens and adds no signal.
    max_noul_questions: int = 40
    #: Hard API cap is 255 options; two options per cell (open/flag). The
    #: default shortlist is `agent.focus_cells` cells, so this is a safety net.
    max_choice_options: int = 60
    ask_danger_score: bool = True

    @property
    def resolved_api_key(self) -> str:
        return self.api_key or os.environ.get(API_KEY_ENV, "")

    @property
    def has_api_key(self) -> bool:
        key = self.resolved_api_key
        return bool(key) and not key.startswith(("YOUR_", "<", "sk-REPLACE"))


@dataclass
class GameConfig:
    rows: int = 9
    cols: int = 9
    mines: int = 10


@dataclass
class AgentConfig:
    #: atomic (default): the code puts the *facts* it derived (constraint
    #: deductions, per-cell code risk) into the state, asks a focused question
    #: set, and lets Jev's own per-cell probabilities rank the moves.
    #: choice: one big Choice over every candidate, played verbatim.
    decision_mode: str = "atomic"  # atomic | choice
    #: off = every move is a Jev decision (the default: this project exists to
    #: measure the model). prefilter = let the constraint solver execute the
    #: moves it can prove and only ask Jev about the genuinely ambiguous ones.
    solver_mode: str = "off"  # off | prefilter
    solver_global_rule: str = "full"  # full | safe_only | off
    action_space: str = "frontier"  # frontier | all_hidden
    #: The only numbers the code adds on top of Jev's answers: open at or below
    #: `safe_threshold`, flag at or above `mine_threshold`. `flag_preference` is
    #: the open-risk above which flagging a probable mine beats gambling on it,
    #: and `choice_tolerance` is how much worse than the safest cell the model's
    #: own Choice may be and still be played (it keeps the model's holistic pick
    #: on a blind board, where every cell carries the same risk).
    safe_threshold: float = 0.10
    mine_threshold: float = 0.5
    flag_preference: float = 0.30
    choice_tolerance: float = 0.05
    #: How many cells the Choice question offers. A 16x30 board has 480 cells;
    #: a shortlist the model can actually weigh beats 240 near-identical
    #: options. Proved-safe cells are always in it.
    focus_cells: int = 24
    #: Cross-check Jev's Choice against Jev's own Noul answers (choice mode).
    safety_override: bool = False
    override_threshold: float = 0.3
    #: Below this Choice confidence the code risk estimate takes over.
    confidence_floor: float = 0.0
    max_steps: int = 800
    max_frontier_entries: int = 40


@dataclass
class RunConfig:
    episodes: int = 1
    log_dir: str = "log/runs"
    pause_between_episodes: float = 1.5


@dataclass
class Config:
    jev: JevConfig = field(default_factory=JevConfig)
    game: GameConfig = field(default_factory=GameConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    run: RunConfig = field(default_factory=RunConfig)
    path: Path | None = None
    raw: dict = field(default_factory=dict)

    def log_dir(self) -> Path:
        p = Path(self.run.log_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p


def _build(section_cls, data: dict | None):
    data = dict(data or {})
    known = {f.name for f in fields(section_cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(
            f"unknown key(s) in config section {section_cls.__name__}: {sorted(unknown)}"
        )
    # YAML 1.1 reads bare `off`/`on`/`yes`/`no` as booleans. For string-typed
    # options (solver_mode: off, decision_mode: ...) that silently turns the
    # value into False, which then compares unequal to "off" — so coerce back.
    for field_info in fields(section_cls):
        value = data.get(field_info.name)
        if isinstance(value, bool) and field_info.type in (str, "str"):
            data[field_info.name] = "on" if value else "off"
    return section_cls(**data)


def load_config(path: str | os.PathLike | None = None, require_key: bool = False) -> Config:
    """Load the YAML config, falling back to defaults when the file is absent."""
    candidate = Path(path) if path else None
    if candidate is None and os.environ.get(ENV_CONFIG_PATH):
        candidate = Path(os.environ[ENV_CONFIG_PATH])
    if candidate is None and DEFAULT_CONFIG_PATH.exists():
        candidate = DEFAULT_CONFIG_PATH

    raw: dict = {}
    if candidate is not None and Path(candidate).exists():
        with open(candidate, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{candidate}: config root must be a mapping")
    elif candidate is not None:
        raise FileNotFoundError(f"config file not found: {candidate}")

    cfg = Config(
        jev=_build(JevConfig, raw.get("jev")),
        game=_build(GameConfig, raw.get("game")),
        agent=_build(AgentConfig, raw.get("agent")),
        run=_build(RunConfig, raw.get("run")),
        path=Path(candidate) if candidate else None,
        raw=raw,
    )

    if os.environ.get(API_KEY_ENV):
        cfg.jev.api_key = cfg.jev.api_key or os.environ[API_KEY_ENV]
    if os.environ.get("JEV_MODEL"):
        cfg.jev.model = os.environ["JEV_MODEL"]
    if os.environ.get("JEV_ENDPOINT"):
        cfg.jev.endpoint = os.environ["JEV_ENDPOINT"]
    if os.environ.get("JEV_BACKEND"):
        cfg.jev.backend = os.environ["JEV_BACKEND"]

    if require_key and cfg.jev.backend != "mock" and not cfg.jev.has_api_key:
        raise SystemExit(
            "No TypeSafe API key found.\n"
            f"  Fill jev.api_key in {cfg.path or DEFAULT_CONFIG_PATH}\n"
            f"  or export {API_KEY_ENV}=<your key>."
        )
    return cfg


def describe(cfg: Config) -> str:
    """Human-readable summary; the API key itself is never shown."""
    key = cfg.jev.resolved_api_key
    masked = f"(set, {len(key)} chars)" if key else "(missing)"
    src = cfg.path if cfg.path else "(built-in defaults)"
    if cfg.agent.decision_mode == "atomic":
        mode = "facts in the state, focused questions, Jev's probabilities rank the moves"
        if cfg.agent.solver_mode == "prefilter":
            mode += "; the solver executes what it can prove first"
    else:
        mode = "one Choice over the candidates, played verbatim"
        if cfg.agent.safety_override:
            mode += " (with the Noul cross-check)"
        if cfg.agent.confidence_floor > 0:
            mode += f" (confidence floor {cfg.agent.confidence_floor})"
    return (
        f"config      : {src}\n"
        f"jev backend : {cfg.jev.backend}  model={cfg.jev.model}  key={masked}\n"
        f"board       : {cfg.game.rows} rows x {cfg.game.cols} cols, {cfg.game.mines} mines\n"
        f"agent       : {mode}\n"
        f"              candidates={cfg.agent.action_space}, "
        f"focus<={cfg.agent.focus_cells} cells, noul<={cfg.jev.max_noul_questions}, "
        f"options<={cfg.jev.max_choice_options}"
    )


__all__ = [
    "Config",
    "JevConfig",
    "GameConfig",
    "AgentConfig",
    "RunConfig",
    "load_config",
    "describe",
    "difficulty",
    "DIFFICULTIES",
    "PROJECT_ROOT",
    "DEFAULT_CONFIG_PATH",
    "API_KEY_ENV",
]
