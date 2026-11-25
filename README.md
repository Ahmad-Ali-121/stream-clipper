# Stream Clipper

Turn live streams into short vertical clips, with a person approving every one.

Stream Clipper watches Twitch chat for the moments where chat suddenly erupts, downloads only those few seconds, adds word-by-word captions, and shows you each draft on a local web page. You approve the good ones; it renders finished 9:16 videos and writes the title, description and hashtags to paste when you upload. It never posts anything itself.

**New here? Read [GUIDE.md](GUIDE.md): install, daily use and troubleshooting in plain steps.** To check an install, work through [docs/testing-checklist.pdf](docs/testing-checklist.pdf).

## What it does

- **Finds moments from chat.** Counts chatters in 10-second slices and compares with the last five minutes. No AI, no key, almost no bandwidth.
- **Optionally listens and looks too.** Modes B, C and D also measure loudness and on-screen motion from the stream's lowest-quality feed, to catch moments chat missed.
- **Never downloads a whole stream.** Only the clip ranges are fetched, at full quality. For streams Twitch is not recording, a rolling copy of the last ten minutes of the live feed stands in for the recording.
- **Listens to finished streams too.** `--signals B` (or the choice on the page) measures a finished stream's sound or picture and adds moments chat missed.
- **AI can read a finished stream.** `--ai` turns all of its speech into text and has Claude pick moments from what was said (needs a Claude key; a few cents a stream).
- **Works live or on finished streams, on Twitch, Kick and YouTube.** `watch` follows several live channels at once (Twitch names, Kick channels like `kick.com/name`, YouTube handles like `@channel`); `run` processes a finished Twitch or Kick stream or a YouTube recording with chat replay.
- **Your own clips.** Give a YouTube, Twitch or Kick video link, or a video file such as a screen recording, with a start and an end; get one clip, or the range split into parts labelled "Part 1", "Part 2" on screen.
- **Built-in help that can do it for you.** An **Ask** button answers questions about the program in plain words, in English or Roman Urdu, and can walk you through the steps. Tell it what to do ("start watching lacy", "make a clip from this link from 1:00 to 1:30") and it does the steps on the page while you watch. Approving, rejecting and deleting clips always stay yours.
- **Kick, like Twitch.** Live Kick channels are watched (chat, hype meter, drafts, clip-the-last); finished Kick streams are searched from their chat replay, and work for your own clips and the cutting board.
- **Templates and a background card.** The clip as a rounded card on a blur, a colour, a gradient or your image; inside a social post; under a breaking-news banner; or with the opening text in a box on top.
- **Safe zones, moment mix, music riser.** See where TikTok, Reels and Shorts cover the video; lean the search towards action or dialogue; let the music grow louder at the end.
- **Transcript search.** Search a video's words, click where a clip starts and ends, and make it. For people with no AI: the transcript in small parts with a ready prompt for any free chatbot, whose answers are read back as moments.
- **Subtitle files.** Download a clip's captions as .srt or .vtt, or use your own subtitle file as its captions.
- **Hype meter and go-live list.** While watching, each chat's heat against its own usual pace; the saved channel list can start by itself and clips each creator as soon as they go live.
- **Six layouts.** Video only, face on top, face below, the whole picture on a blurred background, the face in a corner or in a circle over the video. Chosen per clip with a live preview.
- **Caption movement and your own caption profiles.** The spoken word pops in, one big word at a time, or the line fills in; font, colours and size saved under your own names.
- **Branding.** Your logo in a corner, an outro line over the last seconds, music under the voice.
- **YouTube's own captions as the words.** For YouTube videos the words come from YouTube's captions when it has them: a clip's captions in seconds instead of minutes, and a whole stream's speech read at once. The speech model stays the fallback.
- **Excited words as a signal.** "Oh my god", "no way", "arre yaar", "bhaago" (English, Hindi, Urdu, Roman or their own letters) raise a clip's score, and find moments in a YouTube video even when it has no chat.
- **Clip the last 30 seconds.** While watching live, one button makes a draft of what just happened.
- **Free local AI (optional).** On a computer that can run it, a small AI on the PC (Ollama) writes clip text when Claude cannot be reached. A computer too small for it never downloads it.
- **A percentage on every job.** Downloads, finding moments, captions, text, drafts, final videos, the setup check and file copies show how far they are ("41% · Step 2 of 4: Downloading the clips · clip 3 of 8") and roughly how long is left.
- **Cutting board.** Download the part of a video you chose once, then mark the cuts on a timeline with the sound wave, see how many clips that gives and how long each is, and make them.
- **Face box and video box.** On any clip, drag two boxes over the stream picture to choose what is shown on top (the face) and below (the video); a small preview shows the result. Can be kept for a streamer's next clips.
- **Urdu and Hindi streams.** Speech-to-text in Urdu or Hindi, captions in the language's own letters or in Latin letters ("bhai ne kamaal kar diya"), and opening text and titles in English, Roman Urdu, Hinglish, Urdu or Hindi.
- **Frames the clip by itself.** Detects a full-screen camera or a small facecam over gameplay, and measures each streamer's facecam box from the picture.
- **Captions.** Local Whisper speech-to-text, word-by-word highlighted captions burned in.
- **A strong first three seconds.** Zoom-in, pop-in hook text, a whoosh and a progress bar.
- **A/B versions.** Every approved clip comes out twice, with different hooks and titles, so you can test which performs better.
- **Commentary formats.** A narrated "explained" version (free local voice) and a "top N moments" countdown.
- **Upload text.** Hook, title, description and hashtags per platform. Hashtags come from what well-viewed YouTube Shorts on the topic use right now.
- **Campaign briefs.** Optional rule files that check each clip (length, tags, disclosure) before you post.
- **Uses your GPU when it has one.** NVIDIA, Intel or AMD video encoders are picked automatically. Speech-to-text on an NVIDIA card is optional: add `-Gpu` to the setup command (about 1 GB more). Everything also runs on a plain CPU.

Everything in the core works with no paid service. Optional keys add nicer text and voices:

| Add-on | Key in `.env` | What it adds | Rough cost |
|---|---|---|---|
| Claude | `ANTHROPIC_API_KEY`, or no key: the Claude Code command signed in with your own Claude plan (`llm.backend: claude-code`) | Writes hooks, titles, descriptions, narration; flags filler clips; can read a whole stream and pick moments | about $0.01 per clip with a key; with a plan it uses your usage limits |
| ElevenLabs | `ELEVENLABS_API_KEY` | More natural narrator voice (set `voice.backend: elevenlabs`) | from $6 a month |
| Groq | `GROQ_API_KEY` | Faster speech-to-text on their servers (untested) | about $0.04 per audio hour |

## Quick start (Windows)

Needs [Git](https://git-scm.com/download/win), [uv](https://docs.astral.sh/uv/getting-started/installation/) and [Node.js](https://nodejs.org/), and about 5 GB of free disk.

```powershell
git clone https://github.com/Ahmad-Ali-121/stream-clipper.git
```

Then open the `stream-clipper` folder and **double-click `Install-Stream-Clipper.cmd`**. It asks where to keep the clips, downloads what is needed, and puts a "Stream Clipper" shortcut on the desktop. Double-click that shortcut to start. (The same from a terminal: `powershell -ExecutionPolicy Bypass -File setup.ps1 -DataDir D:\clipper-data`.)

The setup script downloads FFmpeg, TwitchDownloaderCLI and the face and voice models, installs the Python packages and runs the tests. Then:

```powershell
uv run clipper review
```

Open http://127.0.0.1:8765/ in your browser. The page has three steps: find moments (watch live channels, or paste a finished stream), review each draft clip (watch, edit, approve or reject), and make the finished videos.

The same things from the terminal:

```powershell
uv run clipper watch lacy xqc          # asks: A chat, B chat+audio, C chat+video, D all
uv run clipper review                  # approve or reject drafts in the browser
uv run clipper finish                  # render approved clips and write the upload text
```

Finished videos and their text files land in `<data folder>\ready\<date>\`.

The code is plain Python and FFmpeg, so it should run on macOS and Linux, but the setup script and all testing so far are Windows only.

## Commands

| Command | What it does |
|---|---|
| `watch CHANNEL... [--mode A\|B\|C\|D] [--minutes N]` | Watch live Twitch, Kick and YouTube channels and make a draft clip for each moment |
| `cut SOURCE [--start T] [--end T] [--parts N \| --part-seconds S]` | Make clips from a range you choose in a video link or a video file, whole or in numbered parts |
| `range SOURCE [--start T] [--end T]` | Download one part of a video once, for the cutting board on the review page |
| `cut SOURCE --ranges 750-790,3725-3770` | Make a clip from each of several separate stretches of one video |
| `cut SOURCE --range NAME --pieces 0-45,45-110` | Make clips from pieces of a part downloaded with `range` (what the cutting board runs) |
| `run URL [--top 8] [--signals A\|B\|C\|D]` | Process a finished stream: find moments (chat, or chat plus sound and picture), fetch, transcribe, draft |
| `review [URL]` | Local page: options panel, and approve, reject or edit each clip |
| `finish [URL] [--brief FILE]` | Render approved clips (A and B versions, explainers) and export them with upload text |
| `explain URL [--clips a,b]` | Narrated "explained" version of approved clips |
| `top [URL] [--count 5] [--opener TEXT]` | "Top N moments" countdown video |
| `write [URL] [--force]` | Write or refresh hooks, titles, hashtags and narration lines |
| `tags SEED...` | Hashtags that well-viewed YouTube Shorts on a topic use right now |
| `status URL` | List a stream's clips and what exists for each |
| `hardware [--refresh]` | Show which GPU or CPU paths are in use |
| `doctor` | Describe this computer and try speech-to-text several ways, to find why it fails |
| `clean [--dry-run]` | Delete the video files of rejected clips |
| `find`, `fetch`, `transcribe`, `render`, `post` | The single stages of `run` and `finish` |
| `check-brief BRIEF CLIP` | Check one clip's JSON against a campaign brief |

Every setting lives in [config.yaml](config.yaml), with a comment on each line. Personal overrides go in `config.local.yaml`, keys in `.env`; neither is committed.

## How a moment is found

1. Chat is read live over Twitch's public, anonymous, read-only chat connection (or downloaded for a finished stream).
2. Messages are grouped into 10-second buckets. A bucket's score is its number of distinct chatters divided by the median of the previous five minutes. Bursts of hype words count as well, for chats that are always busy.
3. In modes B to D, one FFmpeg process per channel reads the `audio_only` or `160p` feed, prints one loudness value a second and two motion values a second, and discards the feed. A bucket that stands far above the last five minutes becomes a moment even if chat stayed quiet. These are measurements, not a neural network.
4. About a minute later the clip range (20 seconds before the peak to 10 after) is fetched from the stream's recording, transcribed, framed, captioned and rendered as a draft.

## Layout of the code

```
src/clipper/
  cli.py         commands
  config.py      settings (config.yaml)
  chat.py        read chat files            moments.py   score chat, pick moments
  watch.py       live watching              signals.py   audio and video measurements
  youtube.py     live YouTube: chat and clips
  fetch.py       download ranges            transcribe.py speech to text
  layout.py      crop or split, facecam     cuts.py      snap cuts to pauses
  captions.py    caption file (ASS)         render.py    final video with FFmpeg
  review.py      local review page          control.py   options panel behind it
  llm.py         text by Claude             trends.py    hashtags from YouTube
  tts.py         narration voice            compose.py   explained and top-N videos
  post.py        export and upload text     brief.py     campaign rule checks
  streamread.py  AI reads a whole stream's speech
  health.py      setup check, repair, update   scoring.py   one score per clip, learning
  tracker.py     what was posted, views, payouts
  hw.py          GPU / CPU choice           buffer.py    rolling live copy when there is no recording
tests/           354 unit tests (uv run python -m pytest -q)
```

## Limits you should know about

- Chat spikes also happen for giveaways, greetings and raids. Expect filler among the drafts; that is what the review step is for.
- The audio and video signals are new and their thresholds are not tuned on many streams.
- On live YouTube streams moments come from chat only (no sound or picture detection yet), and a clip must be cut within about 15 minutes of its moment, which is how long YouTube keeps a live stream's pieces available.
- Urdu and Hindi: the narrator in explainer videos speaks English only. Latin-letter captions need Claude.
- A stream with recordings switched off costs about 3 GB of internet an hour to watch, and moments during advert breaks cannot be clipped.
- Speech-to-text on an NVIDIA card (`setup.ps1 -Gpu`) is new and has had almost no testing. If it cannot be used, the processor is used instead.
- The first clip or two of a new streamer may be framed slightly off until the facecam box has been measured twice.

## Use it responsibly

- **Clip only with permission.** Use it for streamers who allow clipping or run a clipping campaign, and follow the campaign's rules. Streams are their creators' work.
- **You are responsible for what you post.** Platform rules on reused content apply to clips. Captions and narration do not guarantee monetisation.
- **No posting automation.** The tool deliberately stops at files on your disk. Please keep it that way in forks.
- **Do not send the A and B versions of a clip to the same campaign.** Campaigns reject duplicates.

## Contributing

Issues and pull requests are welcome. Keep the core free to run, keep settings in `config.yaml`, add tests for logic (`uv run pytest -q`), and say in the pull request what you tested on a real stream.

## Licence

[MIT](LICENSE). FFmpeg, TwitchDownloaderCLI, yt-dlp, faster-whisper, Kokoro and YuNet are downloaded by the setup script under their own licences.

Suggested repository topics: `twitch`, `clips`, `youtube-shorts`, `tiktok`, `ffmpeg`, `whisper`, `captions`, `video-editing`, `streaming`, `python`.

## Credits

Some ideas were learned from other open-source clippers and written again in our own code: taking the words from YouTube's own captions, scoring excited words, a "clip now" button and a free local AI come from [NeonClipping](https://github.com/iamajmeena/neonclipping) (MIT) and the projects it credits, such as PeakCut.
