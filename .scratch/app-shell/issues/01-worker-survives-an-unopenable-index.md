# The worker thread survives an index it cannot open

Status: ready-for-agent

`App.run_worker` (clipper/app.py) opens `Index(self._index_path)` before its guarded loop. A corrupt or
unwritable index raises there and ends the worker thread for good, with no problem published: the
Status page shows the app as idle and nothing is ever grabbed or rendered again.

Done when an exception from opening the index is handled like a failed pass (logged, published as a
problem, retried after `WORKER_ERROR_WAIT_SECONDS`), with a test that makes the first open raise.

From the app-shell build (Task 9, deferred to the final review).
