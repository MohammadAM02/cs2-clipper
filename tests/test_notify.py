from clipper.notify import toast_xml


def test_toast_text_is_escaped():
    xml = toast_xml("Rock & roll", "less than <5 GB>")
    assert "<text>Rock &amp; roll</text>" in xml
    assert "<text>less than &lt;5 GB&gt;</text>" in xml
