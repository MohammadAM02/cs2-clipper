# Changelog

What changed in each release of CS2 Clipper. The release workflow publishes a version's section as the notes of
its GitHub Release, and the app shows those notes on the Status page when it offers that version.

## 0.4.0

### New: choose which views to render

Settings → Picture → **Views to render** picks **Both** (as before), **Your view** or **Enemy POV**. With one view,
CS2 opens once per match instead of twice, so a match renders in about half the time. A change applies to every
match not rendered yet, including the ones already waiting; matches that are done keep their Reels.

With **One Clip per: Round**, matches now render your view only. A whole round has no one enemy to follow, so its
Enemy POV was your view again, recorded in a second CS2 run.

### New: raw Clips are deleted once their Reels are made

When a match's Reels are made, its raw Clips are deleted, so each Highlight is stored once instead of twice, and the
Reels stay as they are. Matches finished before this version are cleaned up once, the next time the app starts. A
Demo that failed keeps its Clips, so **Retry** on the Status page can still make its Reels.

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
