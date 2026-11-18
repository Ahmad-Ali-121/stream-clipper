import pytest

from clipper.config import Config
from clipper.cuts import apply_snap, snap_cut
from clipper.layout import Face, auto_layout, choose_layout

LACY_CAM = [0.0, 0.0, 0.256, 0.256]


def f(x, y, w, h, score=0.93):
    return Face(x, y, w, h, score)


# ---- automatic layout ----


def test_full_screen_camera_becomes_a_crop_centred_on_the_face():
    # Measured on Lacy's pre-game camera: a big face in every frame, plus small stray detections.
    frames = [[f(0.581, 0.094, 0.106, 0.28), f(0.129, 0.139, 0.039, 0.08)], [f(0.458, 0.361, 0.126, 0.29)], [f(0.465, 0.438, 0.121, 0.278)]]
    c = choose_layout(frames, LACY_CAM)
    assert c.layout == "crop" and c.crop_x == pytest.approx(0.526, abs=0.01)


def test_facecam_over_gameplay_becomes_split_despite_faces_in_the_game():
    # Measured on GTA 6 gameplay: the facecam face is steady; a large in-game face appears in one frame only.
    frames = [
        [f(0.112, 0.11, 0.037, 0.085), f(0.268, 0.441, 0.028, 0.081, 0.65)],
        [f(0.121, 0.106, 0.029, 0.067), f(0.507, 0.515, 0.162, 0.344, 0.9)],
        [f(0.12, 0.116, 0.035, 0.078), f(0.765, 0.506, 0.017, 0.039)],
    ]
    c = choose_layout(frames, LACY_CAM)
    assert c.layout == "split" and c.facecam == LACY_CAM


def test_unknown_streamer_gets_an_estimated_corner_facecam():
    frames = [[f(0.112, 0.11, 0.037, 0.085)], [f(0.121, 0.106, 0.035, 0.07)], [f(0.12, 0.116, 0.035, 0.078)]]
    c = choose_layout(frames, None)
    assert c.layout == "split" and "estimated" in c.reason
    x, y, w, h = c.facecam
    assert (x, y) == (0.0, 0.0) and 0.2 <= w <= 0.3


def test_no_steady_face_leaves_the_default():
    assert choose_layout([[], [f(0.5, 0.5, 0.03, 0.06)], []], LACY_CAM).layout is None
    assert choose_layout([[f(0.1, 0.1, 0.04, 0.08, 0.5)]] * 3, LACY_CAM).layout is None  # low-confidence detections ignored
    assert choose_layout([], LACY_CAM).layout is None


def test_a_layout_chosen_by_a_person_is_never_overwritten(tmp_path):
    state = {"id": "x", "layout": "crop", "channel": "lacy"}
    assert auto_layout(tmp_path, dict(state), Config()) == state  # no detection attempted, nothing changed


def test_detection_failure_keeps_the_clip(tmp_path):
    state = {"id": "x", "channel": "lacy"}
    out = auto_layout(tmp_path, dict(state), Config())  # no source.mp4, no model: must not raise
    assert "layout" not in out


# ---- cut snapping ----


def test_cut_slides_to_the_nearest_pauses():
    words = [
        {"start": 1.0, "end": 1.4}, {"start": 1.5, "end": 2.2},  # sentence A
        {"start": 4.2, "end": 4.6}, {"start": 4.7, "end": 5.0}, {"start": 5.1, "end": 33.0},  # sentence B; the raw cut starts inside it at 5.0
        {"start": 34.5, "end": 35.0}, {"start": 35.1, "end": 36.2},  # sentence C; the raw cut ends inside it at 35.0
        {"start": 38.0, "end": 39.0},
    ]
    start, end = snap_cut(words, 5.0, 35.0, lo=0.0, hi=40.0)
    assert start == pytest.approx(4.05)  # just before sentence B begins
    assert end == pytest.approx(36.5)  # just after sentence C ends


def test_cut_stays_put_without_a_nearby_pause_or_words():
    assert snap_cut([], 5.0, 35.0, 0.0, 40.0) == (5.0, 35.0)
    nonstop = [{"start": i * 0.5, "end": i * 0.5 + 0.45} for i in range(20, 60)]  # talking from 10 s to 30 s with no pause
    assert snap_cut(nonstop, 15.0, 25.0, 0.0, 40.0, max_shift=4.0) == (15.0, 25.0)


def test_snap_never_leaves_the_fetched_range():
    words = [{"start": 0.05, "end": 3.0}, {"start": 37.0, "end": 39.95}]
    start, end = snap_cut(words, 2.0, 38.0, lo=0.0, hi=40.0)
    assert start == 0.0 and end == 40.0


def test_snap_respects_a_cut_set_by_a_person():
    words = [{"start": 4.2, "end": 9.0}, {"start": 30.0, "end": 36.0}]
    base = {"start_s": 105.0, "end_s": 135.0, "fetch_start_s": 100.0, "fetch_end_s": 140.0, "source_duration_s": 40.0}
    auto = apply_snap(dict(base), words)
    assert auto["cut_auto"] and auto["cut_start_s"] == pytest.approx(104.05) and auto["cut_end_s"] == pytest.approx(136.3)
    manual = {**base, "cut_start_s": 110.0, "cut_end_s": 130.0}
    assert apply_snap(dict(manual), words) == manual


# ---- measuring the facecam box ----


def _frames_with_facecam(box, n=8, size=(270, 480), seed=0):
    """Synthetic frames: a changing flat picture as the game, a steady rectangle with a different look as the facecam."""
    import numpy as np

    rng = np.random.default_rng(seed)
    h, w = size
    x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int((box[0] + box[2]) * w), int((box[1] + box[3]) * h)
    out = []
    for _ in range(n):
        img = np.full((h, w), float(rng.integers(0, 90)), dtype="float32")  # dark game picture, different every frame
        img[y0:y1, x0:x1] = 200 + rng.integers(0, 10, size=(y1 - y0, x1 - x0))  # bright, steady camera picture
        out.append(img)
    return out


def test_measure_box_finds_a_corner_facecam():
    from clipper.layout import measure_box

    truth = [0.0, 0.0, 0.25, 0.25]
    box, score = measure_box(_frames_with_facecam(truth), cx=0.125, cy=0.11, fw=0.04)
    assert all(abs(a - b) < 0.01 for a, b in zip(box, truth)) and score >= 0.95


def test_measure_box_finds_a_floating_facecam_and_rejects_none():
    import numpy as np

    from clipper.layout import measure_box

    truth = [0.6, 0.5, 0.2, 0.3]
    box, score = measure_box(_frames_with_facecam(truth), cx=0.7, cy=0.63, fw=0.04)
    assert all(abs(a - b) < 0.01 for a, b in zip(box, truth)) and score >= 0.95
    plain = [np.full((270, 480), 10.0 * i, dtype="float32") for i in range(8)]
    assert measure_box(plain, cx=0.5, cy=0.5, fw=0.04) is None  # no steady rectangle anywhere


def test_consensus_needs_two_agreeing_measurements(tmp_path):
    from clipper.layout import consensus_box, learned_facecam, remember_facecam

    assert consensus_box([[0, 0, 0.256, 0.256]]) is None
    assert consensus_box([[0, 0, 0.256, 0.256], [0, 0, 0.30, 0.296], [0.0, 0.0, 0.258, 0.256]]) == [0.0, 0.0, 0.257, 0.256]
    cfg = Config()
    cfg.paths.data_dir = str(tmp_path)
    remember_facecam("Lacy", "a/1", [0, 0, 0.256, 0.256], 1.0, cfg)
    remember_facecam("Lacy", "a/1", [0, 0, 0.256, 0.256], 1.0, cfg)  # the same clip again does not count twice
    assert learned_facecam("lacy", cfg) is None
    remember_facecam("Lacy", "a/2", [0, 0, 0.258, 0.256], 1.0, cfg)
    assert learned_facecam("LACY", cfg) == [0.0, 0.0, 0.257, 0.256]
