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
