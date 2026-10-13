"""Windows notifications (spec: The Gate, heads-up). Best effort: a notification that cannot be shown
is logged, never raised.

Toasts go out as CS2 Clipper, with its mark: `register` makes the app's own AppUserModelID known to Windows at
every start, and the installer's Start menu shortcut carries the same id. A button opens a URL, which for the
app's own buttons is a cs2clipper: link (clipper.links) that shows the app window, never a browser tab."""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

log = logging.getLogger(__name__)

APP_ID = "CS2Clipper.App"      # the app's own AppUserModelID; packaging/cs2clipper.iss gives its shortcut the same
APP_NAME = "CS2 Clipper"
# PowerShell's own AppUserModelID, which needs no registering: toasts use it until `register` has run.
POWERSHELL_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_app_id = POWERSHELL_ID
_SCRIPT = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
    " ContentType = WindowsRuntime] | Out-Null;"
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
    " ContentType = WindowsRuntime] | Out-Null;"
    "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument;"
    "$xml.LoadXml($env:CLIPPER_TOAST);"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:CLIPPER_TOAST_APP)"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($xml))"
)


Action = tuple[str, str | None]   # (button label, URL to open); no URL means "dismiss"


def register(icon: Path, write: Callable[[str, str, str], None]) -> None:
    """Makes APP_ID known to Windows as CS2 Clipper with `icon`, its mark as a PNG, and sends every toast after
    it under that id. When it fails (it raises OSError), toasts keep going out under PowerShell's id: a toast
    labelled Windows PowerShell beats none. `write(key, name, value)` sets a string under HKEY_CURRENT_USER
    (`windows.set_user_value`)."""
    global _app_id
    key = rf"Software\Classes\AppUserModelId\{APP_ID}"
    write(key, "DisplayName", APP_NAME)
    write(key, "IconUri", str(icon))
    _app_id = APP_ID


def app_id() -> str:
    """The id toasts go out under now: APP_ID once `register` has run, else PowerShell's."""
    return _app_id


def toast_xml(title: str, body: str, actions: Sequence[Action] = ()) -> str:
    buttons = "".join(
        f'<action content={quoteattr(label)} activationType="protocol" arguments={quoteattr(url)}/>'
        if url else f'<action content={quoteattr(label)} activationType="system" arguments="dismiss"/>'
        for label, url in actions
    )
    first_url = next((url for _, url in actions if url), None)
    launch = f' activationType="protocol" launch={quoteattr(first_url)}' if first_url else ""
    return (
        f"<toast{launch}><visual><binding template='ToastGeneric'>"
        f"<text>{escape(title)}</text><text>{escape(body)}</text>"
        "</binding></visual>" + (f"<actions>{buttons}</actions>" if buttons else "") + "</toast>"
    )


def notify(title: str, body: str, actions: Sequence[Action] = ()) -> None:
    env = {**os.environ, "CLIPPER_TOAST": toast_xml(title, body, actions), "CLIPPER_TOAST_APP": _app_id}
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
            env=env, capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("notification failed: %s", exc)
        return
    if result.returncode != 0:
        log.warning("notification failed: %s", result.stderr.strip()[:300])
