# Changelog

What changed in each release of CS2 Clipper. The release workflow publishes a version's section as the notes of
its GitHub Release, and the app shows those notes on the Status page when it offers that version.

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
