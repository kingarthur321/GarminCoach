# Garmin planned runs import

Plan shows **Garmin Connect planned runs** above the separate local coaching draft. **Refresh from Garmin Connect** (or `python3 scripts/import_planned_runs.py` in a terminal) performs a read-only import:

* the Garmin Connect scheduled-workout calendar for one year from the refresh day, with structured details of each future running workout;
* Garmin Coach plan workouts for five near-term reference weeks. Adaptive Garmin Coach plans may only expose their generated window, so an empty result is not a claim about every future device suggestion. The pinned adapter does not expose device-generated Daily Suggested Workouts.

The snapshot is stored privately in `data/garmin_planned_runs.json` and replaced only after every required read succeeds. An ambiguous remote response leaves the previous snapshot intact. No Garmin workout is uploaded, rescheduled or deleted.

Imported dates enter the coach context and block a new chat workout proposal from using an occupied day. The optional staged weekly planner also receives bounded schedule summaries so it can avoid dates already occupied in Connect; it still cites only allowed profile, activity and wellness references in saved plans. Workout names and steps remain Garmin data, never instructions to the coach. The import does not infer that a locally drafted run is scheduled in Connect.
