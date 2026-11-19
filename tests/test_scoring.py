from clipper import scoring


def chat(score, **extra):
    return {"trigger": "chat", "score": score, **extra}


def test_stronger_and_agreeing_signals_score_higher():
    weak, strong = scoring.combined(chat(2.5)), scoring.combined(chat(8.0))
    assert 0 < weak < strong <= 100
    both = scoring.combined({"trigger": "chat+audio", "score": 4.0, "signals": {"audio": 7.0}})
    assert both > scoring.combined(chat(4.0))  # sound agreeing with chat adds to it
    assert scoring.combined({"trigger": "audio", "score": 8.5, "signals": {"audio": 8.5}}) > 0
    assert scoring.combined({"trigger": "ai", "score": 7.0, "ai_pick": "a line", "ai_strength": 7}) > 0


def test_ai_rating_of_the_finished_draft_carries_most_weight():
    loud_but_dull = scoring.combined(chat(8.0, ai_rating=15))
    quiet_but_good = scoring.combined(chat(2.5, ai_rating=90))
    assert quiet_but_good > loud_but_dull


def test_weights_follow_what_the_user_approved():
    history = [chat(5.0, status="approved")] * 9 + [chat(5.0, status="rejected")] * 1
    history += [{"trigger": "audio", "score": 8, "signals": {"audio": 8}, "status": "rejected"}] * 9
    history += [{"trigger": "audio", "score": 8, "signals": {"audio": 8}, "status": "approved"}] * 1
    result = scoring.learn(history)
    w = result["weights"]
    assert w["chat"] > scoring.BASE_WEIGHTS["chat"] and w["audio"] < scoring.BASE_WEIGHTS["audio"]
    assert result["detail"]["audio"] == {"reviewed": 10, "approved": 1, "rate": 0.1, "weight": w["audio"], "base": 0.5}
    assert w["video"] == scoring.BASE_WEIGHTS["video"]  # too little history: left alone
    # and a clip found by sound alone now ranks lower than before
    clip = {"trigger": "audio", "score": 8, "signals": {"audio": 8}}
    assert scoring.combined(clip, w) < scoring.combined(clip)


def test_few_decisions_change_nothing():
    assert scoring.learn([chat(5.0, status="rejected")] * 3)["weights"] == scoring.BASE_WEIGHTS
