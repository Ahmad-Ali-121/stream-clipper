"""The page in a real browser: the program is started on an empty scratch folder and Edge (or Chrome) presses its buttons.

These tests need Playwright, which is not one of the program's parts, so a plain test run skips them. To run them:

    uv run --with playwright python -m pytest -q tests/test_browser.py

They use the Edge or Chrome that is already on the computer (nothing is downloaded), a data folder and a keys file made for the
test, and made-up keys. Nothing of the person's own is read or changed.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="needs Playwright: uv run --with playwright python -m pytest tests/test_browser.py")

ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "gsk_test_FAKE0123456789abcdefXYZ"          # made up: it opens nothing
PAGES = ("new", "live", "clips", "look", "track", "setup", "settings")
CLIP = "twitch_1/0h10m00s"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill(proc: subprocess.Popen) -> None:
    """The program and anything it started. By its own process number, never by a name that could match something else."""
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            proc.kill()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def program(tmp_path_factory):
    """The program itself, on a scratch data folder that holds one clip waiting for review."""
    home = tmp_path_factory.mktemp("browser")
    data = home / "data"
    clip = data / "work" / "twitch_1" / "clips" / "0h10m00s"
    clip.mkdir(parents=True)
    (clip / "clip.json").write_text(json.dumps({
        "id": "0h10m00s", "start_s": 600.0, "end_s": 630.0, "status": "candidate", "fetch_start_s": 590.0, "source_width": 1920, "source_height": 1080,
        "title": "A clip made for the browser test", "hook": "TEST HOOK", "channel": "tester", "platform": "twitch", "duration_s": 30.0}), encoding="utf-8")
    for name in ("source.mp4", "final.mp4"):
        (clip / name).write_bytes(b"not a real video" * 64)
    (clip / "words.json").write_text("[]", encoding="utf-8")
    (data / "work" / "twitch_1" / "meta.json").write_text(json.dumps({"channel": "tester", "title": "Test stream", "platform": "twitch", "id": "1"}), encoding="utf-8")
    cfg = home / "cfg"
    cfg.mkdir()
    shutil.copy(ROOT / "config.yaml", cfg / "config.yaml")
    (cfg / "config.local.yaml").write_text(f"paths:\n  data_dir: {data.as_posix()}\nllm:\n  enabled: false\n  local: 'off'\n", encoding="utf-8")
    env_file = home / "keys.env"
    env_file.write_text("", encoding="utf-8")
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}       # the person's own keys never reach the test
    env.update(CLIPPER_CONFIG=str(cfg / "config.yaml"), CLIPPER_ENV=str(env_file), PYTHONIOENCODING="utf-8")
    log = open(home / "program.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-u", "-c", "from clipper.cli import main; raise SystemExit(main())", "review", "--port", str(port)],
                            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(120):
            if proc.poll() is not None:
                break
            try:
                urllib.request.urlopen(url, timeout=2).read(64)
                break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail("the program did not answer within a minute:\n" + (home / "program.log").read_text(encoding="utf-8")[-2000:])
        if proc.poll() is not None:
            log.flush()
            pytest.fail("the program stopped as it started:\n" + (home / "program.log").read_text(encoding="utf-8")[-2000:])
        yield {"url": url, "env": env_file, "data": data, "cfg": cfg / "config.local.yaml", "log": home / "program.log"}
    finally:
        _kill(proc)
        log.close()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b, problems = None, []
        for channel in ("msedge", "chrome"):
            try:
                b = p.chromium.launch(channel=channel, headless=True)
                break
            except Exception as e:  # not installed on this computer
                problems.append(f"{channel}: {str(e).splitlines()[0][:120]}")
        if b is None:
            pytest.skip("neither Edge nor Chrome could be started (" + "; ".join(problems) + ")")
        yield b
        b.close()


@pytest.fixture()
def page(program, browser):
    """A fresh page on the program. Its ``faults`` list collects every script error; a question box is answered with OK and kept in ``asked``."""
    pg = browser.new_page(viewport={"width": 1366, "height": 900})
    pg.faults, pg.asked = [], []
    pg.on("pageerror", lambda e: pg.faults.append(str(e)))
    pg.on("console", lambda m: pg.faults.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    pg.on("dialog", lambda d: (pg.asked.append(d.message), d.accept()))
    pg.goto(program["url"])
    pg.wait_for_function("typeof last==='object' && last && !!last.keys", timeout=30000)
    yield pg
    pg.close()


def _state(program) -> dict:
    return json.loads(urllib.request.urlopen(program["url"] + "api/options", timeout=20).read().decode("utf-8"))


def test_every_page_opens_without_a_script_error_and_nothing_sticks_out(page):
    assert page.evaluate("curPage") != "setup"                     # an unfinished setup is a warning in the menu, never a page that opens by itself
    for width in (1366, 390):
        page.set_viewport_size({"width": width, "height": 900})
        for name in PAGES:
            page.evaluate(f"go('{name}')")
            page.wait_for_timeout(500)
            assert page.evaluate("curPage") == name
            over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert over <= 1, f"the {name} page is {over}px wider than a {width}px window"
    assert page.faults == []


def test_a_key_is_saved_from_the_page_and_never_shown_again(page, program):
    page.evaluate("go('settings')")
    page.wait_for_selector("#key-groq", state="visible")
    assert "no key" in page.inner_text("#keyst-groq")
    page.fill("#key-groq", "abc")                                   # not a key: refused with a reason, nothing written
    page.click("button[onclick=\"saveKey('groq')\"]")
    page.wait_for_function("document.body.innerText.includes('does not look like a key')", timeout=10000)
    assert program["env"].read_text(encoding="utf-8").strip() == ""
    page.fill("#key-groq", FAKE_KEY)
    page.click("button[onclick=\"saveKey('groq')\"]")
    page.wait_for_function("document.getElementById('keyst-groq').textContent.includes('a key is saved')", timeout=15000)
    assert page.input_value("#key-groq") == ""                      # the box is emptied at once
    assert f"GROQ_API_KEY={FAKE_KEY}" in program["env"].read_text(encoding="utf-8")
    assert FAKE_KEY not in page.content()
    state = _state(program)
    assert FAKE_KEY not in json.dumps(state) and state["keys"]["groq"] is True and state["keys"]["claude"] is False
    assert FAKE_KEY not in program["log"].read_text(encoding="utf-8")   # never in the terminal either
    page.click("#keyrm-groq")
    page.wait_for_function("document.getElementById('keyst-groq').textContent.includes('no key')", timeout=15000)
    assert "GROQ_API_KEY" not in program["env"].read_text(encoding="utf-8")
    assert any("Remove this key" in q for q in page.asked)          # removing asks first
    assert page.faults == []


def test_delete_puts_a_clip_in_the_bin_and_bring_back_returns_it_to_review(page, program):
    key = json.dumps(CLIP)
    page.evaluate("go('clips')")
    page.wait_for_function(f"known.some(c=>c.key==={key})", timeout=20000)
    page.evaluate(f"delClip({key})")
    page.wait_for_function(f"(known.find(c=>c.key==={key})||{{}}).binned===true", timeout=15000)
    assert not any("forever" in q for q in page.asked)              # nothing is removed yet, so nothing is asked
    clip = program["data"] / "work" / "twitch_1" / "clips" / "0h10m00s"
    assert (clip / "source.mp4").exists() and (clip / "final.mp4").exists()
    assert page.evaluate(f"getComputedStyle(document.getElementById(domId({key}))).display") == "none"   # out of the To review list
    page.evaluate("setFilter('bin')")
    page.wait_for_function(f"getComputedStyle(document.getElementById(domId({key}))).display!=='none'", timeout=10000)
    card = page.evaluate(f"document.getElementById(domId({key})).innerText")
    assert "Bring back" in card and "Delete forever" in card and "7 days" in card
    page.evaluate(f"restoreClip({key})")
    page.wait_for_function(f"(known.find(c=>c.key==={key})||{{}}).binned===false", timeout=15000)
    assert page.evaluate(f"known.find(c=>c.key==={key}).status") == "candidate"
    assert json.loads((clip / "clip.json").read_text(encoding="utf-8"))["status"] == "candidate"
    assert "A STEP TRIED TO PUT A DECIDED CLIP BACK" not in program["log"].read_text(encoding="utf-8")
    assert page.faults == []


def test_settings_show_the_disk_space_and_save_the_choices(page, program):
    page.evaluate("go('settings')")
    page.wait_for_function("document.getElementById('disk-now').textContent.includes('Downloads and drafts take')", timeout=30000)
    text = page.inner_text("#disk-now")
    assert "The bin holds" in text and "NaN" not in text and "undefined" not in text
    page.select_option("#cl-rej", "30")
    page.wait_for_function("last.cleanup && last.cleanup.rejected_days===30", timeout=15000)
    page.select_option("#l-model", "small")                         # the fast speech model: chosen, but never downloaded by the choice
    page.wait_for_function("last.speech_model==='small'", timeout=15000)
    assert "not on this computer yet" in page.inner_text("#l-modelnote")
    saved = program["cfg"].read_text(encoding="utf-8")
    assert "rejected_days: 30" in saved and "model: small" in saved
    assert not (program["data"] / "models").exists() or not any((program["data"] / "models").iterdir())
    assert page.faults == []


def test_choosing_what_the_narrator_speaks_changes_the_voices_offered(page, program):
    page.evaluate("go('settings')")
    page.wait_for_selector("#v-lang", state="visible")
    assert page.input_value("#v-lang") == "English" and not page.is_visible("#v-langnote")
    assert page.evaluate("document.getElementById('v-kokoro').options.length") == 12
    page.select_option("#v-lang", "Hindi")
    page.wait_for_function("last.voice && last.voice.language==='Hindi' && document.getElementById('v-kokoro').options.length===4", timeout=15000)
    assert page.input_value("#v-kokoro") == "hf_alpha"
    assert "reads Hindi letters only" in page.inner_text("#v-langnote")
    page.select_option("#v-kokoro", "hm_psi")
    page.wait_for_function("last.voice.kokoro_voice==='hm_psi'", timeout=15000)
    saved = program["cfg"].read_text(encoding="utf-8")
    assert "language: Hindi" in saved and "kokoro_voice_hindi: hm_psi" in saved
    page.select_option("#v-lang", "English")
    page.wait_for_function("last.voice.language==='English' && document.getElementById('v-kokoro').options.length===12", timeout=15000)
    assert not page.is_visible("#v-langnote") and not page.input_value("#v-kokoro").startswith("h")   # the English voice was kept apart
    over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert over <= 1 and page.faults == []
