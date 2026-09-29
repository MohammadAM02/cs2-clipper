# The window's and the tray's rough edges

Status: needs-triage

Each needs a decision more than code:

- The window process sits in the app's kill-on-close job object. Its last resort, `webbrowser.open`,
  runs inside the job, so if it has to start the default browser, that browser closes when clipper
  quits. Reached only with no WebView2 and no Edge, Chrome or Brave (clipper/window.py).
- The tray menu is rebuilt only when the app's state changes (pystray replaces the menu on each rebuild).
- Ctrl+C is slow to stop the app while the tray runs.
- The window process logs to stderr only, which goes nowhere without a console.

From the app-shell build (Task 14 concerns and the batch D fix).
