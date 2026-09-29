# "The port changes at the next start" shows on every save while on a fallback port

Status: ready-for-agent

`PUT /api/settings` (clipper/web.py) computes `restart_needed` as the saved `page_port` differing from
the port in use. When 8765 was taken at start and the app fell back to 8766, every save reports that a
restart is needed, even one that did not touch the port.

Done when the notice shows only when this save changed `page_port`, with a test on a fallback port.

From the app-shell build (Task 13, deferred to the final review).
