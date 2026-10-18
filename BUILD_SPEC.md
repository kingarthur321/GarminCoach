# Build specification

## Product and finish line

Build a personal AI running coach with this loop:

Watch records a run. Garmin Connect stores it. The Garmin MCP server exposes it to the coding agent. The agent reads a private athlete profile and proposes a training week. Saved results appear in a local dashboard. Authorized workouts are created in Garmin Connect and then synced or sent to the watch.

The first usable result is one evidence-based training summary, one draft week, one structured workout and a verified route to the watch. The goal in the example is 5 km under 20 minutes; viewers replace it with their own goal.

STATUS.md records the current implementation and verification state.

## Default implementation

Use Python 3.12 or later for local validation, storage utilities and a small localhost server. Use HTML, CSS and JavaScript for the dashboard. Prefer the standard library and a simple file store over a large framework.

The original manual coaching and MCP workflow uses an existing authenticated Claude Code or Codex session. The on-demand dashboard conversation and the optional scheduled planning runner use the Claude API with a separate Anthropic key stored outside tracked source. Garmin reads and guarded writes use the pinned MCP adapter.

Keep private state under data/. Normalize and save snapshots there. The dashboard reads saved snapshots through narrow localhost endpoints. Bind to 127.0.0.1 and expose only approved output files, not the whole project or token directory.

The dashboard must clearly identify whether its data is live, cached or a fixture and show the last successful update. The on-demand conversation bridge calls the Claude API using saved context; it does not silently refresh Garmin or replace/publish a plan. A refresh control must actually invoke an implemented runner or clearly explain how to run the coach through the coding agent.

Create synthetic fixtures for development when Garmin is unavailable. Label them as sample data throughout the UI.

## Build stages

### 1. Bootstrap

Inspect the host OS and installed tools. Read the current primary documentation in SOURCES.md where commands or tool interfaces need verification. Record the chosen stack and versions.

Copy athlete_profile.example.json to data/athlete_profile.json and mark it as unconfirmed until personalized. Create private directories and ensure generated personal state is ignored by git.

Implement the local server and basic dashboard. Generate fixtures with an unmistakable sample label. Verify this independently of Garmin login.

### 2. Garmin reads

Start with the candidate MCP implementation in README.md. Authenticate using the user's own terminal and the repository's documented tool. The user enters credentials and MFA themselves.

Configure the selected coding client without putting credentials in shared configuration. Restart the session when necessary. Discover the tools exposed by the running MCP server.

Fetch up to 42 days of running activities and, when available, up to 14 days of relevant wellness observations. Paginate within bounded limits. Record missing or unsupported fields rather than repeatedly querying them.

Normalize distance to kilometers, pace to seconds per kilometer, duration to seconds and timestamps with timezone. Preserve the original activity identifiers. Deduplicate by identifier.

Limit stored wellness data to what the coaching workflow uses. The dashboard also uses private local routes, recorded activity series/splits and readiness/training status/load/load-focus/VO₂ max. Store these bounded supplemental observations separately from the established summary snapshot, preserve dates/units/source IDs and unsupported states, and never add synthetic detail to real runs. Render recorded routes locally with a geographic projection. Recorded Garmin routes default to street maps with an Apple Maps-inspired interface. Retain a persistent offline toggle. The provider sees the viewed tile area; raw samples stay local. Fixture routes render offline. Cache only viewed tiles privately, retain the GPS overlay if tiles fail, and never send raw samples to the provider or coach chat. Raw credentials remain outside the dashboard.

### 3. Personal coach

Create a reusable coaching prompt and an update procedure that load the athlete profile, recent snapshots, the last plan and the user's feedback.

First summarize the actual training pattern and data coverage. Then propose a draft week that fits confirmed availability and accounts for other training and stated constraints. Explain meaningful proposals with dated observations and identify uncertainty.

Do not convert the desired race pace into the intensity of every session. Do not treat a single sleep or HRV reading as a definitive decision. Allow the user to record how a session felt.

When essential profile fields are unconfirmed, prepare an illustrative draft and finish the interface while asking for the required values. Do not upload that illustrative draft.

Save the summary and plan as validated JSON and a readable local summary. Preserve previous plans and revision reasons.

### 4. Dashboard

Provide:
1. Goal and confirmed athlete settings.
2. Recent activities and weekly distance.
3. Available recovery trends with missing-data states.
4. The current draft week with explanations.
5. Feedback and schedule-change history.
6. Source coverage and update time.
7. Garmin workout status: draft, created, scheduled or watch verification pending.

Use restrained visual styling that is readable on a screen recording. Escape imported strings. Persist changes to the private profile or feedback store.

Changing a date or logging feedback must update the actual saved state. Display that the coach needs to review the change if no runner has processed it yet.

### 5. Garmin workouts

Discover the current workout creation and scheduling tools. Inspect their schemas rather than guessing Garmin's raw JSON format.

Convert the selected planned session into structured steps with explicit duration or distance, warmup, recovery blocks where applicable and cooldown. Validate target units and date interpretation before writing.

Support these policies:
* draft_only: save proposals locally.
* selected_workouts: create only workout revisions explicitly selected by the user.
* automatic: allow recurring creation after the user configures this mode and client permissions permit it.

Track each session revision, Garmin workout ID and scheduled entry ID. Prevent repeated runs from creating duplicates. If a write times out with an uncertain outcome, reconcile with Garmin before retrying.

Verify the created workout and calendar date in Garmin Connect. The final watch sync or Send to Device step may still use Garmin Connect. Record watch verification separately; creating a workout in Connect is not proof that it reached a device.

Never bulk-delete unrelated workouts. Updating a published plan must preserve completed sessions and unrelated calendar entries.

### 6. Optional recurring coaching

After a successful manual update, implement a bounded runner using the selected client's documented noninteractive mode. It must load this project directory, discover the same MCP connection, consume the reusable coaching prompt and validate outputs before replacing the previous snapshot.

Generate platform-appropriate scheduler templates and an explicit enable/disable command. Keep scheduling disabled until the user enables it locally.

Default proposed schedule: a daily refresh at 07:00 and a weekly draft on Sunday in the athlete's timezone. Respect daylight saving time. A weekly draft should not be repeatedly rewritten without changed inputs.

Enforce one active run, a configurable timeout, bounded reads and clear failure logs. Retain the last valid snapshot after failures. Handle expired authentication by marking the run blocked instead of looping on login.

A computer that is off cannot run a local scheduled job. Document resume behavior and actual model/account requirements. Do not claim that AGENTS.md itself schedules execution.

### 7. Verification and release

Test the behavior that can change results:
* Unit conversions and local dates.
* Activity deduplication.
* Missing wellness data.
* Saving profile and feedback changes.
* Plan output validation and revision handling.
* Duplicate prevention and uncertain write recovery.
* Prevention of concurrent recurring runs, if that runner is implemented.

Inspect the dashboard in its fixture state and its real-data state when available. Record commands, outcomes and screenshots as evidence.

Verify one real activity read, one real saved plan, one authorized workout in Garmin Connect and the workout on the user's watch. Keep each result separately documented if the final user-device step is pending.

Pin dependencies and the successful MCP version. Document installation, authentication, manual update, dashboard launch, optional automation, Garmin write policy and troubleshooting.

Prepare a shareable release containing working source, lock files and these instructions. Exclude private data, credentials, tokens and local logs. The initial instruction package is not evidence that the app already works.

## Suggested saved outputs

Choose concrete schemas and validate them:
* activities.json: activity IDs, local dates, distance, duration, pace and available HR.
* wellness.json: dated observations, units, sources and coverage.
* coach_summary.json: observations, evidence references and uncertainty.
* training_plan.json: revision ID, dates, sessions, structured steps and rationale.
* feedback.json: user statements, timestamps and related activity or session.
* workout_registry.json: session revisions, creation state and Garmin IDs.
* run_status.json: last attempt, last successful update and blocked steps.

Do not rewrite valid outputs with malformed or partial agent results.
