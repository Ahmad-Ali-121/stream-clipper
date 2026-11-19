# How to use the clipper

A step-by-step guide in plain words. Technical detail is in [README.md](README.md).

## What it does

You give it a Twitch stream. It finds the moments where chat went wild (and, if you choose, where the sound or the picture suddenly jumps), cuts just those few seconds, adds captions, and shows you each clip. You approve the good ones. It then gives you the finished video files and the text to paste when you upload them. You upload by hand.

## Part 1: Install on a computer (once)

You need a Windows PC with about 5 GB free. Do these in order.

1. **Install three programs** from their websites (default options are fine):
   - Git: https://git-scm.com/download/win
   - uv: https://docs.astral.sh/uv/getting-started/installation/
   - Node.js (LTS): https://nodejs.org/
2. **Get the project folder onto the PC.** Copy the whole `Clipping` folder from this laptop (USB stick or a private GitHub repository). You do not need to copy `D:\clipper-data`.
3. **Open a terminal in the folder.** In File Explorer, open the `Clipping` folder, click the address bar, type `powershell` and press Enter.
4. **Run the setup script.** Pick a drive with free space for the data folder:

   ```powershell
   powershell -ExecutionPolicy Bypass -File setup.ps1 -DataDir D:\clipper-data
   ```

   It downloads FFmpeg, TwitchDownloaderCLI, the face model and the voice model, installs the Python packages, and runs the tests. On a slow connection this takes a while; if it stops, run it again and it continues.
5. **Check the hardware it will use:**

   ```powershell
   uv run clipper hardware
   ```

   It prints the video encoder (NVIDIA, Intel or CPU) and whether speech runs on a GPU. Nothing to configure: it uses the fastest thing that works.

The first time a clip is transcribed, the speech model (1.6 GB) downloads by itself.

**On an NVIDIA laptop**, speech on the GPU also needs NVIDIA's CUDA 12 and cuDNN 9 libraries installed. Without them it still works, on the CPU. This path has not been tested yet.

## Part 2: Optional paid keys

The clipper works with none of these. Create a file named `.env` in the `Clipping` folder (copy `.env.example`) and add only the lines you have keys for.

| Key | What it adds | Cost | Where to get it |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Claude writes the hook, title, description and narration, and marks filler clips | About $0.02 per clip with the default model, far less with `claude-haiku-5-5` (set `llm.model` in `config.yaml`). Minimum top-up is $5 | console.anthropic.com |
| `ELEVENLABS_API_KEY` | A more natural narrator voice (set `voice.backend: elevenlabs`) | Starter plan $6 a month | elevenlabs.io |
| `GROQ_API_KEY` | Faster transcription (set `transcribe.backend: groq`) | About $0.04 per hour of audio | console.groq.com |

Every paid call is written to `D:\clipper-data\usage.jsonl` with its cost, so you can see what a day cost.

## Part 3: Daily use

All commands are typed in a terminal opened in the `Clipping` folder.

### The easy way: everything from the page

```powershell
uv run clipper review
```

Open http://127.0.0.1:8765/ in your browser. The page follows three numbered steps:

1. **Find moments** (left side). Two tabs:
   - **Live streams:** type channel names, pick A, B, C or D, press **Start watching**. **Stop** ends it.
   - **Finished stream:** paste the link of a Twitch video that has ended, choose how many clips, press **Find the best moments**.
2. **Review the clips** (right side). Each clip shows its video, why it was picked, what was said and the opening text. Press **Approve** or **Reject**. The buttons at the top switch between clips to review, approved and rejected. New drafts appear by themselves.
3. **Make the videos** (left side). Switch the A/B versions and the eye-catching opening on or off, then press the green button. It makes the videos for every clip you approved.

**Delete** on a clip removes it and its files for good; in the Rejected list, **Delete all rejected** clears them all. Videos you already made stay in the ready folder. Red messages can be closed with the × in their corner.

Each step has an "Activity log" you can open to see what the program is doing. Problems are shown in red, at the top of the page and on the clip they belong to.

Keep the terminal open while you use the page. The steps below do the same things from the terminal.

### A. Live streams (the main way)

1. **Start watching.** Give it channel names. Leave this terminal open.

   ```powershell
   uv run clipper watch lacy xqc clix
   ```

   It asks what to use to find moments:

   | Choice | Uses | Internet used |
   |---|---|---|
   | A | chat only | almost none |
   | B | chat + audio (loud moments) | about 70 MB an hour for each channel |
   | C | chat + video (fast action) | about 100 MB an hour for each channel |
   | D | chat + audio + video | about 100 MB an hour for each channel |

   Press Enter for the usual choice, or skip the question with `--mode B`. B, C and D read the stream's lowest-quality feed, measure it and throw it away; nothing is saved.

   Each time chat spikes it makes a draft clip and prints a `DRAFT` line. Offline channels are checked again every 30 seconds.
2. **Review.** Open a second terminal in the same folder:

   ```powershell
   uv run clipper review
   ```

   Open the address it prints (http://127.0.0.1:8765/) in your browser. For each clip: watch it, fix the hook text if you want, press **Approve** or **Reject**. Tick **Also make an explainer version** on a clip if you want the narrated version of it too. Press Ctrl+C in that terminal when finished.
3. **Finish.** This renders everything you approved and writes the upload text:

   ```powershell
   uv run clipper finish
   ```

   For a campaign, add its brief so each clip is checked against the rules:

   ```powershell
   uv run clipper finish --brief briefs/lacy-gta-marathon.yaml
   ```
   Every approved clip comes out as two videos for A/B testing: `..._A.mp4` and `..._B.mp4`. They are the same moment with different opening text and titles. Post A and B on different accounts or platforms and see which gets more views; never send both to the same campaign. Clips you ticked also get `..._explained_A.mp4` and `..._explained_B.mp4` (different narration line and animation). To get one version only, set `ab_versions: false` in `config.yaml`.
4. **Upload.** Open `D:\clipper-data\ready\` and today's date folder. Each `.mp4` has a `.txt` beside it with the title, description and hashtags for YouTube, TikTok, Instagram and Facebook. Upload the video, paste the text. For a campaign, submit the post link on the campaign site straight away.
5. **Stop watching** with Ctrl+C in the first terminal. Starting it again later carries on.

### B. A stream that already happened

Open the streamer's Videos tab on Twitch, copy the link of the stream (it looks like `https://www.twitch.tv/videos/123456789`), then:

```powershell
uv run clipper run https://www.twitch.tv/videos/123456789 --top 8
```

Or paste the link on the review page: step 1, **Finished stream** tab.

Chat does not catch everything. To also find moments from the stream's sound or picture, add `--signals B` (chat + sound), `C` (chat + picture) or `D` (all), or pick it on the page:

```powershell
uv run clipper run https://www.twitch.tv/videos/123456789 --top 8 --signals B
```

This reads the stream's lowest-quality version once to measure it (about 100 MB per stream hour for sound, 130 MB with picture) and saves none of it. You get the 8 best chat moments, each marked where sound or picture agree, plus up to 4 moments that only sound or picture found.

**Let AI read the stream.** Add `--ai` (or switch on "Let AI read the whole stream" on the page). The whole stream's speech is turned into text and Claude picks moments from what was said, which finds funny lines that chat and loudness miss. It needs a Claude key in `.env`. On a processor it reads about 8 times faster than the stream plays, so a 4-hour stream takes about half an hour; it costs about a cent per stream with the default model. If you stop it, the next run carries on where it stopped.

Then do steps 2 to 4 above. This also works with a YouTube stream recording, if that video has chat replay.

### C. Commentary videos (for your own channel)

These add a narrator, which is what YouTube asks for before it pays for clip content.

- **Explained clip:** the narrator sets the moment up over an animated card (the clip's picture sliding in, the streamer's name, a progress bar, a whoosh), then the clip plays.

  ```powershell
  uv run clipper explain https://www.twitch.tv/videos/123456789
  ```

  It does this for every approved clip of that stream.
- **Top moments:** a countdown of the best clips, each with a rank card and a spoken line.

  ```powershell
  uv run clipper top --count 5 --opener "Five moments from tonight's GTA 6 streams."
  ```

  Without a link it picks the best approved clips from every stream; with a link, from that stream.

The narration lines are written by Claude when the Anthropic key is set. Without it they are plain placeholders, and you should type your own `intro` and `rank_line` into each clip's `clip.json`.

### D. Useful extras

| Command | What it does |
|---|---|
| `uv run clipper tags gta6 lacy` | Hashtags that well-viewed YouTube Shorts on those topics use right now |
| `uv run clipper write` | Write or refresh hooks, titles and hashtags for clips that have none |
| `uv run clipper status <link>` | List a stream's clips and what exists for each |
| `uv run clipper clean` | Free disk space by deleting the video files of rejected clips |

## Part 4: Adding things

- **A new streamer.** Nothing to do: just use the channel name. The clipper measures where the streamer's facecam is from the picture itself, and once two clips of that streamer agree it remembers the position (in `D:\clipper-data\facecams.json`). The first clip or two of a new streamer may be framed a little off; fix those with Layout on the review page. If a streamer's framing stays wrong, you can type the exact box under `channels:` in `config.yaml`, which always wins.
- **A new campaign.** Ask Claude Code: "add a brief for this campaign" and paste the campaign page link. It writes a file under `briefs/`. Check it against the live campaign page yourself.

## Part 5: When something goes wrong

| What you see | What to do |
|---|---|
| A download fails | The connection dropped. Run the same command again; it continues where it stopped |
| "not found on PATH" | Run the setup script again |
| "less than 2 GB free" | Run `uv run clipper clean`, or delete old folders in `D:\clipper-data\work` |
| "live ... but Twitch is not recording this broadcast" | Not an error. The streamer has recordings (VODs) switched off, so the watcher keeps the last 10 minutes of the live stream on disk and cuts clips from that. It uses about 3 GB of internet an hour for that channel, the first minute is lost to Twitch's opening advert, and moments during advert breaks are lost. To skip such streams instead, set `buffer_when_no_vod: false` under `watch:` in `config.yaml` |
| "no chat replay" | That video cannot be scored from chat. Use a live watch instead |
| A clip is framed wrong | On the review page, change Layout and save; then run `finish` again |
| "an NVIDIA card was found but speech-to-text cannot run on it" | Not an error: speech-to-text runs on the processor instead (slower, same result). To use the card, run the setup command again with `-Gpu` added at the end. It downloads NVIDIA's libraries (about 1 GB) and then says whether the card works. If it still cannot, update the NVIDIA driver and run `uv run clipper hardware --refresh` |
| A red box at the top of the page | The watcher stopped by itself, or the program is not answering. The red and yellow lines in the log under it, and the terminal window, say why. A clip that could not be made shows its own reason in red on its card |
| The program closes or says "access violation" when captions are made | The computer's Microsoft Visual C++ runtime is too old. Install the x64 version from https://aka.ms/vs/17/release/vc_redist.x64.exe, restart, and try again. `uv run clipper doctor` shows the installed version |
| Captions miss words | Normal on very noisy moments. The larger model is already the default |
| Too many clips with nothing in them in mode B, C or D | Raise `audio_level` or `video_level` under `watch:` in `config.yaml` |
| Anything else | Open an issue on the project's GitHub page, or open Claude Code in this folder and describe it |

## Where everything is

| Place | What is in it |
|---|---|
| `Clipping\config.yaml` | Every setting, with a comment on each |
| `Clipping\.env` | Your paid keys (never share this file) |
| `Clipping\briefs\` | One file per campaign |
| `D:\clipper-data\work\` | One folder per stream, with its clips |
| `D:\clipper-data\ready\` | Finished videos and their upload text, by date |
| `D:\clipper-data\usage.jsonl` | Cost of each paid call |
