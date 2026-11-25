import pytest

from clipper.buffer import choose_pieces, parse_playlist
from clipper.tools import ToolError

PLAYLIST = """#EXTM3U
#EXT-X-TARGETDURATION:5
#EXT-X-DATERANGE:ID="stitched-ad-1",CLASS="twitch-stitched-ad",START-DATE="2026-11-18T23:14:12.199Z",DURATION=30.2
#EXT-X-DISCONTINUITY
#EXT-X-PROGRAM-DATE-TIME:2026-11-18T23:14:12.199Z
#EXTINF:2.000,Amazon|2474283100495.0
https://cdn.example/ad1.ts
#EXT-X-DISCONTINUITY
#EXT-X-PROGRAM-DATE-TIME:2026-11-18T23:14:14.199Z
#EXTINF:2.000,live
https://cdn.example/a.ts
#EXTINF:2.500,live
b.ts
#EXT-X-TWITCH-PREFETCH:https://cdn.example/soon.ts
"""


def test_playlist_gives_times_and_marks_adverts():
    pieces = parse_playlist(PLAYLIST, "https://cdn.example/list.m3u8")
    assert [p.url for p in pieces] == ["https://cdn.example/ad1.ts", "https://cdn.example/a.ts", "https://cdn.example/b.ts"]
    assert [p.advert for p in pieces] == [True, False, False]
    assert pieces[1].at - pieces[0].at == pytest.approx(2.0)
    # a piece without its own time continues from the one before; prefetch pieces are ignored
    assert pieces[2].at - pieces[1].at == pytest.approx(2.0)
    assert pieces[2].duration == 2.5


def pieces(*starts):
    return [{"t": float(t), "d": 2.0, "file": f"{t}.ts"} for t in starts]


def test_choose_pieces_covers_the_range_from_the_piece_before_it():
    got = choose_pieces(pieces(100, 102, 104, 106, 108), 103.0, 107.5)
    assert [p["t"] for p in got] == [102.0, 104.0, 106.0]


@pytest.mark.parametrize(
    "have, start, end, says",
    [
        ([], 10, 20, "still empty"),
        (pieces(100, 102, 104), 90, 103, "no longer in the live buffer"),
        (pieces(100, 102, 104), 101, 120, "not reached the end"),
        (pieces(100, 102, 110, 112), 101, 113, "missing from the live feed"),  # gap in the middle
        (pieces(100, 102, 110, 112), 105, 113, "missing from the live feed"),  # starts inside the gap
        (pieces(100, 102, 110, 112), 101, 108, "missing from the live feed"),  # ends inside the gap
    ],
)
def test_choose_pieces_refuses_what_it_cannot_cover(have, start, end, says):
    with pytest.raises(ToolError, match=says):
        choose_pieces(have, start, end)


def test_the_newer_kind_of_live_feed_keeps_its_header_piece(tmp_path):
    """Twitch sends some streams as fragmented MP4: every clip of such a stream failed with
    "trun track id unknown, no tfhd was found" because the header piece named once in the playlist was left out."""
    from clipper import buffer

    text = "\n".join(["#EXTM3U", '#EXT-X-MAP:URI="init-0.mp4"', "#EXT-X-PROGRAM-DATE-TIME:2026-11-25T03:00:00Z", "#EXTINF:2.0,live", "1.mp4",
                      "#EXTINF:2.0,live", "2.mp4"])
    pieces = buffer.parse_playlist(text, "https://example.test/v1/playlist.m3u8")
    assert [p.init for p in pieces] == ["https://example.test/v1/init-0.mp4"] * 2
    assert [p.init for p in buffer.parse_playlist(text.replace('#EXT-X-MAP:URI="init-0.mp4"\n', ""), "https://example.test/v1/x.m3u8")] == ["", ""]
