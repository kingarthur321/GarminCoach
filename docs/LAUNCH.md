# Local launch and operation

Tested: macOS arm64, Python 3.14.6, Codex CLI 0.159.3, Claude Code 2.1.231. Runtime uses the standard library. macOS/Linux are supported; locks and deadlines use Unix APIs. Python 3.12+ is required; 3.14.6 is actually tested. Dashboard conversation requires an Anthropic API key.

## Start

```bash
./launch.sh
```

Open http://127.0.0.1:8765. The launcher initializes private state and creates sample data only when no snapshot exists. It never replaces real data with fixtures. Stop with Ctrl-C. Use `./launch.sh --port 8767` if occupied. Equivalent commands:

```bash
python3 -m coach init
python3 -m coach demo
python3 -m coach serve --port 8765
```

A fresh install starts with an unconfirmed example profile; replace it with your own goal, running days, other training and constraints in **Settings**, or ask your coding agent to help (see START_HERE.txt). Unknown baseline and target date can stay empty. Editing training details in **Athlete settings & write policy** clears the checked confirmation box so you can review them again. Profile changes flag another coaching review. Feedback, date changes and completion persist and indicate that the coach must review them. A confirmed profile's availability and time limit are enforced; change availability first if moving a session to another day.

Private state is under `data/` with owner-only permissions. Snapshot generations contain activities.json, wellness.json, coach_summary.json, training_plan.json and summary.md; data/current.json identifies the active generation. One pointer publishes a complete generation; earlier generations retain plans and revision reasons. Profile, feedback, registry and run status are separate private files. The dashboard exposes allowlisted endpoints, never raw directories or token files.

## Running workspace and conversation

Overview opens the newest saved activity. Select another from the sidebar, Runs history or previous/next. HR, pace and elevation tabs inspect recorded samples; the range control supports arrow keys and moves the route marker. Compare runs overlays recorded series by distance. Runs searches recorded activity history. Plan has an interactive week calendar and workout previews. Trends has 7/28/42-day totals, matching weekly graphs and dated recovery inspection. Settings uses the same workspace style; profile, feedback/date controls and review guards are retained.

The route uses Web Mercator with equal geographic scale in both axes, responsive fit, north, distance scale and start/finish markers. Drag or arrow keys pan; +/- zoom; Home fits the route. Street maps are the default for recorded Garmin routes. The Apple Maps-inspired interface uses softened map colors, frosted controls, a compass and a clear blue route. Decorative macOS window buttons are removed. The provider sees the viewed tile area; raw GPS samples stay local. Only visible tiles are requested through the fixed provider, cached privately for seven days and conditionally revalidated. Visible attribution links to OpenStreetMap. **Offline view** disables downloads and persists across reloads; **Street map** enables them again directly. Fixture routes remain offline. If tiles fail, the recorded route stays visible. Provider behavior is documented in the [OpenStreetMap tile policy](https://operations.osmfoundation.org/policies/tiles/); tests intercept images and make no provider requests.

Acute load displays a target outline only when an acute range is supplied. The pinned MCP exposes a chronic-load target range instead; it appears separately in load details alongside chronic load/ratio. Focus shows low aerobic (blue), high aerobic (orange) and anaerobic (red), each with its own Garmin markers/target outline and reported trailing-month period. These are indicators, not editable training sliders. Readiness, status, load, running VO₂, recovery and resting HR are imported. Clicking details opens history, source/date/units and readiness factors. Real records never receive demo measurements. Supplemental data is saved in private data/dashboard_details.json; coverage/missing-field metadata in data/garmin_import_report.json.

Type into the floating Coach bar, or use Cmd/Ctrl+K. It uses the Claude API. Put `ANTHROPIC_API_KEY=your-key` on its own line in the private project `.env` file; `ANTHROPIC_MODEL=claude-sonnet-5-5` is the default. Restart the dashboard after editing the key or model. The editable [chat system prompt](../prompts/coach-chat.md) is loaded for each new answer; [coaching sources and interpretation](COACH_PHILOSOPHY.md) are documented separately. Answers cite saved evidence. An explicit **create a workout** request sends one validated workout to Garmin Connect automatically; a draft or idea request keeps the preview and manual send path. [The chat-to-watch guide](CHAT_WORKOUT_FLOW.md) covers both paths and uncertain writes. Text input is implemented; voice stays disabled.

In Settings, **Coach memory** stores notes you explicitly save. A chat answer's **Remember insight** button opens an editor so you can review its text before saving. Edit or delete entries in Settings. The latest 20 reviewed entries enter chat and staged planning context; [memory behavior and limits](ATHLETE_MEMORY.md) describe provenance, correction, deletion and what remains in short-term chat history. A memory change flags the existing plan for review; it does not publish a Garmin workout.

On **Plan**, **Garmin Connect planned runs** shows the latest imported future running workouts with their Garmin steps. **Refresh from Garmin Connect** performs a read-only calendar and Garmin Coach scan, then replaces the local snapshot if all reads are unambiguous. The checked calendar spans one year from the refresh day; Garmin Coach is queried across five near-term weeks, subject to its generated-window limits. The local coaching draft remains separate. See [planned runs](PLANNED_RUNS_IMPORT.md). For a terminal refresh, run `python3 scripts/import_planned_runs.py`.

The bridge uses the documented [Claude Messages API](https://platform.claude.com/docs/en/api/messages/create) and [structured JSON outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs). A cancellable local worker sends bounded saved context; it has no tools or Garmin credentials. GPS samples remain local. The API key is sent only in the Anthropic request header and is never returned to the browser or written into saved chat. Only validated answers/proposals are saved locally. One request can run at a time, with a two-minute deadline and cancellation. Pending requests reconnect after page reload while the same server is running. Completing an answer leaves unsaved workspace form edits in place.

If the key is missing or the API fails, the conversation displays a specific error; saved data stays available. Check `.env`, your Anthropic API account and model access, then retry. API usage may incur charges. The local recurring runner also uses this key.

For a full sample-data design preview, start `python3 scripts/qa_server.py` and open http://127.0.0.1:8766. This disposable demo has **mock** coach replies and cannot verify model access. The normal dashboard remains at port 8765 with the existing saved data.

## Garmin authentication and client setup

```bash
python3 scripts/install_mcp.py
./.venv-mcp/bin/garmin-mcp-auth
```

The installer checks the pinned source archive SHA-256 and uses requirements-mcp.lock, including build tooling. Enter email, password and MFA **yourself in the authentication tool**. Never paste them into chat or shared configuration. Upstream stores OAuth tokens in `~/.garminconnect`; that directory is outside this project and is not copied, served or packaged. Only the MCP authentication layer consumes tokens.

At `Email:`, type the email you use for Garmin Connect and press Enter. At `Password:`, type your Garmin password and press Enter; the terminal intentionally shows no characters while you type. If asked for MFA, enter the code Garmin sends you. Wait for `Authentication successful!` before reporting success. Ctrl-C cancels login; a `KeyboardInterrupt` traceback means it was interrupted, not that the installation failed. An activated `(.venv-mcp)` prompt is fine: the same command above still works, and activation is optional.

Verify/recover in your own terminal:

```bash
./.venv-mcp/bin/garmin-mcp-auth --verify
./.venv-mcp/bin/garmin-mcp-auth --force-reauth
```

Choose one client setup:

```bash
python3 scripts/configure_client.py codex
python3 scripts/configure_client.py claude
```

These user-run commands modify the chosen client's local configuration through its CLI, storing only the Python executable and shared read-only MCP launcher. Restart the agent session and inspect its MCP tools/status. The wrapper exposes 16 read tools, including original FIT records/splits, scheduled/Garmin Coach workouts and bounded training/recovery reads. Existing registrations already use that wrapper; a new session loads the updated list. Only the guarded workout utility exposes upload/scheduling. Deletion is never exposed.

Installation and schema discovery were tested without an account. Login/data access are separate acceptance checks. GitHub authentication is not needed for this public pinned implementation.

## Manual coach update

Open this folder in your signed-in Codex/Claude Code session and paste the update prompt from START_HERE.txt or the dashboard. Follow COACHING.md. The utility sequence is:

```bash
python3 -m coach workout reconcile
python3 -m coach collect
# The coding agent reads data/pending/context.json and writes the result.
python3 -m coach commit data/pending/coaching-result.json
python3 -m coach validate
```

Skip remote reconciliation when the registry is empty. Context includes profile, observations, derived weekly totals, previous plan/summary, feedback, registry and run status. Agent output contains exactly coach_summary and training_plan, matching schemas/coach-output.schema.json. Invalid results, references, units, revisions, lost completed sessions or changed inputs cannot replace the previous valid snapshot. A readable summary.md accompanies it.

Activity reads cover up to 42 days and five pages of 100. Running subclasses are retained when returned. Wellness covers up to 14 nights through get_sleep_summary_range. Only sleep duration/score, overnight HRV and resting HR are stored. The upstream range tool skips missing/failed nights; gaps remain missing and cannot identify a specific failure or unsupported metric. Local-only timestamps use the profile timezone with an explicit assumption. DST gaps/overlaps preserve the original text and leave the absolute timestamp missing.

The completed dashboard import additionally reads daily readiness, status/load/ratio history, running VO₂ and explicit dated resting HR, plus current focus targets/readiness factors/recovery. For a later requested refresh:

```bash
python3 scripts/import_training.py --days 42
python3 -m coach validate
```

Training history is bounded to 1–42 days; base activity/wellness windows remain 42/14 days. Known routes are preserved and only new activity IDs request FIT detail. Source responses archive privately; dates/units/precision and source methods are validated before saving. Marked carried-forward VO₂ and cycling observations do not become running history. Missing acute targets are reported without borrowing chronic targets. The plan/profile are preserved and coaching review is flagged. It performs no Garmin writes. For separate inspection/staging, use --inspect or --stage --days 42, then --apply-staged; changed saved inputs reject application.

For an explicitly requested route refresh of already saved runs:

```bash
python3 scripts/import_routes.py --limit 12
python3 -m coach validate
```

This reads original FIT session/record messages and recorded splits for at most the selected 12 existing IDs. Each activity is bounded to five pages of 5,000 records; retained samples are bounded to 4,000 with endpoints and GPS gaps preserved. FIT units are checked before semicircle/meters/seconds conversions. Recorded elapsed duration includes pauses and stays separate from active summary time. Importing preserves the current snapshot/profile/plan and existing training observations; changed inputs or failed reads retain the previous sidecar. It does not fetch broader training metrics or write to Garmin.

## Authorize one workout

After a confirmed profile and reviewed **real** plan, choose `selected_workouts` in settings and have the coach review that change. An explicit **Create a workout** coach message now stages and sends one valid workout to Garmin Connect automatically, using that message as the authorization; the chat shows the resulting delivery state. A request for a draft, idea or suggestion follows **Preview workout → Approve exact workout → Plan → Send to Garmin Connect**. For a saved plan session or manual one-off, select an exact session revision in the dashboard or terminal:

```bash
python3 -m coach workout draft SESSION_ID
python3 -m coach workout select SESSION_ID
python3 -m coach workout publish SESSION_ID
```

Selection does not upload. Publishing checks policy, exact session and current input fingerprint. It creates a uniquely marked workout, verifies returned steps/date and records workout and calendar-entry IDs. Repeated commands reuse matching IDs. Pending changes block publishing until review. Fixture/illustrative plans are always blocked.

Timeouts/crashes leave an uncertain state. `python3 -m coach workout reconcile` resolves only through positive library/calendar evidence. Negative or ambiguous listings never permit an automatic retry: inspect Connect before proceeding. The upstream calendar tool does not distinguish every empty result from a GraphQL failure. Published edits are held for manual reconciliation; no automatic deletion/replacement is implemented. Completed or unrelated entries are preserved.

`automatic` is a separate explicit policy choice. After successful reviewed real updates, the optional runner can publish at most seven future planned sessions under the same safeguards and client permissions. Watch transfer remains separate.

## Local Claude planning schedule

Scheduling is disabled by default. Once enabled, the local job (`local.ai-running-coach` on macOS) polls every 15 minutes and performs one due refresh per athlete-local day after 07:00. Sunday's due review may draft the next week. Under the `selected_workouts` policy the scheduled runner does not publish workouts; an explicit coach chat creation request is delivered immediately through the guarded path.

After Garmin login, profile confirmation and a real manual update, test:

```bash
python3 -m coach.runner
```

The runner uses read-only Garmin MCP collection, the private Anthropic key, the [Claude planning prompt](../prompts/coach-plan-api.md) and a strict output schema. Garmin credentials stay with the MCP authentication layer. Narrow status/error reasons stay in private run_status.json. Input/generation changes during reasoning reject output.

One lock permits one run. The configurable overall timeout (default five minutes, maximum fifteen) covers reads, reasoning and automatic writes; timeouts terminate the agent process group. Authentication failure stops the run. Scheduled attempts occur at most once per local day; failure requires manual recovery. Unchanged inputs retain the plan; a weekly planning boundary can request the next week once.

The following commands regenerate templates or enable the local flag after a successful real manual runner update:

```bash
python3 -m coach.runner --templates
python3 -m coach.runner --enable
```

On macOS, install the job with:

```bash
launchctl bootstrap "gui/$(id -u)" "$PWD/data/scheduler/local.ai-running-coach.plist"
```

On Linux, review data/scheduler/cron.example and use `crontab -e`. Templates poll every 15 minutes; ZoneInfo uses the athlete timezone for 07:00 daily and Sunday planning, respecting DST. A computer that is off cannot run. The next poll after waking/loading handles today's overdue update; it does not backfill every missed day.

Disable:

```bash
python3 -m coach.runner --disable
```

Installed polls then exit without updating. Remove the macOS job with `launchctl bootout "gui/$(id -u)/local.ai-running-coach"`, or remove the cron line with `crontab -e`. Build/launch installs no OS job. Moving the folder requires regenerating templates and client configuration.

## Checks and troubleshooting

```bash
python3 -m unittest discover -v
node --check web/app.js
node --check web/map.js
node tests/map-geometry.cjs
.venv-mcp/bin/python scripts/verify_mcp_contract.py
python3 -m coach status
```

Tests use fixtures and mocked account responses. The contract check runs the actual pinned tools with a synthetic API client. For browser checks install Playwright 1.63.0, start `python3 scripts/qa_server.py`, then run `node scripts/qa-dashboard.cjs` with Playwright resolvable and Chrome installed. COACH_PLAYWRIGHT_MODULE, COACH_CHROME and COACH_QA_URL accept explicit test paths. The QA server is disposable; screenshots are synthetic only and conversation responses are mocked. `scripts/qa-saved-dashboard.cjs` renders cached real observations through browser interception without training-state writes or outgoing network, keeping screenshots private. `node scripts/qa-map.cjs` checks tile/route alignment with intercepted synthetic images and no provider requests. `python3 scripts/qa_coach_chat.py` separately calls the actual Claude API with a temporary synthetic store when the key is configured.

* Port occupied: use `./launch.sh --port 8767`.
* Missing/changed tool: run `python3 -m coach discover`; retain the tested commit and inspect schemas.
* Expired auth/MFA: use the authentication tool in your terminal, restart MCP, retry once manually.
* Read failure: last valid snapshot stays visible as cached; check tokens/connectivity. Wellness gaps stay missing.
* Invalid result: correct the staged JSON and rerun commit, without hand-overwriting current files.
* Runner blocked: read status, resolve the named account/profile/permission step, test one manual run.

## Share

```bash
python3 scripts/package_release.py
```

Share dist/ai-running-coach-prototype.zip and its SHA-256 file. Source, docs, locks, schemas, tests and synthetic evidence are allowlisted. Private data/tokens/logs/configuration, environments and upstream source are excluded. Fresh extraction init/demo/validate is checked. This is a prototype distribution; see [verification](VERIFICATION.md) for what has been checked with fixtures and with a real account.
