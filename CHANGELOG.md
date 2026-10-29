# Changelog

What changed in each release of CS2 Clipper. The release workflow publishes a version's section as the notes of
its GitHub Release, and the app shows those notes on the Status page when it offers that version.

## 0.10.0

### Renders work again, and every match gets its stats

- **CS2 no longer crashes when a render starts.** Since CS2's update of 22 October, CS2 crashed a few seconds into
  every FACEIT demo, so each render failed with "CS2 never started the demo". The clipper now gets the demo playing
  the moment it loads, which keeps CS2 clear of the crash. If rendering stopped because renders kept failing, resume
  it on the Status page, and send a failed Demo back through with Retry.
- **A crash says so.** When CS2 crashes while it loads a demo, the render stops straight away and its log names the
  crash dump, instead of trying twice more and saying the demo never started.
- **Stats for every match.** Each match on the Reels page has a Stats button: K/D/A, ADR, HS%, KAST, opening kills,
  trades, flashes, utility damage, clutches and 2K–5K rounds for all ten players, with FACEIT's own numbers first. The
  scorecard has arrows against your last 20 matches, the table sorts by any column, and the round timeline links each
  kill to its place in your Reels.
- **Replay a round.** Replay this round, on a match's stats, draws the round on the map's radar with a kill feed. It is
  built when you ask for it, while CS2 and FACEIT AC are closed.
- Matches analyzed before this version get their stats in the background, newest first, while CS2 and FACEIT AC are
  both closed. Analysis now waits while FACEIT AC runs, as it already did for CS2.

## 0.9.0

### Quieter Reels, and a new look: Press Box

- **Reels start quieter and remember your volume.** CS2 records them at full volume, so the player now starts at 30%.
  Move the slider and every Reel after that starts where you left it, even after the window closes. Mute carries to
  the next Reel while the page is open. A phone keeps to its own volume buttons.
- **Press Box: the comic look, laid out like a broadcast desk.** Nugget, Bangers titles, speech bubbles and halftone
  map tiles stay where they mean something, and labels, tables and numbers line up. The top bar has an orange plate
  with Nugget's head, tabs with an orange underline on the page you're on, and a status pill that keeps its light in
  a narrow window.
- **One meaning per colour.** Orange is working or "act on this", blue is "needs you" (a failed render, a Demo to
  grab, setup), cream is done or Nugget talking, and the red dot is the recording light.
- **Status** puts Nugget's rooftop on a monitor with a lower-third timer and a recording light, beside a rundown of
  the pipeline with the current step lit. On hold, it says On hold instead of a time it isn't keeping.
- **Reels and Demos to grab** show each map in its own colour, and scores and stats line up. A grabbed Demo is no
  longer faded.
- **Settings** marks the section you're in, and a switch that's off is a quiet grey knob. **First run** makes the
  step that waits on you the loud one; finished steps go quiet.
- **Easier to read.** Dim text is brighter, help text and captions are at least 13 px, the Status monitor stays
  beside the rundown down to the smallest window, and Tab skips the Reels carousel's hidden slides.
- Text is set in Barlow and Barlow Condensed, which ship with the app under the SIL Open Font License and replace
  Hanken Grotesk.

## 0.8.0

### Set up in one go, and share what you clip

- **Zero FPS cost, said up front.** CS2 Clipper does nothing while CS2 is open — not even reading a Demo — and never
  opens CS2 while FACEIT AC runs. Status and the tray show how much memory and CPU the app itself is using.
- **One screen to set up.** A fresh install opens on three steps: look up your FACEIT nickname, your clips go to
  Videos\CS2 Clipper (change it if you like), and the tools download by themselves, about 122 MB, with a progress bar.
  The installer says so before it installs.
- **Toasts come from CS2 Clipper**, not Windows PowerShell. "Highlights ready" has **Watch**, which plays the clip in
  the app window, and **Show in folder**. The update toast opens the app window, not a browser tab.
- **Your view first.** With both views on, your view gets its Reels and its toast as soon as it is recorded; the Enemy
  POV records after it, in its own CS2 launch.
- **Share from Reels.** Click a Highlight to play it, switch to the Enemy POV in the player, and: **Copy file** (paste
  it straight into Discord), **Export for Discord** (under the 20 MB free limit, and on the clipboard), **9:16** for
  TikTok, Shorts and Instagram Reels, or **Show in folder**. Exports open on a title card with your nickname, the map,
  the round and the Highlight (Settings → Picture).
- **Readable names.** Reels are called what they are — `ACE Dust2 R9 you.mp4` — in a folder per match, like
  `2026-10-12 Dust2 13-9`. Reels made before this get their new names at the next start.
- **Reels on your phone.** Open the app's address on your phone, as for Demos to grab, to watch your Reels and
  download them. Everything else stays on the PC.
- **A toast when you can fix the wait.** If FACEIT AC or a closed Steam is all that holds rendering up, a toast says
  so after two minutes. Renders wait for Steam instead of failing three times.
- **When renders happen.** Settings → Rendering → **Start renders**: any time, once the PC has been idle 10 minutes,
  or overnight. The heads-up toast has **Not now (1 h)**. CS2, its encoder, csda and FFmpeg run below normal
  priority, and **Encoder** can use the graphics card (NVENC, AMF or Quick Sync).
- **Safer defaults for posting.** X-Ray and Player voices are off by default: no "walls?" replies, and no teammates'
  comms in clips you post. Turn them back on in Settings → HUD.
- **Calmer pages.** Status shows the pipeline as Found → Making clips (with how long is left) → Ready, says each
  problem once with its fix beside it, and uses plain names: System check, Activity log, Ready to download. Quit
  moved to the foot of the page and the tray. Animations stop and the pages check in less often while the window is
  in the background.
- **Settings.** Keys, paths, timeouts and the port moved under **Advanced**. Look up fills in your nickname and
  SteamID, and Save keeps them, like every other setting. Status checks for CS2 and Steam too.
- **Demos to grab** rows show the map, its Highlights, how long the link lasts and Open; the match's numbers sit in a
  line under the map. "Waiting for the download" is remembered by the app, so every window and the phone see it.
- Once set up, the window opens on **Reels**.

## 0.7.0

### New look: Nugget, and pages like a comic

CS2 Clipper has a mascot. **Nugget** is a war-correspondent chicken: CS maps are full of chickens, and this one lived,
put on a PRESS helmet with a camera strapped to it, and films your Highlights. The pages around him are drawn like a
comic: ink borders, halftone dots, caption boxes, speech bubbles and sound effects.

- **Status** opens on a comic panel: Nugget on a rooftop, saying in a speech bubble what he is doing. He films while a
  render runs (his eye follows your pointer), sleeps on his egg when there is nothing to do, loses feathers when
  something needs you, and crows when an Ace lands. The sky changes with him: dusk while he films, night after 8 pm,
  red when something is wrong.
- The pipeline is a strip of seven panels, with Nugget's head on the step a Demo is at and how far its render has got.
- **Demos to grab** on Status are Wanted posters, stamped **Last call** when the link runs out within a day.
- **Reels** tag each Highlight with a burst: ACE!, 4K!, 3K!.
- Nugget also brings the toasts, explains Set up and Update, sums up Demos to grab, Reels and Settings, sleeps on his
  egg in an empty list, and joins the Ace moment with an ACE!! burst. Checks are **The crew** now, and the log is
  **Previously…**.
- Titles, tabs and buttons are lettered in Bangers and the text is set in Hanken Grotesk. Both fonts ship with the
  app now, under the SIL Open Font License.

## 0.6.0

### New: small touches

Keys borrowed from CS, and a few details with some personality. Settings → **Touches** turns them on and off.

- **Hold Tab** on any page for this week's scoreboard: each match's score, K–D, Highlights and where it is now.
- **~** opens a console. `help` lists its commands: `status`, `demos`, `retry`, `skip`, `undo`, `pause`, `check`,
  `open reels`, `goto`, `log` and a couple to find yourself.
- **Hold B** for the quick wheel: aim at Check for demos, Pause, Retry failed, Open Reels folder, Console or Settings,
  and let go. **Check for demos** asks FACEIT for new matches at once (if CS2 is open, as soon as it closes).
- **1** to **4** switch pages, **Q** goes back to the last one, **P** pauses or resumes rendering, and **?** lists
  every key.
- **Demos to grab**: drag a match left to skip it, with Undo for 5 seconds. **↑ ↓** pick a match, **S** skips it and
  **Z** brings it back.
- **Reels**: move the pointer across a Highlight to flick through its Reel. **← →** pick a Reel, **Space** plays it
  and **E** opens its folder.
- **Map glow**: while a match renders, the background glows in its map's colour.
- **The Ace moment**: a new Ace in Reels gets a stamp, a burst of shards and a toast.
- **Status** has a line for the time of day, and short lines to read while a Demo is analyzed, rendered or joined.
  From 10 pm to 5 am it is quiet hours: no sounds and no Ace stamp.
- **UI sounds** (off by default) click softly on buttons, and **Calm mode** turns most of the animation down.

### New look: the Slice mark

The app's mark is new: the play triangle, cut at the lean of the pages' italic type, on the cut-corner tile. It is
in the tray, on the taskbar, on the exe and in the pages' nav.

## 0.5.0

### New: HUD settings

Settings → **HUD** chooses what shows over the picture in Clips rendered from now on. Reels already made keep their
look, and the defaults are the look every Clip had before.

- **HUD**: **Kill feed only** (the default) or **Full HUD**, with the radar, health, ammo and round timer.
- **X-Ray**: players outlined through walls, as when you spectate. On by default.
- **Assists in the kill feed**: a kill a teammate helped with shows both names. On by default.
- **Kill feed time**: how long each kill stays in the kill feed, from 1 to 30 seconds (5 by default).
- **Player voices**: voice chat from the Demo in each Clip's sound. On by default.

## 0.4.0

### New: choose which views to render

Settings → Picture → **Views to render** picks **Both** (as before), **Your view** or **Enemy POV**. With one view,
each match records half as much, so it renders faster. A change applies to every match not rendered yet, including
the ones already waiting; matches that are done keep their Reels.

With **One Clip per: Round**, matches now render your view only. A whole round has no one enemy to follow, so its
Enemy POV was your view again, recorded in a second CS2 run.

### New: raw Clips are deleted once their Reels are made

When a match's Reels are made, its raw Clips are deleted, so each Highlight is stored once instead of twice, and the
Reels stay as they are. Matches finished before this version are cleaned up once, the next time the app starts. A
Demo that failed keeps its Clips, so **Retry** on the Status page can still make its Reels.

### New: one heads-up per match

When CS2 has just closed after rendering a match, the next render of that match starts right away, without another
30-second heads-up or notification. A new match still gets the heads-up, and so does a render after CS2 or FACEIT AC
was open in between.

### New: both views in one CS2 launch

When both views are due, CS2 opens once and records your view, then starts the demo again for the Enemy POV, saving a
CS2 start-up per match. If CS2 fails during the Enemy POV, your view is kept and the Enemy POV is tried again on its
own; retries go one view at a time.

### New: render progress on the Status page

The Status page shows which view and Sequence CS2 is recording, and about how long is left. The Demo's bar moves with
the render, instead of holding at the same place for the whole of it.

### New: render the other view later

On the Reels page, a match rendered with one view has a button to render the other. The match goes back in the queue
for that view only, using the download the app kept. If that view cannot be rendered, the match keeps its Reels and
you get a notification.

## 0.3.0

### Fixed: rendering changed your CS2 video settings

Since 0.2.0, when the app began recording through HLAE itself, it has started CS2 with the size of your clips and
in a window (for example `-width 1280 -height 960 -sw`). CS2 saves its settings into whatever folder the
`USRLOCALCSGO` variable names, and the app was not setting it. So CS2 used your Steam folder
(`userdata\<your id>\730\local\cfg\cs2_video.txt`) and kept the render's resolution and window mode after a render.

CS Demo Manager, which 0.1.0 recorded with, always set that variable to a folder of its own, and the app's copy of
its launcher missed it. HLAE's own changelog says the `-afxDisableSteamStorage` flag the app already passed is
"useful to be combined with USRLOCALCSGO".

Now the CS2 a render starts keeps its settings in a folder of its own, `%LOCALAPPDATA%\CS2Clipper\cs2-settings`.
Your own settings are never touched, so there is nothing to restore afterwards.

- If a render on 0.2.0 changed your resolution or display mode, set them once in CS2's video settings. The values
  from before can't be recovered: Steam Cloud doesn't keep that file.
- The first render on 0.3.0 records with CS2's auto-detected graphics quality, as CS Demo Manager's renders did.

### New: one-click updates

When a new version is out, the Status page says so, shows what changed, and installs it with one click: CS2
Clipper closes for a few seconds and opens again on the new version. Your settings, Demos, Reels and shortcuts stay
as they are. A Windows notification tells you when a new version is out.

This is the last version you install by hand.

## 0.2.0

- CS Demo Manager and its database are no longer needed: the app reads each Demo with csda and records through
  HLAE itself.

## 0.1.0

- The first release.
