from dataclasses import replace

import pytest

from clipper.chat import ChatMessage
from clipper.config import MomentSettings
from clipper.moments import bucket_chat, find_moments, local_baseline_scores, score_buckets

HYPE = ["W", "LOL", "NO WAY", "💀"]


def settings(**kw) -> MomentSettings:
    # The older tests check the volume signal alone; the newer signals have their own tests below.
    base = dict(hype_spikes=False, skip_start_s=0.0)
    base.update(kw)
    return replace(MomentSettings(hype_words=HYPE), **base)


def baseline(duration_s: int = 1800, per_bucket: int = 2) -> list[ChatMessage]:
    """Steady background chat: a few distinct chatters every 10 seconds."""
    out = []
    for start in range(0, duration_s, 10):
        for k in range(per_bucket):
            out.append(ChatMessage(t=start + 1 + k, author=f"bg{start}_{k}", text="hello"))
    return out


def spike(t: float, n: int, text: str = "what", prefix: str = "s") -> list[ChatMessage]:
    return [ChatMessage(t=t + (i % 10) * 0.9, author=f"{prefix}{t}_{i}", text=text) for i in range(n)]


def test_bucket_counts_messages_chatters_and_hype():
    msgs = [
        ChatMessage(0.5, "a", "LOL"),
        ChatMessage(1.0, "A", "lol that was crazy"),  # same chatter, different case
        ChatMessage(9.9, "b", "below"),  # "below" must not match the hype word "W"/"LOL"
        ChatMessage(10.0, "c", "no way 💀"),
        ChatMessage(-5.0, "early", "pre-stream"),
    ]
    b = bucket_chat(msgs, 10, hype_words=HYPE)
    assert [(x.messages, x.chatters, x.hype) for x in b] == [(3, 2, 2), (1, 1, 1)]


def test_duration_extends_timeline():
    assert len(bucket_chat([ChatMessage(5, "a", "x")], 10, duration_s=95)) == 10


def test_finds_spike_with_gist_clip_window():
    msgs = baseline() + spike(600, 60)
    moments = find_moments(msgs, settings())
    assert len(moments) == 1
    m = moments[0]
    assert (m.rank, m.peak_s, m.start_s, m.end_s) == (1, 600, 580, 610)
    assert m.score > 10


def test_ranked_best_first_not_chronological():
    msgs = baseline() + spike(400, 30) + spike(1200, 90)
    moments = find_moments(msgs, settings())
    assert [m.peak_s for m in moments] == [1200, 400]
    assert [m.rank for m in moments] == [1, 2]


def test_gap_merges_neighbouring_spikes():
    msgs = baseline() + spike(600, 60) + spike(630, 50)
    moments = find_moments(msgs, settings(gap_s=90))
    assert [m.peak_s for m in moments] == [600]


def test_top_limits_results():
    msgs = baseline(3600) + spike(300, 40) + spike(900, 50) + spike(1500, 60) + spike(2400, 70)
    assert len(find_moments(msgs, settings(top=2))) == 2


def test_one_account_spamming_is_not_a_moment():
    spam = [ChatMessage(600 + i * 0.1, "bot", "BUY FOLLOWERS") for i in range(80)]
    msgs = baseline() + spam
    assert find_moments(msgs, settings(count_mode="chatters")) == []
    assert len(find_moments(msgs, settings(count_mode="messages"))) == 1


def test_quiet_stream_has_no_moments():
    assert find_moments(baseline(), settings()) == []


def test_clip_start_clamped_to_zero_and_end_to_duration():
    msgs = baseline(600) + spike(590, 60)
    m = find_moments(msgs, settings(min_neighbors=1), duration_s=595)[0]
    assert m.end_s == 595
    early = baseline(600) + spike(10, 60)
    assert find_moments(early, settings(min_neighbors=1))[0].start_s == 0


def test_trailing_mode_never_uses_future_chat():
    past = baseline(900) + spike(600, 60)
    s = settings(mode="trailing")
    before = score_buckets(bucket_chat(past, 10, hype_words=HYPE), s)
    # Chat that arrives later (a much bigger spike) must not change earlier scores.
    later = past + baseline(900, per_bucket=40)[:0] + spike(880, 500, prefix="late")
    after = score_buckets(bucket_chat(later, 10, hype_words=HYPE), s)
    assert after[:88] == before[:88]
    assert find_moments(past, s)[0].peak_s == 600


def test_symmetric_score_is_lower_inside_a_busy_stretch():
    # The same 30-chatter bucket scores high in a quiet part and low in a busy part.
    quiet = [2.0] * 61
    quiet[30] = 30.0
    busy = [25.0] * 61
    busy[30] = 30.0
    assert local_baseline_scores(quiet)[30] == pytest.approx(10.0)
    assert local_baseline_scores(busy)[30] < 1.5


def test_too_little_history_scores_zero():
    scores = local_baseline_scores([50.0, 1.0, 1.0], mode="trailing", min_neighbors=6)
    assert scores == [0.0, 0.0, 0.0]


def test_hype_words_boost_score():
    plain = find_moments(baseline() + spike(600, 60, text="hmm"), settings())[0].score
    hyped = find_moments(baseline() + spike(600, 60, text="NO WAY"), settings())[0].score
    # 60 of the bucket's 62 messages are hype (the other 2 are background chat)
    assert hyped == pytest.approx(plain * (1 + 0.5 * 60 / 62), rel=0.001)


def test_audio_boosts_loud_buckets_only():
    msgs = baseline() + spike(600, 60, text="hmm")
    s = settings()
    buckets = bucket_chat(msgs, 10, hype_words=HYPE)
    base = score_buckets(buckets, s)
    loud = [0.0] * len(buckets)
    loud[60] = 9.0  # clamped to 3
    quiet = [-5.0] * len(buckets)
    assert score_buckets(buckets, s, audio=loud)[60] == pytest.approx(base[60] * 1.5)
    assert score_buckets(buckets, s, audio=quiet) == base


def test_very_large_chat_does_not_crash():
    # The original script spread the whole list into Math.max and died on big chats.
    msgs = [ChatMessage(t=i * 0.05, author=f"u{i % 5000}", text="W") for i in range(400_000)]
    find_moments(msgs, settings())


def test_empty_chat():
    assert find_moments([], settings()) == []


def test_saturated_chat_is_found_by_hype_spike_not_volume():
    # Everyone is always typing (80 chatters every bucket), so volume never spikes.
    msgs = baseline(1800, per_bucket=80)
    # At 10:00 the same crowd switches to hype words; the count does not change.
    msgs = [m for m in msgs if not 600 <= m.t < 610]
    msgs += [ChatMessage(t=600 + (i % 10) * 0.9, author=f"h{i}", text="W" if i % 2 else "hmm") for i in range(80)]
    assert find_moments(msgs, settings()) == []
    found = find_moments(msgs, settings(hype_spikes=True))
    assert [m.peak_s for m in found] == [600]


def test_a_few_hype_words_in_a_quiet_chat_are_not_a_moment():
    msgs = baseline() + [ChatMessage(t=600 + i, author=f"h{i}", text="LOL") for i in range(2)]
    assert find_moments(msgs, settings(hype_spikes=True)) == []


def test_stream_start_flood_is_skipped():
    msgs = baseline(1800) + spike(100, 80) + spike(900, 60)
    assert [m.peak_s for m in find_moments(msgs, settings(min_neighbors=1))] == [100, 900]
    assert [m.peak_s for m in find_moments(msgs, settings(min_neighbors=1, skip_start_s=120))] == [900]
