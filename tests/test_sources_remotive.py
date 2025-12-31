from jobintel.etl.sources.remotive import _strip_html


def test_strip_html_removes_tags():
    s = "<p>Hello <b>world</b> &amp; friends</p>"
    out = _strip_html(s)
    assert "Hello" in out
    assert "world" in out
    assert "&" in out
    assert "<b>" not in out
