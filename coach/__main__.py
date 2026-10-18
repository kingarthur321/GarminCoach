import argparse
import json
import sys
from .core import Invalid, Store, load_json, now, require, validate_bundle
from .fixtures import make_demo
from .mcp import MCP, collect
from .workflow import accept, prepare
from .workouts import Workouts, compile_workout


def main():
    parser = argparse.ArgumentParser(description="Private AI running coach utilities (Python standard library)")
    parser.add_argument("--data-dir", help="Private data directory; default: project/data")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Copy unconfirmed example profile without overwriting state")
    sub.add_parser("demo", help="Save a clearly labeled synthetic snapshot; never overwrite real data")
    p = sub.add_parser("serve"); p.add_argument("--port", type=int, default=8765)
    sub.add_parser("validate")
    sub.add_parser("collect", help="Bounded real MCP reads; stage context without replacing current")
    sub.add_parser("context", help="Prepare context from already saved observations")
    p = sub.add_parser("commit"); p.add_argument("result", help="Agent JSON with coach_summary and training_plan")
    sub.add_parser("status")
    sub.add_parser("discover", help="Inspect actual MCP schemas; no authentication required")
    p = sub.add_parser("workout"); p.add_argument("action", choices=["stage-one-off", "draft", "select", "publish", "reconcile", "watch-verified"]); p.add_argument("session_id", nargs="?"); p.add_argument("--note")
    args = parser.parse_args()
    store = Store(args.data_dir)
    store.bootstrap()
    try:
        if args.command == "init":
            print("Private profile initialized; confirm your own settings in the dashboard.")
        elif args.command == "demo":
            with store.lock():
                old = store.current()
                require(old is None or old["activities"]["source"] == "fixture", "Cannot replace real Garmin data with a fixture")
                if old is None:
                    store.commit(make_demo(store.profile()))
            print("SYNTHETIC SAMPLE snapshot available. No personal workout authorized.")
        elif args.command == "serve":
            from .server import serve
            serve(store, args.port)
        elif args.command == "validate":
            bundle = store.current()
            require(bundle is not None, "No saved snapshot")
            bundle["feedback"] = store.read("feedback")
            details = store.read('dashboard_details')
            if details is not None:
                from .dashboard import validate_details
                validate_details(details,bundle)
            validate_bundle(bundle, store.profile(), details=details, archived_session_digests=store.archived_session_digests())
            print("Valid saved snapshot · " + bundle["activities"]["source"])
        elif args.command == "status":
            print(json.dumps(store.read("run_status"), indent=2))
        elif args.command == "discover":
            with MCP(writes=True) as client:
                print(json.dumps(client.schemas, indent=2))
        elif args.command == "collect":
            with store.lock():
                profile = store.profile()
                store.mark(last_attempt=now(), state="reading")
            with MCP() as client:
                observations = collect(client, profile)
            require(store.profile() == profile, "Profile changed while reading; collect again")
            with store.lock():
                store.mark(state="awaiting_coach", error=None, last_successful_read=now(),
                           blocked_steps=[] if profile["profile_confirmed"] else ["Athlete profile confirmation"])
            context = prepare(store, observations)
            print(f"Staged {len(context['observations']['activities']['items'])} real activities. Context: {store.path / 'pending/context.json'}")
        elif args.command == "context":
            prepare(store)
            print(store.path / "pending/context.json")
        elif args.command == "commit":
            accept(store, load_json(args.result))
            print("Validated agent summary and plan saved. Previous snapshot retained in history.")
        elif args.command == "workout":
            if args.action == "reconcile":
                with MCP(writes=False) as client:
                    registry = Workouts(store, client).reconcile_all()
                print(f"Reconciled {len(registry['entries'])} tracked revisions; no new writes.")
            else:
                require(args.session_id is not None, "Session ID required")
                if args.action == "stage-one-off":
                    s = Workouts(store, None).stage_one_off(load_json(args.session_id))
                    print("One-off local draft staged: " + s["id"])
                elif args.action == "draft":
                    s = Workouts.find(store.current(), args.session_id, store.read("workout_registry"))
                    from .core import atomic_json
                    atomic_json(store.path / "pending/workout-draft.json", compile_workout(s))
                    print("Local draft saved: " + str(store.path / "pending/workout-draft.json"))
                elif args.action == "select":
                    Workouts(store, None).select(args.session_id)
                    print("Exact revision authorized. Configured write policy still applies.")
                elif args.action == "watch-verified":
                    Workouts(store, None).watch_verified(args.session_id, args.note)
                    print("Watch confirmation saved as a user statement, separately from Connect evidence.")
                else:
                    with MCP(writes=True) as client:
                        entry = Workouts(store, client).publish(args.session_id)
                    print(f"Connect state: {entry['state']} · workout ID {entry['workout_id']} · calendar ID {entry['scheduled_entry_id']} · watch verification pending")
    except (Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        if args.command in ("collect", "commit", "workout"):
            with store.lock():
                store.mark(state="blocked" if args.command in ("collect", "workout") else "failed", last_attempt=now(), error=str(exc) if isinstance(exc, Invalid) else "Local operation failed; inspect files and rerun validation")
            prefix = "Update retained previous valid output: "
        else:
            prefix = "Could not start dashboard: " if args.command == "serve" else "Command failed: "
        print(prefix + (str(exc) if isinstance(exc, (Invalid, OSError)) else type(exc).__name__), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
