# Spec: App shell — a desktop app around the pipeline

Status: built on branch app-shell (2026-09-29); the hand checks under To confirm are the user's.
The first of three pieces that turn the pipeline into an installable desktop app: **(1) this app
shell**, (2) the release pipeline (installer, GitHub Releases, auto-update) and (3) the fresh-PC
setup wizard. Each piece gets its own spec. Built on master after match alerts (merged 2026-09-27).
Adopts the app shell of Aegis Clipper (`thelifeofsuleyman/cs2-clipper`, MIT, © 2026
thelifeofsuleyman), studied 2026-09-27.

## Problem

Today the app is a terminal command with no window. What it is doing shows only through
`clipper status`, the log file and notifications; Retry and Resume are terminal commands; settings
live in `clipper.toml` and `.env` inside the repo, and so does the index. That rules out an installed `.exe` (piece 2) and a first-run setup (piece 3), and it makes
the app awkward to use day to day.

Aegis Clipper already has a working desktop shell: a local web server whose pages show in a native
window, with a tray icon. We adopt it instead of designing our own.

## Decisions

| Topic | Decision |
| --- | --- |
| Shell | Aegis's, adapted: Flask served by waitress, a pywebview (WebView2) window, a pystray tray icon, and its fallbacks |
| Processes | **Two**: the main process (tray, web server, worker) and a **separate window process**, so closing the window frees its memory (about 150–300 MB) |
| At sign-in | **Tray only** (`--background`). The start-at-sign-in entry itself comes with piece 2's installer |
| Start menu, or a second launch | Opens the window, or shows the running copy's window |
| Pages | **Status**, **Demos to grab**, **Reels**, **Settings** |
| Reach from other devices | **Demos to grab only** (view, Skip, Undo). Everything else answers this PC only, and a new page is PC-only unless deliberately listed |
| Actions | Need a marker header that other websites cannot add; PC-only pages also check the `Host` header |
| Quit during a render | Asks: **quit now** (the hooked CS2 closes first; the render is redone) or **when this render finishes** |
| Crash safety | HLAE runs inside a kill-on-close Windows job object (Aegis's `winjob.py`) |
| Start-up problems | Never stop the app: Status and the tray say what is wrong, and it checks again every minute |
| App data | `%LOCALAPPDATA%\CS2Clipper\`: settings, index, lock, logs, tools and analyses. `CLIPPER_DATA_DIR` overrides it |
| Clips folder | Unchanged (`E:\cs2clips` on this PC) |
| Settings | `settings.json` holds **only changed values**; defaults fill in the rest; the Settings page validates before saving |
| FACEIT key | **Encrypted** with Windows' per-user protection (DPAPI); never logged, never sent to a page or another device |
| Moving in | The first start of the new code copies `clipper.toml`, `.env`'s FACEIT values, `data\clipper.sqlite` and `home\` from the repo |
| Terminal | `uv run clipper` opens the app; `status`, `retry`, `resume`, `highlights` stay for development; `run --headless` is today's windowless run; `install` / `uninstall` are removed |
| Attribution | Every file adapted from Aegis carries its MIT notice in the module docstring, as `clipper/rating.py` does for faceitperf |

## How the app runs

### Two processes

- **The main process** is `python -m clipper` from source (`CS2Clipper.exe` once piece 2 packages
  it). Its main thread runs the tray icon. Background threads run the web server (Flask served by
  waitress, as Aegis's `_serve` does) and the worker loop (the existing `Worker.tick()` every
  `poll_seconds`, match alerts included).
- The worker thread opens its own `Index`, because an SQLite connection belongs to the thread that
  made it. Every web request opens its own, as the Demos page does today.
- **The window process** is the same Python (or `CS2Clipper.exe`) started with `--window <url>`. It
  shows the page in a pywebview window on WebView2. If pywebview cannot load, or has only the old
  Internet Explorer engine (which cannot run the pages' scripts), it opens a chromeless Edge or
  Chrome `--app` window with its own profile (Aegis's `_open_app_mode`), and failing that, the
  default browser. The process ends when its window closes, taking WebView2 with it, and it sits in
  the app's job object, so it also ends when the main process does.
- **One window at a time.** While it is open, "Open" brings it to the front instead of starting
  another, and a request for a page (a notification button, the tray) switches the open window to
  it. The page asks the server every two seconds whether to switch.

### Starting

- With `--background` (the sign-in mode) the app shows only the tray icon; without it, the tray and
  the window.
- A second start finds the running copy (Aegis's `/health` check), asks it to show its window (on
  the page given by `--open <page>`, if any), and exits. The lock file stays as the hard guarantee
  that two copies never render.
- Order: the single-instance check → move in (first start only) → settings → web server → worker.
  A step that fails does not stop the app (see When something goes wrong).

### The tray

- The tooltip shows what the app is doing, from the same one-line summary the Status page shows:
  *Idle*, *Waiting: <the Gate's reasons>*, *Rendering <map> (<perspective> view)*, *Paused*, or a
  start-up problem.
- Menu: **Open CS2 Clipper** (the default), **Open clips folder**, **Pause rendering** / **Resume
  rendering**, **Quit**.
- Pause sets the same `paused` flag the render pause uses, recording that you paused it, so Status
  can say "Paused by you" rather than "Paused after 3 failed renders". Resume clears the flag and
  the failure count, as `clipper resume` does.

### Closing and quitting

- Closing the window ends only the window process; the app keeps working in the tray.
- **Quit** (tray, or the Status page's button): with no Render Job running, the app exits. With
  one running, it asks:
  - **Quit now**: the render stops the way a FACEIT AC abort stops it (the hooked CS2 is
    closed), the attempt is recorded `aborted`, and it is redone at the next start.
  - **Quit when this render finishes**: the worker takes no new step after the render, then the app
    exits. The tray says "Quitting after this render".
- Every render runs HLAE inside a Windows job object that closes its processes when the app's
  process ends for any reason (a crash, End task). The hooked CS2 is HLAE's descendant, so it should
  close too; see To confirm, item 4.

### Notifications

Unchanged, buttons included. In the installed app (piece 2) the buttons open pages in the app window
through a `cs2clipper:` link the installer registers; until then they open the browser, as today.

### The terminal

- `uv run clipper` starts the app. `clipper run --headless` runs the worker and the web server
  without tray or window: today's `clipper run`, plus the pages.
- `clipper status`, `retry`, `resume` and `highlights` stay, for development.
- `clipper install` / `uninstall` (the Task Scheduler task) are removed: piece 2's installer adds
  start-at-sign-in. The task is not registered on this PC (checked 2026-09-27).

## Pages

Plain HTML, CSS and JavaScript files inside the package, with no build step; the pages fetch their
data from JSON endpoints (Aegis's pattern). Unlike Aegis, the pages are files rather than Python
strings; piece 2's PyInstaller spec bundles them.

### Status (opens first)

- **Now:** the one-line summary, how long the current render has run, and Resume when paused.
- **Checks**, each ✓ or ✗ with a one-line hint:
  - csda: found;
  - HLAE: found, its version (from its `changelog.xml`), and whether a newer advancedfx release is
    out (the failure of 2026-09-27). Checked at start-up and then daily;
  - FFmpeg: found;
  - clips folder: exists, and its free space against `min_free_gb`;
  - match alerts: on, or off with the reason.
- **Demos:** every Demo that is not `done` or `skipped`, with its step and its Render Jobs
  (Perspective, attempt, state). A failed Demo shows its error and **Retry** (what `clipper retry`
  does).
- **Log:** the last 200 lines of the app log (Aegis's ring buffer).
- **Quit.**

### Demos to grab

The match-alerts page, moved into the app with one change: Skip and Undo now send the marker header
(see Reach from other devices). The table, Open / Skip / Undo and the rules (Open only on the PC)
stay as they are. `GET /demos`, `GET /demos.json` and `POST /demos/<match id>/skip|undo` keep their
paths, so notifications already sent keep working.

### Reels

- Every `done` Demo whose match has Reels, newest match first: map, date, score and result.
- Under each, its selected Highlights in round order: round, Highlight Type and reasons, with
  **Player** and **Enemy** buttons that play that Reel in the page, and **Open folder**, which opens
  the match's folder in Explorer.
- Videos are served with HTTP Range support (Flask's `send_file`), so seeking works.

### Settings

- Every setting in today's `Config`, grouped: **You** (SteamID, FACEIT nickname, FACEIT key),
  **Folders** (clips folder, Downloads, HLAE, FFmpeg), **Picture** (aspect ratio,
  Sequence event, padding), **Rendering** (top N, stall and launch timeouts, heads-up, minimum free
  space), **Match alerts** (on/off, minutes after playing), **App** (the web server's port, still
  `page_port`).
- The FACEIT key field is write-only: the page shows whether a key is set, never the key.
- **Save** checks every field (type, range, allowed values, folders that must exist) and refuses the
  whole save with a message beside each bad field. Most changes apply at the worker's next tick;
  the port applies at the next start, and the page says so. A new clips folder applies to Demos
  taken from then on; files already in the old one stay where they are.

## Reach from other devices

- The web server listens on every interface, as the Demos page does today, so your phone reaches it
  over Wi-Fi and Tailscale.
- **Every route is PC-only unless it is on an explicit list of phone routes:** `GET /demos`,
  `GET /demos.json`, `POST /demos/<match id>/skip|undo`, and the stylesheet and script those pages
  load. A PC-only route answers 403 to any request that does not come from 127.0.0.1 or ::1.
- PC-only routes also answer 403 when the `Host` header is anything but `127.0.0.1:<port>` or
  `localhost:<port>`, which defeats DNS rebinding.
- Every request that changes something (POST, PUT, DELETE), phone routes included, must carry the
  header `X-CS2-Clipper: 1`. Our pages add it; a web page on another site cannot send it, because
  the server never answers the browser's permission check (no CORS headers are ever sent).
- Aegis has none of this: its server listens on 127.0.0.1 only and has no action guard.

## Settings and data

### The app data folder

`%LOCALAPPDATA%\CS2Clipper\` (Aegis uses the roaming `%APPDATA%`; ours holds this PC's paths and
databases):

```
settings.json    what you changed; defaults fill in the rest
clipper.sqlite   the index (was data\clipper.sqlite)
clipper.lock     single instance (was data\clipper.lock)
logs\            clipper.log and one log per analysis and render (were in the clips folder)
tools\           the FFmpeg, HLAE and csda that Setup installs
analyses\        what csda found in each match
```

`CLIPPER_DATA_DIR` points it elsewhere, for tests and development. The clips folder (Demos, renders,
Reels) is unchanged, and logs written before the move stay in `E:\cs2clips\logs`. With the logs in
the app data folder, the app can still log when the clips drive is missing.

### settings.json

- Holds only values that differ from the defaults, so a later version's new defaults reach you. This
  is a deliberate difference from Aegis, which saves every value.
- Written atomically (Aegis's `atomic_write_text`); read at start and whenever it changes. The worker
  builds its `Config` from it.
- A key the app does not know is kept and ignored (a newer version may have written it). A value that
  fails validation (a hand edit) falls back to its default, and Status shows a warning. This replaces
  "an unknown setting stops the app".
- The defaults no longer contain your SteamID or `E:/cs2clips`. Until both the SteamID and the clips
  folder are set, the worker takes no Demo from Downloads and does nothing else, and Status says what
  to set. On this PC, moving in sets them; on a fresh PC, the setup wizard (piece 3) will.

### The FACEIT key

- Stored in `settings.json` as `faceit_api_key_protected`: the key encrypted with `CryptProtectData`
  (DPAPI, per user, through ctypes, so no new dependency), in base64.
- Decrypted only when calling FACEIT. Never logged, and never part of any response.

### Moving in from the repo

At start, when the app data folder has no `settings.json` yet and the repo layout exists (running
from source), the app copies:

- `clipper.toml` → `settings.json`, keeping only values that differ from the defaults. The old
  built-in SteamID (`76561198192858303`) and clips folder (`E:/cs2clips`) are written too, unless
  the file set them, because they are no longer defaults;
- `.env`: `FACEIT_NICKNAME` → settings, `FACEIT_API_KEY` → encrypted into settings;
- `data\clipper.sqlite` → `clipper.sqlite`.

Copied, never moved: the originals stay until you delete them. One log line lists what was copied.
It happens once; afterwards only `settings.json` counts. The installed app (piece 2) will find the
folder already filled on this PC.

## When something goes wrong

| What | Then |
| --- | --- |
| The clips folder is missing, a tool is not found, or a required setting is unset | The app keeps running; the worker skips its ticks; Status and the tray tooltip say what is wrong; checked again every minute; work resumes once fixed |
| The web server cannot bind port 8765 | The next free port up to 8774, as the Demos page does today; the tray, window and notifications use the port it got. None free: the app runs without pages, the tray says so, and match alerts switch off with the reason (as today) |
| The window cannot load | A Chromium `--app` window, then the default browser (Aegis's fallback) |
| The main process crashes or is ended | The job object closes HLAE, the hooked CS2 and the window; at the next start, the Render Job left `running` is marked interrupted and redone (exists today) |
| Quit during a render | The choice under Closing and quitting |
| A setting fails validation on Save | Nothing is saved; each bad field says why |
| `settings.json` holds a bad value (a hand edit) | That setting uses its default; Status warns |
| The HLAE release check cannot reach GitHub | The check reads "couldn't check"; nothing else changes |

## Code units

| Module | Job | From Aegis |
| --- | --- | --- |
| `clipper/app.py` | Entry: modes, single instance, start-up order, threads, quit | adapted from `aegis/app.py` |
| `clipper/window.py` | The window process: pywebview, then Chromium `--app`, then the browser | adapted from `aegis/app.py` |
| `clipper/tray.py` | Tray icon, tooltip and menu | adapted from `aegis/app.py` |
| `clipper/web.py` | The Flask app: pages, JSON endpoints, the reach and action guards; takes over `page.py`'s routes | structure from `aegis/web.py` |
| `clipper/pages/` | `status.html`, `demos.html` (was `page.html`), `reels.html`, `settings.html`, shared `app.css` and `app.js` | new |
| `clipper/paths.py` | The app data folder, `CLIPPER_DATA_DIR`, atomic writes | adapted from `aegis/paths.py` |
| `clipper/settings.py` | `settings.json`: load, validate, save, watch; builds `Config` | new (Aegis's merge idea) |
| `clipper/protect.py` | DPAPI encrypt and decrypt | new |
| `clipper/move_in.py` | The one-time copy from the repo | new |
| `clipper/applog.py` | A logging handler that keeps the last lines for Status | adapted from `aegis/log.py` |
| `clipper/winjob.py` | Kill-on-close job object | copied from `aegis/winjob.py` |
| `clipper/checks.py` | Status's checks, including the HLAE release check | new |
| `clipper/state.py` | The "what it's doing now" summary shared by the worker, the tray and Status | new |

Changed:

- `worker.py`: runs on its own thread; publishes to `state`; honours quit-now and
  quit-after-this-render; does nothing while a required setting is unset.
- `render.py`: the render runs in the job object; a stop request joins `should_abort`.
- `config.py`: `Config` stays the one object modules read; its paths come from `paths.py`, and
  `settings.py` replaces `load_config` and `load_env`.
- `faceit.py` and `alerts.py`: the key comes from `settings.py` (decrypted), not `.env`.
- `cli.py`: the default command starts the app; `run --headless`; `install` and `uninstall` removed.
- Removed: `page.py` (moved into `web.py`) and `install.py`.

New dependencies: `flask`, `waitress`, `pywebview`, `pythonnet` (Windows only; pywebview's WebView2
backend), `pystray` and `Pillow` (the tray icon).

## Testing

Written test-first, as before.

- **Web**, through Flask's test client (Aegis's approach): every page and endpoint. A PC-only route
  refuses a non-loopback client and a foreign `Host`; the phone routes answer other devices; a
  change without `X-CS2-Clipper` is refused; no response ever contains the FACEIT key, the settings
  endpoints included.
- **Settings:** defaults fill in; only changed values are written; one message per bad field;
  unknown keys are kept; a bad hand-edited value falls back and warns.
- **protect:** a round trip; the stored text does not contain the key.
- **Moving in**, from a fake repo layout in a temp folder: every item copied, the originals
  untouched, it runs once, and the old built-in SteamID and clips folder are carried over.
- **Quit during a render**, with a scripted render: quit now stops the render like a FACEIT AC abort and
  records it `aborted`; quit-after exits once the render returns.
- **Single instance:** a second start hands over to the first (a fake `/health`).
- **state:** the one-line summary for idle, waiting (the Gate's reasons), rendering, paused (by you
  or after failures) and a start-up problem.
- **Start-up problems:** a missing clips folder or SteamID keeps the worker idle and shows the reason.
- **checks:** each check against fakes, including the HLAE check, which compares the installed
  version with a fake release list and reads "couldn't check" when GitHub cannot be reached.
- The tray and the window (pystray, pywebview) are import-guarded and not unit-tested.

### To confirm on this PC (by hand)

1. Started with `--background`: only the tray icon, and no WebView2 processes in Task Manager.
2. Opening and then closing the window: the WebView2 processes go away when it closes.
3. Quit now during a render: the hooked CS2 closes, and the next start redoes the render.
4. Ending the main process in Task Manager during a render: HLAE, the hooked CS2 and the window close.
5. A notification button opens its page (in the browser, until piece 2).
6. Your phone reaches Demos to grab over Wi-Fi and Tailscale, and gets 403 from Status.
7. The first start copied the repo's settings, FACEIT key and index; the Reels page shows
   the Mirage Reels rendered on 2026-09-27.

## Open

- Whether the hooked CS2 stays inside the render's job object when HLAE starts it (To confirm, item
  4). If it escapes, this spec needs another way to close an orphaned hooked CS2.

## Out of scope

- **Piece 2, the release pipeline:** the PyInstaller build, the Inno Setup installer (start at
  sign-in, the `cs2clipper:` link, WebView2), GitHub Actions releases, auto-update.
- **Piece 3, the fresh-PC setup wizard:** installing HLAE, csda and FFmpeg; asking for the
  SteamID and the clips folder; updating HLAE.
- The Clip library for the phone (feed, thumbnails, POV toggle); Reels stay PC-only until then.
- The clip selection change (3+ Frags) and any other selection rule.
- Sharing and montages.

## Changes to other documents

- `.scratch/orchestrator/spec.md`, Running it: `clipper install`, `clipper.toml` and the in-repo
  index give way to this spec.
- `.scratch/clip-library/spec.md`: "Approach A: static library" and "nothing of ours listens on the
  network" give way to the app's web server; the library itself is still to design.
- `.scratch/match-alerts/spec.md`: the page is served by the app's web server with the same reach
  rules, and the FACEIT credentials come from Settings instead of `.env`.
