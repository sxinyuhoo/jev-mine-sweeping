/* Jev 扫雷 · 网页版自动操作
 *
 * 浏览器负责「环境」：规则、棋盘、点击、可视化。
 * Python 侧负责「智能体」：把棋盘 POST 给 /decide，拿回 Jev 的决策与全部原始答案。
 *
 * 棋盘取值与 Python 侧完全一致：0-6 已翻开的数字、7 未翻开、8 旗、9 雷。
 */

const DIFFICULTIES = {
  beginner: { rows: 9, cols: 9, mines: 10 },
  hard: { rows: 16, cols: 16, mines: 40 },
  expert: { rows: 16, cols: 30, mines: 99 },
};

const HIDDEN = 7, FLAG = 8, MINE = 9;

const el = (id) => document.getElementById(id);

/* ---------------------------------------------------------------- 多语言 */
/* 静态文案用 HTML 里的 data-i18n 标记，动态文案走 t()；切换语言立即重渲染。 */
const I18N = {
  zh: {
    "doc.title": "Jev 扫雷 · 自动操作",
    "app.title": "Jev 扫雷",
    "badge.connecting": "连接中…",
    "badge.unready": "服务未就绪",
    "label.difficulty": "难度",
    "label.speed": "速度",
    "diff.easy": "简单",
    "diff.hard": "困难",
    "diff.expert": "专家",
    "btn.start": "▶ 开始自动操作",
    "btn.stop": "■ 停止",
    "btn.step": "单步决策",
    "btn.reset": "重新开局",
    "speed.0": "最快（不等）",
    "speed.300": "0.3 秒/步",
    "speed.1000": "1 秒/步",
    "speed.3000": "3 秒/步",
    "toggle.prob": "显示 p(雷)",
    "toggle.code": "叠加代码估计",
    "toggle.restartLoss": "踩雷后自动重开",
    "toggle.restartWin": "通关后也重开",
    "foot.status": "状态",
    "foot.revealed": "已翻开",
    "foot.flags": "插旗",
    "foot.minesLeft": "剩余雷",
    "foot.moves": "步数",
    "foot.elapsed": "用时",
    "foot.session": "本会话",
    "status.idle": "未开始",
    "status.running": "进行中",
    "status.won": "通关",
    "status.lost": "踩雷",
    "session.line": "{games} 局 · 胜 {wins} · 负 {losses}",
    "panel.decision": "当前决策",
    "panel.options": "Jev 的选项与概率",
    "panel.facts": "代码侧事实",
    "panel.stats": "会话统计",
    "panel.log": "决策日志",
    "decision.waiting": "等待开始",
    "decision.hint": "点「开始自动操作」，每一步都会请求一次 Jev。",
    "decision.none": "（无动作）",
    "meter.confidence": "置信度",
    "meter.risk": "所选格 p(雷)",
    "meter.danger": "局面危险度",
    "options.empty": "还没有决策。",
    "options.waiting": "等待决策",
    "options.hint": "前 {shown} 个 · 共 {total} 个选项 · 本次可选 {cells} 格",
    "options.head.option": "选项",
    "options.head.prob": "概率",
    "options.head.risk": "p(雷)",
    "options.head.code": "代码",
    "options.head.fact": "事实",
    "facts.hint": "约束规则证明的结论，作为已知条件交给 Jev",
    "facts.provedSafe": "必然安全（{n}）：",
    "facts.provedMines": "必然是雷（{n}）：",
    "facts.none": "无",
    "facts.noteSolved": "约束规则这一步能证明一些格子；这些结论作为事实交给 Jev，落子仍由 Jev 决定。",
    "facts.noteNone": "约束规则这一步证明不了任何格子，完全靠 Jev 判断。",
    "stats.decisions": "决策次数",
    "stats.latency": "平均延迟",
    "stats.inTokens": "输入 token",
    "stats.outTokens": "输出 token",
    "stats.sources": "来源分布",
    "stats.errors": "错误",
    "log.clear": "清空",
    "src.jev": "Jev 的 Choice",
    "src.noul": "Jev 判定安全",
    "src.noul_flag": "Jev 判定是雷",
    "src.noul_guess": "Jev 概率最低者",
    "src.solver": "代码证明",
    "src.fallback": "代码兜底",
    "src.override": "代码改判",
    "src.error": "错误",
    "src.none": "—",
    "act.open": "翻开",
    "act.flag": "插旗",
    "act.openShort": "开",
    "act.flagShort": "旗",
    "res.revealed": "翻开 {n} 格",
    "res.hitMine": "踩雷了",
    "res.flag": "插旗",
    "res.unflag": "取消旗",
    "res.already": "已翻开",
    "res.over": "本局已结束",
    "fact.proved_safe": "安全",
    "fact.proved_mine": "是雷",
    "fact.unknown": "—",
    "cell.codeRisk": "代码估计 p={p}",
    "meta.model": "模型 {v}",
    "meta.backend": "后端 {v}",
    "meta.latency": "延迟 {v} ms",
    "meta.tokens": "token 入 {in} / 出 {out}",
    "meta.est": "state ≈{s} / 问题 ≈{q}",
    "meta.frontier": "前沿格 {v}",
    "meta.offered": "本次可选 {v}",
    "note.paused": "已暂停",
    "note.noDecision": "没有可用决策",
    "note.failed": "请求失败：{msg}",
    "note.won": "通关",
    "note.lost": "踩雷，本局结束",
    "note.ended": "{result}（用时 {elapsed}s，{moves} 步）",
    "mode.atomic": "决策模式：原子问答（安全≤{safe} / 是雷≥{mine}）",
    "mode.choice": "决策模式：单个 Choice",
    "log.won": "通关",
    "log.lost": "踩雷",
    "log.elapsed": "{result} · {elapsed}s"
  },
  en: {
    "doc.title": "Jev Minesweeper · Auto Play",
    "app.title": "Jev Minesweeper",
    "badge.connecting": "connecting…",
    "badge.unready": "server not ready",
    "label.difficulty": "Level",
    "label.speed": "Speed",
    "diff.easy": "Easy",
    "diff.hard": "Hard",
    "diff.expert": "Expert",
    "btn.start": "▶ Start auto play",
    "btn.stop": "■ Stop",
    "btn.step": "Single step",
    "btn.reset": "New game",
    "speed.0": "fastest (no wait)",
    "speed.300": "0.3 s/move",
    "speed.1000": "1 s/move",
    "speed.3000": "3 s/move",
    "toggle.prob": "show p(mine)",
    "toggle.code": "overlay code estimate",
    "toggle.restartLoss": "auto-restart after a loss",
    "toggle.restartWin": "also restart after a win",
    "foot.status": "status",
    "foot.revealed": "revealed",
    "foot.flags": "flags",
    "foot.minesLeft": "mines left",
    "foot.moves": "moves",
    "foot.elapsed": "elapsed",
    "foot.session": "this session",
    "status.idle": "not started",
    "status.running": "in progress",
    "status.won": "cleared",
    "status.lost": "hit a mine",
    "session.line": "{games} games · {wins}W / {losses}L",
    "panel.decision": "Current decision",
    "panel.options": "Jev's options and probabilities",
    "panel.facts": "Code-side facts",
    "panel.stats": "Session stats",
    "panel.log": "Decision log",
    "decision.waiting": "waiting to start",
    "decision.hint": "Press “Start auto play” — every move sends one request to Jev.",
    "decision.none": "(no action)",
    "meter.confidence": "confidence",
    "meter.risk": "chosen cell p(mine)",
    "meter.danger": "position danger",
    "options.empty": "no decision yet.",
    "options.waiting": "waiting for a decision",
    "options.hint": "top {shown} · {total} options · {cells} cells offered",
    "options.head.option": "option",
    "options.head.prob": "prob",
    "options.head.risk": "p(mine)",
    "options.head.code": "code",
    "options.head.fact": "fact",
    "facts.hint": "conclusions proved by the constraint rules, handed to Jev as facts",
    "facts.provedSafe": "proved safe ({n}):",
    "facts.provedMines": "proved mines ({n}):",
    "facts.none": "none",
    "facts.noteSolved": "the constraint rules can prove some cells here; those conclusions go to Jev as facts, and Jev still decides the move.",
    "facts.noteNone": "the constraint rules cannot prove anything here — this move is entirely Jev's judgment.",
    "stats.decisions": "decisions",
    "stats.latency": "avg latency",
    "stats.inTokens": "input tokens",
    "stats.outTokens": "output tokens",
    "stats.sources": "by source",
    "stats.errors": "errors",
    "log.clear": "clear",
    "src.jev": "Jev's Choice",
    "src.noul": "Jev: safe",
    "src.noul_flag": "Jev: a mine",
    "src.noul_guess": "Jev: lowest p",
    "src.solver": "code-proved",
    "src.fallback": "code fallback",
    "src.override": "code override",
    "src.error": "error",
    "src.none": "—",
    "act.open": "open",
    "act.flag": "flag",
    "act.openShort": "open",
    "act.flagShort": "flag",
    "res.revealed": "revealed {n}",
    "res.hitMine": "hit a mine",
    "res.flag": "flagged",
    "res.unflag": "unflagged",
    "res.already": "already revealed",
    "res.over": "game over",
    "fact.proved_safe": "safe",
    "fact.proved_mine": "mine",
    "fact.unknown": "—",
    "cell.codeRisk": "code estimate p={p}",
    "meta.model": "model {v}",
    "meta.backend": "backend {v}",
    "meta.latency": "latency {v} ms",
    "meta.tokens": "tokens in {in} / out {out}",
    "meta.est": "state ≈{s} / questions ≈{q}",
    "meta.frontier": "frontier {v}",
    "meta.offered": "offered {v}",
    "note.paused": "paused",
    "note.noDecision": "no decision available",
    "note.failed": "request failed: {msg}",
    "note.won": "cleared",
    "note.lost": "hit a mine, game over",
    "note.ended": "{result} ({elapsed}s, {moves} moves)",
    "mode.atomic": "mode: atomic Q&A (safe≤{safe} / mine≥{mine})",
    "mode.choice": "mode: single Choice",
    "log.won": "cleared",
    "log.lost": "hit a mine",
    "log.elapsed": "{result} · {elapsed}s"
  },
};

let lang = "zh";

function t(key, vars) {
  const table = I18N[lang] || I18N.zh;
  let text = table[key] !== undefined ? table[key] : (I18N.zh[key] !== undefined ? I18N.zh[key] : key);
  if (vars) for (const [name, value] of Object.entries(vars)) text = text.split(`{${name}}`).join(String(value));
  return text;
}

function applyStatic() {
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  document.title = t("doc.title");
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  el("lang").textContent = lang === "zh" ? "EN" : "中文";
  el("lang").title = lang === "zh" ? "Switch to English" : "切换为中文";
}

function setLang(next) {
  lang = next === "en" ? "en" : "zh";
  try { localStorage.setItem("jev-lang", lang); } catch (err) { /* 隐私模式下忽略 */ }
  applyStatic();
  renderBadges();
  if (!state) return;
  renderAll();
  renderDecision();
  renderOptions();
  renderSolver();
  renderStats();
  renderLog();
}


let difficulty = "expert";
let state = null;
let lastDecision = null;      // 最近一次 /decide 的完整响应
let probabilityMap = {};      // "r3c4" -> p(雷)，来自 Jev 的 Noul
let codeMap = {};             // "r3c4" -> 代码侧风险估计（对照用）
let chosenCell = null;
let history = [];
let running = false;
let busy = false;
let timer = null;
let sessionStart = null;
let restartTimer = null;      // 自动重开的延时句柄
const RESTART_DELAY_MS = 1200;
let session = { games: 0, wins: 0, losses: 0 };   // 本会话累计（自动重开时用来看进度）
let serverConfig = null;       // /config 的响应，供徽标重绘

/* ------------------------------------------------------------------ 游戏逻辑 */

function newGame(key = difficulty) {
  difficulty = key;
  const preset = DIFFICULTIES[key];
  state = {
    rows: preset.rows,
    cols: preset.cols,
    mines: preset.mines,
    cells: Array.from({ length: preset.rows }, () => Array(preset.cols).fill(HIDDEN)),
    mineSet: null,          // 首次点击后才布雷
    flags: 0,
    moves: 0,
    status: "in_progress",
    hitCell: null,
    startedAt: null,
    endedAt: null,          // 结束时刻：计时器到此为止，不再增长
  };
  lastDecision = null;
  probabilityMap = {};
  codeMap = {};
  chosenCell = null;
  history = [];
  sessionStart = null;
  setOptionsHint(t("options.waiting"));
  renderAll();
}

/** 结束一局：冻结计时、记会话战绩（每局只记一次）。 */
function finishGame(status) {
  if (state.status === status) return;
  state.status = status;
  state.endedAt = Date.now();
  session.games++;
  if (status === "won") session.wins++;
  else session.losses++;
}


function inBounds(r, c) {
  return r >= 0 && r < state.rows && c >= 0 && c < state.cols;
}

function neighbours(r, c) {
  const out = [];
  for (let dr = -1; dr <= 1; dr++) {
    for (let dc = -1; dc <= 1; dc++) {
      if (dr === 0 && dc === 0) continue;
      if (inBounds(r + dr, c + dc)) out.push([r + dr, c + dc]);
    }
  }
  return out;
}

/** 首次点击后布雷：排除点击格及其 8 邻域（与桌面 App、Python 模拟器一致）。 */
function placeMines(safeR, safeC) {
  const forbidden = new Set([`${safeR},${safeC}`]);
  for (const [r, c] of neighbours(safeR, safeC)) forbidden.add(`${r},${c}`);
  const free = [];
  for (let r = 0; r < state.rows; r++) {
    for (let c = 0; c < state.cols; c++) {
      if (!forbidden.has(`${r},${c}`)) free.push([r, c]);
    }
  }
  // Fisher-Yates
  for (let i = free.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [free[i], free[j]] = [free[j], free[i]];
  }
  state.mineSet = new Set(free.slice(0, state.mines).map(([r, c]) => `${r},${c}`));
}

function trueValue(r, c) {
  if (state.mineSet.has(`${r},${c}`)) return MINE;
  let n = 0;
  for (const [nr, nc] of neighbours(r, c)) if (state.mineSet.has(`${nr},${nc}`)) n++;
  return n;
}

function openCell(r, c) {
  if (state.status !== "in_progress") return { changed: false, kind: "over" };
  const value = state.cells[r][c];
  if (value === FLAG || value !== HIDDEN) return { changed: false, kind: "already" };
  if (!state.mineSet) placeMines(r, c);
  if (!state.startedAt) state.startedAt = Date.now();

  state.moves++;
  if (trueValue(r, c) === MINE) {
    state.cells[r][c] = MINE;
    state.hitCell = [r, c];
    finishGame("lost");
    revealAllMines();
    return { changed: true, kind: "hitMine", count: 1, lost: true };
  }

  const revealed = flood(r, c);
  checkWin();
  return { changed: true, kind: "revealed", count: revealed, revealed };
}

function flood(startR, startC) {
  const stack = [[startR, startC]];
  const seen = new Set();
  let count = 0;
  while (stack.length) {
    const [r, c] = stack.pop();
    const key = `${r},${c}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (state.cells[r][c] !== HIDDEN) continue;
    const value = trueValue(r, c);
    state.cells[r][c] = value;
    count++;
    if (value === 0) for (const [nr, nc] of neighbours(r, c)) stack.push([nr, nc]);
  }
  return count;
}

function toggleFlag(r, c) {
  if (state.status !== "in_progress") return { changed: false, kind: "over" };
  const value = state.cells[r][c];
  if (value === HIDDEN) {
    state.cells[r][c] = FLAG;
    state.flags++;
    state.moves++;
    return { changed: true, kind: "flag" };
  }
  if (value === FLAG) {
    state.cells[r][c] = HIDDEN;
    state.flags--;
    state.moves++;
    return { changed: true, kind: "unflag" };
  }
  return { changed: false, kind: "already" };
}

/** 把一次落子的结构化结果翻译成当前语言的文字。 */
function resultText(entry) {
  const key = `res.${entry.kind}`;
  if (entry.kind === "revealed") return t(key, { n: entry.count });
  return I18N.zh[key] !== undefined ? t(key) : (entry.result || "");
}

function checkWin() {
  let revealed = 0;
  for (let r = 0; r < state.rows; r++) {
    for (let c = 0; c < state.cols; c++) if (state.cells[r][c] !== HIDDEN && state.cells[r][c] !== FLAG) revealed++;
  }
  if (revealed >= state.rows * state.cols - state.mines) finishGame("won");
}

function revealAllMines() {
  for (let r = 0; r < state.rows; r++) {
    for (let c = 0; c < state.cols; c++) {
      if (state.mineSet.has(`${r},${c}`) && state.cells[r][c] === HIDDEN) state.cells[r][c] = MINE;
    }
  }
}

/* ---------------------------------------------------------------- 渲染 */

function renderAll() {
  renderBoard();
  renderFoot();
}

function renderBoard() {
  const board = el("board");
  board.style.gridTemplateColumns = `repeat(${state.cols}, var(--cell))`;
  board.innerHTML = "";

  for (let r = 0; r < state.rows; r++) {
    for (let c = 0; c < state.cols; c++) {
      const value = state.cells[r][c];
      const key = `r${r}c${c}`;
      const div = document.createElement("div");
      div.className = "cell";
      div.dataset.r = r;
      div.dataset.c = c;

      if (value === HIDDEN || value === FLAG) {
        div.classList.add("hidden");
        if (value === FLAG) div.classList.add("flag");
        const p = probabilityMap[key];
        if (p !== undefined) {
          div.classList.add("prob");
          div.style.background = riskColour(p);
          if (el("show-prob").checked) {
            const span = document.createElement("span");
            span.className = "p";
            span.textContent = p.toFixed(2).slice(1);
            div.appendChild(span);
          }
        }
        if (el("show-code").checked && codeMap[key] !== undefined) {
          div.title = t("cell.codeRisk", { p: codeMap[key].toFixed(3) });
          div.style.outline = `1px dashed ${riskColour(codeMap[key])}`;
          div.style.outlineOffset = "-1px";
        }
      } else if (value === MINE) {
        div.classList.add("open", "mine");
        if (state.hitCell && state.hitCell[0] === r && state.hitCell[1] === c) div.classList.add("hit");
      } else {
        div.classList.add("open", `n${value}`);
        div.textContent = value === 0 ? "" : String(value);
      }

      if (chosenCell && chosenCell[0] === r && chosenCell[1] === c) div.classList.add("chosen");
      board.appendChild(div);
    }
  }
}

function riskColour(p) {
  // 绿 -> 黄 -> 红
  const clamped = Math.max(0, Math.min(1, p));
  const hue = (1 - clamped) * 130;
  return `hsl(${hue}, 45%, 34%)`;
}

function renderFoot() {
  let revealed = 0;
  for (let r = 0; r < state.rows; r++) {
    for (let c = 0; c < state.cols; c++) {
      const v = state.cells[r][c];
      if (v !== HIDDEN && v !== FLAG && v !== MINE) revealed++;
    }
  }
  const status = el("game-status");
  const label = {
    won: t("status.won"),
    lost: t("status.lost"),
    in_progress: state.moves ? t("status.running") : t("status.idle"),
  };
  status.textContent = label[state.status] || state.status;
  const nextClass = state.status === "won" ? "won" : state.status === "lost" ? "lost" : state.moves ? "running" : "idle";
  // 只在变化时赋值，否则每 500ms 重刷会重播一次高亮动画
  if (status.className !== nextClass) status.className = nextClass;

  el("revealed").textContent = `${revealed}/${state.rows * state.cols - state.mines}`;
  el("flags").textContent = state.flags;
  el("mines-left").textContent = Math.max(state.mines - state.flags, 0);
  el("moves").textContent = state.moves;

  // 通关/踩雷后计时停住：用结束时刻，而不是此刻
  const until = state.endedAt || Date.now();
  el("elapsed").textContent = state.startedAt ? `${((until - state.startedAt) / 1000).toFixed(1)}s` : "0.0s";

  el("session").textContent = session.games
    ? t("session.line", { games: session.games, wins: session.wins, losses: session.losses })
    : t("session.line", { games: 0, wins: 0, losses: 0 });
}

function renderDecision() {
  const d = lastDecision;
  const badge = el("source-badge");
  if (!d) {
    badge.textContent = "—";
    badge.className = "source";
    return;
  }
  badge.textContent = I18N.zh[`src.${d.source}`] ? t(`src.${d.source}`) : d.source;
  badge.className = `source ${d.source}`;

  el("decision-action").textContent = d.action
    ? `${d.action === "open" ? t("act.open") : t("act.flag")} ${d.cell_name}`
    : t("decision.none");
  el("decision-note").textContent = d.note || "";

  setMeter("confidence", d.confidence);
  setMeter("risk", d.risk);
  setMeter("danger", d.danger === null || d.danger === undefined ? null : d.danger / 2);

  const tok = d.tokens || {};
  const u = d.usage || {};
  el("decision-meta").innerHTML = [
    t("meta.model", { v: d.model || "-" }),
    t("meta.backend", { v: d.backend || "-" }),
    t("meta.latency", { v: d.latency_ms ?? "-" }),
    t("meta.tokens", { in: u.input_tokens ?? "-", out: u.output_tokens ?? "-" }),
    t("meta.est", { s: tok.state ?? "-", q: tok.questions ?? "-" }),
    t("meta.frontier", { v: d.candidates ?? "-" }),
    t("meta.offered", { v: (d.action_space || []).length }),
  ].map((x) => `<span>${x}</span>`).join("");
}

function setMeter(name, value) {
  const bar = el(`bar-${name}`);
  const val = el(`val-${name}`);
  if (value === null || value === undefined) {
    bar.style.width = "0%";
    val.textContent = "—";
    return;
  }
  bar.style.width = `${Math.max(0, Math.min(1, value)) * 100}%`;
  val.textContent = value.toFixed(2);
}

function renderOptions() {
  const box = el("options");
  const d = lastDecision;
  if (!d || !d.options || !d.options.length) {
    box.innerHTML = `<p class="empty">${t("options.empty")}</p>`;
    setOptionsHint("—");
    return;
  }
  setOptionsHint(t("options.hint", {
    shown: d.options.length,
    total: Object.keys(d.choice_answer?.probabilities || {}).length,
    cells: (d.action_space || []).length,
  }));
  const head = `<div class="opt head"><span class="name">${t("options.head.option")}</span><span></span>` +
    `<span class="num">${t("options.head.prob")}</span><span class="num">${t("options.head.risk")}</span>` +
    `<span class="num">${t("options.head.code")}</span>` +
    `<span class="num">${t("options.head.fact")}</span></div>`;
  box.innerHTML = head + d.options.map((o) => `
    <div class="opt ${o.chosen ? "is-chosen" : ""}">
      <span class="name ${o.action}">${o.action === "open" ? t("act.openShort") : t("act.flagShort")} ${o.cell}</span>
      <span class="bar"><i style="width:${(o.probability * 100).toFixed(1)}%"></i></span>
      <span class="num">${(o.probability * 100).toFixed(1)}%</span>
      <span class="num">${o.jev_risk === null ? "—" : o.jev_risk.toFixed(2)}</span>
      <span class="num">${o.code_risk === null ? "—" : o.code_risk.toFixed(2)}</span>
      <span class="num ${o.status}">${I18N.zh[`fact.${o.status}`] ? t(`fact.${o.status}`) : "—"}</span>
    </div>`).join("");
}

function setOptionsHint(text) {
  el("options-hint").textContent = text;
}

function renderSolver() {
  const box = el("solver-view");
  const d = lastDecision;
  if (!d || !d.solver_view) {
    box.innerHTML = '<p class="empty">—</p>';
    return;
  }
  const sv = d.solver_view;
  const facts = d.derived_facts || {};
  const safe = facts.proved_safe_cells || sv.proved_safe || [];
  const mines = facts.proved_mine_cells || sv.proved_mines || [];
  const note = sv.solved ? t("facts.noteSolved") : t("facts.noteNone");
  box.innerHTML = `
    <p>${note}</p>
    <p>${t("facts.provedSafe", { n: safe.length })}<code>${safe.length ? safe.join(" ") : t("facts.none")}</code></p>
    <p>${t("facts.provedMines", { n: mines.length })}<code>${mines.length ? mines.join(" ") : t("facts.none")}</code></p>`;
}

function renderStats() {
  const d = lastDecision;
  const s = d?.stats;
  const box = el("stats");
  if (!s) {
    box.innerHTML = `<div><span>${t("stats.decisions")}</span><b>0</b></div>`;
    return;
  }
  const sources = Object.entries(s.by_source || {})
    .map(([k, v]) => `${I18N.zh[`src.${k}`] ? t(`src.${k}`) : k} ${v}`)
    .join(" · ") || "—";
  box.innerHTML = `
    <div><span>${t("stats.decisions")}</span><b>${s.decisions}</b></div>
    <div><span>${t("stats.latency")}</span><b>${s.avg_latency_ms} ms</b></div>
    <div><span>${t("stats.inTokens")}</span><b>${s.input_tokens}</b></div>
    <div><span>${t("stats.outTokens")}</span><b>${s.output_tokens}</b></div>
    <div><span>${t("stats.sources")}</span><b>${sources}</b></div>
    <div><span>${t("stats.errors")}</span><b>${s.errors}</b></div>`;
}

function renderLog() {
  const box = el("log");
  box.innerHTML = history.slice(-60).map((h) => {
    const ended = h.end ? `${h.end === "won" ? t("log.won") : t("log.lost")} · ${h.elapsed}s` : "";
    const body = h.end
      ? ended
      : `${h.action === "open" ? t("act.openShort") : t("act.flagShort")} ${h.cell}`;
    const outcome = h.end ? "" : resultText(h);
    const source = h.end ? "" : `<em class="source ${h.source}">${I18N.zh[`src.${h.source}`] ? t(`src.${h.source}`) : h.source}</em>`;
    return `
    <li>
      <span class="idx">${h.step}</span>
      <span>${body} ${source}</span>
      <span class="outcome ${h.outcomeClass || ""}">${outcome}</span>
    </li>`;
  }).join("");
  box.scrollTop = box.scrollHeight;
}

/* ------------------------------------------------------------ Jev 决策回路 */

function boardPayload() {
  return {
    rows: state.rows,
    cols: state.cols,
    mines: state.mines,
    cells: state.cells,
    moves: state.moves,
    status: state.status,
    step: state.moves,
    history: history.slice(-6).map((h) => ({
      step: h.step, action: h.action, cell: h.cell, source: h.source, result: h.result,
    })),
  };
}

async function askJev() {
  const res = await fetch("/decide", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(boardPayload()),
  });
  if (!res.ok) throw new Error(`/decide HTTP ${res.status}`);
  return res.json();
}

async function step() {
  if (busy || state.status !== "in_progress") return;
  busy = true;
  el("conn-dot").className = "dot";
  try {
    const decision = await askJev();
    lastDecision = decision;
    probabilityMap = decision.nouls || {};
    codeMap = {};
    for (const option of decision.options || []) {
      if (option.code_risk !== null && option.code_risk !== undefined) codeMap[option.cell] = option.code_risk;
    }
    chosenCell = decision.cell || null;
    renderDecision();
    renderOptions();
    renderSolver();
    renderStats();
    el("conn-dot").className = "dot ok";

    if (!decision.action) {
      stopAuto(decision.note || t("note.noDecision"));
      renderAll();
      return;
    }

    const [r, c] = decision.cell;
    const result = decision.action === "open" ? openCell(r, c) : toggleFlag(r, c);
    history.push({
      step: history.length + 1,
      action: decision.action,
      cell: decision.cell_name,
      source: decision.source,
      kind: result.kind,
      count: result.count,
      outcomeClass: state.status === "won" ? "win" : state.status === "lost" ? "lose" : "",
    });
    renderAll();
    renderLog();

    if (state.status !== "in_progress") {
      const won = state.status === "won";
      const elapsed = state.startedAt ? ((state.endedAt - state.startedAt) / 1000).toFixed(1) : "0.0";
      stopAuto(t("note.ended", {
        result: won ? t("note.won") : t("note.lost"), elapsed, moves: state.moves,
      }));
      history.push({
        step: history.length + 1, action: "—", cell: "—", source: "none",
        end: won ? "won" : "lost", elapsed, outcomeClass: won ? "win" : "lose",
      });
      renderAll();
      renderLog();
      scheduleRestart(won);
    }
  } catch (err) {
    el("conn-dot").className = "dot err";
    el("decision-note").textContent = t("note.failed", { msg: err.message });
    stopAuto(t("note.failed", { msg: err.message }));
  } finally {
    busy = false;
  }
}

function clearRestart() {
  if (restartTimer) {
    clearTimeout(restartTimer);
    restartTimer = null;
  }
}

/** 一局结束后按开关自动重开：新局会重新计时（startedAt 归零）。 */
function scheduleRestart(won) {
  const enabled = won ? el("restart-win").checked : el("restart-loss").checked;
  if (!enabled) return;
  restartTimer = setTimeout(() => {
    restartTimer = null;
    newGame(difficulty);
    running = true;
    el("autopilot").textContent = t("btn.stop");
    el("autopilot").classList.add("is-running");
    loop();
  }, RESTART_DELAY_MS);
}

function startAuto() {
  clearRestart();
  if (state.status !== "in_progress") newGame(difficulty);
  running = true;
  el("autopilot").textContent = t("btn.stop");
  el("autopilot").classList.add("is-running");
  loop();
}

function stopAuto(reason) {
  clearRestart();
  running = false;
  el("autopilot").textContent = t("btn.start");
  el("autopilot").classList.remove("is-running");
  if (reason) el("decision-note").textContent = reason;
}

function loop() {
  if (!running) return;
  const delay = Number(el("speed").value);
  timer = setTimeout(async () => {
    await step();
    if (running) loop();
  }, delay);
}

/* ------------------------------------------------------------------ 交互 */

function bind() {
  document.querySelectorAll("[data-difficulty]").forEach((btn) => {
    btn.addEventListener("click", () => {
      stopAuto();
      document.querySelectorAll("[data-difficulty]").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      newGame(btn.dataset.difficulty);
    });
  });

  el("autopilot").addEventListener("click", () => (running ? stopAuto(t("note.paused")) : startAuto()));
  el("lang").addEventListener("click", () => setLang(lang === "zh" ? "en" : "zh"));
  el("step").addEventListener("click", () => { stopAuto(); step(); });
  el("reset").addEventListener("click", () => { stopAuto(); newGame(difficulty); });
  el("clear-log").addEventListener("click", () => { history = []; renderLog(); });
  el("show-prob").addEventListener("change", renderBoard);
  el("show-code").addEventListener("change", renderBoard);

  el("board").addEventListener("contextmenu", (event) => {
    event.preventDefault();
    const cell = event.target.closest(".cell");
    if (!cell || running) return;
    const result = toggleFlag(Number(cell.dataset.r), Number(cell.dataset.c));
    if (result.changed) renderAll();
  });

  el("board").addEventListener("click", (event) => {
    const cell = event.target.closest(".cell");
    if (!cell || running) return;
    const result = openCell(Number(cell.dataset.r), Number(cell.dataset.c));
    if (result.changed) { renderAll(); renderFoot(); }
  });

  setInterval(renderFoot, 500);
}

/** 顶栏两个徽标：后端/模型 + 决策模式（语言切换时要重画）。 */
function renderBadges() {
  const cfg = serverConfig;
  if (!cfg) return;
  el("backend-badge").textContent = `${cfg.backend} · ${cfg.model}`;
  const thresholds = cfg.thresholds || {};
  el("mode-badge").textContent = cfg.decision_mode === "atomic"
    ? t("mode.atomic", { safe: thresholds.safe, mine: thresholds.mine })
    : t("mode.choice");
  el("mode-badge").title = `solver=${cfg.solver_mode} override=${cfg.safety_override}`;
}

async function boot() {
  try {
    lang = localStorage.getItem("jev-lang") === "en" ? "en" : "zh";   // 记住上次选的语言
  } catch (err) { /* 隐私模式下拿不到 localStorage */ }
  applyStatic();

  try {
    const cfg = await (await fetch("/config")).json();
    serverConfig = cfg;
    renderBadges();
    el("conn-dot").className = "dot ok";
    if (cfg.board && DIFFICULTIES[difficulty] && cfg.board.cols !== DIFFICULTIES[difficulty].cols) {
      // 服务端默认棋盘与页面默认难度不一致时，跟随服务端
      for (const [key, preset] of Object.entries(DIFFICULTIES)) {
        if (preset.rows === cfg.board.rows && preset.cols === cfg.board.cols) {
          difficulty = key;
          document.querySelectorAll("[data-difficulty]").forEach((b) =>
            b.classList.toggle("is-active", b.dataset.difficulty === key));
        }
      }
    }
  } catch (err) {
    el("backend-badge").textContent = t("badge.unready");
    el("conn-dot").className = "dot err";
  }
  newGame(difficulty);
  bind();
  renderStats();
}

boot();
