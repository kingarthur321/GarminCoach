"""Optional bounded Claude coaching runner. Off until enabled."""
import argparse
import copy
import json
import os
import plistlib
import shutil
import signal
from contextlib import contextmanager
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import ROOT, DAYS, Invalid, Store, atomic_json, load_json, now, require
from .mcp import MCP, collect
from .workflow import accept, prepare
from .workouts import Workouts


def request_claude_plan(context, week_due, timeout):
    from .anthropic_api import PROMPT_SEPARATOR, request_message
    system = (ROOT / "prompts/coach-plan-api.md").read_text(encoding="utf-8")
    question = ("Prepare the next local Monday–Sunday week." if week_due else
                "Review the current week and preserve it unless new observations justify a change.")
    prompt = system + PROMPT_SEPARATOR + json.dumps({"question": question, "context": {**context, "now": now()}}, ensure_ascii=False, allow_nan=False)
    return request_message(prompt, timeout, schema_path=ROOT / "schemas/coach-output.schema.json", max_tokens=12000)


def command(result_path):
    return [shutil.which("codex") or "codex", "exec", "--ignore-user-config", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral", "--cd", str(ROOT),
            "--output-schema", str(ROOT / "schemas/coach-output.schema.json"), "--output-last-message", str(result_path),
            "-c", "mcp_servers.garmin.command=" + json.dumps(sys.executable),
            "-c", "mcp_servers.garmin.args=" + json.dumps([str(ROOT / "scripts/garmin_server.py")]), "-"]


def invoke_agent(cmd, prompt, timeout):
    # Agent traces can contain private Garmin records. Discard them; save only a
    # narrow local status with return code and timestamps, never credentials.
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               text=True, cwd=ROOT, start_new_session=True)
    try:
        process.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise Invalid("Coding agent timed out; last valid snapshot retained") from None
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        raise
    require(process.returncode == 0, "Coding client failed (authentication, account limits, MCP permission or output error). Last valid snapshot retained.")


def run(store, fixture=False, scheduled=False, agent_command=None, refresh=True):
    with store.lock("runner", blocking=False):
        p = store.profile()
        require(p["profile_confirmed"] or fixture, "Confirm athlete profile before recurring coaching")
        local = datetime.now(ZoneInfo(p["timezone"]))
        status = store.read("run_status")
        if scheduled:
            require(p["automation"]["enabled"], "Local scheduling is disabled")
            if local.strftime("%H:%M") < p["automation"]["daily_local_time"] or status.get("last_daily_date") == local.date().isoformat():
                return "Not due; no update performed"
        deadline_minutes = p["automation"]["max_agent_run_minutes"]
        if scheduled and status.get("last_scheduled_attempt_date") == local.date().isoformat():
            return "Already attempted today; waiting for manual recovery"
        with store.lock():
            store.mark(state="running", last_attempt=now(), **({"last_scheduled_attempt_date": local.date().isoformat()} if scheduled else {}))
        try:
            import time
            started = time.monotonic()
            if not refresh:
                require(not scheduled and not fixture, "Saved-only review is for a manual real-data recovery")
                bundle = store.current()
                require(bundle is not None and bundle["activities"]["source"] == "garmin", "Saved Garmin observations are required")
                observations = {key: copy.deepcopy(bundle[key]) for key in ("activities", "wellness")}
            elif fixture:
                bundle = store.current()
                require(bundle is not None and bundle["activities"]["source"] == "fixture", "Fixture runner requires a synthetic snapshot")
                observations = {k: copy.deepcopy(bundle[k]) for k in ("activities", "wellness")}
            else:
                # One read-only MCP session refreshes the Connect schedule and
                # bounded Garmin observations before Claude sees the context.
                with MCP() as client:
                    if store.read("workout_registry")["entries"]:
                        Workouts(store, client).reconcile_all()
                    from .planned import import_planned
                    from .training import apply_training, stage_training
                    import_planned(store, client, start=local.date())
                    staged = stage_training(store, client, 42)
                    apply_training(store, staged)
                    refreshed = store.current()
                    observations = {key: copy.deepcopy(refreshed[key]) for key in ("activities", "wellness")}
            require(store.profile() == p, "Profile changed during reading; retry manually")
            context = prepare(store, observations)
            input_hash = store.fingerprint(observations)
            from datetime import timedelta
            next_monday = (local.date() - timedelta(days=local.weekday()) + timedelta(days=7)).isoformat()
            week_due = DAYS[local.weekday()] == p["automation"]["weekly_plan_day"] and (context.get("current_plan") or {}).get("week_start") != next_monday and store.read("run_status").get("last_weekly_draft_date") != local.date().isoformat()
            if input_hash == status.get("last_input_fingerprint") and not store.read("run_status").get("review_required") and not week_due:
                with store.lock():
                    store.mark(state="success", last_daily_date=local.date().isoformat(), error=None)
                return "No changed coaching inputs; existing plan retained"
            context_path = store.path / "pending/context.json"
            result_path = store.path / "pending/runner-result.json"
            # Never accept a stale result when the client failed to write output.
            if result_path.exists():
                result_path.unlink()
            prompt = (ROOT / "prompts/coach-update.md").read_text() + "\nPrivate staged context: " + str(context_path) + "\n" + ("Weekly draft is due for the next athlete-local week.\n" if week_due else "Daily review: preserve the existing week unless changed inputs justify edits.\n")
            remaining = deadline_minutes * 60 - (time.monotonic() - started)
            require(remaining > 0, "Read budget exhausted; last valid output retained")
            if fixture or agent_command is not None:
                invoke_agent(agent_command or command(result_path), prompt, remaining)
            else:
                atomic_json(result_path, request_claude_plan(context, week_due, remaining))
            accept(store, load_json(result_path), context)
            with store.lock():
                store.mark(last_daily_date=local.date().isoformat(), **({"fixture_runner_verified": True} if fixture else {"manual_runner_verified": True}), error=None,
                           **({"last_weekly_draft_date": local.date().isoformat()} if week_due else {}))
            if not fixture and p["automation"]["garmin_write_policy"] == "automatic":
                sessions = store.current()["training_plan"]["sessions"]
                with MCP(writes=True) as client:
                    for s in [s for s in sessions if s["state"] == "planned" and s["date"] >= local.date().isoformat()][:7]:
                        Workouts(store, client).publish(s["id"])
            return "Validated coaching update saved"
        except Exception as exc:
            with store.lock():
                store.mark(state="blocked" if not fixture else "failed", last_attempt=now(), error=str(exc) if isinstance(exc, Invalid) else "Invalid agent output or local failure; previous output retained")
            raise


def schedule_templates(store):
    folder = store.path / "scheduler"
    folder.mkdir(exist_ok=True, mode=0o700)
    python = str(Path(sys.executable).resolve())
    args = [python, "-m", "coach.runner", "--data-dir", str(store.path.resolve()), "--scheduled"]
    spec = {"Label": "local.ai-running-coach", "ProgramArguments": args, "WorkingDirectory": str(ROOT),
            "RunAtLoad": True, "StartInterval": 900,
            "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
            "StandardOutPath": str(folder / "scheduler.log"), "StandardErrorPath": str(folder / "scheduler-error.log")}
    (folder / "local.ai-running-coach.plist").write_bytes(plistlib.dumps(spec))
    # Polling + ZoneInfo implements athlete-local daily/weekly times across DST.
    import shlex
    cron = "*/15 * * * * cd " + shlex.quote(str(ROOT)) + " && " + " ".join(shlex.quote(x) for x in args) + "\n"
    (folder / "cron.example").write_text(cron)
    return folder


def set_scheduler_enabled(store, enabled):
    """Change only the local timer; it is not a new coaching input."""
    with store.lock():
        profile = store.profile()
        status = store.read("run_status")
        before = store.fingerprint()
        if enabled:
            require(profile["profile_confirmed"] and status.get("manual_runner_verified") and not status.get("review_required")
                    and status.get("last_input_fingerprint") == before,
                    "Complete a current real coaching review before enabling the scheduler")
        if profile["automation"]["enabled"] == enabled:
            return
        profile["automation"]["enabled"] = enabled
        store.write("athlete_profile", profile)
        if status.get("last_input_fingerprint") == before and not status.get("review_required"):
            store.mark(last_input_fingerprint=store.fingerprint())


def main():
    parser = argparse.ArgumentParser(description="Optional local Claude coaching runner (disabled initially)")
    parser.add_argument("--data-dir")
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--fixture", action="store_true", help="Explicit sample-only client smoke test")
    parser.add_argument("--review-saved", action="store_true", help="Retry Claude review of already imported real observations without another Garmin read")
    parser.add_argument("--templates", action="store_true")
    parser.add_argument("--enable", action="store_true", help="Enable the local opt-in flag after a manual runner check; does not install an OS job")
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    store = Store(args.data_dir); store.bootstrap()
    try:
        if args.templates:
            print("Disabled scheduler templates: " + str(schedule_templates(store)))
        elif args.enable or args.disable:
            set_scheduler_enabled(store, args.enable)
            print("Local scheduling " + ("enabled; install the generated OS template explicitly" if args.enable else "disabled; installed jobs exit without running"))
        else:
            with deadline(store.profile()["automation"]["max_agent_run_minutes"] * 60):
                print(run(store, args.fixture, args.scheduled, refresh=not args.review_saved))
    except (Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc) if isinstance(exc, Invalid) else "Runner failed; inspect local state", file=sys.stderr)
        return 1
    return 0


@contextmanager
def deadline(seconds):
    """Overall timeout covers reads, reasoning and automatic writes on macOS/Linux."""
    def expired(*_):
        raise Invalid("Overall runner timeout; last valid snapshot retained and any pending write requires reconciliation")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


if __name__ == "__main__":
    sys.exit(main())
