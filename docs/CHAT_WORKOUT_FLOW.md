# Chat to Garmin Connect: requested delivery and draft review

An explicit **Create a workout** request in the localhost coach now authorizes one automatic Garmin Connect delivery. The coach answer is validated against the current athlete profile, saved observations, running days, session limit and occupied dates. The local app then stages that exact workout, uploads it, checks its steps and schedules it in Garmin Connect. The chat shows whether delivery was scheduled or blocked. A question asking for a draft, idea or suggestion keeps the manual review path below.

Both paths need a reviewed real plan (`review_required` false). The optional daily scheduler refreshes observations and the planning week; it does not automatically send workouts under the `selected_workouts` policy. Scheduling in Connect does not by itself prove the workout reached the watch; inspect the date and steps on the watch for each new workout ([watch sync](WATCH_SYNC.md)).

## Draft and manual review

1. Open the Coach bar and ask for a workout draft or idea. The answer must cite saved observations and contain a date, rationale, warmup, running steps and cooldown.
2. Choose **Preview workout**. Read the date, total duration, intensity cues and each step. Check **I reviewed this exact date, duration, steps and intensity**, then choose **Approve exact workout**. This saves an immutable additional workout and selects that exact revision locally. The saved training week is unchanged.
3. On **Plan → Additional workouts**, choose **Send to Garmin Connect**. This makes the guarded Garmin upload and calendar request. The app checks the returned workout content and scheduled date. It will not silently retry an uncertain write.
4. If the status is uncertain, choose **Check Connect status**. This performs read-only reconciliation. After Connect reports **scheduled**, sync your watch through Garmin Connect and inspect the workout date and steps on the watch. Use **I see it on my watch** only after seeing them; the note records your own device report.

Both paths require confirmed real Garmin observations, a current proposal, the same reviewed training week, a future running day within 30 days, no saved workout on that date and the `selected_workouts` policy. A changed profile, training data, feedback, athlete memory or saved week invalidates the proposal. If the saved week changes after approval, Send is blocked before any Garmin write. A date outside confirmed availability needs an explicit user-requested exception through the separate one-off workflow; chat cannot infer one. Fixture answers stay illustrative. The Claude worker has no Garmin tools or credentials; the local server performs guarded Garmin writes after the answer is saved.

The full browser click path, including Connect readback and a simulated watch report, is covered by a check against a disposable mock Garmin client.

See [verification](VERIFICATION.md) and [mocked browser evidence](evidence/chat-workout-checks.json).
