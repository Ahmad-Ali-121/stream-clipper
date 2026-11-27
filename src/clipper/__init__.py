"""Semi-automatic stream clipping pipeline."""

__version__ = "0.1.0"


def page_source() -> str:
    """The review page as one document, the way the browser gets it.

    It is kept in three files beside this one: ``review_page.html`` (the layout), ``review_page.css`` (the styles) and
    ``review_page.js`` (the script). The layout file carries one marker line for each of the other two; here they are put in place.
    """
    from pathlib import Path

    here = Path(__file__).parent
    page = (here / "review_page.html").read_text(encoding="utf-8")
    for mark, name in (("/*[the styles: review_page.css]*/\n", "review_page.css"), ("/*[the script: review_page.js]*/\n", "review_page.js")):
        if page.count(mark) != 1:
            raise RuntimeError(f"review_page.html must hold the line {mark.strip()} exactly once (it marks where {name} goes); it holds it {page.count(mark)} times")
        page = page.replace(mark, (here / name).read_text(encoding="utf-8"))
    return page
