import json

import pytest

from clipper.config import load_config
from clipper.render import framing, layout_filter


def _cfg(tmp_path):
    cfg = load_config()
    cfg.paths.data_dir = str(tmp_path)
    return cfg


# ---- the face box and the video box ----


def test_a_video_box_set_by_hand_replaces_the_automatic_slice():
    from dataclasses import replace

    r = load_config().render
    auto = layout_filter(1920, 1080, replace(r, game=[]), "crop")
    assert "crop=608:1080:656:0" in auto  # the middle, full height
    boxed = layout_filter(1920, 1080, replace(r, game=[0.5, 0.25, 0.25, 0.5]), "crop")
    assert "crop=480:540:960:270" in boxed
    split = layout_filter(1920, 1080, replace(r, game=[0.1, 0.0, 0.5, 1.0], facecam=[0.75, 0.0, 0.25, 0.3]), "split")
    assert "[fa]crop=480:324:1440:0" in split and "[ga]crop=960:1080:192:0" in split
    with pytest.raises(ValueError, match="game"):
        layout_filter(1920, 1080, replace(r, game=[0.1, 0.0, 1.5, 1.0]), "crop")


def test_the_page_gets_boxes_in_the_shape_they_are_shown_in():
    cfg = load_config()
    f = framing({"source_width": 1920, "source_height": 1080, "layout": "split", "facecam": [0.0, 0.0, 0.25, 0.25], "channel": ""}, cfg)
    assert f["layout"] == "split" and f["source"] == [1920, 1080]
    for layout, boxes in f["boxes"].items():  # every layout the editor can switch to
        for name, (x, y, w, h) in boxes.items():
            assert 0 <= x and 0 <= y and x + w <= 1.001 and y + h <= 1.001
            assert abs((w * 1920) / (h * 1080) - f["shapes"][layout][name]) < 0.02, (layout, name)  # exactly what the viewer sees
    assert set(f["boxes"]["circle"]) == {"face", "video"} and set(f["boxes"]["blur"]) == {"video"}
    assert f["boxes"]["blur"]["video"] == [0.0, 0.0, 1.0, 1.0]  # the whole picture
    assert framing({"layout": "split"}, cfg) is None  # not downloaded yet: nothing to frame


def test_boxes_are_saved_on_a_clip_and_make_its_renders_stale(tmp_path):
    from clipper.review import apply_edit

    d = tmp_path / "clips" / "0h00m10s"
    d.mkdir(parents=True)
    (d / "clip.json").write_text(json.dumps({"id": "0h00m10s", "start_s": 10, "end_s": 40, "layout": "crop", "layout_auto": True}), encoding="utf-8")
    (d / "final.mp4").write_bytes(b"x")
    state = apply_edit(tmp_path, "0h00m10s", {"layout": "split", "facecam": [0.7, 0.0, 0.3, 0.3], "game": [0.0, 0.0, 0.5, 1.0]})
    assert state["game"] == [0.0, 0.0, 0.5, 1.0] and state["facecam"] == [0.7, 0.0, 0.3, 0.3] and state["layout_auto"] is False
    assert not (d / "final.mp4").exists()
    (d / "final.mp4").write_bytes(b"x")
    apply_edit(tmp_path, "0h00m10s", {"game": [0.0, 0.0, 0.5, 1.0]})  # the same box again changes nothing
    assert (d / "final.mp4").exists()
    for bad in ([0.5, 0.5, 0.01, 0.5], [1.2, 0, 0.5, 0.5], "abc", [0.1, 0.1, 0.5]):
        with pytest.raises(ValueError, match="video box"):
            apply_edit(tmp_path, "0h00m10s", {"game": bad})
    assert apply_edit(tmp_path, "0h00m10s", {"game": [0.8, 0.8, 0.5, 0.5]})["game"] == [0.8, 0.8, 0.2, 0.2]  # kept inside the picture


def test_boxes_saved_for_a_streamer_are_used_on_their_next_clips(tmp_path):
    from clipper.layout import auto_layout, save_framing, saved_framing

    cfg = _cfg(tmp_path)
    save_framing("Lacy", {"layout": "split", "facecam": [0.7, 0.0, 0.3, 0.3], "game": [0.0, 0.0, 0.5, 1.0], "source": [1920, 1080]}, cfg)
    assert saved_framing("lacy", cfg)["layout"] == "split"
    state = auto_layout(tmp_path, {"channel": "lacy", "source_width": 1280, "source_height": 720}, cfg)
    assert state["layout"] == "split" and state["game"] == [0.0, 0.0, 0.5, 1.0] and "saved" in state["layout_reason"]
    # A clip a person framed by hand keeps its own boxes.
    own = auto_layout(tmp_path, {"channel": "lacy", "layout": "crop", "layout_auto": False, "source_width": 1280, "source_height": 720}, cfg)
    assert own["layout"] == "crop" and "game" not in own
    save_framing("Lacy", None, cfg)
    assert saved_framing("lacy", cfg) is None


# ---- the cutting board ----


def test_pieces_marked_on_the_cutting_board_are_checked():
    from clipper.pipeline import check_segments

    assert check_segments([[50, 90.04], ["0", "45.5"]], 90) == [(0.0, 45.5), (50.0, 90.0)]  # put in order, the end kept inside
    for pieces, why in (([], "nothing to make"), ([[0, 5]], "at least 8"), ([[0, 400]], "at most 300"), ([[0, 40], [30, 60]], "overlap"),
                        ([[80, 120]], "outside"), ([["a", 2]], "start and an end")):
        with pytest.raises(ValueError, match=why):
            check_segments(pieces, 90 if why != "at most 300" else 500)


def test_a_part_for_the_cutting_board_is_refused_when_it_cannot_work(tmp_path):
    from clipper.control import Control

    cfg = _cfg(tmp_path)
    f = tmp_path / "rec.mp4"
    f.write_bytes(b"x")
    c = Control(cfg)
    with pytest.raises(ValueError, match="after the start"):
        c.start_range({"source": str(f), "start": 100, "end": 50})
    with pytest.raises(ValueError, match="too short"):
        c.start_range({"source": str(f), "start": 0, "end": 4})
    with pytest.raises(ValueError, match="at most 30 minutes"):
        c.start_range({"source": str(f), "start": 0, "end": 3 * 3600})
    with pytest.raises(ValueError, match="not there any more"):
        c.start_range_cut({"key": "local_x/0h00m00s-0h01m30s", "pieces": [[0, 30]]})
    with pytest.raises(ValueError, match="not there any more"):
        c.start_range_cut({"key": "../../etc", "pieces": [[0, 30]]})


def test_downloaded_parts_are_listed_for_the_cutting_board(tmp_path):
    from clipper.review import list_ranges

    cfg = _cfg(tmp_path)
    d = tmp_path / "work" / "youtube_abc" / "ranges" / "0h05m10s-0h06m40s"
    d.mkdir(parents=True)
    (d / "range.json").write_text(json.dumps({"id": d.name, "start_s": 310, "end_s": 400, "duration_s": 90, "title": "A video", "ready": True}), encoding="utf-8")
    assert list_ranges(cfg) == []  # the note without its video is not offered
    (d / "range.mp4").write_bytes(b"x" * 10)
    c = tmp_path / "work" / "youtube_abc" / "clips" / "0h05m10s"
    c.mkdir(parents=True)
    (c / "clip.json").write_text(json.dumps({"id": "0h05m10s", "range_id": d.name}), encoding="utf-8")
    (r,) = list_ranges(cfg)
    assert r["key"] == "youtube_abc/0h05m10s-0h06m40s" and r["duration_s"] == 90 and r["clips"] == 1


def test_a_clip_whose_file_is_in_use_says_so_instead_of_crashing(tmp_path, monkeypatch):
    from pathlib import Path

    from clipper.review import ClipInUse, delete_clip

    d = tmp_path / "clips" / "0h00m10s"
    d.mkdir(parents=True)
    (d / "clip.json").write_text(json.dumps({"id": "0h00m10s", "status": "candidate"}), encoding="utf-8")
    (d / "source.mp4").write_bytes(b"x")
    (d / "final.mp4").write_bytes(b"y")  # comes before source.mp4: it must not be gone when source.mp4 refuses
    (d / "words.json").write_text("[]", encoding="utf-8")
    real = Path.rename

    def locked(self, *a, **k):
        if self.name == "source.mp4":
            raise PermissionError(32, "in use")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "rename", locked)
    with pytest.raises(ClipInUse, match="in use"):
        delete_clip(d)
    assert json.loads((d / "clip.json").read_text(encoding="utf-8"))["status"] == "candidate"
    assert sorted(p.name for p in d.iterdir()) == ["clip.json", "final.mp4", "source.mp4", "words.json"]  # all or nothing
    monkeypatch.setattr(Path, "rename", real)
    delete_clip(d)
    assert json.loads((d / "clip.json").read_text(encoding="utf-8"))["status"] == "deleted" and [p.name for p in d.iterdir()] == ["clip.json"]


def test_every_layout_gives_a_full_size_picture():
    from dataclasses import replace

    from clipper.render import LAYOUTS

    r = replace(load_config().render, facecam=[0.7, 0.05, 0.25, 0.3], game=[])
    for name in LAYOUTS:
        graph = layout_filter(1920, 1080, r, name)
        assert graph.startswith("[0:v]") and graph.endswith("[v0]"), name
    assert "boxblur" in layout_filter(1920, 1080, r, "blur") and "scale=1080:608" in layout_filter(1920, 1080, r, "blur")
    assert layout_filter(1920, 1080, r, "split_under").endswith("[bot][top]vstack=inputs=2[v0]")
    pip = layout_filter(1920, 1080, replace(r, face_corner="bottom-left", face_size=0.4), "pip")
    assert "pad=432:324:6:6:white" in pip and pip.endswith("overlay=36:1446[v0]")  # 40% wide, 4:3, clear of the credit line
    circle = layout_filter(1920, 1080, r, "circle")
    assert "geq=" in circle and "overlay=656:360[v0]" in circle  # top right, below the opening text
