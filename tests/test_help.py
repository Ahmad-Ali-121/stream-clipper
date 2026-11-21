"""The built-in help assistant: what it is told, what it may point at, and what it does without AI."""

import pytest

from clipper import help as help_mod
from clipper import llm
from clipper.config import load_config

PAGE = [{"key": "b-clipnow", "kind": "button", "label": "Clip the last", "where": "Find moments > tab \"Live streams\""},
        {"key": "t-tpl", "kind": "choice", "label": "Template", "where": "Make the videos"}]


def test_the_guide_is_the_knowledge_and_is_split_into_sections():
    text, sections = help_mod.knowledge()
    assert "Stream Clipper" in text and len(sections) > 20
    assert any("Clip the last 30 seconds" in title for title, _ in sections)  # a feature added to the guide is known at once


def test_the_pages_own_list_is_checked_before_it_is_used():
    raw = PAGE + [{"key": "bad key!", "label": "x"}, {"key": "b-clipnow", "label": "again"}, "junk",
                  {"key": "k1", "label": "A title | with a bar\nand a new line " + "z" * 200, "where": "w", "kind": "button"}]
    clean = help_mod.clean_targets(raw)
    assert [t["key"] for t in clean] == ["b-clipnow", "t-tpl", "k1"]
    assert "|" not in clean[2]["label"] and "\n" not in clean[2]["label"] and len(clean[2]["label"]) <= 90
    assert len(help_mod.clean_targets([{"key": f"k{i}", "label": "x"} for i in range(1000)])) == help_mod.MAX_TARGETS


def test_the_prompt_holds_the_guide_the_page_the_state_and_the_question():
    p = help_mod.build_prompt("how do I clip now?", [{"q": "hi", "a": "hello"}], help_mod.clean_targets(PAGE),
                              {"view": "clips", "watching": "yes: lacy", "errors": ["THE PROGRAM NEEDS A RESTART"]})
    assert "b-clipnow | button | Clip the last" in p and "Watching live: yes: lacy" in p and "THE PROGRAM NEEDS A RESTART" in p
    assert "User: hi" in p and p.rstrip().endswith("how do I clip now?") and "Part 3: Daily use" in p


def test_an_answer_can_only_point_at_things_the_page_listed(monkeypatch):
    def fake(cfg, model, system, user, schema, what, images=None, local_ok=False):
        assert model == cfg.llm.help_model and "only use keys" in system.lower()
        return {"answer": "Press Clip the last.", "steps": [{"text": "Press it", "target": "b-clipnow"}, {"text": "Then this", "target": "made-up-key"},
                                                              {"text": "  ", "target": ""}], "follow_ups": ["And then?", " "]}

    monkeypatch.setattr(llm, "ask", fake)
    r = help_mod.answer(load_config(), "  how do I clip now?  ", [], PAGE, {})
    assert r["source"] == "ai" and [(x["text"], x["target"]) for x in r["steps"]] == [("Press it", "b-clipnow"), ("Then this", "")]  # the invented key is dropped
    assert r["run"] is False and all(x["act"] == "" and not x["yours"] for x in r["steps"])  # an answer that only points does nothing
    assert r["follow_ups"] == ["And then?"]


def test_without_claude_the_guide_itself_answers(monkeypatch):
    def down(*a, **k):
        raise llm.LlmUnavailable("Claude Code is not signed in")

    monkeypatch.setattr(llm, "ask", down)
    r = help_mod.answer(load_config(), "how do I add my logo and music?", [], PAGE, {})
    assert r["source"] == "guide" and "not signed in" in r["note"]
    assert r["steps"] and "logo" in r["steps"][0]["text"].lower()
    nothing = help_mod.answer(load_config(), "zzzz qqqq", [], PAGE, {})
    assert nothing["steps"] == [] and "could not find" in nothing["answer"]


def test_an_empty_or_huge_question_is_refused_plainly():
    cfg = load_config()
    with pytest.raises(ValueError, match="type a question"):
        help_mod.answer(cfg, "   ")
    with pytest.raises(ValueError, match="very long"):
        help_mod.answer(cfg, "x" * 2000)


# ---- doing things for the user ----

DOABLE = [
    {"key": "o-channels", "kind": "box", "label": "Channels, separated by spaces", "where": "Find moments > tab \"Live streams\"", "now": "(empty)"},
    {"key": "b-watch", "kind": "button", "label": "Start watching", "where": "Find moments > tab \"Live streams\""},
    {"key": "o-auto", "kind": "tick box", "label": "Start watching by itself", "where": "Find moments", "now": "not ticked"},
    {"key": "c-anim", "kind": "choice", "label": "Caption movement", "where": "Make the videos", "now": "Pop", "choices": "Highlight; Pop; One word"},
    {"key": "k7", "kind": "button", "label": "Approve", "where": "Clips view > on each clip's card (this is the first clip's)"},
    {"key": "k9", "kind": "button", "label": "Fine-tune", "where": "Clips view > on each clip's card (this is the first clip's)"},
    {"key": "b-delrej", "kind": "button", "label": "Delete all rejected", "where": "Clips view"},
    {"key": "b-update", "kind": "button", "label": "Update now", "where": "top bar"},
    {"key": "b-clear", "kind": "button", "label": "Clear filters", "where": "Clips view"},
]


def _steps(*rows):
    return [{"text": t, "target": k, "act": a, "value": v} for t, k, a, v in rows]


def test_the_page_list_carries_what_each_thing_holds_and_its_choices():
    clean = help_mod.clean_targets(DOABLE)
    p = help_mod.build_prompt("set the caption movement to one word", [], clean, {})
    assert "c-anim | choice | Caption movement | Make the videos | now: Pop | choices: Highlight; Pop; One word" in p
    assert "o-channels | box |" in p and "now: (empty)" in p


def test_steps_the_ai_may_do_keep_their_act_and_value():
    got = help_mod.checked_steps(_steps(("Type the channel", "o-channels", "type", "lacy kick.com/name"), ("Tick it", "o-auto", "tick", "ignored"),
                                        ("Choose one word", "c-anim", "type", "One word"), ("Start", "b-watch", "press", ""),
                                        ("Clear", "b-clear", "press", "")), help_mod.clean_targets(DOABLE))
    assert [(s["act"], s["value"], s["yours"]) for s in got] == [("type", "lacy kick.com/name", False), ("tick", "", False), ("type", "One word", False),
                                                                 ("press", "", False), ("", "", True)]  # "Clear ..." is the user's own


def test_decisions_deleting_and_updating_are_never_done_for_the_user():
    got = help_mod.checked_steps(_steps(("Approve it", "k7", "press", ""), ("Open fine-tune", "k9", "press", ""), ("Delete them", "b-delrej", "press", ""),
                                        ("Update", "b-update", "press", "")), help_mod.clean_targets(DOABLE))
    assert all(s["act"] == "" and s["yours"] and s["target"] for s in got)  # still pointed at, never pressed


def test_an_act_that_does_not_fit_the_thing_or_is_made_up_is_dropped():
    long = "x" * 1000
    got = help_mod.checked_steps(_steps(("Type into a button", "b-watch", "type", "hello"), ("Press a box", "o-channels", "press", ""),
                                        ("Run a script", "b-watch", "javascript", "alert(1)"), ("No such thing", "nope", "press", ""),
                                        ("Long value", "o-channels", "type", long)) + ["junk", {"text": ""}], help_mod.clean_targets(DOABLE))
    assert [s["act"] for s in got] == ["", "", "", "", "type"] and got[3]["target"] == "" and len(got[4]["value"]) == help_mod.MAX_VALUE
    assert not any(s["yours"] for s in got)


def test_run_is_only_true_when_there_is_something_left_to_do(monkeypatch):
    answers = {"steps": _steps(("Type", "o-channels", "type", "lacy"), ("Start", "b-watch", "press", "")), "run": True}
    monkeypatch.setattr(llm, "ask", lambda *a, **k: {"answer": "I am starting it.", "follow_ups": [], **answers})
    r = help_mod.answer(load_config(), "start watching lacy", [], DOABLE, {})
    assert r["run"] is True and [s["act"] for s in r["steps"]] == ["type", "press"]
    answers["steps"] = _steps(("Approve it", "k7", "press", ""))
    r = help_mod.answer(load_config(), "approve the first clip", [], DOABLE, {})
    assert r["run"] is False and r["steps"][0]["yours"]  # told to do it, but it is the user's own press


def test_the_ai_is_told_what_it_can_do_and_what_stays_the_users():
    low = help_mod.SYSTEM.lower()
    assert '"press"' in low and '"type"' in low and "do it for me" in low and "yours to press" in low
    assert help_mod.SCHEMA["properties"]["steps"]["items"]["properties"]["act"]["enum"] == ["", "press", "type", "tick", "untick"]


def test_without_claude_nothing_is_done_for_the_user(monkeypatch):
    def down(*a, **k):
        raise llm.LlmUnavailable("Claude Code is not signed in")

    monkeypatch.setattr(llm, "ask", down)
    r = help_mod.answer(load_config(), "start watching lacy", [], DOABLE, {})
    assert r["run"] is False and all(s["act"] == "" for s in r["steps"])


def test_the_users_own_presses_are_whole_word_families_and_what_the_page_marks():
    things = [{"key": f"k{i}", "kind": "button", "label": label, "where": where, "yours": yours} for i, (label, where, yours) in enumerate([
        ("Removes the logo", "Make the videos", False), ("Discard changes", "x", False), ("Erase all", "x", False), ("Take it off", "Branding", False),
        ("Restart now", "top bar", False), ("Repair", "Setup check", False), ("Set up free local AI", "Claude", True), ("×", "top bar", False),
        ("Back to automatic", "in the box editor window", False), ("Save and make the draft again", "in the box editor window", False),
        ("Do it", "Cutting board view", True),
        ("Start watching", "Find moments", False), ("Stop", "Find moments", False), ("Make videos for 2 approved clips", "Make the videos", False),
        ("Rejected 0", "Clips view", False), ("Font (one installed on this PC)", "Captions", False)])]
    listed = help_mod.clean_targets(things)
    got = [help_mod.checked_steps(_steps(("press it", t["key"], "press", "")), listed)[0] for t in things]  # one at a time: an answer holds twelve steps at most
    assert [s["yours"] for s in got] == [True] * 11 + [False] * 5 and [s["act"] for s in got] == [""] * 11 + ["press"] * 5
    assert "k10 | button | Do it | Cutting board view | YOURS" in help_mod.build_prompt("q", [], listed, {})
    assert "k11 | button | Start watching | Find moments" in help_mod.build_prompt("q", [], listed, {}) and "Start watching | Find moments | YOURS" not in help_mod.build_prompt("q", [], listed, {})


def test_a_choice_with_no_value_is_not_chosen_and_a_box_may_be_emptied():
    got = help_mod.checked_steps(_steps(("Choose", "c-anim", "type", "  "), ("Empty the box", "o-channels", "type", "")), help_mod.clean_targets(DOABLE))
    assert [s["act"] for s in got] == ["", "type"]


def test_the_page_never_lets_typed_words_decide_about_clips():
    from pathlib import Path

    import clipper

    page = (Path(clipper.__file__).parent / "review_page.html").read_text(encoding="utf-8")
    keys = page[page.index("const cards=shownCards()") - 400: page.index("const cards=shownCards()")]
    assert "!$('helpbox').hidden" in keys and "HELP.doing" in keys  # the A and R shortcuts are off while the chat is open
    run = page[page.index("async function helpDo(box){"): page.index("/* Bring a thing into view")]
    assert "finally{HELP.doing=false" in run and "helpYours(real)" in run and "HELP.said" in run
    assert page.count("data-yours") >= 8  # the buttons the page itself keeps for the user
