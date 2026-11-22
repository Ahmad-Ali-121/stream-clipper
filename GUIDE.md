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
4. **Install it.** The easy way: in the folder, double-click **`Install-Stream-Clipper.cmd`**. It asks where to keep the clips (press Enter for the suggestion), downloads everything, and puts a **Stream Clipper** shortcut on the desktop. If the PC has no `uv` yet, it offers to install it. You can run it again any time; it only fetches what is missing.

   The same thing from a terminal, if you prefer. Pick a drive with free space for the data folder:

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

**Claude without an API key.** If you pay for a Claude plan (Pro or Max), the clipper can use it instead of an API key:

1. Install Claude Code from https://claude.com/claude-code, open a terminal, run `claude` once and sign in with your own account.
2. Open the review page. The **Claude** box finds it by itself: it tries each connection for real and shows a green dot and "Works", or a red dot with the reason. With "Automatic" it uses your plan first and, if you allow it, your API key when the plan fails (it tells you when that happens). You can also fix the choice to one of the two.

Nothing is billed per use, but each request counts towards your plan's usage limits, the same limits as your normal Claude chats. Reading a whole stream with a large model uses a lot of them. Every person does this on their own PC with their own account; the clipper only runs the `claude` command and never sees your sign-in. The same box chooses the model for clip text and for reading streams.

Every paid call is written to `D:\clipper-data\usage.jsonl` with its cost, so you can see what a day cost.

## Part 3: Daily use

All commands are typed in a terminal opened in the `Clipping` folder.

### The easy way: everything from the page

```powershell
uv run clipper review
```

Or just double-click **Stream Clipper** on the desktop (setup puts it there): it starts the program and opens the page.

The **Setup check** box at the top of the left side shows whether this computer is ready: a green dot per item, or what is wrong and what to do. **Repair** downloads anything missing. **Update** appears when a newer version exists and installs it.

Open http://127.0.0.1:8765/ in your browser. The page follows three numbered steps:

1. **Find moments** (left side). Two tabs:
   - **Live streams:** type channel names, pick A, B, C or D, press **Start watching**. **Stop** ends it.
   - **Finished stream:** paste the link of a Twitch video that has ended, choose how many clips, press **Find the best moments**.
2. **Review the clips** (right side). Each clip shows its video, why it was picked, what was said and the opening text. Press **Approve** or **Reject**. The buttons at the top switch between the lists: To review, Approved, Pending, Done, Rejected and All. New drafts appear by themselves.
3. **Make the videos** (left side). Switch the A/B versions and the eye-catching opening on or off, then press the green button. It makes the videos for every clip you approved. **Stop** next to it stops the making (see below), and **Open the videos folder** shows the finished files.

On each clip you can also:
- **Trim it:** open *Fine-tune*, drag the two handles over the sound wave, and press Save changes.
- **Fix the captions:** open *Captions*, correct a word or empty its box to remove it, and press Save captions.
- **Compare versions:** the small buttons under the video switch between A, B and the raw download.
- **Use the keyboard:** J and K move between clips, Space plays, A approves, R rejects. A key held down decides one clip only.

The caption look (four styles) is chosen in *Make the videos*. So is the **campaign**: pick one and every clip shows whether it meets that campaign's rules, and what to fix. Campaign rule files live in the `briefs` folder; copy `example.yaml` and fill it in from the campaign's own page.

**Tracker.** After you post a clip by hand, open the clip, use *Posted it? Log it*, and paste the link. The **Tracker** tab (above the clip list) then keeps your posts, views, payouts and rejections together, adds them up per campaign, and warns you before the same clip goes to the same campaign twice.

Once a clip's videos are made it moves to the **Done** list. If you change it afterwards, it goes back to Approved until you press the green button again. That includes its title, description and hashtags: the finished file is named after the title and its text file holds all of them, so the green button copies the video again under the new name (nothing is rendered again, it takes a moment).

**The buttons on a clip** are in two rows. The top row decides about the clip and only shows what fits where the clip is now: a clip to review has **Approve** and **Reject**; an approved clip has **Make this clip's videos now**, **Move to Pending**, **Back to review** and **Reject**; a finished clip has **Move to Pending**, **Back to review** and **Reject**; a rejected clip has **Approve** and **Back to review**. The second row holds the tools: **Save changes**, **Write text with AI** and **Delete**.

Under the clip's video, small buttons switch between its versions (A, B, Explained, Raw) and, when it has one, its **Thumbnail**.

The green button, **Stop** and **Open the videos folder** stay in sight at the bottom of the left side while you scroll through step 3.

#### Which versions a clip gets

Open **Versions to make** on a clip. It has two tick boxes:

- **Make the normal video**: the clip as it is, without a narrator. Ticked on every clip to begin with.
- **Make an explainer version**: a narrator sets up the moment, then the clip plays.

Tick one or both, then press Save changes. Untick the normal video and tick the explainer to get only the explained one. A clip needs at least one; the page refuses to save a clip with neither. If you tick a version after the videos were made, the clip goes back to Approved and only the missing version is made.

#### What the finished files are called

Each finished video is named after its title, so you can find it by reading the name:

- The A version is called by **Title (version A)**, the B version by **Title, version B**. If B has no title of its own, it is the A title with "(B)".
- An explainer has "(explained)" after the title.
- A clip with no real title (AI was not available, so its title is only the placeholder "... stream moment") keeps the old kind of name: the streamer, the clip's number, and `_A`, `_B` or `_explained`.
- Signs Windows does not allow in a file name (such as ? : " /) are left out. Two clips with the same title on one day both stay: the second gets "(2)".
- If you change a title and make the video again the same day, the file with the old title is replaced, so you never have two copies of one video.

The text file with the upload text has the same name as its video. A Done clip also lists its finished files on its card (**Finished files in the ready folder**), and **Open the videos folder** under the green button opens today's folder on this computer.

#### Making one clip, and stopping

- **Make this clip's videos now** (on an approved clip) makes that one clip and leaves the other approved clips for later.
- If you reject a clip or move it to Pending while the videos are being made, and its turn has not come yet, it is not made.
- **Stop** (next to the green button) asks what you want, with three buttons: **Cancel** keeps going; **Stop after this video** finishes the clip being made and then stops; **Stop now** ends it in the middle, and that clip is not made. Videos already made are always kept. The clips that were not made stay under Approved, and the Activity log names them. Press the green button again to carry on.

#### Thumbnails (optional)

A thumbnail is a cover picture for a clip: one 1080 x 1920 picture, for YouTube Shorts, Instagram Reels and TikTok. Nothing needs one; it is an extra and it is **Off** to begin with.

**For every clip, by itself.** In step 3, **Thumbnail for each clip** has three choices:

- **Off**: no thumbnail is made by itself.
- **From the clip, with the opening text (free)**: no AI. The program takes the sharpest frame of the clip and puts the opening text on it in big letters. A few seconds per clip.
- **Drawn by ChatGPT (uses your ChatGPT plan)**: ChatGPT is shown three frames of the clip, its title, the opening text and what is said, decides itself what the picture should be, and draws it. You write nothing. About a minute per clip.

With one of the two chosen, the thumbnail is made when you press the green button, as one more step for each clip whose videos are being made. It is saved in the ready folder beside the video, with the same name and `.jpg` at the end. Only a clip whose videos are made for the first time gets one this way. A clip that was finished before (even if it is made again for a new title, a new caption look or a campaign) is left alone; make its thumbnail with the buttons on the clip. That way ChatGPT never draws for a pile of old clips by surprise.

**For one clip, by hand.** Open **Thumbnail** on a clip's card:

- **Make it from the clip (free)** and **Let ChatGPT make it** do the same two things for this one clip.
- **Or describe the picture yourself**: type what you want in your own words and press **Make it from my description**. ChatGPT draws that; it still sees the clip's frames, so "the streamer" or "the car" mean the ones in the video.
- Each press makes a new picture and replaces the old one. **Download** saves it; **Remove thumbnail** takes it off the clip.
- A thumbnail you made by hand is kept: the green button does not replace it.
- One at a time per clip: a second press while one is being made is refused with a message, so ChatGPT is never asked twice for the same picture.
- What you typed as a description stays in the box if ChatGPT fails, so you can press again.

**Setting up ChatGPT (once).** It works through Codex, OpenAI's command-line tool, signed in with your own ChatGPT account, the same way Claude works through Claude Code. In a terminal run `npm install -g @openai/codex`, then `codex login`, and sign in. The line under the choice in step 3 says **ChatGPT: Ready** when it is linked, or what is missing. Pictures need a paid ChatGPT plan; they use the plan's allowance several times faster than text. Frames of the clip, its title and its words are sent to OpenAI when ChatGPT is used. Each use is written to `usage.jsonl` in the data folder. The program never reads Codex's sign-in files.

**If a thumbnail cannot be made** (not signed in, the plan's limit is reached, no internet), the clip's videos are still made. The reason is shown in red in the Activity log and on the clip under Thumbnail, and it is not tried again by itself: press the button on the clip when the problem is solved.

**Where to use it.** Instagram Reels: choose it as the cover when you post. YouTube Shorts: in YouTube Studio on a computer, under the Short's thumbnail (not every channel has this yet). TikTok: you can only pick a frame of the video as the cover.

#### Pending: take a clip out of Approved without losing it

**Move to Pending** on an approved clip takes it out of Approved, so the green button does not make it, and puts it in the **Pending** list instead of mixing it back into To review. In the Approved list, **Move all ... to Pending** does that for every approved clip at once (clips that are already Done are not touched). In Pending, **Approve** sends a clip back to Approved, **Reject** rejects it, and **Back to review** returns it to To review. Nothing is deleted by any of these.

**Delete** on a clip removes it and its files for good; in the Rejected list, **Delete all rejected** clears them all. Videos you already made stay in the ready folder. Red messages can be closed with the × in their corner.

Each step has an "Activity log" you can open to see what the program is doing. Problems are shown in red, at the top of the page and on the clip they belong to.

Keep the terminal open while you use the page. The steps below do the same things from the terminal.

### Your own clip: a link or a recording, with the times you choose

Use this when you already know which part you want, or the video has no chat. In step 1 open the **Your own clip** tab.

1. **The video.** Paste a YouTube or Twitch video link, or press **Choose a file from this PC** for a screen recording or any video file. (You can also paste a file's full path.)
2. **Start and end.** Type hours, minutes and seconds for each. Tick **Until the end of the video** to go to the end.
3. **One clip or parts.** Leave it on *One clip*, or choose:
   - *Split into a number of parts*: for example 3 equal parts;
   - *Split into parts of a set length*: for example 50 seconds each. A short leftover is added to the last part.
4. Leave **Show "Part 1", "Part 2"…** ticked so each video shows which part it is, and "Part 2 next" at the end of part 1.
5. Press **Make the clip** (or **Make the parts**).

Steps 3 to 5 are now under **Or make clips straight away**. The main button is the cutting board, below.

#### The cutting board: download once, then cut by eye

1. Give the video and the start and end as above, then press **Download this part and open the cutting board**. Only that part is downloaded (at most 30 minutes at a time). From YouTube this can take a few minutes.
2. When it is ready the page opens **Cutting board** (third button at the top, next to Clips and Tracker). You see the video, and under it the sound wave of the whole part.
3. Play the video, or click the sound wave to jump. Where one clip should end and the next begin, press **Cut here** (or the **C** key). An orange marker appears; drag it to move the cut. Click a marker and press **Remove the selected cut** (or **Delete**) to take it away.
4. Quick ways: **Split into N equal parts**, or **cut every N seconds**. You can move the markers afterwards.
5. The list under the timeline shows every piece: where it is in the video and how long it is. Untick **make this one** for a piece you do not want (an intro, a break). The heading says how many clips will be made. A piece shorter than 8 seconds or longer than 5 minutes is marked in red and must be fixed first.
6. Press **Make these clips**. They are cut out of the part that is already on this PC, so nothing is downloaded again. Each gets captions and text as usual and appears under **Clips**, in order.

Your cuts are remembered if you leave the page. The downloaded part stays until you press **Delete this download**, so you can cut it again differently.

Keys on the cutting board: Space plays or pauses, the arrow keys move one second (five with Shift), comma and full stop move a tenth of a second.

The drafts appear at the top of the list, in order. Review, approve and make the videos as usual; the titles get "(Part 1)", "(Part 2)" added.

A part can be between 8 seconds and 5 minutes long. A video file can also be searched automatically: in the **Finished stream** tab paste its path and choose sound, picture or AI reading (a file has no chat).

### Ask: the built-in help

The **? Ask** button at the bottom right opens a chat. Type a question about the program, in English or Roman Urdu: "How do I clip a live stream?", "What does the score mean?", "Why is it not working?".

- It answers in simple words, with numbered steps when there is something to do.
- **Show me** beside a step takes you there: it opens the right tab or fold-out, scrolls to the button, highlights it and blurs everything else. **Walk me through it** does this for every step in turn; press **Next**, or **Done** (or Esc) to stop. You can press the highlighted button itself.
- It knows what the program is doing right now, so it can explain an error that is on screen.
- It only answers questions about this program.

**Telling it to do something.** You can also give it an order instead of a question, in English or Roman Urdu: "start watching lacy and kick.com/name with chat only", "stop watching", "set the template to No template", "make a clip from this link from 3:53:30 to 3:54:00", "watching band kar do". It then does the steps on the page while you watch: each thing is highlighted, the text is typed, the choice is made, the button is pressed, and the step gets a tick in the chat. **Stop** (or Esc) ends it at once; steps already done stay done. When it is finished it says how many steps it did. If something could not be done (the page refused a press, a value did not fit, the thing is not there) it says which step and why, and does nothing after it. If a step has to wait for something first, such as a download, it stops before that step and leaves the rest to you.

- If you only asked how to do something, it does nothing by itself; the answer has a **Do it for me** button. The same if you closed the chat before the answer came. **New chat** drops an answer that is still on its way.
- While the chat is open, the keyboard shortcuts for clips (J, K, A, R) are switched off, so words you type can never approve or reject a clip.
- It can use anything that is on the page: boxes, lists, tick boxes, sliders, tabs and buttons. A new feature can be used the day it is added.
- If you left out something it needs (which channel, which link, what time), it asks instead of guessing.
- **Some presses always stay yours.** Approve, Reject, Delete, Back to review, Move to Pending, the Thumbnail buttons, the **Thumbnail for each clip** choice in step 3, the three buttons of the "Stop making videos?" window, the Versions to make tick boxes and everything else on a clip's card, moving all approved clips to Pending, the box editor window, deleting, removing or clearing anything, taking a logo or music off, cutting the cutting board into equal parts (it replaces your cuts), which Claude connection is used, setting up the free local AI, and repairing, restarting or updating the program are never pressed for you. It points at the button and waits for you. That is on purpose: you decide about every clip.
- It cannot post or upload anything; the program has no way to do that.

It needs Claude (your Claude Code plan or an API key). Without Claude it still answers, by showing the parts of this guide that match your question, but it cannot do things for you. Each question counts towards your Claude plan like any other use.

### Order, filters and the New tag

Above the clip list:

- **Order:** newest first (the default, so a clip just made is at the top), best score first, or oldest first.
- **Streamer**, **Found by** (chat, loud moment, fast action, excited words, AI, chosen by you) and **Made** (last hour, 24 hours, 7 days) narrow the list. **Search** looks in the words, the opening text and the titles. **Clear filters** shows everything again.
- Each card says when it was made ("made 12 min ago", "made today 20:57"). A clip made in the last hour that you have not reviewed carries a **NEW** tag.

A clip you approved, rejected or deleted stays that way, through every update and restart. Each decision is also written to a separate record (`decisions.json` in the data folder); every time the program starts it checks the clips against that record and puts back any decision a clip has lost. Only your own **Back to review** button brings a clip back.

### Kick

Kick works like Twitch, live and finished.

- **Live.** In the channels box type the channel as `kick.com/name` (or paste its address). It can be mixed with the others: `lacy kick.com/name @channelname`. When the streamer goes live the program reads the chat, shows the hype meter, finds moments from chat (and from sound and picture if you chose those) and makes draft clips, and **Clip the last** works too. Clips are cut from the recording Kick makes while the stream runs. If the streamer has recordings switched off, the last few minutes of the live feed are kept instead, exactly as for Twitch. "Start watching by itself" covers Kick channels as well.
- **Finished.** Paste a Kick video link (kick.com/…/videos/… or kick.com/video/…) anywhere a video link goes: **Your own clip**, the **cutting board**, or **Finished stream**. Finished stream downloads the video's chat replay and finds the moments from it, just as for Twitch; sound, picture and AI reading can be added. Kick hands chat out in small pages, so a short or quiet stream takes seconds, but the chat of a very long, very busy one can take a quarter of an hour or more (a 13-hour stream with 85,000 messages took 17 minutes). The bar shows how far it is, and if it is stopped, the next try carries on from where it was. If Kick no longer keeps an old video's chat, the page says so; choose sound, picture or AI reading for that one.

Kick has no sound-only feed, so "chat + audio" on a live Kick channel reads its smallest picture instead (about 150 MB an hour rather than 70).

Kick's chat is read from Kick's own public pages, without an account. Nothing is posted there.

### Templates and the background card

In step 3 open **Template and opening text**.

- **Card:** the video as a rounded card. Behind it: a blurred copy of the video, one colour, two colours blending, or your own image.
- **Post (light or dark):** a round letter, your name and handle, the opening text as the post, and the video under it. Type the name and handle to show.
- **Breaking news:** a red banner with the opening text as the headline.
- **Hook box:** the opening text in a black band on top for the whole clip.
- **Opening text stays for … seconds:** from 2 to 60, for the layouts where it pops in at the start.

Clips already drafted get a new template when you press Make videos, or at once with **Make this video's drafts again** (in a clip's Fine-tune): it drafts every unfinished clip of that video again with the caption style, template and branding chosen now.

### Safe zones, moment mix, music that rises

- **Show where an app covers the video** (above the clip list): pick TikTok, Reels or Shorts and red bands show where that app's own buttons and text sit. Only on the previews, never in the video.
- **What kind of moments** (Finished stream tab): Balanced, Action (loud, fast, shouted) or Dialogue (stories, jokes, what is said). It leans the scores and what AI is asked to look for.
- **Music grows louder over the last … seconds** (Branding): the music stays level, then rises to the end.
- Two more caption movements: each line pops in, and plain lines with no highlight.

### Transcript: find a moment by what was said

The fourth button at the top, **Transcript**.

1. Paste a video link and press **Get the words**. A YouTube video with captions is ready in seconds. Other videos need one run in the Finished stream tab with *Let AI read the whole stream*; their words are kept and can be searched here.
2. Type a word or phrase in the search box. Hindi and Urdu speech is shown in its own letters here, so search in those letters.
3. Click the line where the clip should start, then a later line where it should end. Press **Add this stretch to my picks**. Do this for as many clips as you like.
4. Press **Make these clips**. Each pick becomes a draft, 8 seconds to 5 minutes long.

**No AI set up? Use any free chatbot.** Open that section under your picks. The transcript is cut into parts of about 25 minutes, because a chatbot given a whole stream only reads the start. Press **Copy part 1**, paste it into ChatGPT, Gemini or any other, and copy its answer. When all parts are done, paste the answers into the box and press **Read the answers**: the moments are added to your picks. The program itself sends nothing anywhere.

### Subtitle files

On a clip's card, open **Captions**. **Download .srt** and **Download .vtt** give the clip's captions as a subtitle file, timed from the start of the clip. **Use a subtitle file** replaces the captions with your own file (its times must also count from the start of the clip).

### Live: who is live, and how hot the chat is

While watching, the Live streams tab lists every channel on your list: *not live yet* (checked every half minute and clipped as soon as they start), or a **hype meter** from 0 to 100 that compares the last half minute of chat with that stream's own usual pace. *quiet*, *building*, *hot*, *peak hype*. Peak is a good moment to press **Clip the last**.

Tick **Start watching this list by itself when the program opens** and the program watches your saved list every time it starts, without a press. Add only creators you have a campaign or permission for.

### Layouts: what the clip shows

On a clip's card open **Fine-tune**. **Framing** has six choices, and **Set the face box and the video box** shows each one live:

| Layout | What you get |
|---|---|
| Video only, full height | The game fills the screen |
| Face on top, video below | The usual split |
| Video on top, face below | The same, the other way round |
| Whole picture on a blurred background | Nothing is cut off; the wide picture sits in the middle |
| Video full height, face in a corner | A small framed face over the game |
| Video full height, face in a circle | The same with a round face |

For the last two, choose the corner and how big the small face is. A face in a bottom corner pushes the captions just above it.

### Caption movement and your own caption style

In step 3, under **Caption style**:

- **How the captions move:** the spoken word changes colour (as before), pops in, shows one big word at a time, or the line fills in word by word.
- **Your own caption style:** type a font installed on the PC, pick the size, outline, words on screen and the two colours. **Use this style** applies it; **Save as a profile** keeps it under a name you choose, and it then appears in the Caption style list.

Clips already drafted get the new look when you press **Make videos**.

### Branding: logo, outro line, music

In step 3 open **Branding**.

- **Logo or watermark:** choose an image (a PNG with a clear background looks best), then its corner, size and how see-through it is.
- **Outro line:** a short line such as "Follow for part 2" shown over the last four seconds.
- **Music under the voice:** choose a sound file and its loudness. It fades in and out and repeats if it is shorter than the clip. Use only music you have the right to use; platforms mute or remove clips with other people's songs.

Branding is added whenever a draft or a video is made. Press **Make videos** to add it to clips drafted earlier.

### Faster on YouTube: its own captions

For a YouTube video the program now asks YouTube for the words it has already written down, instead of listening to the video itself.

- A clip's captions are ready in seconds. Before, a two-minute clip could take ten minutes on a laptop without a graphics card.
- **Let AI read the whole stream** no longer needs up to an hour of listening first.
- A YouTube video with **no chat** (an upload, not a stream) can now be searched: moments are found from what is said.
- The card shows **Captions from YouTube**. If a word is wrong, fix it under **Captions** as usual.
- If the captions of some video are poor, open **Language** in step 1 and set *Words of YouTube videos come from* to *Listening on this PC*.

Twitch, live streams and video files are still listened to on this PC, because nobody has written their words down.

### Excited words

"Oh my god", "no way", "run run", "arre yaar", "bhaago", "bachao" and the like, in English, Hindi and Urdu, count as a signal. A clip full of them scores higher and shows an **Excited words** chip. On YouTube videos they also find moments that chat missed.

### Clip the last 30 seconds (live)

While watching live, the Live streams tab has **Clip the last** with a length next to it. Saw something chat did not react to? Press it right after the moment. A draft of that stretch appears at the top of the list in two or three minutes; trim it in Fine-tune. It needs a stream being watched.

### Free local AI (optional)

For a computer with no Claude plan or key. A small AI runs on the PC itself and writes the opening text, title and a rating, so clips do not get placeholder text.

- The program checks by itself whether the PC can run it (about 8 GB of memory, 4 processor threads). A PC that cannot never downloads anything for it; the Claude box says so.
- A PC that can shows **Set up free local AI** in the Claude box. One press installs Ollama and a model, about 3 GB in all; progress shows under Setup check.
- Claude is always used first when it works. The local AI is the fallback, and its text is redone by Claude when Claude is back.
- Claude Code is called once per job and leaves no chats behind in your Claude app. Turning captions into Latin letters now uses the small, fast model (about four times quicker, a fraction of your plan's usage), with almost the same result.
- It is plainer than Claude, cannot look at the picture, and is not used to read whole streams or to turn captions into Latin letters.

### How far is it? The percentage bars

Every job on the page has a bar with a line under it, for example **41% · Step 2 of 4: Downloading the clips · clip 3 of 8 · roughly 3 minutes left**.

- The percentage is for the whole job, not just the step it is on.
- A download counts the video it has really received, so the bar moves while it works.
- "Roughly ... left" is an estimate from the time taken so far. It is least reliable at the start.
- Two steps move only once per clip: writing captions for a short clip, and writing the text. The bar waits there and then jumps.
- **Making the videos** has its own bar under the green button, for example **44% · clip 3 of 3: the clip's title · rendering version B · roughly a minute left**. The work is counted before it starts: one step for each video to render and two for each explainer (the narrator's card, then joining it to the clip). Clips whose videos are already made are not counted. While a video is rendered the bar moves with it; during the narrator's card and the joining it waits and then moves on.
- While videos are being made, a green pill at the top of the page shows the same percentage and "clip 2 of 4", so you see it from anywhere on the page. Click it to jump to the bar and the Stop button.
- A live watch has no bar: it runs until you stop it.

### Face box and video box: choose what the clip shows

Works on every clip: live Twitch, Kick and YouTube drafts, finished streams and your own clips.

1. On the clip's card open **Fine-tune** and press **Set the face box and the video box**.
2. You see the stream as it was, with two boxes: orange **Face** and blue **Video**. Drag a box to move it; drag its round corner to make it bigger or smaller. The boxes keep the shape they are shown in, so what is inside a box is exactly what the viewer sees. The small picture on the right is the result. Use the slider or **Play** to check other moments of the clip.
3. Under **What the clip shows** choose *Face on top, video below* (two boxes) or *Video only* (one tall box).
4. Tick **Use the same boxes for the other clips of this video** to apply them to the other unfinished clips of the same video (handy for parts), and **Remember these boxes for new clips of this streamer** so the next drafts of that streamer start with them.
5. Press **Save and make the draft again**. It takes up to a minute; then the card shows the new draft.

**Back to automatic** forgets your boxes (and the ones remembered for the streamer) and lets the program choose again.

### YouTube streams, and Urdu or Hindi streamers

**YouTube.** In the channels box type the channel's handle with its @, for example `@channelname`, or paste the address of the live stream. Twitch names, Kick channels and YouTube handles can be mixed: `lacy @channelname`. For a live YouTube stream the clips are cut from the stream itself; nothing is recorded in the background. Moments come from chat only on YouTube.

**Urdu or Hindi.** Open **Language** at the top of step 1 and choose:
- what the streamer speaks: English, Urdu or Hindi;
- how captions are written: in Latin letters ("bhai ne kamaal kar diya", needs Claude) or in Urdu or Hindi letters;
- what the opening text and titles are written in: English, Roman Urdu, Hinglish, Urdu or Hindi.

The choice applies to clips made from then on. Set it back to English before watching an English streamer.

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

   Open the address it prints (http://127.0.0.1:8765/) in your browser. For each clip: watch it, fix the hook text if you want, press **Approve** or **Reject**. Under **Versions to make** tick **Make an explainer version** on a clip if you want the narrated version of it too. Press Ctrl+C in that terminal when finished.
3. **Finish.** This renders everything you approved and writes the upload text:

   ```powershell
   uv run clipper finish
   ```

   For a campaign, add its brief so each clip is checked against the rules:

   ```powershell
   uv run clipper finish --brief briefs/lacy-gta-marathon.yaml
   ```
   Every approved clip comes out as two videos for A/B testing: `..._A.mp4` and `..._B.mp4`. They are the same moment with different opening text and titles. Post A and B on different accounts or platforms and see which gets more views; never send both to the same campaign. Clips you ticked also get `..._explained_A.mp4` and `..._explained_B.mp4` (different narration line and animation). To get one version only, set `ab_versions: false` in `config.yaml`.
4. **Upload.** Open `D:\clipper-data\ready\` and today's date folder. Each `.mp4` is named after the clip's title and has a `.txt` with the same name beside it, with the title, description and hashtags for YouTube, TikTok, Instagram and Facebook. Upload the video, paste the text. For a campaign, submit the post link on the campaign site straight away.
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
