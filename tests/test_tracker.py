import pytest

from clipper import tracker


def test_posts_are_logged_edited_and_added_up(tmp_path):
    a = tracker.add(tmp_path, {"clip": "twitch_1/0h01m00s", "channel": "Lacy", "platform": "TikTok", "url": "https://tiktok.com/x", "campaign": "Reach"})
    b = tracker.add(tmp_path, {"clip": "twitch_1/0h05m00s", "channel": "Lacy", "platform": "YouTube Shorts", "campaign": "Reach"})
    tracker.update(tmp_path, a["id"], {"views": "12500", "payout": "5.25", "status": "paid"})
    tracker.update(tmp_path, b["id"], {"views": 800, "status": "rejected", "reason": "missing tag"})
    rows = tracker.load(tmp_path)
    assert rows[0]["views"] == 12500 and rows[0]["payout"] == 5.25 and rows[1]["reason"] == "missing tag"
    t = tracker.totals(rows)
    assert (t["posts"], t["views"], t["payout"], t["rejected"], t["paid"]) == (2, 13300, 5.25, 1, 1)
    assert t["campaigns"]["Reach"]["views"] == 13300
    assert tracker.to_csv(rows).splitlines()[0].startswith("clip,channel,title,version,campaign,platform,url")
    assert tracker.delete(tmp_path, a["id"]) == 1 and len(tracker.load(tmp_path)) == 1


def test_same_clip_to_the_same_campaign_is_refused_unless_confirmed(tmp_path):
    tracker.add(tmp_path, {"clip": "twitch_1/0h01m00s", "campaign": "Reach", "version": "A"})
    with pytest.raises(ValueError, match="already logged"):
        tracker.add(tmp_path, {"clip": "twitch_1/0h01m00s", "campaign": "reach", "version": "B"})  # B is the same clip
    tracker.add(tmp_path, {"clip": "twitch_1/0h01m00s", "campaign": "Other campaign"})  # another campaign is fine
    tracker.add(tmp_path, {"clip": "twitch_1/0h01m00s", "campaign": "Reach"}, allow_duplicate=True)
    assert len(tracker.load(tmp_path)) == 3


def test_bad_values_are_refused_with_a_reason(tmp_path):
    row = tracker.add(tmp_path, {"clip": "c"})
    for changes, says in (({"views": -5}, "negative"), ({"platform": "MySpace"}, "platform"), ({"status": "viral"}, "status"),
                          ({"url": "tiktok.com/x"}, "http"), ({"id": "x"}, "cannot be changed")):
        with pytest.raises(ValueError, match=says):
            tracker.update(tmp_path, row["id"], changes)
    with pytest.raises(KeyError):
        tracker.update(tmp_path, "nope", {"views": 1})


def test_campaign_check_before_any_video_is_made():
    from clipper.brief import Brief
    from clipper.post import campaign_check

    brief = Brief(name="Test", min_duration_s=15, max_duration_s=25, required_tags=["#ad"], require_disclosure_overlay=True)
    state = {"start_s": 100, "end_s": 130, "title": "A title", "tags": ["#gta"], "channel": "Lacy", "source_url": "https://www.twitch.tv/lacy"}
    issues = campaign_check(state, brief)
    text = " | ".join(i["message"] for i in issues if i["level"] == "fail")
    assert "30" in text and "disclosure" in text.lower()  # too long, and no on-screen label yet
    fixed = campaign_check({**state, "cut_end_s": 122, "disclosure": True}, brief)
    assert not [i for i in fixed if i["level"] == "fail"]  # the required tag is added to the caption automatically
