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
