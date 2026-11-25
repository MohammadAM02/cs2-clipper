# CS2 Highlight Clipper

A private, local pipeline that turns CS2 match demos into scored, rendered highlight clips.

## Language

**Demo**:
A CS2 match replay file (`.dem`) containing tick-indexed game state and events.
_Avoid_: Replay file, VOD, recording

**Tick**:
The engine time unit of a Demo; CS2 demos run at 64 ticks per second.
_Avoid_: Frame, timestamp, second

**Highlight**:
A candidate moment in a Demo, identified by a Tick and a Highlight Type, that may become a Clip.
_Avoid_: Event, moment, play, spot

**Highlight Type**:
The classification of a Highlight, for example `ACE`, `CLUTCH_1V4`, `COLLATERAL`. With Clip your deaths on in
Settings, the subject's death in a round is a Highlight of its own, a `DEATH`, with a Reel of its own.
_Avoid_: Category, tag, kind

**Kill**:
A single `player_death` event in a Demo.
_Avoid_: Death, frag

**Frag**:
A Kill credited to a specific player.
_Avoid_: Kill (a Kill is not necessarily the subject player's Frag)

**Clutch**:
A round won by the last surviving player of a team against two or more opponents.
_Avoid_: 1vX, save, comeback

**Score**:
The numeric rank a Highlight receives from the scoring rules; it drives ordering. What is recorded is what you Pick.
_Avoid_: Rating, weight, points

**Worth clipping**:
A Highlight with two or more Frags, or with one that has something to it: a Desert Eagle or knife Frag, a no-scope,
a jump shot, a wallbang, a Frag through smoke or while flashed, one on 20 HP or less, or a won Clutch. The Reels page
lists these to Pick.
_Avoid_: Good round, highlight-worthy, selected

**Pick**:
A Highlight or Break the user chose to clip. Picking sends its Demo to record what each view lacks.
_Avoid_: Selection, choice, top N

**Sequence**:
The start Tick and end Tick that define which part of a Demo becomes one Clip.
_Avoid_: Segment, range, clip range

**Clip**:
The rendered video file produced from exactly one Sequence.
_Avoid_: Video, highlight video, montage

**Reel**:
A single video file containing several Clips concatenated in Sequence order.
_Avoid_: Montage (that is an edit), compilation, highlight video

**Break**:
The talk between two rounds where the sides switch or an overtime starts: halftime, and the start and the half of
each overtime. A Break someone talks in can be picked like a Highlight, and becomes one Clip of the subject's POV
with every player's voice in it, both teams'.
_Avoid_: Timeout (that is a team's pause), halftime clip

**Render Job**:
The unit of work that turns one Highlight into one Clip.
_Avoid_: Task, encode job, export

**Render Engine**:
The component that turns a Sequence into a Clip file; currently HLAE, recording CS2 as it plays the Demo.
_Avoid_: Renderer, encoder (encoding is FFmpeg's job inside the engine)

**POV**:
The first-person camera view of a single player.
_Avoid_: First person, in-eye, POV demo (that is a separate Demo kind)

**Perspective**:
Which player's camera a Sequence is recorded from.
_Avoid_: Camera angle, view, cam

**Enemy POV**:
A Clip recorded from an opponent's camera, showing the subject's Frag from the victim's point of view.
_Avoid_: Victim cam, death cam, reverse angle

**Mistake**:
A moment in a Demo that usually costs the round and that the subject could have played another way, worked out from
what the Demo says happened: a death nobody traded, a death while blind, a flash that blinded a teammate. A death is
a Mistake for at most one reason.
_Avoid_: Error, blunder, misplay, bad play

**Mistake Type**:
The classification of a Mistake, for example `OPENING_DEATH`, `DIED_BLIND`, `THREW_ADVANTAGE`, `TEAM_FLASH`.
_Avoid_: Category, kind, tag

**Form**:
The subject's numbers over their last matches (10, 20, 50 or all of them), each against the same number of matches
before those. The Progress page shows it.
_Avoid_: Trend, streak, shape

**Heatmap**:
Where the subject's Frags and deaths happened on a map's radar, over one match or many: where they stood for a Frag,
and where they died.
_Avoid_: Hot zones, position map, kill map
