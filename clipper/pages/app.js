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

// The nav bar every page puts at its top -- only on the PC, since a phone gets 403 from the rest.
function renderNav(current) {
  if (!onThisPC()) return;
  const nav = document.createElement("nav");
  nav.className = "nav";
  nav.innerHTML = NAV_PAGES.map(([href, label]) =>
    `<a href="${href}"${href === current ? ' class="on"' : ""}>${label}</a>`).join("");
  document.body.insertBefore(nav, document.body.firstChild);
}

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

watchWindowRequests();
