"""The page's layout (2026-11-25): a menu on the left, one page at a time, clips as tiles that open into an editor.

The page is one HTML file whose script finds its parts by their ids, so the checks here are about that wiring:
nothing the script looks for is missing, nothing exists twice, and every feature of the old single screen is still
on some page."""

import re
from pathlib import Path

import clipper
from clipper.review import page_html

PAGE = (Path(clipper.__file__).parent / "review_page.html").read_text(encoding="utf-8")
BODY = PAGE[PAGE.index("<body>"):PAGE.index("<script>")]
SCRIPT = PAGE[PAGE.index("<script>"):]


def test_every_part_the_script_looks_for_is_on_the_page_once():
    ids = re.findall(r' id="([^"]+)"', BODY)
    assert sorted({i for i in ids if ids.count(i) > 1}) == []
    used = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", SCRIPT))
    assert sorted(u for u in used if u not in ids) == []


def test_the_menu_has_a_page_for_each_item():
    for name, page in (("new", "page-new"), ("live", "page-live"), ("clips", "view-clips"), ("look", "page-look"), ("track", "view-track"),
                       ("settings", "page-settings")):
        assert f'id="n-{name}" onclick="go(\'{name}\')"' in BODY, name
        assert f'id="{page}"' in BODY and f"{name}:'{page}'" in SCRIPT, page
    assert 'id="helpbtn" onclick="helpToggle()"' in BODY and 'id="b-activity" onclick="actToggle()"' in BODY
    for piece in ("function go(p){", "function sub(n){", "function openClip(key,tabName){", "function closeClip(decided){", "function edGo(key,name){",
                  "function drawVideos(clips){", "function actToggle(open){"):
        assert piece in SCRIPT, piece


def test_nothing_was_lost_in_the_move():
    """Every control of the old single screen is still there, under the same id, so every feature still works."""
    for ident in (
        # finding moments, live and finished, and your own clips
        "o-channels", "o-auto", "o-modes", "b-start", "b-stop", "b-clipnow", "w-last", "w-live", "r-url", "r-sig", "r-ai", "r-mix", "r-top", "b-run", "b-runstop",
        "m-src", "m-file", "m-toend", "b-board", "b-ownstop", "m-split", "b-own", "cb-video", "cb-cut", "cb-make", "tw-url", "tw-get", "tw-make", "tw-ans",
        # the look
        "o-opening", "o-thumb", "o-face", "o-avoid", "o-cap", "o-anim", "k-font", "t-tpl", "t-hook", "g-logofile", "g-outro", "g-musicfile", "o-camp",
        # reviewing and making
        "filters", "clips", "empty", "s-sort", "s-who", "s-how", "s-when", "s-find", "z-pick", "b-finish", "b-fstop", "b-ready", "f-bar", "f-note",
        # setup, AI, language, the tracker, the logs
        "healthbox", "b-hcheck", "b-hrepair", "b-hupdate", "a-backend", "b-aicheck", "b-localai", "l-speech", "l-script", "l-text", "l-yt",
        "t-totals", "t-rows", "b-views", "wlog", "rlog", "flog", "hlog",
        # the windows over the page
        "boxes", "stopbox", "helpbox", "spot", "toast", "errbox",
    ):
        assert f'id="{ident}"' in BODY, ident
    # the four activity logs are one panel now, and the banner is gone
    assert BODY.count("<pre class=\"log\"") == 5 and BODY.count('id="drawer"') == 1 and "class=\"hero\"" not in PAGE


def test_a_clip_is_a_tile_and_the_same_card_is_its_editor():
    for piece in ('<div class="tile">', '<div class="edhead">', '<div class="edtabs">', "class=\"sec pane\" data-tab=\"'+n+'\"", "onclick=\"openClip(",
                  "['text','Text'],['versions','Versions']", "['trim','Trim and framing'],['thumb','Thumbnail'", "if(!decided&&el.dataset.dirty==='1')save(key)",
                  "if(extra&&extra.status&&el.classList.contains('open'))closeClip(true)", "Make its videos again", "Move to Pending", "Back to review"):
        assert piece in SCRIPT, piece
    # the editor's fields are the card's own fields: what Save reads is still found by name
    for name in ("hook", "hook_b", "intro", "intro_b", "title", "title_b", "description", "tags", "simple", "explainer", "cut_start_s", "cut_end_s", "layout",
                 "disclosure"):
        assert f'name="{name}"' in SCRIPT, name


def test_the_page_is_served_whole():
    made = page_html("Test", True)
    assert "__PLAIN__" not in made and "__TITLE__" not in made and "__HAS_PANEL__" not in made and made.count("<script>") == 1
    assert "body.nopanel .nav,body.nopanel .panelonly{display:none!important}" in made  # the page with only the clips (review of one video)
