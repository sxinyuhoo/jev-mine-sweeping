"""Thin Jev (TypeSafe System One) client with three interchangeable backends.

============================  ============================================
backend                       when to use it
============================  ============================================
``http`` (default)            plain ``POST /v1/systemone`` via ``requests``.
                              Fully specified by the public docs, easy to log,
                              no SDK version coupling.
``sdk``                       the official ``typesafe-sdk`` (pydantic typed
                              questions/answers). Same wire format, typed
                              responses.
``mock``                      offline heuristic stand-in. Never calls the
                              network; used by the test suite and by
                              ``sim --backend mock`` so the whole pipeline can
                              be exercised without a key.
============================  ============================================

All three return the same normalised :class:`JevResponse`, so nothing
downstream knows or cares which one ran.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

import requests

from ..config import API_KEY_ENV, JevConfig


class JevError(RuntimeError):
    """Any failure that stops a request from producing usable answers."""


@dataclass
class JevResponse:
    """Normalised answers, independent of the backend that produced them."""

    answers: dict[str, dict[str, Any]]
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    request: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------- readers
    def choice(self, question_id: str) -> tuple[str | None, float, dict[str, float]]:
        """``(option, confidence, probabilities)`` for a Choice question."""
        answer = self.answers.get(question_id) or {}
        return (
            answer.get("choice"),
            float(answer.get("confidence", 0.0) or 0.0),
            {k: float(v) for k, v in (answer.get("probabilities") or {}).items()},
        )

    def noul(self, question_id: str) -> float | None:
        """The yes-probability of a Noul question."""
        answer = self.answers.get(question_id)
        if not answer or answer.get("noul") is None:
            return None
        return float(answer["noul"])

    def noul_map(self, prefix: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for qid, answer in self.answers.items():
            if qid.startswith(prefix) and answer.get("noul") is not None:
                out[qid[len(prefix) :]] = float(answer["noul"])
        return out

    def score(self, question_id: str) -> tuple[float | None, float, dict[str, float]]:
        answer = self.answers.get(question_id) or {}
        raw = answer.get("score")
        return (
            None if raw is None else float(raw),
            float(answer.get("confidence", 0.0) or 0.0),
            {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()},
        )


@runtime_checkable
class JevClient(Protocol):
    name: str

    def ask(self, state: Any, questions: Mapping[str, Any], model: str | None = None) -> JevResponse:
        ...


# --------------------------------------------------------------------------- http
class HttpJevClient:
    """``POST {endpoint}`` with ``Authorization: Bearer <key>``."""

    name = "http"

    def __init__(self, cfg: JevConfig) -> None:
        self.cfg = cfg
        key = cfg.resolved_api_key
        if not key:
            raise JevError(
                f"no API key: set jev.api_key in config.yaml or export {API_KEY_ENV}"
            )
        self.api_key = key
        self.endpoint = cfg.endpoint
        self.default_model = cfg.model

    def ask(self, state: Any, questions: Mapping[str, Any], model: str | None = None) -> JevResponse:
        payload = {
            "state": state,
            "model": model or self.default_model,
            "questions": dict(questions),
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(1, max(self.cfg.max_retries, 1) + 1):
            started = time.perf_counter()
            try:
                response = requests.post(
                    self.endpoint, headers=headers, json=payload, timeout=self.cfg.timeout
                )
            except requests.RequestException as exc:  # network level
                last_error = exc
                self._sleep(attempt)
                continue
            latency_ms = (time.perf_counter() - started) * 1000

            if response.status_code == 200:
                try:
                    body = response.json()
                except json.JSONDecodeError as exc:
                    raise JevError(f"non-JSON response from Jev: {response.text[:200]}") from exc
                return JevResponse(
                    answers=body.get("answers", {}) or {},
                    model=body.get("model", payload["model"]),
                    usage=body.get("usage", {}) or {},
                    latency_ms=latency_ms,
                    request=payload,
                    raw=body,
                )
            if response.status_code in (401, 403):
                raise JevError(
                    f"Jev rejected the API key ({response.status_code}): {response.text[:300]}"
                )
            if response.status_code in (408, 429, 500, 502, 503, 504):
                last_error = JevError(f"HTTP {response.status_code}: {response.text[:200]}")
                self._sleep(attempt)
                continue
            raise JevError(f"Jev request failed ({response.status_code}): {response.text[:300]}")
        raise JevError(f"Jev request failed after {self.cfg.max_retries} attempts: {last_error}")

    def _sleep(self, attempt: int) -> None:
        time.sleep(self.cfg.retry_backoff ** attempt)


# ---------------------------------------------------------------------------- sdk
class SdkJevClient:
    """Official ``typesafe-sdk`` backend (requires the ``sdk`` extra)."""

    name = "sdk"

    def __init__(self, cfg: JevConfig) -> None:
        try:
            from typesafe_sdk import TypeSafeClient
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise JevError(
                "typesafe-sdk is not installed; run `pip install typesafe-sdk` "
                "or set jev.backend: http"
            ) from exc
        key = cfg.resolved_api_key
        if not key:
            raise JevError(
                f"no API key: set jev.api_key in config.yaml or export {API_KEY_ENV}"
            )
        kwargs: dict[str, Any] = {"api_key": key, "model": cfg.model, "timeout": cfg.timeout}
        if cfg.endpoint and "api.typesafe.ai" not in cfg.endpoint:
            base = cfg.endpoint.rsplit("/v1/", 1)[0]
            kwargs["base_url"] = base
        self.client = TypeSafeClient(**kwargs)
        self.default_model = cfg.model

    def ask(self, state: Any, questions: Mapping[str, Any], model: str | None = None) -> JevResponse:
        from typesafe_sdk import Choice, Noul, Score

        typed: dict[str, Any] = {}
        for qid, question in questions.items():
            qtype = question["type"]
            body = {k: v for k, v in question.items() if k != "type"}
            cls = {"choice": Choice, "noul": Noul, "score": Score}.get(qtype)
            if cls is None:
                raise JevError(f"unsupported question type {qtype!r} for {qid}")
            typed[qid] = cls(**body)

        started = time.perf_counter()
        try:
            response = self.client.system_one(
                state=state, questions=typed, model=model or self.default_model
            )
        except Exception as exc:  # SDK raises a family of typed errors
            raise JevError(f"Jev SDK call failed: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000

        answers: dict[str, dict[str, Any]] = {}
        for qid, answer in response.answers.items():
            dumped = answer.model_dump()
            dumped["type"] = answer.type
            if "legend" in dumped and isinstance(dumped["legend"], dict):
                dumped["legend"] = {str(k): v for k, v in dumped["legend"].items()}
            if "probabilities" in dumped and isinstance(dumped["probabilities"], dict):
                dumped["probabilities"] = {
                    str(k): float(v) for k, v in dumped["probabilities"].items()
                }
            answers[qid] = dumped
        usage = response.usage.model_dump() if getattr(response, "usage", None) else {}
        return JevResponse(
            answers=answers,
            model=getattr(response, "model", self.default_model),
            usage={k: v for k, v in usage.items() if v is not None},
            latency_ms=latency_ms,
            request={"state": state, "questions": dict(questions), "model": self.default_model},
            raw={"backend": "sdk"},
        )


# --------------------------------------------------------------------------- mock
class MockJevClient:
    """Offline heuristic stand-in with Jev's exact answer shape.

    It re-derives a risk estimate from the state's ``candidate_cells`` (the same
    arithmetic the code-level fallback uses) and answers Choice / Noul / Score
    accordingly. This exists so the pipeline can be tested end to end — it is
    **not** a model and its accuracy says nothing about Jev's.
    """

    name = "mock"

    def __init__(self, cfg: JevConfig | None = None, noise: float = 0.0, seed: int = 7) -> None:
        self.default_model = "mock-heuristic"
        self.noise = noise
        import random

        self.rng = random.Random(seed)

    # ------------------------------------------------------------------ helpers
    def _risks(self, state: dict[str, Any]) -> dict[str, float]:
        position = state.get("position", {})
        hidden = position.get("hidden_cells") or 1
        remaining = position.get("mines_not_yet_flagged")
        if remaining is None:
            remaining = position.get("total_mines", 0)
        blind = remaining / hidden if hidden else 0.0
        risks: dict[str, float] = {}
        for entry in state.get("candidate_cells", []):
            numbers = entry.get("adjacent_numbers") or []
            estimates = [
                (n.get("mines_still_needed", 0) / n["hidden_neighbours"])
                for n in numbers
                if n.get("hidden_neighbours")
            ]
            risk = max(estimates) if estimates else blind
            if self.noise:
                risk = min(max(risk + self.rng.uniform(-self.noise, self.noise), 0.0), 1.0)
            risks[entry["cell"]] = risk
        return risks

    # --------------------------------------------------------------------- ask
    def ask(self, state: Any, questions: Mapping[str, Any], model: str | None = None) -> JevResponse:
        started = time.perf_counter()
        state = state if isinstance(state, dict) else {}
        risks = self._risks(state)
        answers: dict[str, dict[str, Any]] = {}

        for qid, question in questions.items():
            qtype = question["type"]
            if qtype == "noul":
                cell = qid.split("__", 1)[-1]
                answers[qid] = {"type": "noul", "noul": round(risks.get(cell, 0.2), 4)}
            elif qtype == "score":
                best = min(risks.values()) if risks else 0.2
                level = 0 if best <= 0.001 else (1 if best < 0.25 else 2)
                probabilities = {str(i): (1.0 if i == level else 0.0) for i in range(3)}
                answers[qid] = {
                    "type": "score",
                    "score": float(level),
                    "confidence": 0.75,
                    "legend": {str(i): str(c) for i, c in enumerate(question["criteria"])},
                    "probabilities": probabilities,
                }
            elif qtype == "choice":
                criteria = list(question["criteria"].keys())
                opens = [o for o in criteria if o.startswith("open_")]
                flags = [o for o in criteria if o.startswith("flag_")]
                scored = []
                for option in opens:
                    cell = option[len("open_") :]
                    scored.append((risks.get(cell, 0.2), option))
                scored.sort()
                probabilities = {o: 0.0 for o in criteria}
                if scored:
                    best_risk, best_option = scored[0]
                    for risk, option in scored[:8]:
                        probabilities[option] = max(0.0, 1.0 - risk) / max(len(scored[:8]), 1)
                    if flags and best_risk > 0.35:
                        worst = max(flags, key=lambda o: risks.get(o[len("flag_") :], 0.0))
                        probabilities[worst] = 1.0
                        best_option = worst
                    total = sum(probabilities.values()) or 1.0
                    probabilities = {k: round(v / total, 4) for k, v in probabilities.items()}
                    confidence = 0.55 if best_risk > 0.35 else 0.85
                else:
                    best_option = criteria[0] if criteria else None
                    if best_option:
                        probabilities[best_option] = 1.0
                    confidence = 0.5
                answers[qid] = {
                    "type": "choice",
                    "choice": best_option,
                    "confidence": confidence,
                    "probabilities": probabilities,
                }

        return JevResponse(
            answers=answers,
            model=self.default_model,
            usage={"input_tokens": 0, "output_tokens": 0},
            latency_ms=(time.perf_counter() - started) * 1000,
            request={"state": state, "questions": dict(questions), "model": self.default_model},
            raw={"backend": "mock"},
        )


# ------------------------------------------------------------------------ factory
def build_client(cfg: JevConfig) -> JevClient:
    backend = (cfg.backend or "http").lower()
    if backend == "mock":
        return MockJevClient(cfg)
    if backend == "sdk":
        return SdkJevClient(cfg)
    if backend == "http":
        return HttpJevClient(cfg)
    raise JevError(f"unknown jev.backend {cfg.backend!r} (expected http | sdk | mock)")


__all__ = [
    "JevClient",
    "JevError",
    "JevResponse",
    "HttpJevClient",
    "SdkJevClient",
    "MockJevClient",
    "build_client",
]
