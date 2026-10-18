# Status

Public release checkpoint, October 2026. Coding agents update this file after each substantial milestone in their own fork.

## Implemented

* Local dashboard (Overview, Runs, Plan, Trends, Settings) with interactive routes, street/offline maps, linked charts, splits, run comparison and training signals.
* Bounded Garmin imports through the pinned Taxuspt/garmin_mcp commit: activities, wellness, readiness/status/load/VO₂, recorded routes/series/splits and Connect planned runs.
* Manual coaching workflow for Claude Code or Codex (`COACHING.md`) with validated, atomic snapshot saves.
* Claude API coach chat with cited answers, reviewable athlete memory and chat-to-workout proposals.
* Guarded Garmin workout publishing under the `draft_only` / `selected_workouts` / `automatic` policies, with duplicate prevention, uncertain-write reconciliation and separate watch verification.
* Optional local runner and scheduler templates, disabled by default.

## Verification

* 96 fixture/mock unit tests pass (`python3 -m unittest discover`). Map geometry and MCP contract checks pass with synthetic data.
* Dashboard browser checks pass with synthetic data at desktop and mobile widths.
* Real-account checks were done by the original author during development; see [docs/VERIFICATION.md](docs/VERIFICATION.md). They are not evidence for your account or watch.

## Fresh clone state

* Data: synthetic sample snapshot, clearly labeled in the UI.
* Profile: unconfirmed example (5 km goal, example running days).
* Garmin write policy: `draft_only`. Scheduler: disabled.

## Next steps for a new user

1. `./launch.sh` and explore the sample data.
2. Add an Anthropic API key to `.env` for the coach chat.
3. Install and authenticate the Garmin MCP server, configure your coding client, then use the first session prompt in START_HERE.txt.
4. Record your own real-account and watch checks here, kept separate from fixture results.

## Known limitations

* The pinned MCP adapter exposes chronic-load target ranges but no acute-load target range.
* Device-generated Garmin Daily Suggested Workouts are not exposed.
* Voice input is not implemented.
* The scheduler only runs while the computer is on.
