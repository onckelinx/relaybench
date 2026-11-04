# Todo

Ideas for later, in no particular order. What to work on first gets decided when we get to it.

Most of these came from a nine-model unattended run, where a model got stuck in a loop on the toy task and
used up its full hour. "That run" further down always means this one.

## Group failed tests by root cause

Non-ASCII digits are 4 of the 41 toy tests, so one mistake can cost a build four tests. In that run, the top five
builds each failed for a single reason, so the raw counts overstate the differences between them.

- Keep the raw count as the main result, and add the failing categories next to it in the report.
- One way is a small file in the task folder that maps tests to categories, which models never see.
- Don't let this change any score.

## Mark stopped builds

The report's *Unattended stages* table shows when a build was stopped, but the *Hidden tests* table doesn't,
and in `compare` a stopped build looks like a normal one.

- Mark stopped builds in the *Hidden tests* table. Their scores only show the code at the moment they were
  stopped. In that run, one build's score came from about 75 seconds of work, followed by one runaway answer.
- Add a *Stopped* column to `compare`, counting stops by hand and stops at a limit.
- Keep this out of the judge packet, so judges score the code as it stands.

## Let judges cross-check each other (optional)

Giving each judge the other's report found real errors in that run, including a mix-up between two builds and
bugs that one judge had missed. It also produced false claims about missing tests, because judges can't see
the hidden tests.

- A command could build a packet with all the judges' reports and the hidden test source. Scoring is over by
  then, so showing the tests does no harm.
- Keep the answers as notes, not scores, because second-round scores would be anchored on the other judge.

## Smaller ideas and open offers

- Write release notes to publish on GitHub.
- Check on real hardware whether LM Studio stops generating when `skip` or a limit kills the agent mid-call.
  That decides how much electricity stopping a model really saves.
- Decide whether to force-push to remove the hardware line that commit `b009b6f` left in the history. The
  recommendation so far is to leave it.
- Validate review fields and `preferred_build`.
- Tidy the packet and report for runs with no reviewers.
- Add a total-time row and cached tokens to the report.
- Add a packet hash.
- Tag a new version after a real run has exercised the latest changes.
