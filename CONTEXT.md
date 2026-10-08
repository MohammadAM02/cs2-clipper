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
The classification of a Highlight, for example `ACE`, `CLUTCH_1V4`, `COLLATERAL`.
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
The numeric rank a Highlight receives from the scoring rules; it drives selection and ordering.
_Avoid_: Rating, weight, points

**Sequence**:
The start Tick and end Tick that define which part of a Demo becomes one Clip.
_Avoid_: Segment, range, clip range

**Clip**:
The rendered video file produced from exactly one Sequence.
_Avoid_: Video, highlight video, montage

**Reel**:
A single video file containing several Clips concatenated in Sequence order.
_Avoid_: Montage (that is an edit), compilation, highlight video

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
