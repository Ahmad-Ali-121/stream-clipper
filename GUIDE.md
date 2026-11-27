# How to use the clipper

A step-by-step guide in plain words. Technical detail is in [README.md](README.md).

## What it does

You give it a Twitch stream. It finds the moments where chat went wild (and, if you choose, where the sound or the picture suddenly jumps), cuts just those few seconds, adds captions, and shows you each clip. You approve the good ones. It then gives you the finished video files and the text to paste when you upload them. You upload by hand.

## Part 1: Install on a computer (once)

**The easy way: the installer.** Download **Stream-Clipper-Setup.exe** from https://github.com/Ahmad-Ali-121/stream-clipper/releases/latest/download/Stream-Clipper-Setup.exe (or get it from whoever gave you the program) and double-click it. You do not need Python, Git or anything else first, and it does not ask for administrator rights (one exception, described below: a computer that lacks Microsoft's Visual C++ runtime).

1. It shows two folders: where the program goes (about 2 GB with Python and its parts), and where your clips, downloads and models go (it suggests the drive with the most free space; 5 GB or more). Change them if you want, then press **Install**. Everything it installs stays inside those two folders, Python included.
2. It works through five steps and ticks each one: fetch **uv** (the small tool that installs Python for this program only), make sure **Git** is there (for the Update button), fetch the program, install Python and the program's parts (the longest step), download the video tools and models (about 300 MB). It takes 5 to 20 minutes and needs the internet.
3. At the end a **Stream Clipper** shortcut is on your desktop. Leave **Start Stream Clipper now** ticked and press **Finish**.

If a step fails (most often the internet dropped), the window says so in plain words and has a **Try again** button; it carries on where it stopped. The full log is in `install-log.txt` in the program folder. **Stop** (or closing the window) asks first, then really stops: whatever was downloading is ended, and running the installer again later carries on. Running the installer again over a copy that is already installed keeps that copy's clips folder (it shows it and uses it again) and its settings. If Windows does not let a shortcut be put on the desktop, the last screen says so and names the file to start the program with (`Stream-Clipper.cmd` in the program's folder).

Windows may show a blue box, "Windows protected your PC", the first time: the installer is not signed with a paid certificate. Press **More info**, then **Run anyway**. If Windows instead says **Smart App Control blocked an app**, there is no such button: see that line in the problems table near the end of this guide.

A new or freshly reset computer often lacks the **Microsoft Visual C++ runtime**, a free part of Windows that captions need. The installer notices and adds it by itself during the last step: it fetches Microsoft's own installer for it (about 25 MB, from Microsoft's address, and it is run only after Windows confirms that Microsoft signed the file), and Windows then asks **"Do you want to allow this app to make changes to your device?"** for Microsoft Visual C++. Press **Yes**. The question can be hiding behind the installer window or blinking on the taskbar. This is the only thing installed outside the two folders and the only time permission is asked. If you press **No**, the install still finishes but captions cannot work yet; the **Set up** page's Video tools step then says the runtime is missing and its **Install the video tools** button asks again, and so does **Repair** under Settings, Setup check. On a computer where you cannot give that permission (no administrator password), ask whoever looks after the computer to install https://aka.ms/vs/17/release/vc_redist.x64.exe once.

**Then: the Set up page.** The program never opens this page by itself and never downloads anything large without your own press. It opens on **New clips** (or where you were last). While something the program needs is still missing, the **Set up** entry in the menu on the left turns into a warning: amber, with a warning triangle and how many steps are left, for example **1 to do**. Click it. The page takes you through what is left, one step per screen:

1. **Video tools** (done by the installer; here in case something is missing).
2. **Captions:** downloads the speech model (about 1.6 GB) and tries it on this computer.
3. **Claude, for titles and ratings** (optional): **Install Claude Code**, then **Sign in to Claude**.
4. **ChatGPT, for thumbnails** (optional): **Install Node.js first** if the PC has none, then **Install the ChatGPT tool**, then **Sign in to ChatGPT**.
5. **Free AI on this computer** (optional, about 3 GB).

Each step says what it is for and how big its download is, and has one button. **Skip this** leaves an optional step out; **Next** goes on; **Back** goes back. A sign-in button opens that company's own window and then your browser: you sign in there, with your own account. Stream Clipper never sees your password; it only asks the tool afterwards whether you are signed in, and the step turns green by itself. **Check again** (top right) asks every tool again at once and shows the new answer. If a step says *Installed, but its sign-in could not be checked*, the tool did not answer in time: press **Check again**, and if it stays the same press the **Sign in** button. An install that prints nothing for half an hour is ended by the program and that is said in the step, so a hidden question can never block the page. The speech model (about 1.6 GB) is only downloaded by the **Download and try it** button of the Captions step; the **Update** button and the daily check never fetch it by themselves. On the last screen press **Start using Stream Clipper**. The page stays in the menu as **Set up**, so anything skipped can be done later.

**The other way, for people who use a terminal.** Do these in order.

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

   It downloads FFmpeg, TwitchDownloaderCLI, the face model and the voice model, installs the Python packages, and runs the tests. If Windows lacks Microsoft's Visual C++ runtime it also fetches Microsoft's installer for it, and Windows asks you to allow it (press Yes). On a slow connection this takes a while; if it stops, run it again and it continues.
5. **Check the hardware it will use:**

   ```powershell
   uv run clipper hardware
   ```

   It prints the video encoder (NVIDIA, Intel or CPU) and whether speech runs on a GPU. Nothing to configure: it uses the fastest thing that works.

The speech model (1.6 GB) is downloaded when you press **Download and try it** on the Set up page's Captions step. Captions is a needed step (Next stays grey until it is done). If you leave the page without doing it, nothing is downloaded behind your back: when you then press **Find the best moments**, **Start watching** or make your own clip, the program does not start and says *Captions are not set up on this computer yet*, with where to go. (Only someone who types the commands in a terminal gets the model downloaded by itself on the first clip, with a line that says so.)

**On an NVIDIA laptop**, speech on the GPU also needs NVIDIA's CUDA 12 and cuDNN 9 libraries installed. Without them it still works, on the CPU. This path has not been tested yet.

## Part 2: Optional paid keys

The clipper works with none of these. Create a file named `.env` in the program's folder (copy `.env.example`; with the installer that folder is `app` inside the program folder you chose, normally `%LOCALAPPDATA%\StreamClipper\app`) and add only the lines you have keys for.

| Key | What it adds | Cost | Where to get it |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Claude writes the hook, title, description and narration, and marks filler clips | About $0.02 per clip with the default model, far less with `claude-haiku-5-5` (set `llm.model` in `config.yaml`). Minimum top-up is $5 | console.anthropic.com |
| `ELEVENLABS_API_KEY` | A more natural narrator voice. After putting the key in `.env`, choose ElevenLabs on the Settings page under **Narrator voice** | Starter plan $6 a month | elevenlabs.io |
| `GROQ_API_KEY` | Faster transcription (set `transcribe.backend: groq`) | About $0.04 per hour of audio | console.groq.com |

**Claude without an API key.** If you pay for a Claude plan (Pro or Max), the clipper can use it instead of an API key:

1. Open **Set up** in the menu, go to the **Claude** step, press **Install Claude Code**, then **Sign in to Claude** and sign in with your own account in the window and browser that open. (By hand instead: install Claude Code from https://claude.com/claude-code, open a terminal and run `claude auth login`.)
2. Open the page and go to **Settings**. Under **AI for titles and ratings** it finds it by itself: it tries each connection for real and shows a green dot and "Works", or a red dot with the reason. With "Automatic" it uses your plan first and, if you allow it, your API key when the plan fails (it tells you when that happens). You can also fix the choice to one of the two.

Nothing is billed per use, but each request counts towards your plan's usage limits, the same limits as your normal Claude chats. Reading a whole stream with a large model uses a lot of them. Every person does this on their own PC with their own account; the clipper only runs the `claude` command and never sees your sign-in. The same box chooses the model for clip text and for reading streams.

Every paid call is written to `D:\clipper-data\usage.jsonl` with its cost, so you can see what a day cost.

## Part 3: Daily use

All commands are typed in a terminal opened in the program's folder (the one that holds `Stream-Clipper.cmd`).

### The easy way: everything from the page

```powershell
uv run clipper review
```

Or just double-click **Stream Clipper** on the desktop (setup puts it there): it starts the program and opens the page.

Open http://127.0.0.1:8765/ in your browser. The page has a menu on the left and shows one page at a time:

- **New clips:** make clips from a finished stream, a video link or a file on this PC. Four tabs: **Find the best moments** (paste a link, press the button, the program picks the moments), **Pick the part myself** (you give the start and the end), **Cutting board** (cut a downloaded part into pieces) and **Search what was said** (find a moment by its words). Under them, **Your videos** lists the videos you have clips of, newest first; click one to see only that video's clips on My clips (a line under the filters says so, and **Clear filters** shows all clips again).
- **Live channels:** type channel names, pick A, B, C or D, press **Start watching**. **Stop** ends it. **Clip the last** makes a clip of what just happened.
- **My clips:** every clip as a tile with its video, score, length and title. This is where you review, edit and approve, and where **Make videos** is. The number beside it in the menu is how many clips wait for review. **Select several** lets you tick many clips and handle them together (see *Several clips at once* below).
- **Look and branding:** the caption style, the template and opening text, your logo, closing text and music, and the thumbnails. What you choose here is used for every clip made from now on. A **Preview** on the right of the page shows one of your own clips with these choices: three real frames (**Start** with the opening text, **Middle** with the captions, **End** with the closing text), made the same way as the videos, so you do not have to make a video to see the result. It follows every change a few seconds later; **Try another clip** shows the same choices on another of your newest clips. Music cannot be shown. With no clip made yet, the preview says so.
- **Posted and earnings:** what you posted, its views and what it earned, and the campaign choice.
- **Set up:** the step-by-step guide that installs what the program needs and signs you in to Claude and ChatGPT. It never opens by itself. While a needed step (Video tools, Captions) is missing, its menu entry is an amber warning with the number of steps left; with only optional steps left it shows their number quietly.
- **Settings:** the **Setup check**, the AI connection, the **Narrator voice** for explainer videos, the language, and updates.
- **Ask for help:** the help chat.

At the bottom of the menu are the status lines (setup, watching, how many clips) and the **Activity log**.

**Where is it? A quick map.**

| You want to | Go to |
|---|---|
| Make clips from a finished stream or a video | **New clips**, tab **Find the best moments** |
| Cut a part you choose yourself | **New clips**, tab **Pick the part myself** (then the **Cutting board** tab) |
| Find a moment by what was said | **New clips**, tab **Search what was said** |
| Watch channels while they are live | **Live channels** |
| Watch, approve, reject or edit clips | **My clips** |
| Make the finished videos | **My clips**, button **Make videos** (top right) |
| Change a clip's text, captions, start and end, framing, thumbnail or versions | **My clips**, press **Edit** on the clip, then the tab with that name |
| Caption style, the caption font, where captions sit, template, logo, music, thumbnails for every clip, and a preview of how a clip looks with them | **Look and branding** |
| Approve, reject, change or delete many clips in one go | **My clips**, **Select several** |
| The voice of the narrator in explainer videos, from this computer or from ElevenLabs | **Settings**, **Narrator voice** |
| Whether one clip gets a version B, and what its thumbnail must leave out | The clip's **Edit**, tabs **Versions** and **Thumbnail** |
| Log a post, see views and earnings, choose a campaign | **Posted and earnings** (log a post from the clip's **Posted** tab) |
| Install something that is missing, or sign in to Claude or ChatGPT | **Set up** |
| Setup check, repair, update, the AI connection, the language | **Settings** |
| See what the program is doing, or read an error in full | **Activity log**, at the bottom of the menu |
| Stop something | The **Stop** button beside the button that started it; it is only there while that job runs |

**Setup check** (on the Settings page) shows whether this computer is ready: a green dot per item, or what is wrong and what to do. **Repair** downloads anything missing; if the Microsoft Visual C++ runtime is missing, it fetches Microsoft's installer for it and Windows asks you to allow it. **Update** appears when a newer version exists and installs it.

**Where the files are.** In this guide `D:\clipper-data` stands for your data folder: the one you chose in the installer (it suggests `clipper-data` on the drive with the most free space, or `data` inside the program folder on a PC with one drive). **Open the videos folder** on My clips opens its `ready` folder. The program's own folder is the one that holds `Stream-Clipper.cmd`; with the installer that is `app` inside the program folder you chose.

**Reviewing on My clips.** Each tile has **Approve**, a red cross to **Reject**, and **Edit**. Click the video to play it. The tabs above the tiles switch between the lists: To review, Approved, Pending, Done, Rejected and All. **Sort and filter** opens the order, streamer, found-by and date filters. New drafts appear by themselves. When you have approved what you want, press **Make videos** at the top right (it shows how many clips are approved, for example *Make videos (3)*): it makes the videos for every approved clip. Each tile also says in a few words why the clip is there (a chat spike, a loud moment, chosen by you, AI pick); the full details are in its editor. **Stop** appears next to it while videos are being made (see below), and **Open the videos folder** shows the finished files.

**Editing a clip.** Press **Edit** on a tile (or its title, or Enter). The clip opens over the page with its video on the left and tabs on the right:
- **Text:** the opening text on the video, the title, description and hashtags to paste when you post. **Write text with AI** is under the tabs.
- **Versions:** the normal video, the explainer version, and what the narrator says.
- **Captions:** correct a word or empty its box to remove it, press **Save captions**. A subtitle file can replace them.
- **Start, end and picture:** drag the two handles over the sound wave to set where it starts and ends, choose the framing, **Set the face box and the video box**, the ad label, and **Make this video's drafts again**. **Need more of the stream?** has three buttons (**Add 15 s before**, **Add 15 s after**, **Add 30 s on both sides**): they download more of the stream around the clip, so you can drag its start or end further out. The clip itself does not move and its captions are kept; the added seconds get their captions too. It takes a minute or two; while it runs the buttons are switched off and say what is happening, and a second press is refused. The draft and any finished video stay as they are, because the clip's start and end did not move. It cannot be done for a clip from a live stream that was not being recorded, and a clip keeps at most ten minutes of the stream.
- **Picture** (under the player): **Automatic** lets the program frame this clip (boxes you asked it to remember for the streamer are kept); **Whole picture** shows the whole stream picture in the middle with a blurred, moving copy of it above and below, so nothing is cut off; **Choose boxes…** lets you draw what is shown. One click, and the draft is made again.
- **Thumbnail:** make a cover picture from the clip, or let ChatGPT draw it.
- **Posted:** (on a finished clip) log where you posted it.

At the top of the editor are the buttons that decide about the clip, and only the ones that fit where the clip is now: a clip to review has **Approve** and **Reject**; an approved clip has **Make this clip's videos now**, **Move to Pending**, **Back to review** and **Reject**; a finished clip has **Make its videos again**, **Move to Pending**, **Back to review** and **Reject**; a rejected clip has **Approve** and **Back to review**. Under the tabs are **Save changes**, **Write text with AI** and **Delete**. **Clips** at the top left (or Esc) goes back to the list; what you typed is saved. Approving or rejecting also goes back to the list, and so does **Make this clip's videos now** (the progress bar is at the top of My clips). A problem that comes up while a clip is open shows in a red box at the top of the editor. Esc closes one thing at a time: first the help chat or the Activity log if one is open, then the editor.

Under the video in the editor, small buttons switch between its versions (**A**, **B**, **Explained A**, **Explained B**, **Original**, which is the video as it was downloaded) and, when it has one, its **Thumbnail**. **Make B preview** makes version B now so you can compare it with version A.

**Keyboard on My clips:** J and K move between clips, Space plays, A approves, R rejects, Enter opens the clip, Esc closes it. A key held down decides one clip only.

The **campaign** is chosen on the Posted and earnings page: pick one and every clip shows whether it meets that campaign's rules, and what to fix. Campaign rule files live in the `briefs` folder; copy `example.yaml` and fill it in from the campaign's own page.

**Tracker.** After you post a clip by hand, open the clip with **Edit**, go to its **Posted** tab, and paste the link. The **Posted and earnings** page then keeps your posts, views, payouts and rejections together, adds them up per campaign, and warns you before the same clip goes to the same campaign twice.

**Make videos only makes what is under Approved.** A clip under **Done** is finished and **Make videos** leaves it alone, even if you have since moved its file out of the videos folder, changed the caption look or the opening effects, or switched A and B versions on. To make one finished clip once more (its file is gone, or you want it with the new look), press **Make its videos again** at the top of that clip's editor. A clip whose making was stopped or failed half-way is not Done: it stays under Approved and **Make videos** completes it.

Once a clip's videos are made it moves to the **Done** list. If you change it afterwards, it goes back to Approved until you press **Make videos** again. That includes its title, description and hashtags: the finished file is named after the title and its text file holds all of them, so **Make videos** copies the video again under the new name (nothing is rendered again, it takes a moment).

#### Which versions a clip gets

Open a clip with **Edit** and go to its **Versions** tab. It has two tick boxes:

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

The text file with the upload text has the same name as its video. A Done clip also lists its finished files in its editor (**Finished files in the ready folder**), and **Open the videos folder** beside **Make videos** opens today's folder on this computer.

#### Making one clip, and stopping

- **Make this clip's videos now** (on an approved clip) makes that one clip and leaves the other approved clips for later.
- If you reject a clip or move it to Pending while the videos are being made, and its turn has not come yet, it is not made.
- **Stop** (next to **Make videos**) asks what you want, with three buttons: **Cancel** keeps going; **Stop after this video** finishes the clip being made and then stops; **Stop now** ends it in the middle, and that clip is not made. Videos already made are always kept. The clips that were not made stay under Approved, and the Activity log names them. Press **Make videos** again to carry on.

#### Thumbnails (optional)

A thumbnail is a cover picture for a clip: one 1080 x 1920 picture, for YouTube Shorts, Instagram Reels and TikTok. Nothing needs one; it is an extra and it is **Off** to begin with.

**For every clip, by itself.** On the **Look and branding** page, **Thumbnail for each clip** has three choices:

- **Off**: no thumbnail is made by itself.
- **From the clip, with the opening text (free)**: no AI. The program takes the sharpest frame of the clip and puts the opening text on it in big letters. A few seconds per clip.
- **Drawn by ChatGPT (uses your ChatGPT plan)**: ChatGPT is shown three frames of the clip, its title, the opening text and what is said, decides itself what the picture should be, and draws it. You write nothing. Usually one to three minutes per clip, sometimes up to six. The program takes the picture as soon as it has been saved and has not changed for half a minute, even if Codex itself does not close; if no picture has come after twelve minutes it gives up with a red message, and you can press again.

With one of the two chosen, the thumbnail is made when you press **Make videos**, as one more step for each clip whose videos are being made. It is saved in the ready folder beside the video, with the same name and `.jpg` at the end. Only a clip whose videos are made for the first time gets one this way. A clip that was finished before (even if it is made again for a new title, a new caption look or a campaign) is left alone; make its thumbnail with the buttons on the clip. That way ChatGPT never draws for a pile of old clips by surprise.

**For one clip, by hand.** Open the clip with **Edit** and go to its **Thumbnail** tab:

- **Make it from the clip (free)** and **Let ChatGPT make it** do the same two things for this one clip.
- **Or describe the picture yourself**: type what you want in your own words and press **Make it from my description**. ChatGPT draws that; it still sees the clip's frames, so "the streamer" or "the car" mean the ones in the video.
- Each press makes a new picture and replaces the old one. **Download** saves it; **Remove thumbnail** takes it off the clip.
- **What ChatGPT thumbnails look like** is a fold-out under the Thumbnail choice on the Look and branding page, with two things:
  - **The streamer's face, big and excited** (ticked at the start): when the streamer is visible in the clip (a face camera, or filmed directly), their face is the main thing in the picture, with a shocked or excited look that fits the moment. If no real face is in the clip, none is invented. It is used when ChatGPT decides the picture itself, not when you describe the picture yourself.
  - **Things I do not want in thumbnails**: write them in your own words (for example: no guns, no blood, no half-naked people, no red arrows) and press **Save this list**. Every ChatGPT thumbnail made from then on leaves them out, also the ones made from your own description. The list is sent to OpenAI with the request. It cannot change the free thumbnail, which is a real frame of the clip. Thumbnails that were made before are not changed; make them again with the button on the clip.
  - **For one clip only:** in the clip's **Edit**, **Thumbnail** tab, **Not wanted in this clip's thumbnail** adds things for that clip alone. ChatGPT gets your general list and then this one. It is saved with the clip.
- A thumbnail you made by hand is kept: **Make videos** does not replace it.
- One at a time per clip: a second press while one is being made is refused with a message, so ChatGPT is never asked twice for the same picture.
- What you typed as a description stays in the box if ChatGPT fails, so you can press again.

**Setting up ChatGPT (once).** It works through Codex, OpenAI's command-line tool, signed in with your own ChatGPT account, the same way Claude works through Claude Code. Open **Set up** in the menu and go to the **ChatGPT** step: it installs Node.js if the PC has none, then the tool, then **Sign in to ChatGPT** opens the sign-in. (By hand instead: in a terminal run `npm install -g @openai/codex`, then `codex login`.) The line under the choice on the Look and branding page says **ChatGPT: Ready** when it is linked, or what is missing. Pictures need a paid ChatGPT plan; they use the plan's allowance several times faster than text. Frames of the clip, its title and its words are sent to OpenAI when ChatGPT is used. Each use is written to `usage.jsonl` in the data folder. The program never reads Codex's sign-in files.

**If a thumbnail cannot be made** (not signed in, the plan's limit is reached, no internet), the clip's videos are still made. The reason is shown in red in the Activity log and in the clip's editor under Thumbnail (the tab gets a red dot), and it is not tried again by itself: press the button there when the problem is solved.

**Where to use it.** Instagram Reels: choose it as the cover when you post. YouTube Shorts: in YouTube Studio on a computer, under the Short's thumbnail (not every channel has this yet). TikTok: you can only pick a frame of the video as the cover.

#### Pending: take a clip out of Approved without losing it

**Move to Pending** on an approved clip takes it out of Approved, so **Make videos** does not make it, and puts it in the **Pending** list instead of mixing it back into To review. In the Approved list, **Move all ... to Pending** does that for every approved clip at once (clips that are already Done are not touched). In Pending, **Approve** sends a clip back to Approved, **Reject** rejects it, and **Back to review** returns it to To review. Nothing is deleted by any of these.

**Delete** on a clip removes it and its files for good; in the Rejected list, **Delete all rejected** clears them all. Videos you already made stay in the ready folder. Red messages can be closed with the × in their corner.

The **Activity log** at the bottom of the menu shows what the program is doing, line by line, in four parts: watching live channels, finding moments, making videos, and setup. Problems are shown in red, at the top of the page and on the clip they belong to.

Keep the terminal open while you use the page. The steps below do the same things from the terminal.

### Your own clip: a link or a recording, with the times you choose

Use this when you already know which part you want, or the video has no chat. Open **New clips** and choose the **Pick the part myself** tab.

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
2. When it is ready the page opens the **Cutting board** tab of the New clips page. You see the video, and under it the sound wave of the whole part.
3. Play the video, or click the sound wave to jump. Where one clip should end and the next begin, press **Cut here** (or the **C** key). An orange marker appears; drag it to move the cut. Click a marker and press **Remove the selected cut** (or **Delete**) to take it away.
4. Quick ways: type a number beside *Split into ... equal parts* or *or cut every ... seconds* and press the **Do it** button next to it. You can move the markers afterwards.
5. The list under the timeline shows every piece: where it is in the video and how long it is. Untick **make this one** for a piece you do not want (an intro, a break). The heading says how many clips will be made. A piece shorter than 8 seconds or longer than 5 minutes is marked in red and must be fixed first.
6. Press **Make these clips**. They are cut out of the part that is already on this PC, so nothing is downloaded again. Each gets captions and text as usual and appears under **My clips**, in order.

Your cuts are remembered if you leave the page. The downloaded part stays until you press **Delete this download**, so you can cut it again differently.

Keys on the cutting board: Space plays or pauses, the arrow keys move one second (five with Shift), comma and full stop move a tenth of a second.

The drafts appear at the top of My clips, in order. Review, approve and make the videos as usual; the titles get "(Part 1)", "(Part 2)" added.

A part can be between 8 seconds and 5 minutes long. A video file can also be searched automatically: under **Find the best moments** paste its path and choose sound, picture or AI reading (a file has no chat).

### Ask: the built-in help

**Ask for help** in the menu on the left opens a chat (bottom left, beside the menu). Type a question about the program, in English or Roman Urdu: "How do I clip a live stream?", "What does the score mean?", "Why is it not working?".

- It answers in simple words, with numbered steps when there is something to do.
- **Show me** beside a step takes you there: it opens the right page, tab or clip, scrolls to the button, highlights it and darkens everything else. **Show me step by step** does this for every step in turn; press **Next**, or **Done** (or Esc) to stop. You can press the highlighted button itself.
- It knows what the program is doing right now, so it can explain an error that is on screen.
- It only answers questions about this program.

**Telling it to do something.** You can also give it an order instead of a question, in English or Roman Urdu: "start watching lacy and kick.com/name with chat only", "stop watching", "set the template to No template", "make a clip from this link from 3:53:30 to 3:54:00", "watching band kar do". It answers with the steps and a **Do it for me** button; press it and it does the steps on the page while you watch: each thing is highlighted, the text is typed, the choice is made, the button is pressed, and the step gets a tick in the chat. **Stop** (or Esc) ends it at once; steps already done stay done. When it is finished it says how many steps it did. If something could not be done (the page refused a press, a value did not fit, the thing is not there) it says which step and why, and does nothing after it. If a step has to wait for something first, such as a download, it stops before that step and leaves the rest to you.

- If you only asked how to do something, it does nothing by itself; the answer has a **Do it for me** button. When you told it to do something, the answer begins *Nothing has been done yet* until you press that button. As installed it never does anything by itself: every answer that has steps comes with the **Do it for me** button, so you see the steps first and one press does them. This is so that text written by strangers, such as a stream's title, can never make it do something. If you would rather have it act at once, set `help_act_at_once: true` under `llm` in config.yaml; it then acts at once only when the steps follow from your own words (what it types must be something you wrote in the chat, and a button it presses must be one you named), and shows the button otherwise. The same if you closed the chat before the answer came. **New chat** drops an answer that is still on its way.
- While the chat is open, the keyboard shortcuts for clips (J, K, A, R) are switched off, so words you type can never approve or reject a clip.
- It can use anything that is on the page: boxes, lists, tick boxes, sliders, tabs and buttons. A new feature can be used the day it is added.
- If you left out something it needs (which channel, which link, what time), it asks instead of guessing.
- **Some presses always stay yours.** Approve, Reject, Delete, Back to review, Move to Pending, the Thumbnail buttons, the **Thumbnail for each clip** choice on the Look and branding page, the three buttons of the "Stop making videos?" window, the tick boxes of a clip's Versions tab and everything else on a clip's tile and in its editor, the install and sign-in buttons of the Set up page, moving all approved clips to Pending, the box editor window, deleting, removing or clearing anything, taking a logo or music off, cutting the cutting board into equal parts (it replaces your cuts), which Claude connection is used, setting up the free local AI, and repairing, restarting or updating the program are never pressed for you. It points at the button and waits for you. That is on purpose: you decide about every clip.
- It cannot post or upload anything; the program has no way to do that.

It needs Claude (your Claude Code plan or an API key). Without Claude it still answers, by showing the parts of this guide that match your question, but it cannot do things for you. Each question counts towards your Claude plan like any other use.

### Order, filters and the New tag

On My clips, press **Sort and filter** above the tiles (the search box beside it is always there):

- **Order:** newest first (the default, so a clip just made is at the top), best score first, or oldest first.
- **Streamer**, **Found by** (chat, loud moment, fast action, excited words, AI, chosen by you) and **Made** (last hour, 24 hours, 7 days) narrow the list. The search box beside **Sort and filter** (it says *Search titles and words*) looks in the words, the opening text and the titles. **Clear filters** shows everything again.
- Each tile says when it was made ("made 12 min ago", "made today 20:57"). A clip made in the last hour that you have not reviewed carries a **NEW** tag.

A clip you approved, rejected or deleted stays that way, through every update and restart. Each decision is also written to a separate record (`decisions.json` in the data folder); every time the program starts it checks the clips against that record and puts back any decision a clip has lost. Only your own **Back to review** button brings a clip back.

### Kick

Kick works like Twitch, live and finished.

- **Live.** In the channels box type the channel as `kick.com/name` (or paste its address). It can be mixed with the others: `lacy kick.com/name @channelname`. When the streamer goes live the program reads the chat, shows the hype meter, finds moments from chat (and from sound and picture if you chose those) and makes draft clips, and **Clip the last** works too. Clips are cut from the recording Kick makes while the stream runs. If the streamer has recordings switched off, the last few minutes of the live feed are kept instead, exactly as for Twitch. "Start watching by itself" covers Kick channels as well.
- **Finished.** Paste a Kick video link (kick.com/…/videos/… or kick.com/video/…) anywhere a video link goes: **Pick the part myself** (which also feeds the Cutting board) or **Find the best moments**. Find the best moments downloads the video's chat replay and finds the moments from it, just as for Twitch; sound, picture and AI reading can be added. Kick hands chat out in small pages, so a short or quiet stream takes seconds, but the chat of a very long, very busy one can take a quarter of an hour or more (a 13-hour stream with 85,000 messages took 17 minutes). The bar shows how far it is, and if it is stopped, the next try carries on from where it was. If Kick no longer keeps an old video's chat, the page says so; choose sound, picture or AI reading for that one.

Kick has no sound-only feed, so **Chat + sound** on a live Kick channel reads its smallest picture instead (about 150 MB an hour rather than 70).

Kick's chat is read from Kick's own public pages, without an account. Nothing is posted there.

### Templates and the background card

Open the **Look and branding** page, **Template and opening**.

- **Card:** the video as a rounded card. Behind it: a blurred copy of the video, one colour, two colours blending, or your own image.
- **Post (light or dark):** a round letter, your name and handle, the opening text as the post, and the video under it. Type the name and handle to show.
- **Breaking news:** a red banner with the opening text as the headline.
- **Text box:** the opening text in a black band on top for the whole clip.
- **Opening text stays for … seconds:** from 2 to 60, for the layouts where it pops in at the start.

Clips already drafted get a new template when you press Make videos, or at once with **Make this video's drafts again** (in a clip's editor, under Start, end and picture): it drafts every unfinished clip of that video again with the caption style, template and branding chosen now.

### Safe zones, moment mix, music that rises

- **Show where an app covers the video** (under **Sort and filter** on My clips): pick TikTok, Reels or Shorts and red bands show where that app's own buttons and text sit. Only on the previews, never in the video.
- **What kind of moments** (under Find the best moments): Balanced, Action (loud, fast, shouted) or Dialogue (stories, jokes, what is said). It leans the scores and what AI is asked to look for.
- **Music grows louder over the last … seconds** (Logo, closing text and music): the music stays level, then rises to the end.
- Two more caption movements: each line pops in, and plain lines with no highlight.

### Search what was said: find a moment by its words

Open **New clips** and choose the fourth tab, **Search what was said**.

1. Paste a video link and press **Get the words**. A YouTube video with captions is ready in seconds. Other videos need one run under Find the best moments with *Let AI read the whole stream*; their words are kept and can be searched here.
2. Type a word or phrase in the search box. Hindi and Urdu speech is shown in its own letters here, so search in those letters.
3. Click the line where the clip should start, then a later line where it should end. Press **Add this part to my picks**. Do this for as many clips as you like.
4. Press **Make these clips**. Each pick becomes a draft, 8 seconds to 5 minutes long.

**No AI set up? Use any free chatbot.** Open that section under your picks. The transcript is cut into parts of about 25 minutes, because a chatbot given a whole stream only reads the start. Press **Copy part 1**, paste it into ChatGPT, Gemini or any other, and copy its answer. When all parts are done, paste the answers into the box and press **Read the answers**: the moments are added to your picks. The program itself sends nothing anywhere.

### Subtitle files

Open a clip with **Edit** and go to its **Captions** tab. **Download .srt** and **Download .vtt** give the clip's captions as a subtitle file, timed from the start of the clip. **Use a subtitle file…** replaces the captions with your own file (its times must also count from the start of the clip).

### Live: who is live, and how hot the chat is

While watching, the Live channels page lists every channel on your list: *not live yet* (checked every half minute and clipped as soon as they start), or a **hype meter** from 0 to 100 that compares the last half minute of chat with that stream's own usual pace. *quiet*, *getting busy*, *very busy*, *at its busiest*. When it is at its busiest is a good moment to press **Clip the last**.

Tick **Start watching this list by itself when the program opens** and the program watches your saved list every time it starts, without a press. Add only creators you have a campaign or permission for.

### Layouts: what the clip shows

Open the clip with **Edit** and go to **Start, end and picture**. **What the clip shows** has seven choices (Automatic, which lets the program decide, and the six below), and **Set the face box and the video box** shows each one live. The two commonest are one click under the player, in the **Picture** row: **Automatic** and **Whole picture**. In the box editor the two boxes always keep the shape the finished video shows them in: you change a box's size by its corner and its height follows its width, so a box can never be stretched out of shape:

| Layout | What you get |
|---|---|
| Video only, full height | The game fills the screen |
| Face on top, video below | The usual split |
| Video on top, face below | The same, the other way round |
| Whole picture on a blurred background | Nothing is cut off; the wide picture sits in the middle |
| Video full height, face in a corner | A small framed face over the game |
| Video full height, face in a circle | The same with a round face |

For the last two, choose the corner and how big the small face is. A face in a bottom corner pushes the captions just above it.

### Several clips at once

On **My clips**, press **Select several**. Every tile gets a tick box; press a clip (anywhere on its picture) to tick it, or **Select all shown** to tick every clip in the list you are looking at. A bar shows how many are ticked and what you can do with them:

- **Approve**, **Reject**, **Back to review**.
- **More to do with them…**, then **Do it**: move to Pending; **Picture: show the whole picture** or **Picture: automatic**; make or do not make **version B**; make or do not make the **explainer version**; **Delete forever**.

Deleting, and changing the picture (each draft is made again, up to a minute per clip), ask first. While it works the bar counts the clips, and **Stop after this one** ends it early. At the end it says how many were done and, if some could not be, the first reason. Clips that leave the list (approved, rejected, deleted) are unticked. **Done** leaves selecting; nothing else changes. Everything a single clip's own buttons refuse is refused here too, and the help assistant may not press these buttons for you.

### Caption movement and your own caption style

On the **Look and branding** page, under **Captions**:

- **How the captions move:** the spoken word changes colour (as before), pops in, shows one big word at a time, or the line fills in word by word.
- **Captions higher or lower** and **Captions left or right** (two sliders under *How the captions move*): move the captions anywhere on the screen. The first says how far up from the bottom they sit, the second how far from the middle to the left or right. The Preview on the right shows the result a few seconds later. **Back to the usual place** puts them a little below the middle, centred. A small face in a bottom corner, and some templates, still move the captions out of their own way. Drafts already made get the new position when you press Make videos.
- **Fonts:** open **Your own caption style**. The **Font** list holds 20 caption fonts that come with the program, so they work on every computer without installing anything: heavy clean ones (Poppins ExtraBold, Be Vietnam Pro ExtraBold, Kanit ExtraBold, Prompt ExtraBold, Fira Sans Black, Lato Black, Archivo Black), tall narrow ones (Bebas Neue, Anton, Fjalla One, Barlow Condensed ExtraBold), round friendly ones (Paytone One, Lilita One, Titan One), comic ones (Bangers, Luckiest Guy, Chewy), a hand-written one (Permanent Marker) and two showy ones (Russo One, Passion One Bold). Choosing one applies it at once. They are free fonts from Google Fonts; each one's licence is in the program's `src/clipper/fonts` folder. Below the list you can still type the name of any font installed on this computer.
- **Your own caption style:** choose or type a font, pick the size, outline thickness, words shown at once and the two colours. **Use this style** applies it; **Save this style…** keeps it under a name you choose, and it then appears in the Caption style list.

Clips already drafted get the new look when you press **Make videos**.

### Logo, closing text and music

Open the **Look and branding** page, **Logo, closing text and music**.

- **Your logo:** choose an image (a PNG with a clear background looks best), then its corner, size and how solid it is (the **How solid** slider: to the left you can see the video through the logo).
- **Closing text:** a short line such as "Follow for part 2" shown over the last four seconds.
- **Background music:** choose a sound file and its loudness. It fades in and out and repeats if it is shorter than the clip. Use only music you have the right to use; platforms mute or remove clips with other people's songs.

Your logo, closing text and music are added whenever a draft or a video is made. Press **Make videos** to add it to clips drafted earlier.

### Faster on YouTube: its own captions

For a YouTube video the program now asks YouTube for the words it has already written down, instead of listening to the video itself.

- A clip's captions are ready in seconds. Before, a two-minute clip could take ten minutes on a laptop without a graphics card.
- **Let AI read the whole stream** no longer needs up to an hour of listening first.
- A YouTube video with **no chat** (an upload, not a stream) can now be searched: moments are found from what is said.
- The clip's editor shows **Captions from YouTube**. If a word is wrong, fix it under **Captions** as usual.
- If the captions of some video are poor, open **Settings**, **Language**, and set *Words of YouTube videos come from* to *Listening on this PC*.

Twitch, live streams and video files are still listened to on this PC, because nobody has written their words down.

### Excited words

"Oh my god", "no way", "run run", "arre yaar", "bhaago", "bachao" and the like, in English, Hindi and Urdu, count as a signal. A clip full of them scores higher and shows an **Excited words** chip. On YouTube videos they also find moments that chat missed.

### Clip the last 30 seconds (live)

While watching live, the Live channels page has **Clip the last** with a length next to it. Saw something chat did not react to? Press it right after the moment. A draft of that stretch appears at the top of My clips in two or three minutes; trim it in its editor under Start, end and picture. It needs a stream being watched. Every press makes a clip of its own, also two presses close together. On a stream that is not being recorded only the last few minutes are kept (`watch.buffer_minutes`, 10 as installed): a longer stretch than that is shortened to what is kept, and the Activity log says so; a stretch from before the watch began cannot be clipped.

### Free local AI (optional)

For a computer with no Claude plan or key. A small AI runs on the PC itself and writes the opening text, title and a rating, so clips do not get placeholder text.

- The program checks by itself whether the PC can run it. It reads three things from Windows: how much memory is installed (about 8 GB is needed), how many processor threads there are (4 are needed), and whether there is an NVIDIA card with 4 GB or more (then the first two do not matter). The Set up page's step shows what it measured. When you press the button it also wants 8 GB free on the drive with your clips. It does not time the computer or try the model first, so a PC that only just passes can be slow. A PC that cannot never downloads anything for it; Settings says so under AI.
- A PC that can shows **Set up the free AI** on the Settings page, under AI. One press installs Ollama and a model, about 3 GB in all; progress shows under Setup check on the same page.
- Claude is always used first when it works. The local AI is the fallback, and its text is redone by Claude when Claude is back.
- Claude Code is called once per job and leaves no chats behind in your Claude app. Turning captions into Latin letters now uses the small, fast model (about four times quicker, a fraction of your plan's usage), with almost the same result.
- It is plainer than Claude, cannot look at the picture, and is not used to read whole streams or to turn captions into Latin letters.

### How far is it? The percentage bars

Every job on the page has a bar with a line under it, for example **41% · Step 2 of 4: Downloading the clips · clip 3 of 8 · roughly 3 minutes left**.

- The percentage is for the whole job, not just the step it is on.
- A download counts the video it has really received, so the bar moves while it works.
- "Roughly ... left" is an estimate from the time taken so far. It is least reliable at the start.
- Two steps move only once per clip: writing captions for a short clip, and writing the text. The bar waits there and then jumps.
- **Making the videos** has its own bar at the top of My clips, for example **44% · clip 3 of 3: the clip's title · rendering version B · roughly a minute left**. The work is counted before it starts: one step for each video to render and two for each explainer (the narrator's card, then joining it to the clip). Clips whose videos are already made are not counted. While a video is rendered the bar moves with it; during the narrator's card and the joining it waits and then moves on.
- While videos are being made, a line at the bottom of the menu shows the same percentage and "clip 2 of 4", so you see it from every page. Click it to go to My clips, where the bar and the Stop button are.
- A live watch has no bar: it runs until you stop it.

### Face box and video box: choose what the clip shows

Works on every clip: live Twitch, Kick and YouTube drafts, finished streams and your own clips.

1. Open the clip with **Edit**, go to **Start, end and picture** and press **Set the face box and the video box**.
2. You see the stream as it was, with two boxes: orange **Face** and blue **Video**. Drag a box to move it; drag its round corner to make it bigger or smaller. The boxes keep the shape they are shown in, so what is inside a box is exactly what the viewer sees. The small picture on the right is the result. Use the slider or **Play** to check other moments of the clip.
3. Under **What the clip shows** choose one of its six layouts, for example *Face on top, video below* (two boxes) or *Video only, full height* (one tall box).
4. Tick **Use the same boxes for the other clips of this video** to apply them to the other unfinished clips of the same video (handy for parts), and **Remember these boxes for new clips of this streamer** so the next drafts of that streamer start with them.
5. Press **Save and make the draft again**. It takes up to a minute; then the clip shows the new draft.

**Back to automatic** forgets your boxes (and the ones remembered for the streamer) and lets the program choose again.

### YouTube streams, and Urdu or Hindi streamers

**YouTube.** In the channels box type the channel's handle with its @, for example `@channelname`, or paste the address of the live stream. Twitch names, Kick channels and YouTube handles can be mixed: `lacy @channelname`. For a live YouTube stream the clips are cut from the stream itself; nothing is recorded in the background. Moments come from chat only on YouTube.

**Urdu or Hindi.** Open **Settings**, **Language**, and choose:
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

   Open the address it prints (http://127.0.0.1:8765/) in your browser. For each clip: watch it, fix the hook text if you want, press **Approve** or **Reject**. Press **Edit** on a clip, open its **Versions** tab and tick **Make an explainer version** if you want the narrated version of it too. Press Ctrl+C in that terminal when finished.
3. **Finish.** This renders everything you approved and writes the upload text:

   ```powershell
   uv run clipper finish
   ```

   For a campaign, add its brief so each clip is checked against the rules:

   ```powershell
   uv run clipper finish --brief briefs/lacy-gta-marathon.yaml
   ```
   Every approved clip comes out as two videos for A/B testing: `..._A.mp4` and `..._B.mp4`. They are the same moment with different opening text and titles. Post A and B on different accounts or platforms and see which gets more views; never send both to the same campaign. Clips you ticked also get `..._explained_A.mp4` and `..._explained_B.mp4` (different narration line and animation). Whether a clip gets a version B is chosen on the clip itself: **Edit**, **Versions**, tick or untick **Make a version B too**. It is ticked for new clips (to have it unticked for new clips, set `ab_versions: false` in `config.yaml`). If you tick it on a clip whose videos are already made, the clip goes back to Approved: press **Make videos** and version B is made.
4. **Upload.** Open `D:\clipper-data\ready\` and today's date folder. Each `.mp4` is named after the clip's title and has a `.txt` with the same name beside it, with the title, description and hashtags for YouTube, TikTok, Instagram and Facebook. Upload the video, paste the text. For a campaign, submit the post link on the campaign site straight away.
5. **Stop watching** with Ctrl+C in the first terminal. Starting it again later carries on.

### B. A stream that already happened

Open the streamer's Videos tab on Twitch, copy the link of the stream (it looks like `https://www.twitch.tv/videos/123456789`), then:

```powershell
uv run clipper run https://www.twitch.tv/videos/123456789 --top 8
```

Or paste the link on the page: **New clips**, **Find the best moments**.

Chat does not catch everything. To also find moments from the stream's sound or picture, add `--signals B` (chat + sound), `C` (chat + picture) or `D` (all), or pick it on the page:

```powershell
uv run clipper run https://www.twitch.tv/videos/123456789 --top 8 --signals B
```

This reads the stream's lowest-quality version once to measure it (about 100 MB per stream hour for sound, 130 MB with picture) and saves none of it. You get the 8 best chat moments, each marked where sound or picture agree, plus up to 4 moments that only sound or picture found.

**Let AI read the stream.** Add `--ai` (or switch on "Let AI read the whole stream" on the page). The whole stream's speech is turned into text and Claude picks moments from what was said, which finds funny lines that chat and loudness miss. It needs Claude, either way the program reaches it: your own Claude plan signed in on the **Set up** page (the Claude step), or a Claude key in `.env`. The Claude Desktop app itself cannot be used by other programs; the Set up page installs Claude Code, which uses the same account and plan. On a processor it reads about 8 times faster than the stream plays, so a 4-hour stream takes about half an hour. With a plan it uses your plan's limits; with a key it costs about a cent per stream with the default model. If you stop it, the next run carries on where it stopped.

Then do steps 2 to 4 above. This also works with a YouTube stream recording, if that video has chat replay.

### C. Commentary videos (for your own channel)

These add a narrator, which is what YouTube asks for before it pays for clip content.

**The narrator's voice** is chosen on the **Settings** page, under **Narrator voice**. *Where the voice comes from* has two choices:

- **This computer (free):** the voice model that came with the program. **Voice** lists its twelve best voices, the two its makers rate highest first (Heart and Bella, American women), then other American women and men, and British voices (Emma, George, Fable), which read with British sounds. **Speaking speed** makes it slower or faster. It costs nothing and works without the internet.
- **ElevenLabs (paid, your own key):** sounds more natural. Put a line `ELEVENLABS_API_KEY=your-key` in the file `.env` in the program's folder first (the key is made on elevenlabs.io; it is never typed on the page). When you choose ElevenLabs, the program asks ElevenLabs whether the key works, and if it does it switches to the best sounding model your account offers. You can then pick another **ElevenLabs model** and a **Voice** from your account's own list. Each explainer video uses characters of your ElevenLabs plan, about one for every letter spoken.
- **Check ElevenLabs** asks ElevenLabs whether the key works, which models and voices it offers and how many characters your plan has left. It makes no speech, so it costs nothing. If it says the key *may not list models*, the key works but was made without that permission: the usual models are offered instead.
- **Listen** plays one short line in the voice as it is set now. With ElevenLabs it uses about 60 characters of your plan and asks first.
- The narrator speaks English with either choice.

- **Explained clip:** the narrator sets the moment up over an animated card (the clip's picture sliding in, the streamer's name, a progress bar, a whoosh), then the clip plays.

  ```powershell
  uv run clipper explain https://www.twitch.tv/videos/123456789
  ```

  It does this for every approved clip of that stream.
- **Top moments:** a countdown of the best clips, each with a rank card and a spoken line.

  ```powershell
  uv run clipper top --count 5 --opener "Five moments from tonight's GTA 6 streams."
  ```

  `--count` must be 2 or more. The video is made fresh each time, with the caption look and branding of today.

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

- **A new streamer.** Nothing to do: just use the channel name. The clipper measures where the streamer's facecam is from the picture itself, and once two clips of that streamer agree it remembers the position (in `D:\clipper-data\facecams.json`). The first clip or two of a new streamer may be framed a little off; fix those in the clip's editor: press **Edit**, open **Start, end and picture**, and change **Framing** or press **Set the face box and the video box**. If a streamer's framing stays wrong, you can type the exact box under `channels:` in `config.yaml`, which always wins.
- **A new campaign.** Ask Claude Code: "add a brief for this campaign" and paste the campaign page link. It writes a file under `briefs/`. Check it against the live campaign page yourself.

## Part 5: When something goes wrong

| What you see | What to do |
|---|---|
| A download fails | The connection dropped. Run the same command again; it continues where it stopped |
| "not found on PATH" | Run the setup script again |
| "less than 2 GB free" | Run `uv run clipper clean`, or delete old folders in `D:\clipper-data\work` |
| "live ... but Twitch is not recording this broadcast" | Not an error. The streamer has recordings (VODs) switched off, so the watcher keeps the last 10 minutes of the live stream on disk and cuts clips from that. It uses about 3 GB of internet an hour for that channel, the first minute is lost to Twitch's opening advert, and moments during advert breaks are lost. To skip such streams instead, set `buffer_when_no_vod: false` under `watch:` in `config.yaml` |
| "no chat replay" | That video cannot be scored from chat. Use a live watch instead |
| A clip is framed wrong, or too much of the stream picture is cut off | Open the clip with Edit. Under the player, **Picture**: press **Whole picture** to show everything with blur above and below, or **Choose boxes…** to draw what is shown. The draft is made again at once |
| The clip starts too late or ends too early and the handles will not go further | Edit, **Start, end and picture**, **Need more of the stream?**: press **Add 15 s before** or **Add 15 s after**, wait a minute or two, then drag the handle into the new part and press Save changes |
| "this clip comes from a live stream that is not being recorded" when adding seconds | The seconds around that clip were only kept for a few minutes while the stream was live and are gone. Nothing can be added to it |
| "an NVIDIA card was found but speech-to-text cannot run on it" | Not an error: speech-to-text runs on the processor instead (slower, same result). To use the card, run the setup command again with `-Gpu` added at the end. It downloads NVIDIA's libraries (about 1 GB) and then says whether the card works. If it still cannot, update the NVIDIA driver and run `uv run clipper hardware --refresh` |
| A red box at the top of the page | The watcher stopped by itself, or the program is not answering. The red and yellow lines in the **Activity log** (bottom of the menu), and the terminal window, say why. A clip that could not be made says so on its tile and shows its own reason in red in its editor |
| The program closes or says "access violation" when captions are made | The computer's Microsoft Visual C++ runtime is too old. Install the x64 version from https://aka.ms/vs/17/release/vc_redist.x64.exe, restart, and try again. `uv run clipper doctor` shows the installed version |
| "Windows' C++ runtime (msvcp140.dll) is missing", or "Could not find module ... ctranslate2.dll (or one of its dependencies)", or the installer stops at **Download the video tools and models** with "Tests failed" on a new computer | The computer does not have the Microsoft Visual C++ runtime, a free part of Windows that many programs share and that a new or freshly reset PC often lacks. Captions (speech-to-text) need it. The program adds it by itself: press **Try again** in the installer, or **Install the video tools** on the **Set up** page, or **Repair** under Settings, Setup check, and press **Yes** when Windows asks to allow Microsoft Visual C++. If that does not work (no administrator password, the download is blocked, or the copy was fetched before 26 November 2026 and has no such step), download Microsoft's own installer from https://aka.ms/vs/17/release/vc_redist.x64.exe (about 25 MB), run it, restart the computer, then press **Try again** or **Check again**. Nothing already downloaded is fetched again |
| Windows says "Smart App Control blocked an app that may be unsafe" when the installer is opened | Smart App Control is a Windows 11 protection that blocks programs not signed with a paid certificate, and this installer is not signed. There is no "Run anyway" button for it. Either install the other way, from a terminal (Part 1, **The other way, for people who use a terminal**), or turn Smart App Control off in Windows Security, App & browser control, Smart App Control settings. Know first that Windows does not let you turn it back on without resetting Windows; that choice is yours |
| "Captions are not set up on this computer yet" when you start a job | The speech model (about 1.6 GB) has not been downloaded, and the program does not fetch something that size without being asked. Open **Set up** in the menu, go to the **Captions** step, press **Download and try it**, wait for it to turn green, then start the job again |
| The **Set up** entry in the menu is amber with a warning triangle | Something the program needs is missing (Video tools or Captions). Hover over it to read which, or click it: the page shows the step and its button. Nothing is downloaded until you press that button |
| "ElevenLabs cannot be chosen yet: no key is set" | Put a line `ELEVENLABS_API_KEY=your-key` in the file `.env` in the program's folder, restart the program, then choose ElevenLabs again under Settings, Narrator voice |
| "ElevenLabs refused the key" | The key in `.env` is wrong or was deleted on elevenlabs.io. Make a new key there and put it in `.env`. The narrator stays on this computer's free voice meanwhile |
| A caption font looks different in the finished video than expected | Look at the Preview on Look and branding: it is drawn by the same engine as the videos. A font typed by name must be installed on this computer; the 20 in the Font list always work |
| Captions miss words | Normal on very noisy moments. The larger model is already the default |
| Too many clips with nothing in them in mode B, C or D | Raise `audio_level` or `video_level` under `watch:` in `config.yaml` |
| Anything else | Open an issue on the project's GitHub page, or open Claude Code in this folder and describe it |

## When something goes wrong: how errors are shown

**A mistake in a settings file.** If `config.yaml` or `config.local.yaml` has a wrong value, the program says which setting and which file, in one line: for example *config.local.yaml.moments.top must be a number; it is 'five'*, or *config.yaml has a writing mistake at line 12*. Numbers and `true`/`false` are written without quotes; each setting is `name: value` on its own line, indented with spaces, never tabs. A damaged clip file is named the same way (*...clip.json is damaged*): delete that clip on My clips and make it again.

Every error is shown in plain words, in three parts:

1. **What happened**, in one or two ordinary sentences.
2. **What to do**, as a concrete next step.
3. **Technical details**, folded shut. Click it to see the original text from the tool that failed. You never need to understand it; it is there to copy and send to whoever set the program up, or to paste into the Ask chat. The terminal window and the Activity log always carry the original text too.

Where errors appear: on the clip itself (its tile says it has a problem; open it with Edit to read the red box "This clip has a problem"), in the red box at the top of the page ("A job in the background hit a problem", which is also in the Activity log in red), and in the small message at the bottom of the page after a press. An explained message at the bottom stays until you close it. When two jobs have each hit a problem (say the watcher and Make videos), the red box at the top shows both, one under the other.

A clip whose own record on disk is damaged (the computer was switched off at the wrong moment) is shown as a tile that says so; **Delete** on that tile removes it. The other clips of that video are not affected.

Two things the program now handles by itself and only mentions in the Activity log: a video with **no sound** gets a silent sound track so that it can still be made into a clip (it has no captions), and when the **graphics card cannot make a video** (a game is using it, or its driver changed) that video is made with the processor instead, which is slower.

**Stop** on the Live channels page (beside Start watching) also removes the copy of the last minutes of the live feed that is kept for streams the site is not recording. If the site says once that a stream is not live, the program asks again a minute later before it stops watching it.

The most common ones and what they mean:

- **"This live stream sends its video in a newer format..."**: some Twitch streams are sent in a newer video format. Older copies of the program could not put its pieces together, so every clip of that stream failed (the technical text says "trun track id unknown, no tfhd was found"). Update and restart the program; the clips that failed cannot be rescued, new ones work.
- **"This moment happened too long ago..."**: for a stream the site is not recording, only the last few minutes of live video are kept, and this moment had already been dropped. Reject the clip.
- **"A piece of this moment is missing..."**: an advert break or a short internet drop cut a hole in the live video. Reject the clip.
- **"The internet connection dropped, or the site did not answer"**: check the internet; most steps try again by themselves.
- **"The video site is refusing for a while..."**: too many requests from this connection. Wait 15 to 30 minutes.
- **"The video site refused to hand over the video..."**: the site wants a signed-in viewer or suspects a robot. Wait an hour or try another video.
- **"The video is not available..."**: deleted, private or for subscribers only.
- **"The disk is full"**: free space on the drive with the data folder.
- **"A file could not be changed because another program has it open"**: close the video player or folder window showing it and press again.
- **"The AI service refused: the limit of your plan is reached..."**: wait for the limit to reset, or open **Settings** and pick another way under **AI for titles and ratings** (*How to reach Claude*).
- **"The AI program on this computer is not signed in any more"**: open **Set up** in the menu; the Claude or ChatGPT step has a **Sign in** button.
- **"The graphics card could not do this step"** or **"The computer ran out of memory"**: close games and other heavy programs and try again.
- **"The video file for this step is damaged or was only half downloaded"**: make the draft again: open the clip with Edit, Start, end and picture, Make this video's drafts again.
- **"This step took far longer than it should and was stopped"**: check the internet and try again.
- **"This step did not work, and the program does not have a plain explanation..."**: a fault the program has not met before. Try once more; if it repeats, send the Technical details to whoever set the program up.

## Where everything is

| Place | What is in it |
|---|---|
| `config.yaml` in the program's folder | Every setting, with a comment on each |
| `.env` in the program's folder | Your paid keys (never share this file) |
| `briefs\` in the program's folder | One file per campaign |
| `D:\clipper-data\work\` | One folder per stream, with its clips |
| `D:\clipper-data\ready\` | Finished videos and their upload text, by date |
| `D:\clipper-data\usage.jsonl` | Cost of each paid call |
