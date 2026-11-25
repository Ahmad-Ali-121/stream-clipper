import pytest


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """Tests must never reach a real Claude: hide the Claude Code command unless a test puts a fake one in place."""
    from clipper import llm

    monkeypatch.setattr(llm.shutil, "which", lambda name: None)


@pytest.fixture(autouse=True)
def no_background_setup_check(monkeypatch):
    """The page's controls start a full setup check by themselves the first time they are asked for their state. It
    loads the speech model in a program of its own (over a gigabyte) that outlives the test run. No test needs it."""
    from clipper import control

    monkeypatch.setattr(control.Control, "start_full_check", lambda self: {})
