import pytest


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """Tests must never reach a real Claude: hide the Claude Code command unless a test puts a fake one in place."""
    from clipper import llm

    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
