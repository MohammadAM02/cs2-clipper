// Shared by every page (spec: Pages). No framework, no build step.
"use strict";

const MARKER_HEADER = "X-CS2-Clipper";
const NAV_PAGES = [["/status", "Status"], ["/demos", "Demos to grab"], ["/reels", "Reels"], ["/progress", "Progress"],
  ["/heatmaps", "Heatmaps"], ["/settings", "Settings"]];
// The side rack's two groups, each named above its first page; Settings sits apart, at the foot.
const NAV_GROUPS = {"/status": "Clips", "/progress": "Your game"};

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
const MARK = '<svg width="28" height="28" viewBox="0 0 64 64" aria-hidden="true"><rect x="2" y="2" width="60" height="60" rx="14" fill="#1E2228" stroke="#3A404A" stroke-width="2"/><polygon points="18,50 30.05,43.22 36.22,24.25 18,14" fill="#FF5500"/><polygon points="51.6,28.4 40.86,22.36 36.06,37.14" fill="#FF5500"/></svg>';
// The icons are pixel art on an 8 x 8 grid, shown at 2 or 3 px a pixel so they stay crisp.
// Line icons on a 24-unit grid, drawn in the text colour.
const svgIcon = (body, size = 16, cls = "") => `<svg${cls ? ` class="${cls}"` : ""} width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
const ICON = {
  play: svgIcon('<path d="M7.5 5.2v13.6a.8.8 0 0 0 1.2.7l11-6.8a.8.8 0 0 0 0-1.4l-11-6.8a.8.8 0 0 0-1.2.7z" fill="currentColor" stroke="none"/>'),
  pause: svgIcon('<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/>'),
  check: svgIcon('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
  bang: svgIcon('<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.01"/>'),
  dash: svgIcon('<path d="M6 12h12"/>'),
  info: svgIcon('<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.5v.01"/>'),
  next: svgIcon('<path d="M9 6l6 6-6 6"/>'),
  out: svgIcon('<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>'),
  folder: svgIcon('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
  bin: svgIcon('<path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-12M10 11v5M14 11v5"/>'),
  chev: svgIcon('<path d="M6 9l6 6 6-6"/>', 22, "chev"),
  skip: svgIcon('<path d="M6 5.5v13l9-6.5z" fill="currentColor" stroke="none"/><path d="M18 5v14"/>'),
  copy: svgIcon('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
  down: svgIcon('<path d="M12 4v13M6 11l6 6 6-6"/>'),
  empty: svgIcon('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18"/>', 26),
  chart: svgIcon('<path d="M5 20v-6M12 20V5M19 20v-10"/>', 26),
  target: svgIcon('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>', 26),
};
// The side rack's icons, one per page.
const NAV_ICONS = {
  "/status": svgIcon('<path d="M3 12h4l3-8 4 16 3-8h4"/>', 20),
  "/demos": svgIcon('<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>', 20),
  "/reels": svgIcon('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>', 20),
  "/progress": svgIcon('<path d="M5 20v-6M12 20V5M19 20v-10"/>', 20),
  "/heatmaps": svgIcon('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>', 20),
  "/settings": svgIcon('<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>', 20),
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

// A map as people say it: "de_inferno" is Inferno (alerts.py's map_label, for the pages the app labels itself).
const mapName = map => String(map || "").replace(/^(de|cs)_/, "").split("_").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");

// A run of numbers as a line (app.css, .spark), oldest first, with a soft fill under it. A gap (null) is skipped.
// Nothing is drawn for fewer than two values.
function spark(values) {
  const flat = '<svg class="spark" viewBox="0 0 100 34" preserveAspectRatio="none" aria-hidden="true"><path class="base" d="M0 17H100"/></svg>';
  const known = values.map((v, i) => [i, v]).filter(([, v]) => v !== null && v !== undefined);
  if (known.length < 2) return flat;
  const low = Math.min(...known.map(([, v]) => v)), high = Math.max(...known.map(([, v]) => v)), span = high - low || 1;
  const x = i => (values.length === 1 ? 50 : 100 * i / (values.length - 1)).toFixed(1), y = v => (30 - 26 * (v - low) / span).toFixed(1);
  const pts = known.map(([i, v]) => [x(i), y(v)]);
  let d = `M${pts[0][0]} ${pts[0][1]}`;
  for (let k = 1; k < pts.length; k++) {                      // a gentle curve through every point
    const [x0, y0] = pts[k - 1], [x1, y1] = pts[k], mid = ((+x0 + +x1) / 2).toFixed(1);
    d += ` C${mid} ${y0} ${mid} ${y1} ${x1} ${y1}`;
  }
  const area = `${d} L${pts[pts.length - 1][0]} 34 L${pts[0][0]} 34 Z`;
  return `<svg class="spark" viewBox="0 0 100 34" preserveAspectRatio="none" aria-hidden="true"><path class="fill" d="${area}"/><path d="${d}"/></svg>`;
}

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
  document.body.appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 400); }, ms);
}

// Whether the running app has a route yet (spec-independent: new backend pieces arrive one at a time).
// Flask answers OPTIONS for every route it has, and 404 for one it does not.
async function hasRoute(path) {
  try { return (await fetch(path, {method: "OPTIONS", cache: "no-store"})).status !== 404; } catch (e) { return false; }
}

// --- the background, and the map Status last showed --------------------------------------------------------------
// Every page stands on the same slate (app.css, .bg). The map Status last showed is kept (localStorage) so the other
// pages can tint things with it.
const BACKDROP_KEY = "clipper.backdrop";
function backdropMap() { try { return localStorage.getItem(BACKDROP_KEY) || ""; } catch (e) { return ""; } }
function setBackdrop(map) {
  if (!map) return;
  try { localStorage.setItem(BACKDROP_KEY, map); } catch (e) { /* a page without storage forgets it */ }
}
function drawBackground() {
  const bg = document.createElement("div");
  bg.className = "bg";
  bg.setAttribute("aria-hidden", "true");
  document.body.insertBefore(bg, document.body.firstChild);
}

// How fast the radar sweeps: quick while a render runs, slow when something needs fixing.
function setSweep(mode) {
  document.documentElement.style.setProperty("--sweep", mode === "live" ? "6s" : mode === "problem" ? "30s" : "16s");
}

// --- the nav bar -----------------------------------------------------------------------------------------
let navPill = null;
let navCount = null;

// The shell every page stands in: the side rack with the pages, and the strip along the top with the page's name,
// the status pill and the window's buttons (app.css). Only on the PC, since a phone gets 403 from the rest.
function renderNav(current) {
  if (!onThisPC()) return;
  document.body.classList.add("has-nav");
  const nav = document.createElement("nav");
  nav.className = "nav";
  nav.setAttribute("aria-label", "Pages");
  const face = MARK;
  const tab = ([href, label], i) => {
    const group = NAV_GROUPS[href];
    const cls = ["tab", href === current ? "on" : "", group && i ? "sep" : "", href === "/settings" ? "foot" : ""].filter(Boolean).join(" ");
    return `${group ? `<div class="navgroup">${group}</div>` : ""}<a class="${cls}" href="${href}" title="${label}"${href === current ? ' aria-current="page"' : ""}>${NAV_ICONS[href] || ""}<span>${label}</span>${href === "/demos" ? '<span class="count" hidden></span>' : ""}</a>`;
  };
  nav.innerHTML = `<a class="brand" href="/status" aria-label="CS2 Clipper"><span class="face">${face}</span><span>CS2<br>Clipper</span></a>
    <div class="tabs">${NAV_PAGES.map(tab).join("")}</div>`;
  const bar = document.createElement("header");
  bar.className = "topbar";
  bar.innerHTML = `<div class="crumb">${esc((NAV_PAGES.find(([href]) => href === current) || ["", ""])[1])}</div>
    <div class="spacer pywebview-drag-region"></div>
    <div class="navpill" hidden><span class="dot"></span><span></span></div>
    <span class="winctl"></span>`;
  document.body.insertBefore(bar, document.body.firstChild);
  document.body.insertBefore(nav, bar);
  navPill = $(".navpill", bar);
  navCount = $(".count", nav);

  // Our own window buttons, once the window is frameless and the window API has them.
  addEventListener("pywebviewready", () => {
    const w = window.pywebview?.api;
    if (!w?.minimize) return;
    const ctl = $(".winctl", bar);
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
// Highlights worth putting up front: Aces first, then 4K, 3K, 2K; newer matches first within a type.
const RANK = {ACE: 0, "5K": 0, "4K": 1, "3K": 2, "2K": 3};
function bestHighlights(matches, n) {
  const all = [];
  matches.forEach((m, mi) => (m.highlights || []).forEach(h => {
    if (h.player === null && h.enemy === null || String(h.type).toUpperCase() === "DEATH") return;   // not a best
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
  let slides = [], si = 0, timer = 0, started = 0, left = slideMs, shown = "";

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
      scenes.innerHTML = slides.map(s => {
        const name = mapName(s.map), tint = MAP_TINTS[name] || "#FF5500";
        return `<div class="scene" style="--tint:${tint}"><span class="mapword" aria-hidden="true">${esc(name)}</span>${s.thumb ? `<img alt="" src="/reels/${+s.thumb}.jpg" onerror="this.remove()">` : ""}</div>`;
      }).join("");
      copy.innerHTML = slides.map(s => `<div class="hb" aria-hidden="true">
        <div style="display:flex;gap:8px;align-items:center">${s.type ? badge(s.type) : ""}<span style="font-size:13px;color:var(--ink2)">${esc(s.meta)}</span></div>
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
