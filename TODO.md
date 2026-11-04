# Todo

Ideas for later, in no particular order. What to work on first gets decided when we get to it.

Most of these came from a nine-model unattended run, where a model got stuck in a loop on the toy task and
used up its full hour. "That run" further down always means this one.

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
- Add cached tokens to the report, once real server data shows what servers send.
- Tag a new version after a real run has exercised the latest changes.
