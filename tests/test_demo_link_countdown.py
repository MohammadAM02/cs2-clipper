"""How long a Demo link has left, in words: `timeLeft` and `expires` in app.js.

Demos to grab (the Link expires column, and the line Nugget says under the title) and Status's Demos card all count
FACEIT's link down with them, so the two pages say the same. Each had its own copy, which put "1 hours" on a link in
its last hour, and "expires in 1 hours" on one past its 30 days until the app marked the match expired.
These run app.js under Node.js, as the page runs it, and are skipped when Node is not on PATH.
"""
from __future__ import annotations

import functools
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / "clipper" / "pages" / "app.js"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)

# Time left on the link -> the Link expires column ("2 days"), and the end of a sentence ("Vertigo expires in 1 hour.").
CASES = {
    # 2 days or more: whole days, rounded up
    "a-new-link": (30 * DAY, "30 days", "expires in 30 days"),
    "2-days-and-an-hour": (49 * HOUR, "3 days", "expires in 3 days"),
    "2-days": (48 * HOUR, "2 days", "expires in 2 days"),
    # 2 to 47 hours: whole hours, rounded up
    "47-and-a-half-hours": (47 * HOUR + 30 * MINUTE, "2 days", "expires in 2 days"),
    "47-hours": (47 * HOUR, "47 hours", "expires in 47 hours"),
    "5-hours-20-minutes": (5 * HOUR + 20 * MINUTE, "6 hours", "expires in 6 hours"),
    "an-hour-and-a-minute": (61 * MINUTE, "2 hours", "expires in 2 hours"),
    "2-hours": (2 * HOUR, "2 hours", "expires in 2 hours"),
    # the last hour: "1 hour", never "1 hours"
    "1-hour": (HOUR, "1 hour", "expires in 1 hour"),
    "59-minutes": (59 * MINUTE, "1 hour", "expires in 1 hour"),
    "a-second": (timedelta(seconds=1), "1 hour", "expires in 1 hour"),
    # past its 30 days, until the app marks the match expired and the row goes
    "the-moment-it-runs-out": (timedelta(0), "expired", "expired"),
    "a-second-late": (-timedelta(seconds=1), "expired", "expired"),
    "3-days-late": (-3 * DAY, "expired", "expired"),
}

# app.js draws the backdrop and starts listening as it loads, so Node runs it in a page that takes whatever it is
# told and shows nothing.
PAGE = """
const anything = new Proxy(function () {}, {
  get: (_, key) => key === Symbol.toPrimitive ? () => '' : anything, apply: () => anything, set: () => true,
});
const page = {document: anything, addEventListener() {}, location: {hostname: 'example.test'}};
"""


@functools.cache
def said() -> dict[str, list[str]]:
    """What `timeLeft` and `expires` say for each of CASES, from app.js as it ships, in one run of Node.js."""
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    now = int(NOW.timestamp() * 1000)
    ends = [(NOW + remaining).isoformat() for remaining, _, _ in CASES.values()]
    script = (PAGE + f"const src = require('fs').readFileSync({json.dumps(str(APP_JS))}, 'utf8');"
              "const {timeLeft, expires} = require('vm').runInNewContext(src + '\\n;({timeLeft, expires})', page);"
              f"const now = {now}, ends = {json.dumps(ends)};"
              "process.stdout.write(JSON.stringify(ends.map(end => [timeLeft(end, now), expires(end, now)])));")
    run = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return dict(zip(CASES, json.loads(run.stdout)))


@pytest.mark.node
@pytest.mark.parametrize("case", CASES)
def test_a_demo_link_says_how_long_it_has_left(case):
    _, left, sentence = CASES[case]
    assert said()[case] == [left, sentence]
