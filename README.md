# jev-mine-sweeping

**中文** | [English](README.en.md)

用 **TypeSafe Jev**（System One 模型）驱动扫雷自动通关，并把模型每一步的「选项、概率、事实」实时画出来。

浏览器里是一个完整的扫雷游戏（简单 / 困难 / 专家），Python 侧把局面翻译成英文结构化 `state` 问 Jev，按它给出的概率落子；页面上能看到模型考虑过哪些格子、给了什么概率、代码证明了什么、这一步是谁决定的、花了多少 token。

它想回答一个问题：**这类「决策型模型」在需要推理的确定性任务上，能替代多少代码逻辑、成本多少、错在哪。**

| | |
|---|---|
| 环境 | HTML 扫雷（浏览器跑规则与交互），Python 侧只做决策 |
| 模型 | TypeSafe Jev / System One（`jev-latest`，实测 `jev-1.13.0`） |
| 依赖 | 只有 `PyYAML` + `requests`；无 GPU、无数据库、无外部服务 |
| 配置 | **只需要填一个 Jev API key**，其余全有默认值 |
| 已验证 | 82 个 Python 测试 + 36 项前端断言全过；真实 key 跑过 9×9 五局全胜 |

## 演示

<!-- 视频：docs/media/jev-mine-sweeping-demo.mp4（1440×852 / 30fps / 2.6 MB）；换视频时同名覆盖即可 -->

<video src="docs/media/jev-mine-sweeping-demo.mp4" controls muted width="720" poster="docs/media/jev-mine-sweeping-demo-poster.png"></video>

没看到播放器就直接点 **[docs/media/jev-mine-sweeping-demo.mp4](docs/media/jev-mine-sweeping-demo.mp4)**（2 分 31 秒，困难 16×16 一整局）。

## 快速开始

前置：**Python 3.10+** 和一个 [TypeSafe API key](https://console.typesafe.ai/settings/keys)。不需要 GPU、数据库、Docker。

```bash
# 1. 建环境
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # 只跑网页版的话 pip install -e . 就够

# 2. 填 key（复制模板 → 只改这一行）
cp config/config.example.yaml config/config.yaml
$EDITOR config/config.yaml         # 填 jev.api_key

# 3. 验证连通性
jev-ms jev-check

# 4. 启动网页版
jev-ms serve --open                # 打开 http://127.0.0.1:8765/
```

页面上点 **「开始自动操作」**（英文界面是 *Start auto play*），就能看着 Jev 一步一步下棋。

**装好了吗**——一分钟自检，前四条都不需要 key：

```bash
jev-ms state-demo --difficulty 困难 --warmup 30            # 打印将发给 Jev 的完整 state 与 questions
jev-ms sim --difficulty 简单 --backend mock --episodes 5   # 不联网、不花钱，跑通链路
jev-ms serve --backend mock                                # 不联网也能看界面
pytest tests -q                                            # 82 个测试
node web/test/game-harness.js                              # 前端 36 项断言（需要 Node）
```

**跑真模型前先看成本**：每次请求 7k~10k token、1~2 秒。

| 难度 | 一局大致步数 | 一局大致 token |
|---|---|---|
| 简单 9×9 | 约 20 步 | ≈ 20 万 |
| 困难 16×16 | 80~100 步 | ≈ 80 万 |
| 专家 16×30 | 200~400 步 | 200~400 万 |

想省就加 `--guarded`：约束规则能证明的格子交给代码，调用集中到真正要判断的残局。

## 配置：只需要填一个 key

```yaml
# config/config.yaml
jev:
  api_key: "在这里填 TypeSafe 的 API key"
```

也可以完全不落盘，用环境变量（优先级更高）：

```bash
export TYPESAFE_API_KEY="..."
```

`config/config.yaml` 已在 `.gitignore` 里；`jev-ms` 打印配置时只会显示 `key=(set, 108 chars)`，不会回显内容。带完整中文注释的模板是 `config/config.example.yaml`。

**其余字段都有默认值，按需再改**：

| 字段 | 默认 | 什么时候要改 |
|---|---|---|
| `jev.model` | `jev-latest` | 想钉住某个版本（如 `jev-1.13.0`） |
| `jev.backend` | `http` | `sdk` 用官方 SDK；`mock` 离线替身（不联网、不花钱） |
| `game.rows/cols/mines` | 9 / 9 / 10 | 换默认棋盘（示例配置里预置的是困难 16×16/40） |
| `agent.decision_mode` | `atomic` | 想跑老做法对照时改 `choice` |
| `agent.solver_mode` | `off` | 想让代码直接走能证明的格子时改 `prefilter` |
| `agent.safe_threshold` 等四个阈值 | 0.10 / 0.50 / 0.30 / 0.05 | 原子模式里代码唯一加的四个数，调参就调它们 |
| `run.log_dir` | `log/runs` | 每次决策的完整 JSONL（state/questions/answers/usage）落盘位置 |

命令行参数会覆盖配置，可以写在子命令前后：

```bash
jev-ms serve --difficulty 专家 --backend http
jev-ms sim --difficulty 困难 --episodes 5 --pure-choice    # 老做法对照
jev-ms sim --difficulty 专家 --guarded --episodes 20       # 让代码帮忙，省调用
```

## 网页版能看到什么

- **棋盘**：p(雷) 热力图（每个候选格的模型概率直接上色），可叠加代码估计的虚线框。
- **当前决策**：这一步是谁定的（`Jev 判定安全` / `Jev 的 Choice` / `Jev 判定是雷` / `Jev 概率最低者` / `代码兜底`）、置信度、所选格 p(雷)、局面危险度、延迟与 token。
- **Jev 的选项与概率**：模型考虑过的候选按概率排序，附该格的模型 p(雷)、代码估计、约束规则给出的事实（安全 / 是雷）。
- **代码侧事实**：约束规则这一步证明了哪些格子必然安全、哪些必然是雷——它们是作为已知条件交给 Jev 的。
- **实时战况**：状态（未开始 / 进行中 / 通关 / 踩雷，大字高亮）、已翻开、插旗、剩余雷、步数、用时（通关或踩雷后立即冻结）、本会话战绩。
- **控制项**：难度、`开始自动操作`、`单步决策`、`重新开局`、速度、显示 p(雷)、叠加代码估计、**踩雷后自动重开**（默认开，停留 1.2 秒后开新局并重新计时）、**通关后也重开**、右上角 **EN / 中文 一键切换界面语言**。也可以手动点格子（左键开、右键插旗）。

| 难度 | 参数 | 棋盘 | 雷数 |
|---|---|---|---|
| 简单 | `--difficulty 简单` / `beginner` | 9 行 × 9 列 | 10 |
| 困难 | `--difficulty 困难` / `hard` | 16 行 × 16 列 | 40 |
| 专家 | `--difficulty 专家` / `expert` | 16 行 × 30 列 | 99 |

## 命令速查

| 命令 | 作用 |
|---|---|
| `jev-ms serve` | 网页版扫雷 + 自动操作（浏览器跑游戏，每一步实时问 Jev） |
| `jev-ms jev-check` | 一条最小请求验证 key 与连通性 |
| `jev-ms state-demo` | **不需要 key**：打印将发给 Jev 的完整 state 与 questions |
| `jev-ms sim` | 离线跑局，统计胜率、步数、调用次数与 token |

## 更多文档

| 文档 | 内容 |
|---|---|
| [docs/jev-integration.md](docs/jev-integration.md) | 逐条对照 Jev 官方手册的集成说明、问题设计与四个阈值、发给模型的 state |
| [docs/benchmarks.md](docs/benchmarks.md) | 基线与实测（纯代码 96%/90%/10%、原子模式 vs 老做法同种子对照）、复现命令 |
| [docs/rewrite-notes.md](docs/rewrite-notes.md) | 相对原项目的改动、目录结构、保留了哪些、删了哪些、修了哪些缺陷 |
| [IDEA.md](IDEA.md) | 为什么放弃强化学习、现在的设计思路、想验证的问题 |
| [docs/media/README.md](docs/media/README.md) | 演示视频的规格、重新录制与压缩参数 |

## 常见问题

- **`jev-ms: command not found`** → 虚拟环境没激活，或没装：`pip install -e .`；也可以 `python -m jev_mine_sweeping ...`。
- **提示没有 key** → 填 `config/config.yaml` 的 `jev.api_key`，或 `export TYPESAFE_API_KEY=...`。
- **端口被占用** → `--port 8766`；不想自动开浏览器就去掉 `--open`。
- **`mock` 胜率很低是不是坏了** → 没坏。`mock` 是离线启发式替身、不是模型，只用来跑测试和演示界面；任何胜率结论都要用 `--backend http` 重跑。
- **模型概率偏高** → 实测空盘上它给每格 ≈0.25（真实先验 0.12），所以原子模式只用**相对排序**与两个阈值，不做绝对判断。
- **发给模型的是中文吗** → 不是。Jev 以英文训练为主，payload 全英文；界面本身支持中英切换。

---

MIT License（保留原项目版权声明，见 [LICENSE](LICENSE)）。
