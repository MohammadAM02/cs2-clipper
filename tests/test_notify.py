import subprocess

import pytest

from clipper import notify
from clipper.notify import toast_xml


def test_toast_text_is_escaped():
    xml = toast_xml("Rock & roll", "less than <5 GB>")
    assert "<text>Rock &amp; roll</text>" in xml
    assert "<text>less than &lt;5 GB&gt;</text>" in xml


def test_a_toast_can_open_a_page_or_be_dismissed():
    url = "http://127.0.0.1:8765/demos?a=1&b=2"
    xml = toast_xml("2 new matches have Highlights", "Inferno 4K", actions=(("Show matches", url), ("Not now", None)))
    assert xml.startswith('<toast activationType="protocol" launch="http://127.0.0.1:8765/demos?a=1&amp;b=2">')
    assert ('<action content="Show matches" activationType="protocol"'
            ' arguments="http://127.0.0.1:8765/demos?a=1&amp;b=2"/>') in xml
    assert '<action content="Not now" activationType="system" arguments="dismiss"/>' in xml
    assert xml.endswith("</actions></toast>")


def test_a_toast_without_actions_is_unchanged():
    assert toast_xml("Title", "Body") == (
        "<toast><visual><binding template='ToastGeneric'><text>Title</text><text>Body</text>"
        "</binding></visual></toast>")


def _sent_under(monkeypatch) -> list[str]:
    """The app ids the toasts sent from now on go out under, with PowerShell itself never run."""
    sent = []

    def run(command, *, env, **kwargs):
        sent.append(env["CLIPPER_TOAST_APP"])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(notify.subprocess, "run", run)
    return sent


def test_toasts_go_out_as_powershell_until_the_app_has_registered(monkeypatch):
    sent = _sent_under(monkeypatch)
    notify.notify("Title", "Body")
    assert sent == [notify.POWERSHELL_ID]


def test_registering_names_the_app_with_its_mark_and_toasts_go_out_as_cs2_clipper(monkeypatch, tmp_path):
    sent, written = _sent_under(monkeypatch), []
    notify.register(tmp_path / "mark.png", lambda key, name, value: written.append((key, name, value)))
    notify.notify("Title", "Body")
    key = r"Software\Classes\AppUserModelId\CS2Clipper.App"
    assert written == [(key, "DisplayName", "CS2 Clipper"), (key, "IconUri", str(tmp_path / "mark.png"))]
    assert sent == ["CS2Clipper.App"] and notify.app_id() == "CS2Clipper.App"


def test_a_registration_that_fails_leaves_toasts_going_out_as_powershell(monkeypatch, tmp_path):
    sent = _sent_under(monkeypatch)

    def refuse(key, name, value):
        raise OSError("access denied")

    with pytest.raises(OSError):
        notify.register(tmp_path / "mark.png", refuse)
    notify.notify("Title", "Body")
    assert sent == [notify.POWERSHELL_ID]
