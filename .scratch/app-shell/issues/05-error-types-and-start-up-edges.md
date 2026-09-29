# Error types and start-up edges

Status: ready-for-agent

- `protect.protect()` raises `ctypes.WinError` when CryptProtectData fails; callers expect
  `ProtectError` (clipper/protect.py).
- `applog.setup` removes the old handlers before it knows it can build the new ones, so a failing
  logs folder leaves the root logger with no handler (clipper/applog.py).
- `settings._fmt` crashes on a field with a `None` bound (no current field has one).
- `App.checks` has no lock, so two requests that find the cache stale both run the checks.

From the app-shell build (Tasks 1, 2, 4 and 11-13 minors).
