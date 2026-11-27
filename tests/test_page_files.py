"""The review page is kept in three files (layout, styles, script) and handed to the browser as one document."""

from pathlib import Path

import pytest

import clipper

HERE = Path(clipper.__file__).parent


def test_the_three_files_make_one_whole_page():
    page = clipper.page_source()
    layout, styles, script = ((HERE / n).read_text(encoding="utf-8") for n in ("review_page.html", "review_page.css", "review_page.js"))
    assert styles and script and styles in page and script in page
    assert "/*[the styles: review_page.css]*/" not in page and "/*[the script: review_page.js]*/" not in page
    assert page.count("<style>") == 1 and page.count("</style>") == 1 and page.count("<script>") == 1 and page.count("</script>") == 1
    assert page.index("<style>") < page.index(styles[:200]) < page.index("</style>") < page.index("<script>") < page.index(script[:200]) < page.index("</script>")
    assert "<script" not in layout.replace("<script>", "", 1) and "</script" not in script and "</style" not in styles   # neither file can close its own block early
    assert len(page) == len(layout) + len(styles) + len(script) - len("/*[the styles: review_page.css]*/\n") - len("/*[the script: review_page.js]*/\n")


def test_a_layout_file_without_its_marker_is_named_not_served_half(tmp_path, monkeypatch):
    for n in ("review_page.html", "review_page.css", "review_page.js"):
        (tmp_path / n).write_text((HERE / n).read_text(encoding="utf-8").replace("/*[the script: review_page.js]*/\n", ""), encoding="utf-8")
    monkeypatch.setattr(clipper, "__file__", str(tmp_path / "__init__.py"))
    with pytest.raises(RuntimeError, match="marks where review_page.js goes"):
        clipper.page_source()


def test_the_served_page_is_the_whole_page():
    from clipper import review

    served = review.page_html("All streams", True)
    assert "function go(p){" in served and ":root{" in served and 'id="n-clips"' in served and "__TITLE__" not in served
