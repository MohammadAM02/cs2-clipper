# Cleanups and test gaps

Status: needs-triage

Low priority; nothing here is a bug today.

- Duplication: the page-serving one-liner (x4 in web.py), `esc` copied into four pages, `PERSPECTIVES`
  twice, `GB = 1024**3` in both checks.py and gate.py, `settings._FOLDER_FIELDS` hand-kept instead of
  derived from `FIELDS` (kind "folder"), and the argtypes boilerplate repeated in protect.py.
- Size: web.py (~470 lines) and tests/test_web.py (~850 lines); weigh a split.
- Tests: a secret with an embedded NUL; `data_dir` honouring the override also creates the folder;
  `cli.main`'s move-in call; a ragged HLAE tag in `_version_tuple` (non-numeric parts count as 0);
  `file_version` tested on kernel32.dll rather than our own executable.

From the app-shell build (minors from Tasks 1-3, 7 and 11-13).
