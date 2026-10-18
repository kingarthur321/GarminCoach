# Athlete memory

The dashboard has a small, private memory for details worth carrying beyond the latest six chat exchanges. Open **Settings → Coach memory** to add a note, edit one, or delete it. A coach answer has a **Remember insight** button: review or rewrite the suggested text in the dialog before saving. Chat answers are never promoted automatically. The confirmed athlete profile, Garmin measurements, feedback, saved plan and chat history remain separate stores.

Each memory records its type (`user_statement` or `coach_insight`), origin (manual or saved chat), original chat ID and evidence references when applicable, creation/update times and a revision. An edited insight retains its origin but remains an AI suggestion; it does not become a measured result. The card shows the type, origin and last update. Changes use revision checks, so an old browser tab cannot silently overwrite a newer edit.

The private file is `data/athlete_memory.json`, ignored by Git and excluded from the release archive. At most 80 reviewed entries of 600 characters can be saved. The 20 most recently saved/edited entries are included with each chat question and staged planning context; older entries stay visible and editable in Settings. A coach insight from synthetic sample context, or with an unknown data source, is excluded when the current observations are Garmin data. Prior chat remains separate: deleting a memory removes it from future memory context, but it does not erase the original chat exchange. The latest six exchanges can still appear in short-term conversation context until they age out.

Memory is context, never a command or a Garmin measurement. The chat prompt distinguishes manual statements from reviewed AI suggestions and requires current observations for measured claims. The planning prompt likewise treats memory as context, while persisted plan references still need a valid profile, feedback or observation source. This avoids making old memory IDs a permanent dependency of historical plans. Creating, editing or deleting memory flags `review_required` and changes the planning input fingerprint; an agent result prepared before the edit cannot replace the plan. A chat answer started before a memory edit is not saved as a current answer. No memory action creates or schedules a workout in Garmin.

## Verification

[Memory checks](evidence/memory-checks.json) cover fixture and browser behavior. A [live synthetic Claude check](evidence/memory-live-check.json) confirmed that a saved note reaches the API and is recalled.
