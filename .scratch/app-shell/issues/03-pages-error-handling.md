# The pages' error handling

Status: ready-for-agent

Rough edges in clipper/pages/, each small:

- status.html and reels.html wrap fetch and render in one try/catch, so a render bug shows as
  "Can't reach clipper right now" instead of surfacing.
- settings.html has no handling for a failed fetch (load or save): the page just stays as it was.
- settings.html's "Remove key" rebuilds the form and drops the user's unsaved edits.
- reels.html counts a paused Reel as not playing, so a poll that finds new Reels re-renders and removes
  the paused video (there is no close control; a playing one is kept).

From the app-shell build (Tasks 11-13 minors; the paused Reel from the batch C fix).
