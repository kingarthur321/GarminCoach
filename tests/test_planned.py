"""Garmin planned-run import contract using only synthetic MCP responses."""
import tempfile
import json
import threading
import unittest
import urllib.request
from contextlib import nullcontext
from datetime import date, timedelta

from coach.core import Invalid, Store
from coach.chat import context
from coach.fixtures import make_demo
from coach.planned import import_planned, read_planned
from coach.server import make_server
from coach.workflow import prepare


class PlannedClient:
    def __init__(self, *, ambiguous=False):
        self.calls = []
        self.ambiguous = ambiguous
        self.run_date = None

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "get_scheduled_workouts":
            if self.ambiguous:
                return {"empty": True, "message": "No scheduled workouts found or error querying data."}
            self.run_date = (date.fromisoformat(args["start_date"]) + timedelta(days=2)).isoformat()
            return {"count": 2, "date_range": {"start": args["start_date"], "end": args["end_date"]},
                    "scheduled_workouts": [
                        {"date": self.run_date, "name": "Easy run", "sport": "running", "workout_id": 11,
                         "scheduled_workout_id": 51, "completed": False, "estimated_duration_seconds": 1800},
                        {"date": (date.fromisoformat(self.run_date)+timedelta(days=1)).isoformat(), "name": "Gym", "sport": "strength_training", "workout_id": 12,
                         "scheduled_workout_id": 52, "completed": False}]}
        if name == "get_garmin_coach_workouts":
            return {"count": 1, "workouts": [{"date": self.run_date, "name": "Easy run", "sport": "running", "workout_id": 11,
                                             "completed": False}]}
        if name == "get_workout_by_id":
            return {"id": args["workout_id"], "name": "Easy run", "sport": "running",
                    "segments": [{"steps": [{"type": "warmup", "end_condition": "time", "end_condition_value": 300}]}]}
        raise AssertionError(name)


class PlannedImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.bootstrap()

    def test_import_deduplicates_coach_and_calendar_and_ignores_other_sports(self):
        client = PlannedClient()
        result = import_planned(self.store, client, start=date(2026, 10, 3), horizon_days=30, coach_weeks=2)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["source_kind"], "calendar")
        self.assertEqual(result["items"][0]["scheduled_workout_id"], 51)
        self.assertEqual(len(result["items"][0]["segments"][0]["steps"]), 1)
        self.assertEqual(read_planned(self.store), result)
        self.assertEqual([name for name, _ in client.calls].count("get_workout_by_id"), 1)
        self.assertFalse(any(name in ("upload_workout", "schedule_workout") for name, _ in client.calls))

    def test_ambiguous_remote_read_keeps_previous_snapshot(self):
        before = import_planned(self.store, PlannedClient(), start=date(2026, 10, 3), horizon_days=30, coach_weeks=1)
        with self.assertRaisesRegex(Invalid, "ambiguous"):
            import_planned(self.store, PlannedClient(ambiguous=True), start=date(2026, 10, 3), horizon_days=30, coach_weeks=1)
        self.assertEqual(read_planned(self.store), before)

    def test_same_origin_refresh_reads_garmin_and_saves_dashboard_sidecar(self):
        with self.store.lock():
            self.store.commit(make_demo(self.store.profile()))
        client = PlannedClient()
        server = make_server(self.store, 0, workout_read_factory=lambda: nullcontext(client))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/api/dashboard") as response:
            token = json.load(response)["csrf_token"]
        request = urllib.request.Request(base + "/api/garmin/planned/refresh", b"{}",
                                         {"Content-Type": "application/json", "X-Coach-Token": token, "Origin": base}, method="POST")
        with urllib.request.urlopen(request) as response:
            self.assertEqual(json.load(response)["count"], 1)
        with urllib.request.urlopen(base + "/api/dashboard") as response:
            self.assertEqual(len(json.load(response)["garmin_planned_runs"]["items"]), 1)
        self.assertFalse(any(name in ("upload_workout", "schedule_workout") for name, _ in client.calls))

    def test_chat_context_cites_current_garmin_calendar_and_occupies_its_date(self):
        with self.store.lock():
            self.store.commit(make_demo(self.store.profile()))
        import_planned(self.store, PlannedClient(), start=date(2026, 10, 3), horizon_days=30, coach_weeks=1)
        payload, refs, _ = context(self.store, None)
        self.assertIn("2026-10-05", payload["existing_workout_dates"])
        self.assertIn("garmin_planned:2026-10-05:11", refs)
        self.assertEqual(payload["garmin_planned_runs"]["items"][0]["reference"], "garmin_planned:2026-10-05:11")
        self.assertEqual(prepare(self.store)["garmin_planned_runs"]["items"][0]["date"], "2026-10-05")
