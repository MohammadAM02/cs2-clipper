"""Windows notifications (spec: The Gate, heads-up). Best effort: a notification that cannot be shown
is logged, never raised."""

from __future__ import annotations

import logging
import os
import subprocess
from xml.sax.saxutils import escape

log = logging.getLogger(__name__)

# PowerShell's own AppUserModelID, so the toast needs no registered app.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_SCRIPT = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
    " ContentType = WindowsRuntime] | Out-Null;"
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
    " ContentType = WindowsRuntime] | Out-Null;"
    "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument;"
    "$xml.LoadXml($env:CLIPPER_TOAST);"
    f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_APP_ID}')"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($xml))"
)


def toast_xml(title: str, body: str) -> str:
    return (
        "<toast><visual><binding template='ToastGeneric'>"
        f"<text>{escape(title)}</text><text>{escape(body)}</text>"
        "</binding></visual></toast>"
    )


def notify(title: str, body: str) -> None:
    env = {**os.environ, "CLIPPER_TOAST": toast_xml(title, body)}
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
