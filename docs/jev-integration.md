# Jev 集成说明

本文记录本项目如何按 TypeSafe（Jev / System One）官方手册使用模型，以及每条设计决定的依据。
手册入口：<https://docs.typesafe.ai>（`llms.txt` 可拿全量文档索引）。

## 1. 接口契约

```http
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <TYPESAFE_API_KEY>
Content-Type: application/json
```

请求体三个顶层字段：

| 字段 | 说明 |
|---|---|
| `state` | 被评估的内容，字符串 / JSON 对象 / 文本数组 |
| `model` | 模型别名，默认 `jev-latest` |
| `questions` | 问题字典：键是自定义的问题 id，值是问题定义 |

问题定义（三种原语）：

```json
{
  "next_action": {
    "type": "choice",
    "instructions": "Given `board_text` ... which single action should be played next?",
    "criteria": { "open_r3c4": "Open r3c4 (left click). ...", "flag_r3c4": "Flag r3c4 ..." }
  },
  "is_mine__r3c4": {
    "type": "noul",
    "instructions": "In the position described by `board_text`, is the hidden cell r3c4 a mine?",
    "criteria": { "true": "r3c4 is one of the mines still on the board", "false": "r3c4 is safe to open" }
  },
  "danger": {
    "type": "score",
    "instructions": "How dangerous is the current position for the player?",
    "criteria": ["Safe: ...", "Manageable: ...", "Risky: ..."]
  }
}
```

响应按问题 id 回填：

```json
{
  "model": "jev-latest",
  "answers": {
    "next_action": { "type": "choice", "choice": "open_r3c4", "confidence": 0.82, "probabilities": {"open_r3c4": 0.7, "flag_r3c4": 0.2} },
    "is_mine__r3c4": { "type": "noul", "noul": 0.07 },
    "danger": { "type": "score", "score": 1.1, "confidence": 0.6, "legend": {"0": "..."}, "probabilities": {"0": 0.1, "1": 0.8, "2": 0.1} }
  },
  "usage": { "input_tokens": 1543, "output_tokens": 51 }
}
```

三种原语的分工（手册原话的落地）：

| 原语 | 回答什么 | 本项目的用法 |
|---|---|---|
| `Choice` | 从选项集里选一个 | 下一步动作：`open_<格>` / `flag_<格>` |
| `Noul` | 是/否，返回 0~1 的是概率 | 每个候选格问一次「这格是雷吗」 |
| `Score` | 落在有序光谱的哪一级 | 局面危险度（只进日志与看板，不参与决策） |

## 2. 照手册落地的七条规则

1. **问题 id 不会发给模型。** 所以每个 `instructions` 都是自包含的完整问题，绝不依赖 id 自解释。
2. **一次请求问完同一 state 上的所有问题。** 手册实测：13 个问题打包成 1 次调用，比 13 次单独调用便宜 11.5 倍、快 9.6 倍，答案完全一致。本项目因此把 `next_action` + 每个候选格的 `is_mine` + `danger` 放在同一次请求里。
3. **投机式发散（speculative fan-out），但要问对格子。** 手册提醒「coding agent 容易掉进一次只问一个问题的习惯」，同时也提醒选项集会稀释注意力。本项目的做法：只对**本次可能落子的那批格子**（短名单，默认 24 格）同时问 `is_mine`，代码再决定用哪些答案；对代码已经证明的格子不再发问（问已知的事只是烧 token）。
4. **原子问题 + 代码组合（原子模式的核心）。** 手册要求把复杂判断拆成多个单维问题、再用代码组合。本项目据此把「下一步动哪里」从「480 选 1 的分类题」改成：每格一个 `is_mine`（原子、可校准）→ 代码用模型自己给出的概率排序落子。**这是默认的 `decision_mode: atomic`**；老的「一个大 Choice 走到底」保留为 `choice` 模式用于对照。
5. **算术留在代码里，事实写进 state。** 模型不是计算器：与其让它从 480 格的文本里自己推出「这三个格里恰好一个雷」，不如把约束传播的结论（`derived_facts.proved_safe_cells` / `proved_mine_cells`）、每格的局部算术（`adjacent_numbers`）和代码风险估计（`code_risk`）作为**类型化事实**放进 state，让它在事实之上做判断。实测中模型最初的失误正是「让它自己算」造成的。
6. **state 只用英文。** 手册明确：Jev 的训练语言以英文为主，其他语言（含 CJK）可以接受但准确率更低。因此发给模型的 payload 里没有中文，连棋盘图例都是英文。
7. **按 confidence 分档处置。** 手册建议高置信自动执行、中置信谨慎推进、低置信不执行。原子模式里代码只用四个阈值处置（见第 6 节）；`choice` 模式下低置信（< `agent.confidence_floor`）直接降级为代码风险估计，并在日志里标注 `fallback`。

## 3. 需要遵守的硬约束

| 约束 | 手册数值 | 本项目的处理 |
|---|---|---|
| 上下文预算 | 约 32,000 token（state 与问题共享，约 15 万英文字符） | 原子模式下 9x9 开局请求 ≈4k token；`estimate_tokens()` 超过 24k 时写进日志警告 |
| Choice 选项数 | 单题上限 255 | 原子模式只摆 `agent.focus_cells`（默认 24 格 → 48 个选项）的短名单；`jev.max_choice_options` 默认 60 是安全网 |
| 输入类型 | 只支持文本（字符串/JSON/数组），不支持图像 | 棋盘由 HTML 游戏在浏览器里维护，模型只收结构化 JSON |
| 校准含义 | 概率按组校准，不保证单次答案正确 | 原子模式只依赖**相对排序**（谁的概率最低/最高）与两个阈值，不依赖单点概率的绝对值 |
| 问题独立性 | 同一请求内的问题互不影响 | 增删问题不改变其他问题结果，因此可以放心多问 |

## 4. 模型结论与代码结论怎么合成

### 默认：原子模式（`decision_mode: atomic`）

代码先把**事实**算清楚（约束传播的证明结论、每格局部算术、代码风险估计）写进 state，然后**一次请求**问一组聚焦的问题（短名单上的 `is_mine` + 一个覆盖短名单的 `next_action` + 一个 `danger`）。落子按下面的顺序合成，**每一步用的都是模型自己给的数字**：

```
一次请求：N 个 is_mine（短名单） + Choice next_action + Score danger
        │
        ├─ ① 有格子 p(雷) ≤ safe_threshold(0.10) ──► 开它            source=noul
        │     （模型自己说这格安全，这是它最可靠的一种判断）
        ├─ ② Choice 是 flag 且该格 p(雷) ≥ mine_threshold(0.5) ──► 插旗 source=jev
        ├─ ③ Choice 是 open 且该格 p(雷) ≤ 最安全格 + choice_tolerance(0.05) ──► 开它 source=jev
        │     （盲盘上所有格子风险相同，靠这条保留模型自己的选择）
        ├─ ④ 最好的可开格风险 > flag_preference(0.3) 且有格子 p(雷) ≥ 0.5 ──► 插旗 source=noul_flag
        └─ ⑤ 其余 ──► 开概率最低的那格（被迫猜）                        source=noul_guess
```

代码总共只加四个阈值，全部可配置，且每次落子的 `source` 都写进日志，所以「模型想开、代码改成插旗」这类分歧都能复盘。

### 对照：`choice` 模式（`--pure-choice` / `--decision-mode choice`）

老的「一个大 Choice 走到底」：

```
一次请求：Choice next_action（覆盖全部候选格） + 每格 Noul + Score danger
        │
        ├── 选项不存在 / 指向已翻开的格 ──► source=fallback（代码风险估计兜底）
        ├── 雷已插满却还要插旗 ─────────► source=fallback
        └── 其余 ────────────────────► source=jev（就按模型说的落子）
```

### 代码帮忙：`--guarded`

```
约束传播（代码，证明）
        │  有必然解 ──► 直接执行，不调用 API（source=solver）
        ▼  无必然解
Jev 一次请求
        ├── Choice 置信度 < confidence_floor ──► source=fallback
        ├── Noul 显示所选格 p_mine 高于阈值 ──► 改开更安全的格，或改插旗（source=override）
        ├── Noul 显示所选插旗格 p_mine 很低 ──► 改成开格（source=override）
        └── 否则 ──────────────────────────► source=jev
```

每次决策都会写进 `log/runs/*.jsonl`：完整 state、完整 questions、完整 answers、`source`、`p_mine`、延迟与 token 用量。任何一次「模型想开、代码拦下」都能事后复盘——这也是判断 override 到底帮了忙还是帮了倒忙的唯一依据（离线实测：概率源不准时 override 会让胜率从 90% 掉到 88.3%，所以默认关掉）。

## 5. 三个后端的差异

| backend | 说明 |
|---|---|
| `http` | 直接用 `requests` POST，最透明，依赖最少（默认） |
| `sdk` | 官方 `typesafe-sdk`，pydantic 类型化问题与答案，同一套 wire format |
| `mock` | 离线启发式替身，不联网。用于跑通链路、跑测试、跑无 key 的演示；**它的准确率不代表 Jev** |

`jev-check` 会用一条最小请求（一个 Noul）验证 key 与连通性。

## 6. 一次请求到底问什么

| 问题 id | 类型 | 问什么 | 代码怎么用 |
|---|---|---|---|
| `is_mine__r3c4` | Noul | 这格是雷吗（0~1） | **原子模式的主信号**：按它排序落子 |
| `next_action` | Choice | 下一步动哪个格、开还是插旗 | 与 Noul 一致时采纳（`source=jev`） |
| `danger` | Score | 当前局面有多危险 | 只进日志与看板 |

**代码加在模型答案之上的全部内容，就是这四个阈值**（`agent` 段，都能改）：

| 参数 | 默认 | 含义 |
|---|---|---|
| `safe_threshold` | 0.10 | Jev 给的 p(雷) ≤ 它 → 直接开（模型自己说安全） |
| `mine_threshold` | 0.50 | Jev 给的 p(雷) ≥ 它 → 视为雷（可插旗） |
| `flag_preference` | 0.30 | 最好的可开格风险都高于它、又有格子被判为雷 → 插旗而不是赌 |
| `choice_tolerance` | 0.05 | Jev 的 Choice 比「最安全格」差多少之内仍然采纳 |
| `focus_cells` | 24 | 一次提问让 Jev 看到的格子数（短名单） |

**短名单怎么来**：必然安全的格子（约束规则已证明，选项描述里写明这是事实）→ 其余按代码风险排序取最安全的一批 → 再取少量最危险的（供插旗考虑）；开局没有任何信息时在整盘**均匀取样**（否则截断会把模型限制在最上面几行）。`is_mine` 只问短名单里的格子——**代码不会去问它已经证明的事情**。

## 7. 发给模型的 state 长什么样

英文，含棋盘文本、每格的局部算术、约束、代码已证明的事实（`state-demo` 可以随时打印真实 payload）：

```json
{
  "goal": "Clear a Minesweeper board: open every cell that is not a mine. ...",
  "position": {"rows": 9, "cols": 9, "total_mines": 10, "mines_flagged": 8,
               "mines_not_yet_flagged": 2, "hidden_cells": 12, "moves_played": 20},
  "board_text": ["??F211FF1", "?3?F11221", "?F2110000", "..."],
  "candidate_cells": [
    {"cell": "r0c0", "status": "proved_safe", "code_risk": 0.0,
     "adjacent_numbers": [{"cell": "r1c1", "value": 3, "flagged_neighbours": 2,
                           "hidden_neighbours": 5, "mines_still_needed": 1}]}
  ],
  "mine_constraints": ["r1c1=3: exactly 1 mine(s) among [r0c0, r0c1, r1c0, r2c0, r2c1]"],
  "derived_facts": {
    "proved_safe_cells": ["r0c0", "r0c1"],
    "proved_mine_cells": ["r2c2"],
    "note": "Derived in code from `mine_constraints`. These are facts, not moves: ..."
  },
  "recent_actions": [{"step": 20, "action": "open", "cell": "r6c0", "source": "noul",
                      "result": "revealed 3 cell(s)"}]
}
```

```bash
jev-ms state-demo --difficulty 困难 --warmup 30          # 打印真实的 state 与 questions
jev-ms state-demo --difficulty 困难 --warmup 30 --live   # 顺便真发一次请求，打印答案
```
