# jev-mine-sweeping

[中文](README.md) | **English**

Play Minesweeper end to end with **TypeSafe Jev** (the System One model) — and watch every move the model considers, the probability it assigns, the facts the code proved, and who actually decided each move.

The browser holds a complete Minesweeper game (easy / hard / expert). The Python side turns the position into a structured English `state`, asks Jev, and plays the move its probabilities rank best. The page shows the per-cell risk heat map, the model's option table, the code-side facts, latency and token usage.

It answers one question: **for a deterministic reasoning task, how much of the code logic can a decision model replace, at what cost, and where does it fail.**

| | |
|---|---|
| Environment | HTML Minesweeper (the browser owns rules and interaction); Python only decides |
| Model | TypeSafe Jev / System One (`jev-latest`, measured on `jev-1.13.0`) |
| Dependencies | just `PyYAML` + `requests`; no GPU, no database, no external service |
| Configuration | **one Jev API key**, everything else has a default |
| Verified | 82 Python tests + 36 front-end assertions pass; 5/5 wins on real 9×9 games |

## Demo

<!-- Video: docs/media/jev-mine-sweeping-demo.mp4 (1440×852 / 30fps / 2.6 MB). Replace in place to update. -->

<video src="docs/media/jev-mine-sweeping-demo.mp4" controls muted width="720" poster="docs/media/jev-mine-sweeping-demo-poster.png"></video>

No player above? Open **[docs/media/jev-mine-sweeping-demo.mp4](docs/media/jev-mine-sweeping-demo.mp4)** directly (2:31, one full hard 16×16 game).

## Quick start

Requirements: **Python 3.10+** and a [TypeSafe API key](https://console.typesafe.ai/settings/keys). No GPU, no database, no Docker.

```bash
# 1. environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # pip install -e . is enough for the web game alone

# 2. the key (copy the template, edit one line)
cp config/config.example.yaml config/config.yaml
$EDITOR config/config.yaml         # fill in jev.api_key

# 3. check connectivity
jev-ms jev-check

# 4. run the web game
jev-ms serve --open                # opens http://127.0.0.1:8765/
```

Press **Start auto play** (「开始自动操作」 in the Chinese UI) and watch Jev play move by move.

**Did it install correctly?** A one-minute self-check — the first four need no API key:

```bash
jev-ms state-demo --difficulty 困难 --warmup 30            # print the exact state/questions sent to Jev
jev-ms sim --difficulty 简单 --backend mock --episodes 5   # offline, free: exercise the whole loop
jev-ms serve --backend mock                                # offline UI (heuristic stand-in instead of Jev)
pytest tests -q                                            # 82 tests
node web/test/game-harness.js                              # 36 front-end assertions (needs Node)
```

**Check the cost before a real run**: each request is 7k–10k tokens and 1–2 seconds.

| Difficulty | Moves per game | Tokens per game |
|---|---|---|
| Easy 9×9 | ~20 | ≈ 200k |
| Hard 16×16 | 80–100 | ≈ 800k |
| Expert 16×30 | 200–400 | 2–4M |

Add `--guarded` to spend less: the constraint rules execute the cells they can prove, so the calls concentrate on the genuinely ambiguous endgame.

## Configuration: one key is all you need

```yaml
# config/config.yaml
jev:
  api_key: "your TypeSafe API key here"
```

Or keep it off disk entirely with an environment variable (takes precedence):

```bash
export TYPESAFE_API_KEY="..."
```

`config/config.yaml` is git-ignored, and `jev-ms` only ever prints `key=(set, 108 chars)` — never the value. A fully commented template ships as `config/config.example.yaml` (comments are in Chinese).

**Everything else has a default; change what you need:**

| Field | Default | When to change it |
|---|---|---|
| `jev.model` | `jev-latest` | pin a specific version (e.g. `jev-1.13.0`) |
| `jev.backend` | `http` | `sdk` for the official SDK; `mock` for the offline stand-in (no network, no cost) |
| `game.rows/cols/mines` | 9 / 9 / 10 | change the default board (the example config ships hard 16×16/40) |
| `agent.decision_mode` | `atomic` | set `choice` to run the older one-shot-Choice behaviour for comparison |
| `agent.solver_mode` | `off` | set `prefilter` to let code execute what it can prove |
| `agent.safe_threshold` and the three other thresholds | 0.10 / 0.50 / 0.30 / 0.05 | the only numbers the code adds on top of the model's answers |
| `run.log_dir` | `log/runs` | where the full per-decision JSONL goes (state/questions/answers/usage) |

Command-line flags override the config and may be written before or after the subcommand:

```bash
jev-ms serve --difficulty 专家 --backend http
jev-ms sim --difficulty 困难 --episodes 5 --pure-choice    # the older behaviour, for comparison
jev-ms sim --difficulty 专家 --guarded --episodes 20       # let code help, spend fewer calls
```

## What the page shows

- **Board**: p(mine) heat map (each candidate cell coloured by the model's own answer), with an optional dashed overlay of the code's estimate.
- **Current decision**: who decided this move (`Jev: safe` / `Jev's Choice` / `Jev: a mine` / `Jev: lowest p` / `code fallback`), confidence, chosen cell's p(mine), position danger, latency and tokens.
- **Jev's options and probabilities**: the candidates the model weighed, ranked, with its p(mine), the code estimate, and what the constraint rules proved (safe / mine).
- **Code-side facts**: which cells the constraint rules proved safe or proved to be mines — these are handed to Jev as given facts.
- **Live status**: state (not started / in progress / cleared / hit a mine, large and highlighted), revealed, flags, mines left, moves, elapsed (frozen the moment the game ends), session tally.
- **Controls**: difficulty, Start auto play, single step, new game, speed, show p(mine), overlay code estimate, **auto-restart after a loss** (on by default; waits 1.2 s, then starts a fresh game and restarts the timer), **also restart after a win**, and an **EN / 中文** switch in the top right. You can also click cells yourself (left click opens, right click flags) while auto play is stopped.

| Difficulty | Flag | Board | Mines |
|---|---|---|---|
| Easy | `--difficulty 简单` / `beginner` | 9 × 9 | 10 |
| Hard | `--difficulty 困难` / `hard` | 16 × 16 | 40 |
| Expert | `--difficulty 专家` / `expert` | 16 × 30 | 99 |

## Commands

| Command | What it does |
|---|---|
| `jev-ms serve` | The HTML game with auto play: the browser runs the game, every move asks Jev |
| `jev-ms jev-check` | One tiny request to verify the key and connectivity |
| `jev-ms state-demo` | **No key needed**: print the exact state and questions sent to Jev |
| `jev-ms sim` | Offline episodes against the local simulator; win rate, moves, calls, tokens |

## More documentation

The deep docs are in Chinese for now:

| Document | Content |
|---|---|
| [docs/jev-integration.md](docs/jev-integration.md) | How this maps onto the official Jev manual, the question set, the four thresholds, the state payload |
| [docs/benchmarks.md](docs/benchmarks.md) | Baselines and measurements (code-only 96%/90%/10%; atomic vs one-shot Choice on the same seeds), reproduction commands |
| [docs/rewrite-notes.md](docs/rewrite-notes.md) | What changed relative to the original RL project: directory layout, what was kept, removed, fixed |
| [IDEA.md](IDEA.md) | Why reinforcement learning was dropped, the current design, the open questions |
| [docs/media/README.md](docs/media/README.md) | Demo video specs, re-recording and compression settings |

## FAQ

- **`jev-ms: command not found`** → the virtualenv is not active, or the package is not installed: `pip install -e .`. `python -m jev_mine_sweeping ...` works too.
- **"No usable API key"** → fill `jev.api_key` in `config/config.yaml`, or `export TYPESAFE_API_KEY=...`.
- **Port already in use** → `--port 8766`; drop `--open` if you do not want a browser tab.
- **`mock` wins so rarely — is it broken?** → No. `mock` is an offline heuristic stand-in, not the model; it exists to exercise the pipeline and the UI. Every win-rate claim must be re-run with `--backend http`.
- **The model's probabilities look too high** → measured ≈0.25 per cell on an empty board (the true prior is 0.12), which is why the atomic mode only uses *relative* ranking plus two thresholds.
- **Is the payload sent to the model in Chinese?** → No. Jev is trained primarily in English, so the payload is English throughout; the UI itself switches between Chinese and English.

---

MIT License (the original project's copyright notice is retained — see [LICENSE](LICENSE)).
