/* 网页版游戏逻辑的自动化测试（Node 直接跑真实 app.js，用最小 DOM 桩）。
 *
 *   node web/test/game-harness.js
 *
 * 覆盖：通关/踩雷后计时冻结、踩雷自动重开并重新计时、开关关掉就不重开、
 *       结束状态下按"开始自动操作"会先开新局、状态高亮类名随状态变化。
 * 退出码 0 = 全过。
 */
/* 用 Node 跑真实 app.js 的游戏逻辑与自动重开回路（最小 DOM 桩）。
 * 验证：通关后计时停住 / 踩雷后自动重开并重新计时 / 关掉开关就不重开 / 状态高亮。
 */
const fs = require("fs");

const ids = {};
function makeEl(id) {
  return {
    id, textContent: "", innerHTML: "", className: "", title: "", style: {}, dataset: {},
    checked: false, value: "0", children: [], scrollTop: 0, scrollHeight: 0,
    classList: {
      _s: new Set(),
      add(...c) { c.forEach((x) => this._s.add(x)); },
      remove(...c) { c.forEach((x) => this._s.delete(x)); },
      contains(c) { return this._s.has(c); },
    },
    appendChild(c) { this.children.push(c); return c; },
    addEventListener() {},
    querySelectorAll() { return []; },
    closest() { return null; },
  };
}
// index.html 里用到的所有 i18n 键（用来造桩元素并核对字典完整性）
const htmlKeys = [...new Set(
  [...fs.readFileSync("web/index.html", "utf8").matchAll(/data-i18n="([^"]+)"/g)].map((m) => m[1])
)];
const i18nNodes = htmlKeys.map((key) => { const n = makeEl(`i18n:${key}`); n.dataset.i18n = key; return n; });

const document = {
  documentElement: makeEl("html"),
  getElementById(id) { if (!ids[id]) ids[id] = makeEl(id); return ids[id]; },
  createElement() { return makeEl("dyn"); },
  querySelectorAll(sel) { return sel === "[data-i18n]" ? i18nNodes : []; },
  addEventListener() {},
};
const i18nText = (key) => (i18nNodes.find((n) => n.dataset.i18n === key) || {}).textContent;

globalThis.localStorage = {
  store: {},
  getItem(k) { return this.store[k] === undefined ? null : this.store[k]; },
  setItem(k, v) { this.store[k] = String(v); },
};
const E = (id) => document.getElementById(id);

let decideCalls = 0;
async function fetch(url) {
  if (url === "/config") {
    return { ok: true, json: async () => ({ backend: "stub", model: "stub", decision_mode: "atomic", thresholds: {}, solver_mode: "off", safety_override: false, board: { rows: 16, cols: 16, mines: 40 } }) };
  }
  decideCalls++;
  const st = T.getState();
  const hidden = [];
  for (let r = 0; r < st.rows; r++) for (let c = 0; c < st.cols; c++) if (st.cells[r][c] === 7) hidden.push([r, c]);
  const cell = hidden[Math.floor(Math.random() * hidden.length)];
  return {
    ok: true,
    json: async () => ({
      action: "open", cell, cell_name: `r${cell[0]}c${cell[1]}`, source: "jev",
      confidence: 0.5, risk: 0.1, danger: 1, note: "stub", latency_ms: 1,
      usage: { input_tokens: 1, output_tokens: 1 }, model: "stub", backend: "stub",
      nouls: {}, options: [], choice_answer: {}, tokens: { state: 1, questions: 1 },
      candidates: 1, board_rows: [], solver_view: { proved_safe: [], proved_mines: [], note: "" },
      derived_facts: {}, action_space: [], stats: {},
    }),
  };
}

const code = fs.readFileSync("web/app.js", "utf8");
new Function("document", "fetch", code + `
;globalThis.T = {
  newGame, openCell, step, startAuto, stopAuto, scheduleRestart, clearRestart, renderFoot, trueValue,
  getState: () => state, getSession: () => session, isRunning: () => running,
  restartPending: () => restartTimer !== null, HIDDEN, FLAG, MINE,
  I18N, t, setLang, getLang: () => lang, resultText,
};`)(document, fetch);
const T = globalThis.T;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ok = (cond, msg) => { console.log(`${cond ? "PASS" : "FAIL"}  ${msg}`); if (!cond) process.exitCode = 1; };

/** 一步步开到结束；步间留一点间隔，让"用时"可测。 */
async function playToEnd(limit = 600) {
  let n = 0;
  while (T.getState().status === "in_progress" && n++ < limit) {
    await T.step();
    await sleep(25);
  }
  return T.getState();
}

(async () => {
  await sleep(80);                       // 等 app.js 末尾的 boot() 跑完（newGame + bind）
  E("speed").value = "3000";             // 慢速：重开后不会马上又走棋，断言才稳定

  // ---------- 1. 通关后计时停住 + 状态高亮 ----------
  T.newGame("hard");
  const st = T.getState();
  T.openCell(0, 0);
  ok(st.startedAt !== null, "首步开始计时");
  await sleep(180);
  for (let r = 0; r < st.rows && st.status === "in_progress"; r++) {
    for (let c = 0; c < st.cols && st.status === "in_progress"; c++) {
      if (st.cells[r][c] === T.HIDDEN && T.trueValue(r, c) !== T.MINE) T.openCell(r, c);
    }
  }
  ok(st.status === "won", `开完所有安全格 → 通关（实际 ${st.status}）`);
  T.renderFoot();
  const t1 = E("elapsed").textContent, s1 = E("game-status").textContent, c1 = E("game-status").className;
  await sleep(700);
  T.renderFoot();
  ok(E("elapsed").textContent === t1 && t1 !== "0.0s", `通关后计时冻结（${t1}）`);
  ok(s1 === "通关" && c1 === "won", `状态高亮为通关（${s1} / class=${c1}）`);
  ok(E("session").textContent.includes("胜 1"), `会话战绩累计（${E("session").textContent}）`);

  // ---------- 2. 踩雷 → 自动重开 + 重新计时 ----------
  T.newGame("hard");
  E("restart-loss").checked = true;
  const before = T.getSession().games;
  const oldState = T.getState();
  const lost = await playToEnd();
  ok(lost.status === "lost", `随机开格最终踩雷（${lost.moves} 步）`);
  T.renderFoot();
  ok(E("game-status").className === "lost", "状态高亮为踩雷");
  const frozen = E("elapsed").textContent;
  await sleep(300);
  T.renderFoot();
  ok(E("elapsed").textContent === frozen, `踩雷后计时也冻结（${frozen}）`);
  ok(T.getSession().games === before + 1, "本局计入会话战绩");
  ok(!T.isRunning(), "结束后自动操作暂停，等待重开");
  ok(T.restartPending(), "已排定一次自动重开");

  await sleep(1400);
  const fresh = T.getState();
  ok(fresh !== oldState, "踩雷后换了新棋盘（自动重开）");
  ok(fresh.status === "in_progress" && fresh.moves === 0, `新局从零开始（moves=${fresh.moves}）`);
  ok(fresh.startedAt === null, "新局计时归零，等首步重新起表");
  ok(T.isRunning(), "自动操作继续运行");

  // ---------- 3. 关掉开关：不重开 ----------
  T.stopAuto();
  E("restart-loss").checked = false;
  T.newGame("hard");
  const gamesBefore = T.getSession().games;
  const stateBefore = T.getState();
  await playToEnd();
  await sleep(1500);
  ok(T.getSession().games === gamesBefore + 1, "只结束一局，没有重开");
  ok(T.getState() === stateBefore, "棋盘仍停在结束的那一局");
  ok(!T.restartPending(), "没有排定重开");

  // ---------- 4. 结束时按"开始自动操作"会先重开 ----------
  T.stopAuto();
  E("restart-win").checked = true;
  E("restart-loss").checked = false;
  T.newGame("hard");
  const st4 = T.getState();
  T.openCell(0, 0);
  for (let r = 0; r < st4.rows && st4.status === "in_progress"; r++) {
    for (let c = 0; c < st4.cols && st4.status === "in_progress"; c++) {
      if (st4.cells[r][c] === T.HIDDEN && T.trueValue(r, c) !== T.MINE) T.openCell(r, c);
    }
  }
  ok(st4.status === "won", "第二局同样通关");
  T.startAuto();
  ok(T.getState().status === "in_progress" && T.getState().moves === 0, "手动开始时自动开了新局");
  T.stopAuto();

  // ---------- 5. 中英文切换 ----------
  ok(htmlKeys.length >= 35, `index.html 有 ${htmlKeys.length} 处 data-i18n 标记`);
  ok(Object.keys(T.I18N.zh).length === Object.keys(T.I18N.en).length,
     `中英字典键数一致（${Object.keys(T.I18N.zh).length} / ${Object.keys(T.I18N.en).length}）`);
  const symDiff = Object.keys(T.I18N.zh).filter((k) => T.I18N.en[k] === undefined)
    .concat(Object.keys(T.I18N.en).filter((k) => T.I18N.zh[k] === undefined));
  ok(symDiff.length === 0, `两种语言键集合相同${symDiff.length ? "（差异：" + symDiff.join(",") + "）" : ""}`);
  const missing = htmlKeys.filter((k) => T.I18N.zh[k] === undefined || T.I18N.en[k] === undefined);
  ok(missing.length === 0, `HTML 用到的键在两种语言里都存在${missing.length ? "（缺：" + missing.join(",") + "）" : ""}`);
  const badVars = Object.keys(T.I18N.zh).filter((k) => {
    const vars = (s) => (s.match(/\{\w+\}/g) || []).sort().join(",");
    return vars(T.I18N.zh[k]) !== vars(T.I18N.en[k]);
  });
  ok(badVars.length === 0, `中英占位符一一对应${badVars.length ? "（不一致：" + badVars.join(",") + "）" : ""}`);

  T.newGame("hard");
  T.openCell(0, 0);
  T.setLang("en");
  ok(T.getLang() === "en", "切换到英文");
  ok(i18nText("btn.start") === "▶ Start auto play", `静态按钮文案变英文（${i18nText("btn.start")}）`);
  ok(i18nText("foot.status") === "status", `表头变英文（${i18nText("foot.status")}）`);
  ok(E("game-status").textContent === T.I18N.en["status.running"], `状态跟随语言（${E("game-status").textContent}）`);
  ok(E("session").textContent.includes("games"), `会话统计跟随语言（${E("session").textContent}）`);
  ok(E("lang").textContent === "中文", "切换按钮显示目标语言");
  ok(globalThis.localStorage.getItem("jev-lang") === "en", "语言选择被记住");

  T.setLang("zh");
  ok(i18nText("btn.start") === "▶ 开始自动操作", "切回中文");
  ok(E("game-status").textContent === T.I18N.zh["status.running"], `状态切回中文（${E("game-status").textContent}）`);
  ok(T.t("options.hint", { shown: 3, total: 9, cells: 5 }).includes("3") &&
     T.t("options.hint", { shown: 3, total: 9, cells: 5 }).includes("5"), "占位符替换正常");
  ok(T.t("nonexistent.key") === "nonexistent.key", "缺键时回退成键名而不是报错");

  console.log(`\n完成（/decide 调用 ${decideCalls} 次）`);
  process.exit(process.exitCode || 0);   // bind() 里的 setInterval 会吊住进程
})();
