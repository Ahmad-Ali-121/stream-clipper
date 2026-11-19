import sys

from clipper import progress


def test_a_job_reports_how_far_it_is_as_one_percentage(tmp_path):
    f = tmp_path / "progress.json"
    progress.begin([("download", "Downloading the clips", 50), ("captions", "Writing the captions", 30), ("skipped", "Never used", 0),
                    ("draft", "Making the draft videos", 20)], path=f)
    assert progress.read(f)["percent"] == 0 and progress.read(f)["stages"] == 3  # a step with no share is not counted
    progress.stage("download")
    progress.item(1, 4, "clip 2 of 4")  # the first of four is done
    progress.sub(0.5)
    now = progress.snapshot()
    assert now["percent"] == 18.8 and now["stage_no"] == 1 and now["text"] == "clip 2 of 4"  # (1 + 0.5) / 4 of the first 50%
    progress.sub(0.2)  # a tool that starts a second pass must not send the bar backwards
    assert progress.snapshot()["percent"] == 18.8
    progress.stage("captions")
    assert progress.snapshot()["percent"] == 50 and progress.snapshot()["stage"] == "Writing the captions"
    progress.stage("something this job never named")  # ignored, with what is reported inside it
    progress.sub(0.9)
    assert progress.snapshot()["percent"] == 50
    progress.finish()
    assert progress.read(f)["percent"] == 100
    progress.begin([])  # nothing leaks into the next test


def test_nothing_is_written_when_no_page_is_listening(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPPER_PROGRESS", raising=False)
    progress.begin([("download", "Downloading", 1)])
    progress.stage("download")
    progress.sub(0.5)
    assert list(tmp_path.iterdir()) == [] and progress.read(tmp_path / "progress.json") is None
    progress.begin([])


def test_the_percentage_is_read_from_what_the_tools_print():
    ff = "frame=  204 fps= 28 q=31.0 size=    1536KiB time=00:00:30.00 bitrate=3553.3kbits/s speed=0.489x"
    assert progress.tool_fraction(ff, 120) == 0.25 and progress.is_progress_line(ff)
    assert progress.tool_fraction(ff) is None  # without the length there is nothing to compare with
    assert progress.tool_fraction("[STATUS] - Fetching Video Info [1/4]") == 0
    assert round(progress.tool_fraction("[STATUS] - Downloading 50% [2/4]"), 2) == 0.45
    assert round(progress.tool_fraction("[STATUS] - Finalizing Video 100% [4/4]"), 2) == 1.0
    assert progress.tool_fraction("[STATUS] - Downloading 50%") == 0.45  # the chat download: no step numbers
    assert round(progress.tool_fraction("[STATUS] - Backfilling Commenter Info 100%"), 2) == 1.0
    assert progress.tool_fraction("[STATUS] - Writing Output File") is None
    # FFmpeg's closing statistics are full of percent signs and must not be taken for progress.
    assert progress.tool_fraction("[libx264 @ 000002] i8c dc,h,v,p: 56% 21% 16%  7%", 120) is None


def test_a_tool_is_watched_line_by_line_and_its_progress_lines_stay_out_of_errors():
    from clipper.tools import ToolError, run

    code = ("import sys\n"
            "sys.stdout.write('frame=1 time=00:00:01.00 bitrate=1kbits/s\\r'); sys.stdout.flush()\n"
            "sys.stdout.write('frame=2 time=00:00:02.00 bitrate=1kbits/s\\r'); sys.stdout.flush()\n"
            "sys.stderr.write('the real reason\\n'); sys.exit(int(sys.argv[1]))\n")
    seen: list[str] = []
    proc = run([sys.executable, "-c", code, "0"], on_line=seen.append, timeout_s=60)
    assert len([x for x in seen if "time=" in x]) == 2 and "the real reason" in proc.stdout and "frame=" not in proc.stdout
    try:
        run([sys.executable, "-c", code, "3"], on_line=lambda line: 1 / 0, timeout_s=60)  # a broken reader does not stop the tool
    except ToolError as e:
        assert "exit code 3" in str(e) and "the real reason" in str(e) and "frame=" not in str(e)
    else:
        raise AssertionError("a failing tool must still raise")
