// The small touches (touches.css), on every page after app.js. Keys borrowed from CS: hold Tab for this week's
// scoreboard, ~ for the console, hold B for the quick wheel, ? for all of them. The glow takes the colour of the
// map being rendered, a new Ace gets its moment, Status has time-aware and loading lines, Demos rows drag away to
// skip, and Reels scrub under the pointer. Settings -> Touches turns them on and off.
"use strict";

(() => {
const PAGE = location.pathname;
const PC = onThisPC();
const PAGES = [["/status", "Status"], ["/demos", "Demos to grab"], ["/reels", "Reels"], ["/settings", "Settings"]];
const MAP_TINT = MAP_TINTS;        // app.js: the map tiles wear the same colours
const STRIP_FRAMES = 12;           // as many frames as the app cuts into a Reel's strip (web.py, STRIP_FRAMES)

// --- what Settings says, remembered for the next page so it applies before the app answers ------------------
const store = {
  get(key, fallback) { try { const v = sessionStorage.getItem("t-" + key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
  set(key, value) { try { sessionStorage.setItem("t-" + key, JSON.stringify(value)); } catch (e) { /* private window */ } },
};
const prefs = Object.assign({ui_sounds: false, map_glow: true, calm_mode: false, keyboard_shortcuts: true}, store.get("prefs", {}));
const quiet = (now = new Date()) => now.getHours() >= 22 || now.getHours() < 5;
const stillness = () => prefs.calm_mode || matchMedia("(prefers-reduced-motion: reduce)").matches;

function applyPrefs() {
  document.body.classList.toggle("t-calm", prefs.calm_mode);
  document.body.classList.toggle("t-night", quiet());
  paintGlow();
}
// Not /api/settings: that one mints a new FACEIT sign-in state each time, which would undo a sign-in under way.
async function loadPrefs() {
  if (!PC) return;
  try {
    const r = await api("/api/touches");
    if (r.ok) {
      const values = await r.json();
      Object.keys(prefs).forEach(k => { if (typeof values[k] === "boolean") prefs[k] = values[k]; });
      store.set("prefs", prefs);
    }
  } catch (e) { /* keep what was remembered */ }
  applyPrefs();
}
addEventListener("clipper:settings", loadPrefs);

const el = (tag, cls, html = "") => { const n = document.createElement(tag); n.className = cls; n.innerHTML = html; return n; };
const kc = k => `<kbd class="t-kc">${esc(k)}</kbd>`;      // not a span: `.t-kr span:first-child` is the keys' box
const getJSON = async path => { const r = await api(path); if (!r.ok) throw new Error(String(r.status)); return r.json(); };
const errorOf = async r => { try { return (await r.json()).error || ""; } catch (e) { return ""; } };
const go = path => { location.href = path; };
const clock = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const say = (text, ok = true) => toast(`${ok ? ICON.check : ICON.info}<span>${esc(text)}</span>`, 3600);

// --- UI sounds: a soft click, never in quiet hours ----------------------------------------------------------
let audio = null;
function blip(kind = "tick") {
  if (!prefs.ui_sounds || quiet()) return;
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    const notes = kind === "ace" ? [660, 880, 1320] : kind === "open" ? [520] : [880];
    notes.forEach((f, i) => {
      const t = audio.currentTime + i * 0.07, o = audio.createOscillator(), g = audio.createGain();
      o.type = "triangle";
      o.frequency.setValueAtTime(f, t);
      o.frequency.exponentialRampToValueAtTime(f * 0.36, t + 0.08);
      g.gain.setValueAtTime(kind === "ace" ? 0.12 : 0.07, t);
      g.gain.exponentialRampToValueAtTime(0.001, t + 0.12);
      o.connect(g).connect(audio.destination);
      o.start(t); o.stop(t + 0.13);
    });
  } catch (e) { /* no sound here */ }
}
document.addEventListener("click", e => { if (e.target.closest?.("button, a.primary, a.ghost, .tab")) blip(); }, true);

// --- the map glow: a soft wash in the colour of the map being rendered ---------------------------------------
let renderingMap = null;
const glow = el("div", "t-glow", "<i></i><i></i>");
glow.setAttribute("aria-hidden", "true");
document.body.appendChild(glow);
function paintGlow() {
  const on = prefs.map_glow && renderingMap !== null;
  if (on) document.documentElement.style.setProperty("--tint", MAP_TINT[renderingMap] || "var(--accent)");
  document.documentElement.style.setProperty("--tint-op", on ? "1" : "0");      // the tint stays while it fades
}
addEventListener("clipper:summary", e => { renderingMap = e.detail.rendering ? e.detail.map || "" : null; paintGlow(); });
addEventListener("clipper:status", e => { renderingMap = e.detail.rendering ? e.detail.rendering.map || "" : null; paintGlow(); });

// --- the Ace moment: Nugget crowing by an ACE!! burst, shards and a toast, only for a new Ace --------------------
const SHARDS = [[-290, -170, 200, "#FF5500"], [260, -190, -160, "#F2EFEA"], [-330, 70, 320, "#FFB27A"], [310, 100, -280, "#FF5500"],
  [-90, -260, 120, "#F2EFEA"], [140, -280, -220, "#FF8A3D"], [-220, 210, 260, "#FF5500"], [240, 240, -300, "#F2EFEA"],
  [0, 290, 180, "#FFB27A"], [360, -20, 90, "#FF5500"]];
function aceMoment(ace, count) {
  const text = count > 1 ? `${count} new Aces are in your Reels` : `${ace.map} Ace is in your Reels`;
  toast(`${ICON.check}<span>${esc(text)}</span>${ace.reel !== null ? `<a class="primary dark sm" href="/reels?play=${+ace.reel}">Watch</a>` : ""}`, 6500);
  dispatchEvent(new CustomEvent("clipper:ace", {detail: {map: ace.map, round: ace.round, count}}));     // Nugget crows on Status
  blip("ace");
  if (quiet() || stillness()) return;                  // quiet hours and calm mode get the toast alone
  const parts = [el("div", "t-acefl"), el("div", "t-acest", NUGGET.burst("ACE!!")), el("div", "t-acenug", NUGGET.svg("ace", "Nugget crowing")),
    ...SHARDS.map(([dx, dy, r, c]) => { const s = el("i", "t-shard"); s.style.cssText = `--dx:${dx}px;--dy:${dy}px;--r:${r}deg;background:${c}`; return s; })];
  parts.forEach(p => { p.setAttribute("aria-hidden", "true"); document.body.appendChild(p); });
  setTimeout(() => parts.forEach(p => p.remove()), 2800);
}
// The Aces already in Reels when a window first looks are not news; the ones that come after are, once each.
function watchAces() {
  let seen = store.get("aces", null);
  const poll = async () => {
    try {
      const aces = [];
      (await getJSON("/api/reels")).forEach(m => m.highlights.forEach(h => {
        if (["ACE", "5K"].includes(String(h.type).toUpperCase())) aces.push({key: `${m.checksum}-${h.round}`, map: m.map, round: h.round, reel: h.player ?? h.enemy});
      }));
      if (seen === null) seen = aces.map(a => a.key);
      const fresh = aces.filter(a => !seen.includes(a.key));
      if (fresh.length) { seen.push(...fresh.map(a => a.key)); aceMoment(fresh[0], fresh.length); }
      store.set("aces", seen);
    } catch (e) { /* the app is busy or has gone */ }
    setTimeout(poll, 10000);
  };
  poll();
}

// --- overlays: the scoreboard, the quick wheel and the shortcut sheet share one layer ---------------------------
function layer(label, html) {
  const n = el("div", "t-layer", html);
  n.setAttribute("role", "dialog");
  n.setAttribute("aria-label", label);
  document.body.appendChild(n);
  return n;
}

// The scoreboard (hold Tab): this week's matches, from /api/week.
let board = null, week = null, weekAt = 0;
async function loadWeek(fresh) {
  if (!week || fresh || Date.now() - weekAt > 15000) { week = await getJSON("/api/week"); weekAt = Date.now(); }
  return week;
}
function played(iso) {
  const d = new Date(iso), days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(iso).setHours(0, 0, 0, 0)) / 86400000);
  return days === 0 ? "Today" : days === 1 ? "Yesterday" : d.toLocaleDateString([], {weekday: "short"});
}
const tile = map => `<span class="t-tile" style="background:${MAP_TINT[map] || "#5A5650"}">${mapAbbr(map)}</span>`;
function boardRow(r, i) {
  const pips = Array.from({length: 4}, (_, j) => `<i${j < r.highlights ? "" : ' class="o"'}></i>`).join("");
  const tone = r.tone === "done" ? " g" : r.tone === "off" ? " s" : "";
  return `<div class="t-r${r.ace ? " mvp" : ""}" style="animation-delay:${i * 22}ms">
    <span class="t-map">${tile(r.map)}${esc(r.map)}${r.ace ? '<span class="t-ace">ACE</span>' : ""}</span>
    <span>${esc(played(r.played_at))}</span><span class="${r.won ? "t-w" : "t-l"}">${esc(r.score)}</span><span>${esc(r.kd || "–")}</span>
    <span class="t-pips" title="${esc(plural(r.highlights, "Highlight"))}">${pips}</span>
    <span>${r.status ? `<span class="t-st${tone}">${esc(r.status)}</span>` : ""}</span></div>`;
}
function fillBoard(data) {
  if (!board) return;
  const sub = board.querySelector(".t-sbh span"), rows = board.querySelector(".t-rows");
  if (!data) { sub.textContent = "Can't reach CS2 Clipper right now."; return; }
  sub.textContent = `${plural(data.matches, "match", "matches")} · ${plural(data.highlights, "Highlight")} · ${plural(data.aces, "Ace")}`;
  rows.innerHTML = data.rows.length ? data.rows.map(boardRow).join("")
    : '<div class="t-r" style="grid-template-columns:1fr;color:#8E8B86">No matches in the last 7 days. Go queue.</div>';
}
function showBoard() {
  if (board) return;
  board = layer("This week", `<div class="t-sb"><div class="t-sbh"><h2>This week</h2><span>Loading…</span><em>Let go of ${kc("Tab")} to close</em></div>
    <div class="t-r h"><span>Map</span><span>Played</span><span>Score</span><span>K–D</span><span>Highlights</span><span>Status</span></div><div class="t-rows"></div></div>`);
  if (week) fillBoard(week);
  loadWeek(false).then(fillBoard, () => fillBoard(null));
}
function hideBoard() { if (board) { board.remove(); board = null; } }

// --- what the wheel, the console and the keys do ------------------------------------------------------------------
async function pausedNow() { try { return (await getJSON("/api/summary")).paused_by !== null; } catch (e) { return false; } }
async function setPaused(pause) {
  const r = await api(pause ? "/api/pause" : "/api/resume", {method: "POST"});
  return r.ok ? {ok: true, text: pause ? "Rendering paused. P to resume." : "Rendering resumed."}
    : {ok: false, text: "Couldn't reach CS2 Clipper. Is it running?"};
}
async function togglePause() { return setPaused(!(await pausedNow())); }
async function retryFailed() {
  const failed = (await getJSON("/api/status")).demos.filter(d => d.retry);
  if (!failed.length) return {ok: true, text: "Nothing has failed. Nice."};
  await Promise.all(failed.map(d => api(`/api/demos/${+d.id}/retry`, {method: "POST"})));
  return {ok: true, text: `Retrying ${failed.map(d => `${d.map || "Demo"} #${d.id}`).join(", ")}.`};
}
async function checkFaceit() {
  const r = await api("/api/faceit/check", {method: "POST"});
  return r.status === 202 ? {ok: true, text: "Asking FACEIT nicely… A Match Alert says what turns up."}
    : {ok: false, text: (await errorOf(r)) || "Couldn't ask FACEIT right now."};
}
async function openReels() {
  const r = await api("/api/reels/open-folder", {method: "POST"});
  return r.ok ? {ok: true, text: "Opened your Reels folder."} : {ok: false, text: "No Reels folder yet: the first Reel makes it."};
}
async function doing(action) {
  try { const {ok, text} = await action(); say(text, ok); } catch (e) { say("Couldn't reach CS2 Clipper. Is it running?", false); }
  if (PAGE === "/status") setTimeout(() => window.loadStatus?.(), 300);
}

// --- the quick wheel (hold B): aim at an action and let go -----------------------------------------------------
const WHEEL_PATHS = ["M76.8 24.1 A132 132 0 0 1 203.2 24.1 L164.9 94.3 A52 52 0 0 0 115.1 94.3 Z",
  "M208.8 27.3 A132 132 0 0 1 272.0 136.8 L192.0 138.7 A52 52 0 0 0 167.1 95.6 Z",
  "M272.0 143.2 A132 132 0 0 1 208.8 252.7 L167.1 184.4 A52 52 0 0 0 192.0 141.3 Z",
  "M203.2 255.9 A132 132 0 0 1 76.8 255.9 L115.1 185.7 A52 52 0 0 0 164.9 185.7 Z",
  "M71.2 252.7 A132 132 0 0 1 8.0 143.2 L88.0 141.3 A52 52 0 0 0 112.9 184.4 Z",
  "M8.0 136.8 A132 132 0 0 1 71.2 27.3 L112.9 95.6 A52 52 0 0 0 88.0 138.7 Z"];
const WHEEL = [["Check for demos", () => doing(checkFaceit)], ["Pause rendering", () => doing(togglePause)],
  ["Retry failed", () => doing(retryFailed)], ["Open Reels folder", () => doing(openReels)],
  ["Console", () => toggleConsole(true)], ["Settings", () => go("/settings")]];
let wheel = null;          // {node, aim, held, at}
function showWheel(held) {
  if (wheel) return;
  const buttons = WHEEL.map(([label], i) => {
    const a = i * Math.PI / 3;
    return `<button class="t-wb" data-i="${i}" style="left:${(150 + 98.5 * Math.sin(a)).toFixed(1)}px;top:${(150 - 98.5 * Math.cos(a)).toFixed(1)}px">${esc(label)}</button>`;
  }).join("");
  const node = layer("Quick wheel", `<div class="t-wh"><svg width="300" height="300" viewBox="0 0 280 280" aria-hidden="true"><circle cx="140" cy="140" r="49" fill="#0B0B0B" stroke="#2E2E2E"/>${WHEEL_PATHS.map(d => `<path d="${d}"/>`).join("")}</svg>${buttons}<div class="t-wm">Quick wheel<small>${held ? "Aim, then let go" : "Pick one"}</small></div></div>`);
  wheel = {node, aim: -1, held, at: Date.now()};
  node.addEventListener("click", e => {
    const b = e.target.closest(".t-wb");
    if (b) { const i = +b.dataset.i; hideWheel(); WHEEL[i][1](); } else if (!e.target.closest(".t-wh")) hideWheel();
  });
  pausedNow().then(paused => { if (paused && wheel?.node === node) node.querySelector('[data-i="1"]').textContent = "Resume rendering"; });
  blip("open");
}
function aimWheel(e) {
  if (!wheel) return;
  const r = wheel.node.querySelector(".t-wh").getBoundingClientRect();
  const dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
  const aim = Math.hypot(dx, dy) > 50 ? Math.round(((Math.atan2(dy, dx) * 180 / Math.PI + 450) % 360) / 60) % 6 : -1;
  if (aim === wheel.aim) return;
  wheel.aim = aim;
  wheel.node.querySelectorAll("path").forEach((p, i) => p.classList.toggle("on", i === aim));
  wheel.node.querySelectorAll(".t-wb").forEach((b, i) => b.classList.toggle("on", i === aim));
  const mid = wheel.node.querySelector(".t-wm");
  mid.innerHTML = aim < 0 ? `Quick wheel<small>${wheel.held ? "Aim, then let go" : "Pick one"}</small>`
    : `${esc(wheel.node.querySelector(`[data-i="${aim}"]`).textContent)}<small>${wheel.held ? "Let go of B" : "Click it"}</small>`;
}
function hideWheel() { if (wheel) { wheel.node.remove(); wheel = null; } }
function releaseWheel() {
  if (!wheel || !wheel.held) return;
  const aim = wheel.aim;
  if (aim < 0 && Date.now() - wheel.at < 350) {          // a tap: it stays open to be clicked
    wheel.held = false;
    wheel.node.querySelector(".t-wm").innerHTML = "Quick wheel<small>Pick one</small>";
    return;
  }
  hideWheel();
  if (aim >= 0) WHEEL[aim][1]();
}
addEventListener("pointermove", aimWheel, {passive: true});

// --- the shortcut sheet (?) ----------------------------------------------------------------------------------------
const KEYMAP = [
  ["Anywhere", [[["Tab"], "Hold for this week's scoreboard"], [["~"], "Console"], [["B"], "Hold for the quick wheel"],
    [["1", "2", "3", "4"], "Go to a page"], [["Q"], "Back to the last page"], [["P"], "Pause or resume rendering"],
    [["?"], "All shortcuts"], [["Esc"], "Close whatever is open"]]],
  ["Status", [[["R"], "Retry what failed"]]],
  ["Demos to grab", [[["↑", "↓"], "Pick a match"], [["S"], "Skip it (or drag it left)"], [["Z"], "Undo the skip"]]],
  ["Reels", [[["←", "→"], "Pick a Reel"], [["Space"], "Play or pause"], [["E"], "Open its folder"]]],
];
let sheet = null;
function toggleSheet() {
  if (sheet) { sheet.remove(); sheet = null; return; }
  const group = ([h, rows]) => `<div class="t-kg"><h3>${esc(h)}</h3>${rows.map(([keys, label]) =>
    `<div class="t-kr"><span>${keys.map(kc).join("")}</span><span>${esc(label)}</span></div>`).join("")}</div>`;
  sheet = layer("Keyboard shortcuts", `<div class="t-sh"><div>${group(KEYMAP[0])}</div><div>${KEYMAP.slice(1).map(group).join("")}</div></div>`);
  sheet.addEventListener("click", e => { if (!e.target.closest(".t-sh")) toggleSheet(); });
}
function lightKey(key) {
  if (!sheet) return;
  const want = key === " " ? "Space" : key.length === 1 ? key.toUpperCase()
    : {ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→", Escape: "Esc", Tab: "Tab"}[key];
  sheet.querySelectorAll(".t-kc").forEach(k => {
    if (k.textContent !== want) return;
    k.classList.add("on");
    setTimeout(() => k.classList.remove("on"), 260);
  });
}

// --- the console (~) ---------------------------------------------------------------------------------------------
const HELP = [["help", "this list"], ["status", "what CS2 Clipper is doing right now"], ["demos", "this week's matches, numbered"],
  ["reels", "your best Reels"], ["retry [n]", "retry a failed Demo (every one if you leave n out)"], ["skip <n>", "skip a match to grab"],
  ["undo", "bring back the match you skipped last"], ["pause", "stop rendering for now"], ["resume", "start rendering again"],
  ["check", "ask FACEIT for new matches now"], ["open reels", "open the Reels folder"], ["goto <page>", "status, demos, reels or settings"],
  ["log [n]", "the last n log lines (5 if you leave n out)"], ["binds", "the keyboard shortcuts"], ["sounds on|off", "UI sounds"],
  ["clear", "clear the console"], ["quit", "close CS2 Clipper"]];
const COMMANDS = [...new Set(HELP.map(([c]) => c.split(" ")[0]))];
let con = null, conLines = store.get("con-lines", null), conHist = store.get("con-hist", []), conAt = -1, quitAt = 0;
if (!conLines) conLines = [{t: "CS2 Clipper console. Type help for the list of commands.", c: "hd"}];

function buildConsole() {
  con = el("section", "t-con", `<div class="t-coh"><b>CONSOLE</b><span>Type help for the list. Tab completes, ↑ brings back the last one.</span>
    <span style="margin-left:auto;display:flex;align-items:center;gap:6px">${kc("Esc")}or${kc("~")}to close</span></div>
    <div class="t-cl t-mono" role="log" aria-live="polite"></div>
    <div class="t-ci t-mono"><label for="t-cmd">]</label><input id="t-cmd" class="t-mono" spellcheck="false" autocomplete="off" aria-label="Console command"></div>`);
  con.setAttribute("aria-label", "Console");
  document.body.appendChild(con);
  const input = con.querySelector("input");
  input.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); const cmd = input.value; input.value = ""; run(cmd); }
    else if (e.key === "Tab") { e.preventDefault(); complete(input); }
    else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      if (!conHist.length) return;
      conAt = Math.max(-1, Math.min(conHist.length - 1, conAt + (e.key === "ArrowUp" ? 1 : -1)));
      input.value = conAt < 0 ? "" : conHist[conAt];
    }
  });
  paintLines();
}
function paintLines() {
  if (!con) return;
  const box = con.querySelector(".t-cl");
  box.innerHTML = conLines.map(l => `<div class="t-ln${l.c ? " " + l.c : ""}">${esc(l.t)}</div>`).join("");
  box.scrollTop = box.scrollHeight;
}
function print(t, c = "") {
  conLines.push({t, c});
  conLines = conLines.slice(-150);
  store.set("con-lines", conLines);
  paintLines();
}
function toggleConsole(open) {
  if (!con) buildConsole();
  const now = open === undefined ? !con.classList.contains("open") : open;
  con.classList.toggle("open", now);
  if (now) { hideWheel(); if (sheet) toggleSheet(); blip("open"); setTimeout(() => con.querySelector("input").focus(), 40); }
  else if (con.contains(document.activeElement)) document.activeElement.blur();
}
function complete(input) {
  const typed = input.value.trim().toLowerCase();
  const found = COMMANDS.filter(c => c.startsWith(typed));
  if (found.length === 1) input.value = found[0] + " ";
  else if (found.length > 1 && typed) print("  " + found.join("   "), "mut");
}
const pad = (s, n) => String(s).padEnd(n);
// `demos` numbers this week's matches; `skip` and `retry` take those numbers, a map's name, or a Demo's #id.
function pick(arg) {
  const rows = store.get("con-week", []);
  if (/^\d+$/.test(arg)) return rows[+arg - 1] || null;
  if (/^#\d+$/.test(arg)) return {demo_id: +arg.slice(1), map: `Demo ${arg}`, status: "Failed"};
  return rows.find(r => r.map.toLowerCase() === arg && (r.status === "To grab" || r.status === "Failed")) || rows.find(r => r.map.toLowerCase() === arg) || null;
}
async function run(raw) {
  const s = raw.trim();
  print("] " + s, "in");
  if (!s) return;
  conHist = [s, ...conHist.filter(h => h !== s)].slice(0, 30);
  conAt = -1;
  store.set("con-hist", conHist);
  const [c0, ...rest] = s.toLowerCase().split(/\s+/), arg = rest.join(" ");
  try {
    await command(c0, arg);
  } catch (e) {
    print("Couldn't reach CS2 Clipper. Is it running?", "err");
  }
}
async function command(c0, arg) {
  switch (c0) {
    case "help":
      print("Commands", "hd");
      HELP.forEach(([c, d]) => print("  " + pad(c, 16) + d));
      print("…and a couple you'll have to find yourself.", "mut");
      return;
    case "status": {
      const d = await getJSON("/api/status");
      print(d.summary, d.problems.length ? "err" : d.rendering ? "ok" : "");
      d.problems.forEach(p => print("  " + p, "err"));
      const failed = d.demos.filter(x => x.retry);
      if (failed.length) print(`${plural(failed.length, "Demo")} failed: ${failed.map(x => `${x.map || "Demo"} #${x.id}`).join(", ")}. Type retry.`, "err");
      const busy = d.demos.filter(x => !x.retry);
      if (busy.length) print(`In progress: ${busy.map(x => `${x.map || "Demo"} #${x.id} (${x.state})`).join(", ")}.`);
      if (!failed.length && !busy.length) print("Nothing in progress.");
      return;
    }
    case "demos": {
      const data = await loadWeek(true);
      if (!data.rows.length) { print("No matches in the last 7 days. Go queue.", "mut"); return; }
      print("  n   map        score    K–D      status", "mut");
      data.rows.forEach((r, i) => print("  " + pad(i + 1, 4) + pad(r.map, 11) + pad(r.score + (r.won ? " W" : " L"), 9) + pad(r.kd || "–", 9) + r.status + (r.ace ? "   ACE" : "")));
      store.set("con-week", data.rows.map(r => ({match_id: r.match_id, demo_id: r.demo_id, map: r.map, status: r.status})));
      print("skip <n> skips one to grab; retry <n> retries a failed one.", "mut");
      return;
    }
    case "reels": {
      const best = bestHighlights(await getJSON("/api/reels"), 6);
      if (!best.length) { print("No Reels yet. Finished Demos show up there.", "mut"); return; }
      best.forEach(h => print("  " + pad(h.match.map, 10) + pad(`${String(h.type).toUpperCase() === "ACE" ? "Ace" : h.type} in round ${h.round}`, 20) + dateLabel(h.match.played_at)));
      return;
    }
    case "retry": {
      if (!arg) { const {ok, text} = await retryFailed(); print(text, ok ? "ok" : "err"); return; }
      const row = pick(arg);
      if (!row) { print(`No match ${arg} this week. Type demos to see the numbers.`, "err"); return; }
      if (!row.demo_id || row.status !== "Failed") { print(`${row.map} didn't fail. Nothing to retry.`, "err"); return; }
      const r = await api(`/api/demos/${+row.demo_id}/retry`, {method: "POST"});
      print(r.ok ? `Retrying ${row.map}. It's back in the queue.` : (await errorOf(r)) || `Couldn't retry ${row.map}.`, r.ok ? "ok" : "err");
      return;
    }
    case "skip": {
      const row = arg ? pick(arg) : null;
      if (!row) { print(arg ? `No match ${arg} this week. Type demos to see the numbers.` : "Usage: skip <n>. Type demos to see the numbers.", "err"); return; }
      if (!row.match_id || row.status !== "To grab") { print(`Can't skip ${row.map}: it's ${String(row.status).toLowerCase()}.`, "err"); return; }
      const r = await api(`/demos/${row.match_id}/skip`, {method: "POST"});
      if (r.ok) { store.set("last-skip", {id: row.match_id, map: row.map}); print(`Skipped ${row.map}. Type undo to bring it back.`, "ok"); }
      else print(`Couldn't skip ${row.map}.`, "err");
      if (PAGE === "/demos") window.load?.();
      return;
    }
    case "undo": { const {ok, text} = await undoSkip(); print(text, ok ? "ok" : "err"); return; }
    case "pause": case "resume": {
      const paused = await pausedNow();
      if (paused === (c0 === "pause")) { print(paused ? "Already paused." : "Already running.", "err"); return; }
      const {ok, text} = await setPaused(c0 === "pause");
      print(text, ok ? "ok" : "err");
      return;
    }
    case "check": { const {ok, text} = await checkFaceit(); print(text, ok ? "ok" : "err"); return; }
    case "open": {
      if (arg !== "reels") { print("Usage: open reels", "err"); return; }
      const {ok, text} = await openReels();
      print(text, ok ? "ok" : "err");
      return;
    }
    case "goto": {
      const i = PAGES.findIndex(([, label]) => label.toLowerCase().startsWith(arg) && arg);
      const j = i >= 0 ? i : +arg >= 1 && +arg <= 4 ? +arg - 1 : -1;
      if (j < 0) { print("Usage: goto status | demos | reels | settings", "err"); return; }
      print(`Now on ${PAGES[j][1]}.`, "ok");
      store.set("con-open", true);
      go(PAGES[j][0]);
      return;
    }
    case "log": {
      const n = Math.max(1, Math.min(60, parseInt(arg, 10) || 5));
      const lines = (await getJSON("/api/log")).lines.slice(-n);
      if (!lines.length) print("The log is empty.", "mut");
      lines.forEach(l => {
        const m = /^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d),\d+ (\w+) [\w.]+: ([\s\S]*)$/.exec(l);
        print("  " + (m ? `${m[1]}  ${pad(m[2], 8)}${m[3]}` : l), m && m[2] !== "INFO" && m[2] !== "DEBUG" ? "err" : "");
      });
      return;
    }
    case "binds":
      KEYMAP.forEach(([, rows]) => rows.forEach(([keys, label]) => print("  " + pad(keys.join(" "), 12) + label)));
      return;
    case "sounds": {
      if (arg !== "on" && arg !== "off") { print("Usage: sounds on | sounds off", "err"); return; }
      const r = await api("/api/settings", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ui_sounds: arg === "on"})});
      if (!r.ok) { print("Couldn't save that.", "err"); return; }
      prefs.ui_sounds = arg === "on";
      store.set("prefs", prefs);
      print(`UI sounds ${arg}.${arg === "on" && quiet() ? " Quiet hours keep them silent until 5." : ""}`, "ok");
      blip();
      return;
    }
    case "clear":
      conLines = [];
      store.set("con-lines", conLines);
      paintLines();
      return;
    case "quit": {
      const mode = arg === "now" ? "now" : arg === "after" ? "after_render" : null;
      if (!mode && Date.now() - quitAt > 4000) { quitAt = Date.now(); print("Type quit again to close CS2 Clipper."); return; }
      quitAt = 0;
      const r = await api("/api/quit", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({mode})});
      const answer = (await r.json()).result;
      print(answer === "ask" ? "A render is running. Type quit now (it's redone later) or quit after (once it finishes)."
        : answer === "after_render" ? "CS2 Clipper closes when this render finishes." : "Closing CS2 Clipper. gg.", answer === "ask" ? "" : "ok");
      return;
    }
    case "rush":
      if (arg !== "b") { print("Rush where?", "err"); return; }
      print("Rush B. No stopping.", "hd");
      print("…straight into your Reels.");
      store.set("con-open", true);
      setTimeout(() => go("/reels"), 600);
      return;
    case "gg":
      print("gg wp. Your Reels are ready when you are.", "hd");
      return;
    default:
      print(`Unknown command "${c0}". Type help.`, "err");
  }
}
async function undoSkip() {
  const last = store.get("last-skip", null);
  if (!last) return {ok: false, text: "Nothing to undo."};
  const r = await api(`/demos/${last.id}/undo`, {method: "POST"});
  store.set("last-skip", null);
  if (PAGE === "/demos") window.load?.();
  return r.ok ? {ok: true, text: `${last.map} is back.`} : {ok: false, text: "Nothing to undo."};
}

// --- Status: a line for the time of day, and loading lines while something runs ---------------------------------
function timeLine(now = new Date()) {
  const h = now.getHours(), day = now.getDay();
  if (h >= 22 || h < 1) return "Late one. I'll keep it quiet.";
  if (h < 5) return `It's ${now.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}. One more, or bed? Your Reels will still be here tomorrow.`;
  if (h < 11) return day === 1 ? "Monday morning. Hold Tab for last week's scoreboard." : "Morning. Coffee first, Highlights second.";
  if (h < 17) return day === 0 || day === 6 ? "Weekend. Long sessions welcome: every Demo you grab gets picked up."
    : "Afternoon. Go play; I'll pick up the Demos you grab.";
  return day === 5 ? "Friday night. Go frag. Renders wait until CS2 closes." : "Prime time. Renders wait until CS2 closes, then I get to work.";
}
function statusLines() {
  const intro = document.getElementById("intro");
  if (!intro) return;
  const line = el("p", "t-line");
  line.id = "t-line";
  intro.after(line);
  const paint = () => {
    const now = new Date();
    line.innerHTML = esc(timeLine(now)) + (quiet(now) ? '<span class="t-quiet" title="No sounds and no Ace stamp until 5">Quiet hours</span>' : "");
    document.body.classList.toggle("t-night", quiet(now));
  };
  paint();
  setInterval(paint, 60000);

  const QUIPS = {
    analyzing: ["Counting your headshots…", "Reading every round…", "Finding the rounds worth keeping…", "Checking who you traded…"],
    rendering: ["Finding the best angle…", "Waiting for the smoke to clear…", "Keeping the crosshair steady…", "Rewinding the round…"],
    enemy: ["Lining up the Enemy POV…"],
    joining: ["Stitching the Clips together…", "Lining up the cuts…"],
  };
  const sub = document.getElementById("now-sub");
  let set = null, i = 0, timer = 0;
  const show = () => {
    document.getElementById("t-quip")?.remove();
    if (!set) return;
    const q = el("span", "t-quip", esc(set[i++ % set.length]));
    q.id = "t-quip";
    q.setAttribute("aria-hidden", "true");     // the real status above is what a screen reader hears
    sub.after(q);
  };
  addEventListener("clipper:status", e => {
    const d = e.detail, p = d.rendering && d.rendering.progress;
    const stage = d.problems.length || d.quitting ? null : p && p.stage === "joining" ? "joining" : d.rendering ? "rendering"
      : d.demos.some(x => ["spotted", "unpacked", "analyzed", "scored"].includes(x.state)) ? "analyzing" : null;
    const next = !stage ? null : stage === "rendering" && d.rendering.perspective !== "player" ? [...QUIPS.rendering, ...QUIPS.enemy] : QUIPS[stage];
    if (next === set || (next && set && next.join() === set.join())) return;
    set = next; i = Math.floor(Math.random() * 4);
    clearInterval(timer);
    show();
    if (set) timer = setInterval(show, 3600);
  });
}

// --- Demos: drag a row left to skip it ---------------------------------------------------------------------------
function trackArm(wrap, on) {
  const track = wrap.querySelector(".t-trk");
  if (!track) return;
  track.classList.toggle("arm", on);
  track.querySelector("span").textContent = on ? "Let go to skip" : "Skip";
}
function taught() {
  store.set("taught", true);
  document.querySelectorAll(".t-peek").forEach(n => n.classList.remove("t-peek"));
}
function springBack(wrap) {
  const row = wrap.querySelector(".drow");
  row.style.transition = "transform .6s cubic-bezier(.34,1.7,.5,1)";
  row.style.transform = "translateX(0px)";
  wrap.style.setProperty("--p", "0");
  trackArm(wrap, false);
  setTimeout(() => { row.style.transition = ""; row.style.transform = ""; wrap.classList.remove("t-pull"); }, 620);
}
// The row flies off, then its own Skip button does the skipping, so the page's list and its Undo stay in charge.
function fling(wrap) {
  const row = wrap.querySelector(".drow"), skip = wrap.querySelector('button[data-act="skip"]');
  if (!skip) { springBack(wrap); return; }
  wrap.classList.add("t-pull");
  trackArm(wrap, true);
  wrap.style.setProperty("--p", "1");
  row.style.transition = "transform .26s cubic-bezier(.5,0,.9,.6)";
  row.style.transform = "translateX(-110%)";
  blip();
  setTimeout(() => {
    const id = skip.dataset.id, map = wrap.dataset.map || "it";
    store.set("last-skip", {id, map});
    skip.click();
    toast(`${ICON.check}<span>Skipped ${esc(map)}</span><button class="primary dark sm" data-act="undo" data-id="${esc(id)}">Undo</button>`, 5000);
    setTimeout(() => { if (wrap.isConnected && wrap.querySelector('button[data-act="skip"]')) springBack(wrap); }, 2500);   // it didn't go
  }, 200);
}
function dragToSkip() {
  const list = document.getElementById("rows");
  if (!list) return;
  let d = null;
  list.addEventListener("pointerdown", e => {
    const row = e.target.closest(".drow.t-drag");
    if (!row || e.button !== 0 || e.target.closest("a, button")) return;
    d = {row, wrap: row.parentElement, x0: e.clientX, y0: e.clientY, id: e.pointerId, moved: false, armed: false};
  });
  list.addEventListener("pointermove", e => {
    if (!d || e.pointerId !== d.id) return;
    let dx = e.clientX - d.x0;
    if (!d.moved) {
      if (Math.abs(dx) < 6) return;
      if (Math.abs(e.clientY - d.y0) > Math.abs(dx)) { d = null; return; }       // a scroll, not a drag
      d.moved = true;
      try { d.row.setPointerCapture(e.pointerId); } catch (_) { /* it went */ }
      d.row.style.transition = "none";
      d.wrap.classList.add("t-pull");
      taught();
    }
    if (dx > 0) dx *= 0.18;                                                      // right gives a little, and no more
    const reach = d.wrap.offsetWidth * 0.36, p = Math.min(1, Math.max(0, -dx / reach));
    d.row.style.transform = `translateX(${dx.toFixed(1)}px)`;
    d.wrap.style.setProperty("--p", p.toFixed(3));
    const armed = -dx > reach;
    if (armed !== d.armed) { d.armed = armed; trackArm(d.wrap, armed); if (armed) blip(); }
  });
  const end = e => {
    if (!d || e.pointerId !== d.id) return;
    const x = d;
    d = null;
    if (!x.moved) return;
    if (x.armed && e.type === "pointerup") fling(x.wrap); else springBack(x.wrap);
  };
  list.addEventListener("pointerup", end);
  list.addEventListener("pointercancel", end);
  // A first-time hint: once a visit, the first row leans left twice, until a row has been dragged.
  const hint = new MutationObserver(() => {
    const first = list.querySelector(".drow.t-drag");
    if (!first) return;
    hint.disconnect();
    if (!store.get("taught", false) && !stillness()) first.classList.add("t-peek");
  });
  hint.observe(list, {childList: true});
  // Skip and S do what a drag does; the button's own click comes after the row has gone.
  document.addEventListener("click", e => {
    const b = e.target.closest('.drow.t-drag button[data-act="skip"]');
    if (!b || !e.isTrusted) return;
    e.preventDefault(); e.stopPropagation();
    fling(b.closest(".t-wrap"));
  }, true);
  document.addEventListener("click", e => { if (e.target.closest('.toast [data-act="undo"]')) e.target.closest(".toast").remove(); });
}
function moveRow(step) {
  const rows = [...document.querySelectorAll("#rows > .t-wrap")];
  if (!rows.length) return;
  const at = rows.indexOf(document.activeElement.closest?.(".t-wrap"));
  const next = rows[at < 0 ? (step > 0 ? 0 : rows.length - 1) : (at + step + rows.length) % rows.length];
  next.tabIndex = -1;
  next.focus();
}
function skipPicked() {
  const wrap = document.activeElement.closest?.(".t-wrap");
  if (!wrap) { say("Pick a match first: ↑ and ↓.", false); return; }
  if (wrap.querySelector(".drow.t-drag")) fling(wrap); else say("That one isn't waiting to be grabbed.", false);
}

// --- Reels: move across a Highlight to flick through its Reel ------------------------------------------------------
function hoverScrub() {
  let scrub = null, current = null;
  const hide = () => { if (scrub) scrub.hidden = true; current = null; };
  document.addEventListener("pointermove", e => {
    if (e.pointerType === "touch") return;
    const row = e.target.closest?.(".hl[data-scrub]");
    if (!row) { hide(); return; }
    if (!scrub) { scrub = el("div", "t-scrub", "<b></b><span></span>"); scrub.setAttribute("aria-hidden", "true"); document.body.appendChild(scrub); }
    if (current !== row) {
      current = row;
      scrub.style.backgroundImage = `url("/reels/${+row.dataset.scrub}.strip.jpg")`;
      scrub.style.backgroundSize = `${STRIP_FRAMES * 320}px 180px`;
    }
    scrub.hidden = false;
    const r = row.getBoundingClientRect(), t = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    scrub.style.backgroundPosition = `${-Math.min(STRIP_FRAMES - 1, Math.floor(t * STRIP_FRAMES)) * 320}px 0`;
    scrub.firstChild.style.width = (t * 100).toFixed(1) + "%";
    const secs = +row.dataset.secs || 0;
    scrub.lastChild.textContent = secs ? `${clock(t * secs)} / ${clock(secs)}` : "";
    scrub.style.left = Math.max(16, Math.min(innerWidth - 336, e.clientX - 160)) + "px";
    scrub.style.top = (r.top - 192 >= 64 ? r.top - 192 : r.bottom + 12) + "px";
  }, {passive: true});
  addEventListener("scroll", hide, {passive: true});
  document.addEventListener("pointerleave", hide);
}
function moveReel(step) {
  const povs = [...document.querySelectorAll(".pov:not(:disabled)")];
  if (!povs.length) return;
  const at = povs.indexOf(document.activeElement);
  povs[at < 0 ? (step > 0 ? 0 : povs.length - 1) : (at + step + povs.length) % povs.length].focus();
}
function playPause() {
  const video = document.getElementById("player-video");
  if (video) { if (video.paused) video.play().catch(() => {}); else video.pause(); }
  else say("Pick a Reel first: ← and →, then Enter.", false);
}
function openFolder() {
  const card = (document.activeElement.closest?.(".mcard")) || document.querySelector(".hl.on")?.closest(".mcard");
  const button = card?.querySelector("button[data-open]");
  if (button) { button.click(); say("Opened its folder."); } else say("Pick a Reel first: ← and →.", false);
}

// --- the keys -------------------------------------------------------------------------------------------------------
const typing = n => n && (n.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(n.tagName));
function closeAll() {
  const open = !!(board || wheel || sheet || con?.classList.contains("open"));
  hideBoard(); hideWheel();
  if (sheet) toggleSheet();
  if (con?.classList.contains("open")) toggleConsole(false);
  return open;
}
document.addEventListener("keydown", e => {
  if (!PC) return;
  const inConsole = !!con && e.target === con.querySelector("input");
  const mods = e.ctrlKey || e.altKey || e.metaKey;
  if (e.code === "Backquote" && !mods) {
    if (inConsole || (!typing(e.target) && prefs.keyboard_shortcuts)) { e.preventDefault(); if (!e.repeat) toggleConsole(); }
    return;
  }
  if (e.key === "Escape") { if (closeAll()) e.preventDefault(); return; }
  if (inConsole || typing(e.target) || mods || !prefs.keyboard_shortcuts) return;
  lightKey(e.key);
  if (e.key === "Tab" && !e.shiftKey) {
    // A quick Tab still moves through the page; held, or with nothing in focus, it is the scoreboard.
    const resting = !document.activeElement || document.activeElement === document.body;
    if (board || resting || e.repeat) { e.preventDefault(); showBoard(); }
    return;
  }
  if (e.repeat || e.target.closest?.(".hero")) return;
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (key === "b") { if (wheel && !wheel.held) hideWheel(); else showWheel(true); return; }
  if (key === "?") { toggleSheet(); return; }
  if ("1234".includes(key) && key.length === 1) { const [path] = PAGES[+key - 1]; if (path !== PAGE) go(path); return; }
  if (key === "q") { const prev = store.get("prev", null); if (prev && prev !== PAGE) go(prev); return; }
  if (key === "p") { doing(togglePause); return; }
  if (PAGE === "/status" && key === "r") { doing(retryFailed); return; }
  if (PAGE === "/demos") {
    if (key === "ArrowDown" || key === "ArrowUp") { e.preventDefault(); moveRow(key === "ArrowDown" ? 1 : -1); return; }
    if (key === "s") { skipPicked(); return; }
    if (key === "z") { undoSkip().then(({ok, text}) => say(text, ok)); return; }
  }
  if (PAGE === "/reels") {
    if (key === "ArrowRight" || key === "ArrowLeft") { e.preventDefault(); moveReel(key === "ArrowRight" ? 1 : -1); return; }
    if (key === " " && !e.target.closest?.("button, a")) { e.preventDefault(); playPause(); return; }
    if (key === "e") { openFolder(); return; }
  }
});
document.addEventListener("keyup", e => {
  if (e.key === "Tab") hideBoard();
  if (e.key === "b" || e.key === "B") releaseWheel();
});
addEventListener("blur", () => { hideBoard(); if (wheel?.held) hideWheel(); });

// --- start -----------------------------------------------------------------------------------------------------------
applyPrefs();
if (PAGE === "/demos") dragToSkip();                 // the phone gets this one too
if (!PC) return;
const cur = store.get("cur", null);
if (cur && cur !== PAGE) store.set("prev", cur);
store.set("cur", PAGE);
loadPrefs();
watchAces();
if (PAGE === "/status") statusLines();
if (PAGE === "/reels") hoverScrub();
if (store.get("con-open", false)) { store.set("con-open", false); toggleConsole(true); }
})();
