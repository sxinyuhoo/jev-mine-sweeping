# 相对原项目的改动

这个仓库的起点是 [mine-sweeping-reinforcement-learning](https://github.com/sxinyuhoo/mine-sweeping-reinforcement-learning)（DQN 训练扫雷）。现在的版本把强化学习与桌面自动化整套换掉，改成「HTML 扫雷 + Jev 决策」。这里记录改了什么、为什么。

## 0. 现在的目录结构

```
jev-mine-sweeping/
├── README.md / README.en.md     # 中英文说明：介绍 / 演示 / 部署 / 配置 / 用法
├── IDEA.md                      # 为什么放弃强化学习、现在的设计思路、想验证的问题
├── LICENSE                      # MIT（保留原项目版权声明）
├── pyproject.toml               # 打包与依赖（版本号唯一来源是包里的 __version__）
├── .env.example                 # TYPESAFE_API_KEY 等环境变量示例
├── config/
│   ├── config.yaml              # 你的配置（含 API key，已被 .gitignore 忽略）
│   └── config.example.yaml      # 带完整中文注释的配置模板
├── docs/
│   ├── jev-integration.md       # 对照 Jev 官方手册的集成说明、问题设计与阈值
│   ├── benchmarks.md            # 基线与实测数据、复现命令
│   ├── rewrite-notes.md         # 本文件
│   ├── removed-rl-local-changes.patch   # 删除 RL 代码前的本地改动存档
│   └── media/                   # 演示视频 + 封面（见该目录 README.md）
├── web/                         # 网页版扫雷（自动操作 + Jev 决策可视化 + 中英双语）
│   ├── index.html               # 棋盘 / 当前决策 / 选项概率 / 代码侧事实 / 统计 / 日志
│   ├── style.css
│   ├── app.js                   # 游戏规则 + 决策回路（POST /decide）+ i18n
│   └── test/game-harness.js     # 用 Node 跑真实 app.js 的自动化测试
├── src/jev_mine_sweeping/
│   ├── config.py                # 配置加载（YAML + 环境变量覆盖）
│   ├── cli.py                   # jev-ms 命令行
│   ├── runner.py                # 离线局主循环 + JSONL 日志
│   ├── web/server.py            # 网页版后端：静态页 + POST /decide（复用同一个 agent）
│   ├── game/                    # board（棋盘模型）/ solver（约束传播）/ local_game（离线模拟器）
│   │                            # state_builder（发给模型的 state）
│   ├── jev/                     # client（http / sdk / mock 三后端）/ questions（Choice·Noul·Score）
│   └── agent/jev_agent.py       # 决策合成：原子模式的五条规则 + choice 模式
├── tests/                       # 网页接口、决策合成、state/问题、求解器 soundness、配置
└── log/runs/                    # 每次运行的 JSONL 记录（含完整 state/questions/answers）
```

## 1. 保留并重构

| 原文件 | 现在 |
|---|---|
| `core/mine_sweeper_game.py` | `game/local_game.py`（离线模拟器，与网页版同规则） |
| 单元格取值约定 `0-6 / 7未开 / 8旗 / 9雷` | 原样保留（`game/board.py` 有完整说明，网页版 JS 用同一套取值） |

## 2. 移除

- **强化学习那套**：`core/reinforcement_learning_*.py`（tianshou + torch DQN）、`core/ref/`、`core/x.ipynb`、`log/` 下的模型权重，以及依赖里的 `torch` / `tianshou` / `gymnasium` / `tqdm`。理由见 [IDEA.md](../IDEA.md)。
- **桌面 App 那一整套**：`vision/`（截图、模板匹配读盘、多屏与 HiDPI 处理）、`game/controller.py`（pyautogui 点击）、`runner.DesktopRunner`、`jev-ms calibrate` / `jev-ms play` 两条命令、`config/screenshot_template/*.png` 模板图，以及依赖里的 `opencv-python` / `mss` / `pyautogui`。
  改成 HTML 版之后，棋盘由页面直接给出结构化数据，读盘这一层（连同它的所有误读风险）整个不需要了；依赖也从 6 个降到 2 个。
- **屏幕读数自检**（`verify_reads`，发现矛盾时问 Jev 复核）——同样只对截图读盘有意义，网页版棋盘是精确的。

> 删除前 `core/reinforcement_learning_train_9x9.py` 还有一份未提交的本地改动，已存为 [`removed-rl-local-changes.patch`](removed-rl-local-changes.patch)（其余文件的历史版本都在 git 里）。

## 3. 修复的缺陷

1. **未匹配的格子返回 `0`**（= 已翻开的空格，最危险的误读，会让求解器推错）→ 改为 `7`（未翻开）。（桌面读盘时代的修复，随桌面模式一起退役，教训记在这里）
2. **点击坐标硬编码 `//2`**（假定 2x Retina）→ 曾改为自动测量 `screen_scale`；桌面模式移除后不再需要。
3. **`api_calls` 跨局累加**（200 局报 24770 次调用）→ 按局取差值。
4. **YAML 1.1 把不带引号的 `off` 读成布尔 `False`**，导致 `solver_mode != "off"` 成立、**悄悄跑回代码优先模式**（与「每一步都问模型」相反）→ 配置加引号 + 加载器做布尔→字符串纠正，并写了回归测试。
5. **`--solver-only` 被付费保护拦住**（纯代码基线不该弹「即将调用付费 API」）→ 保护条件排除该模式。

## 4. 新增

- `web/` + `web/server.py`：网页版扫雷与 `/decide` 接口（标准库 `http.server`，无额外依赖），页面把 Jev 的选项、概率、事实、延迟与 token 全部可视化；中英双语一键切换。
- `agent/jev_agent.py` 的**原子决策模式**：代码算事实、问短名单、按模型自己的概率排序落子，落子来源分五类（`noul` / `jev` / `noul_flag` / `noul_guess` / `fallback`），全部进日志。
- `state_builder.py` 的 `derived_facts`：把约束传播的证明结论、每格局部算术、代码风险估计作为**类型化事实**写进 state。
- `game/solver.py`：确定性约束求解器（单点规则 + 子集规则 1-2-1 + 雷数预算，不动点迭代）。
  有一条属性测试用**隐藏真值**校验每一次「证明」：60 个种子下 0 次错误推理——这个测试真的抓到过一个 bug（同一轮里刚证明的雷没有从雷数预算里扣掉，导致全局规则把安全格判成雷）。
- `jev/client.py`：三后端（`http` / `sdk` / `mock`）+ 统一响应归一化 + 重试与 401 明确报错。
- `runner.py`：JSONL 全量日志（state、questions、answers、usage、延迟、来源），可事后复盘每一次「模型想开、代码拦下」。
- 测试：82 个 Python 测试（网页接口端到端含真实 HTTP 层、原子模式五条规则的逐个分支、state/问题构造、求解器 soundness 属性测试、配置与难度预设）+ 36 项前端断言（`web/test/game-harness.js`，Node 加载真实 `app.js` 跑）。
