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
    assert r["source"] == "ai" and r["steps"] == [{"text": "Press it", "target": "b-clipnow"}, {"text": "Then this", "target": ""}]  # the invented key is dropped
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
