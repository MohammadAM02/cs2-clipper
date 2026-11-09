// Shared by every page (spec: Pages). No framework, no build step.
"use strict";

const MARKER_HEADER = "X-CS2-Clipper";
const NAV_PAGES = [["/status", "Status"], ["/demos", "Demos to grab"], ["/reels", "Reels"], ["/settings", "Settings"]];

// fetch() that never caches and marks every non-GET request so the server knows it is one of ours
// (spec: Reach from other devices -- a page on another site can never send this header).
function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = Object.assign({}, options.headers);
  if (method !== "GET") headers[MARKER_HEADER] = "1";
  return fetch(path, Object.assign({}, options, {method, headers, cache: "no-store"}));
}

// Whether this page is being viewed on the PC itself: the other pages answer 403 anywhere else.
function onThisPC() {
  return location.hostname === "127.0.0.1" || location.hostname === "localhost" || location.hostname === "[::1]";
}

// --- the window at rest -----------------------------------------------------------------------------------
// While the window is hidden, minimized or behind another (CS2 on the other screen, say), every animation stops
// (the .asleep rule in app.css) and the pages ask the app for news less often. It wakes on focus.
const asleep = () => document.hidden || !document.hasFocus();
function markAsleep() { document.documentElement.classList.toggle("asleep", asleep()); }
addEventListener("blur", markAsleep);
addEventListener("focus", markAsleep);
document.addEventListener("visibilitychange", markAsleep);
markAsleep();

// Runs `fn` now and then every `ms`, or every `restingMs` while the window is asleep, and at once when it wakes.
// One run at a time: a slow answer never stacks another request behind it.
function every(ms, fn, restingMs = 20000) {
  let timer = 0, running = false;
  const run = async () => {
    if (running) return;
    running = true;
    clearTimeout(timer);
    try { await fn(); } catch (e) { /* the next run asks again */ }
    running = false;
    timer = setTimeout(run, asleep() ? Math.max(ms, restingMs) : ms);
  };
  addEventListener("focus", () => { if (!document.hidden) run(); });
  run();
}

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const $ = (sel, root = document) => root.querySelector(sel);
const plural = (n, word, many) => `${n} ${n === 1 ? word : (many || word + "s")}`;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MARK = '<svg width="28" height="28" viewBox="0 0 64 64" aria-hidden="true"><path d="M14 2H62V50L50 62H2V14Z" fill="#141414"/><polygon points="18,50 30.05,43.22 36.22,24.25 18,14" fill="#FF5500"/><polygon points="51.6,28.4 40.86,22.36 36.06,37.14" fill="#FF5500"/></svg>';
// The icons are pixel art on an 8 x 8 grid, shown at 2 or 3 px a pixel so they stay crisp.
const ICON = {
  play: '<svg width="16" height="16" viewBox="0 0 8 8" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true"><path d="M0 0h1v1h-1zM0 1h3v1h-3zM0 2h5v1h-5zM0 3h7v1h-7zM0 4h5v1h-5zM0 5h3v1h-3zM0 6h1v1h-1z"/></svg>',
  pause: '<svg width="16" height="16" viewBox="0 0 8 8" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true"><path d="M1 1h2v1h-2zM5 1h2v1h-2zM1 2h2v1h-2zM5 2h2v1h-2zM1 3h2v1h-2zM5 3h2v1h-2zM1 4h2v1h-2zM5 4h2v1h-2zM1 5h2v1h-2zM5 5h2v1h-2zM1 6h2v1h-2zM5 6h2v1h-2z"/></svg>',
  check: '<svg width="16" height="16" viewBox="0 0 8 8" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true"><path d="M7 1h1v1h-1zM6 2h2v1h-2zM0 3h1v1h-1zM5 3h2v1h-2zM0 4h2v1h-2zM4 4h2v1h-2zM1 5h4v1h-4zM2 6h2v1h-2z"/></svg>',
  bang: '<svg width="16" height="16" viewBox="0 0 8 8" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true"><path d="M3 0h2v1h-2zM3 1h2v1h-2zM3 2h2v1h-2zM3 3h2v1h-2zM3 4h2v1h-2zM3 6h2v1h-2zM3 7h2v1h-2z"/></svg>',
  dash: '<svg width="16" height="16" viewBox="0 0 8 8" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true"><path d="M1 3h6v1h-6zM1 4h6v1h-6z"/></svg>',
  info: '<svg width="16" height="16" viewBox="0 0 8 8" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true"><path d="M2 0h4v1h-4zM1 1h1v1h-1zM6 1h1v1h-1zM0 2h1v1h-1zM3 2h2v1h-2zM7 2h1v1h-1zM0 3h1v1h-1zM7 3h1v1h-1zM0 4h1v1h-1zM3 4h2v1h-2zM7 4h1v1h-1zM0 5h1v1h-1zM3 5h2v1h-2zM7 5h1v1h-1zM1 6h1v1h-1zM6 6h1v1h-1zM2 7h4v1h-4z"/></svg>',
  next: '<svg width="16" height="16" viewBox="0 0 8 8" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true"><path d="M2 1h2v1h-2zM3 2h2v1h-2zM4 3h2v1h-2zM4 4h2v1h-2zM3 5h2v1h-2zM2 6h2v1h-2z"/></svg>',
  out: '<svg width="16" height="16" viewBox="0 0 8 8" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true"><path d="M3 0h2v1h-2zM2 1h4v1h-4zM1 2h6v1h-6zM3 3h2v1h-2zM3 4h2v1h-2zM0 5h1v1h-1zM3 5h2v1h-2zM7 5h1v1h-1zM0 6h1v1h-1zM7 6h1v1h-1zM0 7h8v1h-8z"/></svg>',
  folder: '<svg width="16" height="16" viewBox="0 0 8 8" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true"><path d="M0 1h3v1h-3zM0 2h1v1h-1zM3 2h4v1h-4zM0 3h7v1h-7zM0 4h1v1h-1zM6 4h1v1h-1zM0 5h1v1h-1zM6 5h1v1h-1zM0 6h7v1h-7z"/></svg>',
  bin: '<svg width="16" height="16" viewBox="0 0 8 8" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true"><path d="M2 0h4v1h-4zM0 1h8v1h-8zM1 3h6v1h-6zM1 4h1v1h-1zM3 4h2v1h-2zM6 4h1v1h-1zM1 5h1v1h-1zM3 5h2v1h-2zM6 5h1v1h-1zM1 6h1v1h-1zM3 6h2v1h-2zM6 6h1v1h-1zM2 7h4v1h-4z"/></svg>',
  chev: '<svg class="chev" width="24" height="24" viewBox="0 0 8 8" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true"><path d="M0 2h1v1h-1zM7 2h1v1h-1zM0 3h2v1h-2zM6 3h2v1h-2zM1 4h2v1h-2zM5 4h2v1h-2zM2 5h4v1h-4zM3 6h2v1h-2z"/></svg>',
  skip: '<svg width="16" height="16" viewBox="0 0 8 8" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true"><path d="M0 0h1v1h-1zM6 0h2v1h-2zM0 1h2v1h-2zM6 1h2v1h-2zM0 2h3v1h-3zM6 2h2v1h-2zM0 3h4v1h-4zM6 3h2v1h-2zM0 4h3v1h-3zM6 4h2v1h-2zM0 5h2v1h-2zM6 5h2v1h-2zM0 6h1v1h-1zM6 6h2v1h-2z"/></svg>',
};
const BADGE = {ACE: "b-ace", "5K": "b-ace", "4K": "b-4k", "3K": "b-3k", "2K": "b-2k"};
const badge = type => `<span class="badge ${BADGE[String(type).toUpperCase()] || "b-2k"}">${esc(String(type).toUpperCase())}</span>`;
const mapAbbr = map => { const m = String(map || "?").replace(/[^A-Za-z0-9]/g, ""); const d = m.match(/\d/); return esc((d ? m[0] + d[0] : m.slice(0, 2)).toUpperCase()); };
// Each map's colour (touches.js's map glow uses them too), and its tile: the colour with halftone dots on it.
const MAP_TINTS = {Mirage: "#E7C995", Inferno: "#E59A7A", Nuke: "#8FC9C4", Ancient: "#B9C98F", Dust2: "#D9B57A",
  Anubis: "#7FC4B8", Train: "#B8BEC6", Vertigo: "#A9C4E8", Overpass: "#9DB7A5"};
const maptile = (map, style = "") => {
  const tint = MAP_TINTS[map];
  const css = [tint ? `--tint:${tint}` : "", style].filter(Boolean).join(";");
  return `<span class="maptile${tint ? " tinted" : ""}"${css ? ` style="${css}"` : ""}>${mapAbbr(map || "?")}</span>`;
};

// --- updating lists without replaying everything ------------------------------------------------------
// Every page polls and gets the whole picture back. `sync` keeps each row's element while its HTML is
// unchanged, swaps in a new one when it changed (a short flash), adds new rows with an entrance, and
// moves progress bars (`<i data-w="40%">`) smoothly from where the old row had them.
function sync(container, items, key, html) {
  const old = new Map();
  for (const node of [...container.children]) {
    if (node.dataset.key !== undefined) old.set(node.dataset.key, node); else node.remove();
  }
  let prev = null;
  items.forEach((item, i) => {
    const k = String(key(item, i));
    const h = html(item, i);
    let node = old.get(k);
    if (node && node._h === h) {
      old.delete(k);
    } else {
      const t = document.createElement("template");
      t.innerHTML = h.trim();
      const fresh = t.content.firstElementChild;
      fresh.dataset.key = k;
      fresh._h = h;
      const from = node ? [...node.querySelectorAll("[data-w]")].map(n => n.style.width) : [];
      fresh.querySelectorAll("[data-w]").forEach((n, j) => { n.style.width = from[j] || (container._ready ? "0%" : n.dataset.w); });
      if (container._ready) fresh.classList.add(node ? "changed" : "enter");
      if (node) { node.replaceWith(fresh); old.delete(k); }
      node = fresh;
      requestAnimationFrame(() => requestAnimationFrame(() =>
        node.querySelectorAll("[data-w]").forEach(n => { n.style.width = n.dataset.w; })));
    }
    const want = prev ? prev.nextElementSibling : container.firstElementChild;
    if (want !== node) container.insertBefore(node, want);
    prev = node;
  });
  old.forEach(n => n.remove());
  container._ready = true;
}

function toast(html, ms = 5200) {
  const el = document.createElement("div");
  el.className = "toast";
  el.setAttribute("role", "status");
  el.innerHTML = html;
  // Nugget brings the news (nugget.js): worried for a problem, otherwise all eyes.
  if (typeof NUGGET !== "undefined") el.insertAdjacentHTML("afterbegin", NUGGET.head(html.startsWith(ICON.info) ? "problem" : "rendering"));
  document.body.appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 400); }, ms);
}

// Whether the running app has a route yet (spec-independent: new backend pieces arrive one at a time).
// Flask answers OPTIONS for every route it has, and 404 for one it does not.
async function hasRoute(path) {
  try { return (await fetch(path, {method: "OPTIONS", cache: "no-store"})).status !== 404; } catch (e) { return false; }
}

// --- the backdrop: the map's weather behind every page ----------------------------------------------------------
// Pixel weather fills the window behind the panels (app.css): snow for Office, sand for Dust2 and Mirage, embers for
// Inferno and Nuke, rain for Anubis and Overpass, fireflies for Ancient, and a clear night for any other map. It
// follows the map Status last showed (kept in localStorage, so every page opens on the same one), and every page
// starts its loops from the clock, so going from page to page does not jump. It rests while the window is not in
// front (.asleep), and holds still when Windows asks for less motion or Calm mode is on.
const BACKDROP_KEY = "clipper.backdrop";
// by a demo's map name or label ("de_inferno", "Inferno"), or by the scene's setting that 0.12 kept here ("desert")
const WEATHER_OF = {office: "snow", snow: "snow", dust2: "sand", mirage: "sand", desert: "sand",
  inferno: "embers", italy: "embers", village: "embers", nuke: "embers", plant: "embers",
  anubis: "rain", overpass: "rain", river: "rain", ancient: "fireflies", jungle: "fireflies"};
const weatherFor = map => WEATHER_OF[String(map || "").toLowerCase().replace(/^(de|cs)_/, "")] || "night";
// The few weathers drawn with sprites besides their layers: where each sits and how it moves (app.css's keyframes).
const bgRun = (name, seconds, steps, phase) => `${name} ${seconds}s steps(${steps},end) calc(var(--wt,0s) + ${phase}s) infinite`;
const WEATHER_BITS = {
  rain: [[11, "bottom:24px", bgRun("bgwSplash", 1.2, 1, -0.3)], [26, "bottom:24px", bgRun("bgwSplash", 0.9, 1, -0.6)],
    [41, "bottom:24px", bgRun("bgwSplash", 1.2, 1, -0.1)], [59, "bottom:24px", bgRun("bgwSplash", 1, 1, -0.8)],
    [77, "bottom:24px", bgRun("bgwSplash", 1.2, 1, -0.4)], [92, "bottom:24px", bgRun("bgwSplash", 0.9, 1, -0.7)]],
  fireflies: [[9, "bottom:180px", bgRun("bgwFly1", 12, 6, -2) + "," + bgRun("bgwGlow", 3.2, 1, -1)],
    [20, "bottom:100px", bgRun("bgwFly2", 15, 6, -5) + "," + bgRun("bgwGlow", 2.4, 1, -0.4)],
    [33, "bottom:220px", bgRun("bgwFly3", 16, 6, -7) + "," + bgRun("bgwGlow", 4, 1, -2.2)],
    [44, "bottom:60px", bgRun("bgwFly1", 12, 6, -9) + "," + bgRun("bgwGlow", 3.6, 1, -1.7)],
    [55, "bottom:160px", bgRun("bgwFly2", 15, 6, -3) + "," + bgRun("bgwGlow", 3, 1, -0.9)],
    [67, "bottom:100px", bgRun("bgwFly3", 12, 6, -6) + "," + bgRun("bgwGlow", 4, 1, -3)],
    [78, "bottom:200px", bgRun("bgwFly1", 16, 6, -4) + "," + bgRun("bgwGlow", 3.2, 1, -2.6)],
    [89, "bottom:120px", bgRun("bgwFly2", 18, 6, -8) + "," + bgRun("bgwGlow", 4, 1, -0.2)],
    [42, "top:22%", bgRun("bgwFly3", 18, 6, -1) + "," + bgRun("bgwGlow", 3.6, 1, -1.3)],
    [97, "top:46%", bgRun("bgwFly1", 15, 6, -10) + "," + bgRun("bgwGlow", 2.4, 1, -2)]],
  night: [[37, "top:14%", bgRun("bgwTwinkle", 8, 1, -1)], [48, "top:20%", bgRun("bgwTwinkle", 9, 1, -4)],
    [11, "top:74%", bgRun("bgwTwinkle", 8, 1, -2)], [28, "top:85%", bgRun("bgwTwinkle", 12, 1, -7)],
    [46, "top:78%", bgRun("bgwTwinkle", 6, 1, -3)], [64, "top:92%", bgRun("bgwTwinkle", 10, 1, -6)],
    [79, "top:72%", bgRun("bgwTwinkle", 9, 1, -5)], [98, "top:36%", bgRun("bgwTwinkle", 12, 1, -8)]],
};
let backdrop = null;
function backdropMap() { try { return localStorage.getItem(BACKDROP_KEY) || "rooftop"; } catch (e) { return "rooftop"; } }
// Status says which map is on: the backdrop follows, now and on the next page opened.
function setBackdrop(map) {
  if (!map) return;
  try { localStorage.setItem(BACKDROP_KEY, map); } catch (e) { /* a page without storage keeps today's weather */ }
  if (backdrop) paintWeather(backdrop, weatherFor(map));
}
const loopStart = () => (Date.now() / 1000) % 30;

function paintWeather(bg, weather) {
  if (bg.dataset.weather === weather) return;
  bg.dataset.weather = weather;
  // every loop runs a whole number of times an hour, so a phase taken from the clock picks up where the last page was
  bg.style.setProperty("--wt", `-${((Date.now() / 1000) % 3600).toFixed(2)}s`);
  bg.innerHTML = `<i class="w-sky"></i><i class="w-a"></i><i class="w-b"></i><i class="w-c"></i>` +
    (WEATHER_BITS[weather] || []).map(([left, place, run]) => `<i class="w-bit" style="left:${left}%;${place};animation:${run}"></i>`).join("");
}

function drawBackground() {
  const bg = document.createElement("div");
  bg.className = "bg";
  bg.setAttribute("aria-hidden", "true");
  document.body.insertBefore(bg, document.body.firstChild);
  backdrop = bg;
  paintWeather(bg, weatherFor(backdropMap()));
}

// How fast the radar sweeps: quick while a render runs, slow when something needs fixing.
function setSweep(mode) {
  document.documentElement.style.setProperty("--sweep", mode === "live" ? "6s" : mode === "problem" ? "30s" : "16s");
}

// --- the nav bar -----------------------------------------------------------------------------------------
let navPill = null;
let navCount = null;

// The nav bar every page puts at its top -- only on the PC, since a phone gets 403 from the rest.
function renderNav(current) {
  if (!onThisPC()) return;
  const nav = document.createElement("nav");
  nav.className = "nav";
  nav.setAttribute("aria-label", "Pages");
  const face = typeof NUGGET !== "undefined" ? NUGGET.head("rendering") : MARK;
  nav.innerHTML = `<a class="brand" href="/status" aria-label="CS2 Clipper"><span class="face">${face}</span><span>CS2 Clipper</span></a>
    <div class="tabs"><span class="navhover"></span><span class="navline"></span>
    ${NAV_PAGES.map(([href, label]) => `<a class="tab${href === current ? " on" : ""}" href="${href}"${href === current ? ' aria-current="page"' : ""}>${label}${href === "/demos" ? '<span class="count" hidden></span>' : ""}</a>`).join("")}</div>
    <div class="spacer pywebview-drag-region"></div>
    <div class="navpill" hidden><span class="dot"></span><span></span></div>
    <span class="winctl"></span>`;
  document.body.insertBefore(nav, document.body.firstChild);
  navPill = $(".navpill", nav);
  navCount = $(".count", nav);

  const tabs = $(".tabs", nav), hover = $(".navhover", nav), line = $(".navline", nav);
  const at = el => ({left: el.offsetLeft, width: el.offsetWidth});
  const active = () => $(".tab.on", tabs) || $(".tab", tabs);
  const place = (el, show) => {
    const p = at(el);
    line.style.left = (p.left + 16) + "px"; line.style.width = Math.max(0, p.width - 32) + "px";
    hover.style.left = (p.left + 4) + "px"; hover.style.width = (p.width - 8) + "px";
    hover.style.opacity = show ? "1" : "0";
  };
  tabs.addEventListener("pointerover", e => { const t = e.target.closest(".tab"); if (t) place(t, true); });
  tabs.addEventListener("focusin", e => { const t = e.target.closest(".tab"); if (t) place(t, true); });
  tabs.addEventListener("pointerleave", () => place(active(), false));
  tabs.addEventListener("focusout", () => place(active(), false));
  const settle = () => place(active(), false);
  settle();
  document.fonts?.ready.then(settle);
  addEventListener("resize", settle);

  // Our own window buttons, once the window is frameless and the window API has them.
  addEventListener("pywebviewready", () => {
    const w = window.pywebview?.api;
    if (!w?.minimize) return;
    const ctl = $(".winctl", nav);
    ctl.style.display = "flex";
    ctl.innerHTML = `<button class="winbtn" data-w="minimize" aria-label="Minimize"><svg width="12" height="12" viewBox="0 0 12 12" stroke="currentColor" stroke-width="1.2"><line x1="1" y1="6" x2="11" y2="6"/></svg></button>
      <button class="winbtn" data-w="toggle_maximize" aria-label="Maximize"><svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.2"><rect x="1.5" y="1.5" width="9" height="9"/></svg></button>
      <button class="winbtn x" data-w="close" aria-label="Close window"><svg width="12" height="12" viewBox="0 0 12 12" stroke="currentColor" stroke-width="1.2"><line x1="1.5" y1="1.5" x2="10.5" y2="10.5"/><line x1="10.5" y1="1.5" x2="1.5" y2="10.5"/></svg></button>`;
    ctl.addEventListener("click", e => { const b = e.target.closest("[data-w]"); if (b) w[b.dataset.w]?.(); });
  });

  if (current !== "/demos") watchDemoCount();
  if (current !== "/status") watchSummary();
}

// The status pill in the nav. `mode`: "live" (rendering), "hold" (paused or waiting), "bad", or "".
function setNavStatus(text, mode = "") {
  if (!navPill) return;
  navPill.hidden = !text;
  navPill.className = "navpill" + (mode ? " " + mode : "");
  navPill.title = text || "";      // the narrow window shows only its light
  navPill.firstElementChild.className = "dot" + (mode ? " " + mode : "");
  navPill.lastElementChild.textContent = text || "";
}

function setNavCount(n) {
  if (!navCount) return;
  navCount.hidden = !n;
  navCount.textContent = n || "";
}

const pendingRows = rows => rows.filter(r => r.state === "ready" || r.state === "announced");

// How long a Demo link has left (FACEIT keeps one about 30 days: LINK_LIFETIME in alerts.py), rounded up: whole hours
// up to 47 ("5 hours", "1 hour"), then whole days ("2 days"). Past its 30 days it is "expired", for the little while
// before the app marks the match expired and drops its row.
function timeLeft(iso, now = Date.now()) {
  const hours = Math.ceil((new Date(iso) - now) / 3600000);
  if (hours <= 0) return "expired";
  return hours >= 48 ? plural(Math.ceil(hours / 24), "day") : plural(hours, "hour");
}
// The same, ending a sentence about the link: "expires in 3 days", "expires in 1 hour", "expired".
function expires(iso, now) {
  const left = timeLeft(iso, now);
  return left === "expired" ? left : `expires in ${left}`;
}

function watchDemoCount() {
  every(15000, async () => {
    const r = await api("/demos.json");
    if (r.ok) setNavCount(pendingRows((await r.json()).rows).length);
  }, 60000);
}

// Other pages show the summary too once the app has the light /api/summary route (it skips the checks
// that /api/status runs); without it the pill stays hidden there. Each one goes out as a `clipper:summary`
// event too: the map glow (touches.js) follows the map being rendered.
async function watchSummary() {
  if (!(await hasRoute("/api/summary"))) return;
  every(4000, async () => {
    const r = await api("/api/summary");
    if (r.ok) {
      const s = await r.json();
      setNavStatus(navLabel(s), summaryMode(s));
      setSweep(summaryMode(s));
      dispatchEvent(new CustomEvent("clipper:summary", {detail: s}));
    }
  });
}

// The nav pill's words: a problem is said once, on Status with its fix, so here it is only "On hold".
const navLabel = s => s.problems && s.problems.length ? "On hold: open Status" : s.summary;

function summaryMode(s) {
  if (s.problems && s.problems.length) return "bad";
  if (s.rendering) return "live";
  if (s.paused_by || /^Waiting/.test(s.summary || "")) return "hold";
  return "";
}

// --- the Highlights carousel ------------------------------------------------------------------------------
// Each slide's still: the moment of the scene's loop that reads best in that setting (pixelscene.js draws it).
const PIXELSCENE_ROTA = ["rooftop", "village", "desert", "river", "jungle"];   // for slides with no map
const SCENE_STILLS = {rooftop: 21.4, desert: 6.3, jungle: 27.8, village: 13.4, plant: 24.5, railyard: 6.3, snow: 10.5, river: 20.3};
const paintScene = canvas => {
  if (typeof PIXELSCENE === "undefined") return;
  const theme = PIXELSCENE.themeFor(canvas.dataset.map);
  PIXELSCENE.paint(canvas, {map: theme, t: SCENE_STILLS[theme], lift: 56});   // the agent stands above the tabs
};

// Highlights worth putting up front: Aces first, then 4K, 3K, 2K; newer matches first within a type.
const RANK = {ACE: 0, "5K": 0, "4K": 1, "3K": 2, "2K": 3};
function bestHighlights(matches, n) {
  const all = [];
  matches.forEach((m, mi) => (m.highlights || []).forEach(h => {
    if (h.player === null && h.enemy === null) return;
    all.push({...h, match: m, mi});
  }));
  all.sort((a, b) => (RANK[String(a.type).toUpperCase()] ?? 9) - (RANK[String(b.type).toUpperCase()] ?? 9)
    || (b.reasons || []).length - (a.reasons || []).length || a.mi - b.mi);
  return all.slice(0, n);
}
const reasonTags = h => (h.reasons || []).filter(r => r.toLowerCase() !== String(h.type).toLowerCase())
  .map(r => r.charAt(0).toUpperCase() + r.slice(1));
function dateLabel(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

// `root` is an empty .hero element. Slides: {key, type, meta, title, sub, thumb, map, acts: [{label, primary,
// href | play}]}. A slide's thumb is a Reel id: its frame shows when the app serves /reels/<id>.jpg, and
// the pixel scene, in the setting of the slide's map, shows when it does not.
function Carousel(root, {slideMs = 7000, onPlay} = {}) {
  root.setAttribute("aria-roledescription", "carousel");
  root.style.setProperty("--slide", slideMs + "ms");
  root.innerHTML = `<div class="scenes"></div><div class="hshade"></div>
    <div class="hcopy"></div><div class="htabs" role="group" aria-label="Choose a slide"></div>`;
  const scenes = $(".scenes", root), copy = $(".hcopy", root), tabs = $(".htabs", root);
  let slides = [], si = 0, timer = 0, started = 0, left = slideMs, shown = "", sized = 0;
  const repaint = () => scenes.querySelectorAll("canvas").forEach(paintScene);
  // a still is drawn for the box it is in: a new width draws them again (once per frame at most)
  if (typeof ResizeObserver === "function") {
    new ResizeObserver(() => { if (!sized) sized = requestAnimationFrame(() => { sized = 0; repaint(); }); }).observe(root);
  }

  const show = i => {
    si = (i + slides.length) % slides.length;
    [...scenes.children].forEach((n, j) => n.classList.toggle("on", j === si));
    // A slide out of view is inert too: its buttons leave the Tab order, not only the screen.
    [...copy.children].forEach((n, j) => { n.classList.toggle("on", j === si); n.setAttribute("aria-hidden", j === si ? "false" : "true"); n.inert = j !== si; });
    [...tabs.children].forEach((n, j) => {
      n.classList.remove("on");
      n.classList.toggle("seen", j < si);
      n.setAttribute("aria-current", j === si ? "true" : "false");
      if (j === si) { void n.offsetWidth; n.classList.add("on"); }
    });
    schedule(slideMs);
  };
  const schedule = ms => {
    clearTimeout(timer);
    left = ms; started = Date.now();
    if (slides.length > 1 && !root.matches(":hover")) timer = setTimeout(() => show(si + 1), ms);
  };
  root.addEventListener("mouseenter", () => { clearTimeout(timer); left -= Date.now() - started; });
  root.addEventListener("mouseleave", () => schedule(Math.max(400, left)));
  root.addEventListener("keydown", e => {
    if (e.key === "ArrowRight") show(si + 1);
    if (e.key === "ArrowLeft") show(si - 1);
  });
  tabs.addEventListener("click", e => { const b = e.target.closest("[data-i]"); if (b) show(+b.dataset.i); });
  copy.addEventListener("click", e => {
    const b = e.target.closest("[data-play]");
    if (b && onPlay) { e.preventDefault(); onPlay(+b.dataset.play); }
  });

  return {
    set(next) {
      const text = JSON.stringify(next);
      if (text === shown) return;
      const keep = slides[si] && next.findIndex(s => s.key === slides[si].key);
      shown = text; slides = next;
      scenes.innerHTML = slides.map((s, i) => `<div class="scene"><canvas aria-hidden="true" data-map="${esc(s.map || PIXELSCENE_ROTA[i % PIXELSCENE_ROTA.length])}"></canvas>${s.thumb ? `<img alt="" src="/reels/${+s.thumb}.jpg" onerror="this.remove()">` : ""}</div>`).join("");
      repaint();
      copy.innerHTML = slides.map(s => `<div class="hb" aria-hidden="true">
        <div style="display:flex;gap:8px;align-items:center">${s.type ? badge(s.type) : ""}<span style="font-size:14px;color:#D6D3CE">${esc(s.meta)}</span></div>
        <h2>${esc(s.title)}</h2><p>${esc(s.sub)}</p>
        <div class="acts">${(s.acts || []).map(a => a.play !== undefined
          ? `<button class="${a.primary ? "primary" : "ghost"}" data-play="${+a.play}">${ICON.play}${esc(a.label)}</button>`
          : `<a class="${a.primary ? "primary" : "ghost"}" href="${esc(a.href)}">${a.icon === false ? "" : ICON.play}${esc(a.label)}</a>`).join("")}</div></div>`).join("");
      tabs.innerHTML = slides.length > 1 ? slides.map((s, i) => `<button class="htab" data-i="${i}"><span class="tb"><i></i></span>${esc(s.tab || s.title)}</button>`).join("") : "";
      show(keep > -1 ? keep : 0);
    },
  };
}

// --- the window -------------------------------------------------------------------------------------------
// The app has one window. A request for a page (a notification button, the tray's Open, a second
// start) is recorded by the app, and reaches the page in the open window here, within 2 s: it comes
// to the front and goes to the page asked for. Only on the PC, and a page never reacts to the `seq` it
// loaded with (spec: How the app runs, Two processes).
const WINDOW_POLL_MS = 2000;

function watchWindowRequests() {
  if (!onThisPC()) return;
  let seen = null;
  async function poll() {
    try {
      const response = await api("/api/window");
      if (response.ok) {
        const request = await response.json();
        if (seen === null) {
          seen = request.seq;
        } else if (request.seq > seen) {
          seen = request.seq;
          window.pywebview?.api?.front?.();
          if (request.page !== location.pathname) location.href = request.page;
        }
      }
    } catch (error) {
      // the app is busy or has gone; ask again next time
    }
    setTimeout(poll, WINDOW_POLL_MS);
  }
  poll();
}

drawBackground();
watchWindowRequests();
