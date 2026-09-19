# 基线与实测数据

README 只给结论，数据、口径和复现命令都在这里。

## 1. 不含真实 Jev 的参照系

真实 Jev 的表现要跑 `jev-ms sim --difficulty 专家 --backend http --episodes N` 才知道。下表是**不花钱**的对照：

| 配置 | 棋盘 | 局数 | 胜率 | 平均步数 |
|---|---|---|---|---|
| `--solver-only`（纯代码，完全不调用模型） | 9x9/10 | 300 | 96.0% | 18.1 |
| `--solver-only` | 16x16/40 | 20 | 90.0% | 87.2 |
| `--solver-only` | 16x30/99 | 20 | 10.0% | 183.0 |
| `--backend mock`，原子模式（每步都问「模型」） | 9x9/10 | 200 | 8.5% | 17.0 |
| `--backend mock`，choice 模式（老做法） | 9x9/10 | 200 | 4.0% | 16.8 |
| `--backend mock --guarded`（代码帮忙） | 9x9/10 | 200 | 87.5% | — |

复现：

```bash
jev-ms sim --difficulty 困难 --solver-only --episodes 20
jev-ms sim --difficulty 简单 --backend mock --episodes 200 --seed 1 --no-log
jev-ms sim --difficulty 简单 --backend mock --episodes 200 --seed 1 --no-log --pure-choice
jev-ms sim --difficulty 简单 --backend mock --episodes 200 --seed 1 --no-log --guarded
```

怎么读：

- **纯代码基线随难度陡降**：9x9 96% → 16x16 90% → 16x30 仅 10%。9x9 有 79.5% 的盘面可以一次都不猜就解完（`tests/test_solver_soundness.py` 可复现），而 16x30 到了后期几乎每一步都要猜，代码那套朴素风险估计就撑不住了——**这正是 Jev 该发挥价值的地方**。
- 同一个替身、同一批种子，**原子模式 8.5% vs 老做法 4.0%**：把「480 选 1 的分类题」换成「每格一个原子判断 + 代码按模型的概率排序」是有收益的，而且请求规模小得多。
- `mock` 纯靠自己决策仍然打不过代码基线（8.5% vs 96%）。它只是离线启发式替身，**它的数字不代表 Jev**，只说明这套 harness 不会靠代码兜底偷偷赢。

## 2. 真实 Jev 的第一批观测

`jev-1.13.0`，老做法 choice 模式，9x9/10：

| 指标 | 实测 |
|---|---|
| 单步延迟 | 开局 1.9s，中局 1.0~1.9s |
| 单步 token | 开局 in 14.8k / out 3.2k；中局 in 6.1k / out 0.6k |
| 一局 | 4 步踩雷，合计 38.5k token |
| 开局 p(雷) | 模型给每个未翻开格 ≈0.24~0.28（真实先验 10/81≈0.12，**偏高约 2 倍**） |
| 决策质量 | 开局选中 r0c0（自身概率 0.49）一口气翻开 24 格，是好手；随后插了一面**错旗**（该步置信度仅 0.12、概率几乎摊平）；再开一个自评 22% 是雷的格子，踩雷结束 |

这批数据直接决定了后来的三项优化：**问得少而准**（短名单）、**把事实写进 state**、**用模型自己的概率排序**而不是让它在 240 个选项里做分类。

## 3. 原子模式 vs 老做法（同一批种子，真实 Jev）

9x9/10，种子 1–5，两种模式跑同一批盘面：

| 指标 | 原子模式（默认） | 老做法 `--pure-choice` |
|---|---|---|
| 胜率 | **5/5** | 5/5 |
| 平均步数 | **22.0** | 26.6 |
| 平均 API 调用/局 | **22.0** | 26.6 |
| 其中插旗步 | **8** | 25 |
| 落子来源 | `noul` 97 / `jev` 12 / `noul_flag` 1 | `jev` 133（全部） |
| 输入 token 中位/步 | 9382 | 7410 |
| 输出 token 中位/步 | 666 | 531 |
| 每局 token 合计 | ≈218k | ≈218k |
| 每步问的格子数（中位） | 14 | 11 |

复现：

```bash
jev-ms sim --difficulty 简单 --backend http --episodes 5 --seed 1 --yes --decision-mode atomic
jev-ms sim --difficulty 简单 --backend http --episodes 5 --seed 1 --yes --pure-choice
```

怎么读：

- 9x9 上两种模式都 5/5（9x9 本来就有 96% 的盘面不猜也能解完，区分度低），**差别体现在效率上**：同样的 token 总额，原子模式少走了 17% 的步数（22.0 vs 26.6）。
- 最直观的一处：**插旗 8 次 vs 25 次**。老做法里「模型自己的 p(雷) 只有 0.4 却选择插旗」这种浪费步数的旗照插不误（第 2 节那面错旗，置信度只有 0.12）；原子模式只在模型自评 p(雷) ≥ 0.5 时才插旗，或者干脆开最安全的那格。
- 每步的输入 token 在 9x9 上原子模式反而更高（9382 vs 7410）：9x9 的前沿格本来只有 11~14 个，短名单并不比它小，多出来的是 state 里的 `derived_facts`、每格的 `code_risk`/`status`，以及每格 `is_mine` 题面里带的局部算术。**棋盘越大短名单越划算**：16x16 第 30 步的局面，原子模式只摆 24 格（估算 8.9k token），老做法要摆全部 36 格（估算 11.8k token）。
- 想真正拉开差距要看 16x30（代码基线只有 10%，后期几乎每步都要猜）：按上面的单价，一局 200~400 步 × ≈9k token ≈ 200~400 万 token。**这一步还没跑**，跑之前建议先想清楚预算，或者用 `--guarded` 把「约束规则能证明的格子」交给代码，把调用集中到真正要判断的残局上。

## 4. 每次决策的原始记录

所有跑分都会往 `log/runs/*.jsonl` 写一条决策一条记录，含**完整 state、questions、answers、usage、延迟、落子来源**。事后复盘「模型想开、代码拦下」这类分歧靠的就是它：

```bash
jev-ms sim --difficulty 简单 --backend http --episodes 1 --seed 1 --yes   # 默认就写日志
python -c "
import json,glob
path = sorted(glob.glob('log/runs/*.jsonl'))[-1]
for line in open(path):
    rec = json.loads(line)
    if rec.get('event') == 'decision':
        d = rec['decision']
        print(rec['step'], d['action'], d['cell'], d['source'], d['risk'], d['note'][:60])
"
```
