"""Behavioral verification. All account responses here are synthetic mocks."""
import copy
import json
from contextlib import nullcontext
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from coach.core import Invalid, Store, atomic_json, digest, iso_date, local_timestamp, normalize_activities, normalize_wellness, now, validate_bundle, weekly_distance
from coach.chat import CoachChat, wants_workout_created
from coach.fixtures import make_demo
from coach.mcp import MCP, collect
from coach.runner import deadline, invoke_agent, run, set_scheduler_enabled
from coach.server import make_server
from coach.workflow import accept, prepare
from coach.workouts import Workouts, compile_workout, session_key
from coach.proposals import review_chat_proposal


class FakeGarmin:
    """Curated response formats from the inspected pinned upstream implementation."""
    schemas = {n: {} for n in ("get_activities_by_date", "get_sleep_summary_range", "get_workouts", "get_workout_by_id", "get_scheduled_workouts", "upload_workout", "schedule_workout")}
    def __init__(self):
        self.library = []
        self.calendar = []
        self.uploads = 0
        self.schedules = 0
        self.timeout_upload = False
        self.timeout_schedule = False
        self.hide_library = False
        self.ambiguous_calendar = False
        self.calls = []
        self.payload = None

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "get_workouts":
            return {"workouts": [] if self.hide_library else self.library}
        if name == "get_workout_by_id":
            p = self.payload
            rows = []
            for t in p["workoutSegments"][0]["workoutSteps"]:
                row = {"type": t["stepType"]["stepTypeKey"], "description": t["description"], "end_condition": t["endCondition"]["conditionTypeKey"], "end_condition_value": t["endConditionValue"]}
                if "targetValueOne" in t:
                    row.update(target_type="heart.rate.zone", target_value_low=t["targetValueOne"], target_value_high=t["targetValueTwo"])
                rows.append(row)
            return {"id": 101, "name": p["workoutName"], "sport": "running", "segments": [{"steps": rows}]}
        if name == "get_scheduled_workouts":
            if self.ambiguous_calendar:
                return {"empty": True, "message": "No scheduled workouts found or error querying data."}
            return {"scheduled_workouts": self.calendar}
        if name == "upload_workout":
            self.uploads += 1
            self.payload = copy.deepcopy(args["workout_data"])
            self.library.append({"id": 101, "name": self.payload["workoutName"], "sport": "running"})
            if self.timeout_upload:
                self.timeout_upload = False
                raise Invalid("Simulated timeout after successful remote upload")
            return {"status": "success", "workout_id": 101}
        if name == "schedule_workout":
            self.schedules += 1
            self.calendar.append({"date": args["calendar_date"], "workout_id": args["workout_id"], "scheduled_workout_id": 201, "completed": False})
            if self.timeout_schedule:
                self.timeout_schedule = False
                raise Invalid("Simulated timeout after calendar insertion")
            return {"status": "success"}
        raise AssertionError(name)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.bootstrap()
        with self.store.lock():
            self.store.commit(make_demo(self.store.profile()))

    def mocked_real(self, policy="selected_workouts"):
        # Tests only: simulated provenance exercises real-path policy branches.
        p = self.store.profile(); p["profile_confirmed"] = True
        p["automation"]["garmin_write_policy"] = policy
        self.store.save_profile(p)
        b = self.store.current()
        for key in ("activities", "wellness", "coach_summary", "training_plan"):
            b[key]["source"] = "garmin"
        for a in b["activities"]["items"]:
            a["source"] = "garmin"
        for n in b["wellness"]["items"]:
            for v in n["values"].values():
                v["source"] = "garmin"
        b["training_plan"].update(illustrative=False, revision_id="mocked_real_revision")
        with self.store.lock():
            self.store.commit(b)
        return b["training_plan"]["sessions"][0]["id"]


class ChatProposalTests(StoreCase):
    def chat_with_workout(self, *, same_plan_date=False):
        session = self.store.current()["training_plan"]["sessions"][0]
        draft = {key: copy.deepcopy(session[key]) for key in ("title", "date", "rationale", "references", "steps")}
        if not same_plan_date:
            draft["date"] = (iso_date(draft["date"]) + timedelta(days=7)).isoformat()
        answer = {"answer": "A controlled workout draft.", "references": draft["references"], "action": "none", "workout": draft}
        chat = CoachChat(self.store, executor=lambda *_: answer)
        job = chat.start({"message": "Create a workout for me"})
        for _ in range(100):
            result = chat.get(job["id"])
            if result["state"] != "working":
                break
            time.sleep(.01)
        self.assertEqual(result["state"], "complete", result.get("error"))
        return job["id"]

    def test_chat_review_select_publish_and_watch_receipt_keep_week_intact(self):
        self.mocked_real()
        before = digest(self.store.current())
        chat_id = self.chat_with_workout()
        session = review_chat_proposal(self.store, chat_id)
        self.assertEqual(digest(self.store.current()), before)
        self.assertIn(session_key(session), self.store.read("workout_registry")["authorizations"])
        self.assertEqual(review_chat_proposal(self.store, chat_id), session)
        garmin = FakeGarmin()
        writer = Workouts(self.store, garmin)
        entry = writer.publish(session["id"])
        self.assertEqual(entry["state"], "scheduled")
        self.assertEqual(writer.publish(session["id"])["state"], "scheduled")
        self.assertEqual((garmin.uploads, garmin.schedules), (1, 1))
        writer.watch_verified(session["id"], "Watch test: date and steps visible")
        self.assertEqual(self.store.read("workout_registry")["entries"][session_key(session)]["watch_evidence"]["source"], "user_statement")

    def test_changed_inputs_or_calendar_collision_block_review(self):
        self.mocked_real()
        conflicting = self.chat_with_workout(same_plan_date=True)
        with self.assertRaisesRegex(Invalid, "planned workout already uses"):
            review_chat_proposal(self.store, conflicting)
        fresh = self.chat_with_workout()
        self.store.feedback({"text": "Gym moved", "feeling": "schedule"})
        with self.assertRaisesRegex(Invalid, "review changed inputs"):
            review_chat_proposal(self.store, fresh)
        self.assertFalse(self.store.read("workout_registry").get("one_off_sessions"))


class ChatAutoSendTests(StoreCase):
    def test_explicit_creation_schedules_once_through_guarded_garmin(self):
        self.mocked_real()
        session = self.store.current()["training_plan"]["sessions"][0]
        draft = {key: copy.deepcopy(session[key]) for key in ("title", "date", "rationale", "references", "steps")}
        draft["date"] = (iso_date(session["date"]) + timedelta(days=7)).isoformat()
        answer = {"answer": "A controlled workout for your next open running day.", "references": draft["references"],
                  "action": "none", "workout": draft}
        fake = FakeGarmin()
        server = make_server(self.store, 0, chat_executor=lambda *_: copy.deepcopy(answer),
                             workout_client_factory=lambda: nullcontext(fake))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/api/dashboard") as response:
            token = json.load(response)["csrf_token"]
        request = urllib.request.Request(base + "/api/coach", json.dumps({"message": "Create a workout for my next running day", "auto_send": True}).encode(),
                                         headers={"Content-Type": "application/json", "X-Coach-Token": token, "Origin": base})
        with urllib.request.urlopen(request) as response:
            job = json.load(response)
        for _ in range(200):
            with urllib.request.urlopen(base + "/api/coach/job?id=" + job["id"]) as response:
                result = json.load(response)
            if result["state"] != "working":
                break
            time.sleep(.01)
        self.assertEqual(result["state"], "complete", result.get("error"))
        self.assertEqual(result["result"]["delivery"]["state"], "scheduled")
        self.assertEqual((fake.uploads, fake.schedules), (1, 1))
        self.assertEqual(self.store.read("coach_chat")["items"][-1]["delivery"]["state"], "scheduled")
        self.assertEqual(Workouts(self.store, fake).publish(result["result"]["delivery"]["session_id"])["state"], "scheduled")
        self.assertEqual((fake.uploads, fake.schedules), (1, 1))

    def test_review_flag_blocks_explicit_auto_send_before_garmin_write(self):
        self.mocked_real()
        self.store.mark(review_required=True)
        session = self.store.current()["training_plan"]["sessions"][0]
        draft = {key: copy.deepcopy(session[key]) for key in ("title", "date", "rationale", "references", "steps")}
        draft["date"] = (iso_date(session["date"]) + timedelta(days=7)).isoformat()
        answer = {"answer": "A proposed workout.", "references": draft["references"], "action": "none", "workout": draft}
        fake = FakeGarmin()
        def publish(chat_id):
            review_chat_proposal(self.store, chat_id, auto_request=True)
            return Workouts(self.store, fake).publish("oneoff-chat-" + chat_id)
        chat = CoachChat(self.store, executor=lambda *_: answer, workout_publisher=publish)
        job = chat.start({"message": "Create a workout for me", "auto_send": True})
        for _ in range(200):
            result = chat.get(job["id"])
            if result["state"] != "working":
                break
            time.sleep(.01)
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["result"]["delivery"]["state"], "blocked")
        self.assertIn("review", result["result"]["delivery"]["message"].lower())
        self.assertEqual((fake.uploads, fake.schedules), (0, 0))
        self.assertFalse(self.store.read("workout_registry").get("one_off_sessions"))

    def test_draft_question_does_not_authorize_delivery(self):
        self.assertTrue(wants_workout_created("Can you create a workout for me?"))
        self.assertTrue(wants_workout_created("Create a workout for me"))
        self.assertFalse(wants_workout_created("Draft a workout idea for me"))
        self.assertFalse(wants_workout_created("Do not send a workout; just suggest one"))
        self.assertFalse(wants_workout_created("Why did you create a workout?"))
        chat = CoachChat(self.store, executor=lambda *_: {"answer": "Sample", "references": [], "action": "none", "workout": None})
        with self.assertRaisesRegex(Invalid, "explicitly"):
            chat.start({"message": "What did my last run show?", "auto_send": True})


class ChatProposalSafetyTests(StoreCase):
    chat_with_workout = ChatProposalTests.chat_with_workout

    def test_changed_saved_week_blocks_old_proposal_even_when_inputs_match(self):
        self.mocked_real()
        chat_id = self.chat_with_workout()
        fingerprint = self.store.fingerprint()
        bundle = self.store.current()
        bundle["training_plan"]["revision_id"] = "mocked_second_revision"
        with self.store.lock():
            self.store.commit(bundle)
        self.assertEqual(self.store.fingerprint(), fingerprint)
        with self.assertRaisesRegex(Invalid, "Saved training week changed"):
            review_chat_proposal(self.store, chat_id)

    def test_changed_saved_week_after_approval_blocks_garmin_send(self):
        self.mocked_real()
        session = review_chat_proposal(self.store, self.chat_with_workout())
        bundle = self.store.current()
        bundle["training_plan"]["revision_id"] = "mocked_after_approval"
        with self.store.lock():
            self.store.commit(bundle)
        garmin = FakeGarmin()
        with self.assertRaisesRegex(Invalid, "Saved training week changed since workout approval"):
            Workouts(self.store, garmin).publish(session["id"])
        self.assertEqual((garmin.uploads, garmin.schedules), (0, 0))

    def test_existing_garmin_planned_run_blocks_chat_date(self):
        self.mocked_real()
        session = self.store.current()["training_plan"]["sessions"][0]
        day = (iso_date(session["date"]) + timedelta(days=7)).isoformat()
        with self.store.lock():
            self.store.write("garmin_planned_runs", {"version": 1, "source": "garmin", "imported_at": now(),
                "window": {"start": day, "end": day}, "coach_week_dates": [], "coach_visibility": "no_workouts_reported",
                "items": [{"date": day, "name": "Existing run", "source_kind": "calendar", "workout_id": 123,
                           "workout_uuid": None}]})
        chat_id = self.chat_with_workout()
        with self.assertRaisesRegex(Invalid, "Garmin Connect planned run already uses"):
            review_chat_proposal(self.store, chat_id)

    def test_fixture_or_missing_workout_cannot_be_approved(self):
        chat_id = self.chat_with_workout()
        with self.assertRaisesRegex(Invalid, "confirmed Garmin"):
            review_chat_proposal(self.store, chat_id)
        self.mocked_real(policy="draft_only")
        chat_id = self.chat_with_workout()
        with self.assertRaisesRegex(Invalid, "selected_workouts"):
            review_chat_proposal(self.store, chat_id)


class NormalizationTests(unittest.TestCase):
    def rows(self):
        return [{"id": 1, "type": "running", "start_time": "2026-09-01T23:30:00+00:00", "distance_meters": 5000, "duration_seconds": 1500}, {"id": 1, "type": "running", "start_time": "2026-09-01T23:30:00+00:00", "distance_meters": 5000, "duration_seconds": 1500}]

    def test_conversion_timezone_and_dedup(self):
        a = normalize_activities(self.rows(), "Europe/Stockholm", "fixture", "2026-09-01", "2026-09-10")
        self.assertEqual(len(a["items"]), 1)
        self.assertEqual(a["items"][0]["distance_km"], 5)
        self.assertEqual(a["items"][0]["pace_seconds_per_km"], 300)
        self.assertEqual(a["items"][0]["local_date"], "2026-09-02")
        self.assertEqual(weekly_distance(a)[0]["week_start"], "2026-08-31")

    def test_dst_ambiguity_and_nonexistent_times_not_invented(self):
        for raw in ("2026-10-25T02:30:00", "2026-03-29T02:30:00"):
            started, basis = local_timestamp(raw, "Europe/Stockholm")
            self.assertIsNone(started); self.assertIn("ambiguous", basis)
        self.assertTrue(local_timestamp("2026-10-25T03:30:00", "Europe/Stockholm")[0].endswith("+01:00"))

    def test_missing_distance_and_duration_do_not_create_pace(self):
        rows = self.rows()[:1]; rows[0].pop("duration_seconds")
        a = normalize_activities(rows, "Europe/Stockholm", "fixture", "2026-09-01", "2026-09-10")
        self.assertIsNone(a["items"][0]["pace_seconds_per_km"])
        rows[0]["distance_meters"] = 0
        a = normalize_activities(rows, "Europe/Stockholm", "fixture", "2026-09-01", "2026-09-10")
        self.assertIsNone(a["items"][0]["pace_seconds_per_km"])

    def test_wellness_missing_and_correct_curated_field_names(self):
        w = normalize_wellness([{"date": "2026-09-01", "sleep_seconds": 27000, "avg_overnight_hrv": 51}], "fixture", "2026-09-01", "2026-09-03")
        self.assertEqual(w["items"][0]["values"]["hrv_ms"]["value"], 51)
        self.assertEqual(w["items"][0]["values"]["sleep_seconds"]["unit"], "s")
        self.assertIsNone(w["items"][1]["values"]["sleep_seconds"]["value"])
        self.assertEqual(w["items"][1]["values"]["hrv_ms"]["missing_reason"], "not_reported")

    def test_nonfinite_input_rejected(self):
        rows = self.rows(); rows[0]["distance_meters"] = float("nan")
        with self.assertRaises(Invalid):
            normalize_activities(rows, "Europe/Stockholm", "fixture", "2026-09-01", "2026-09-10")

    def test_collection_bounded_and_marks_missing_wellness(self):
        class Client:
            schemas = {"get_activities_by_date": {}}
            def __init__(self): self.calls = 0
            def call(self, *_): self.calls += 1; return {"activities": [], "has_more": True}
        from coach.core import ROOT, load_json
        client = Client(); result = collect(client, load_json(ROOT / "athlete_profile.example.json"))
        self.assertEqual(client.calls, 5); self.assertTrue(result["activities"]["coverage"]["truncated"])
        self.assertEqual(result["wellness"]["coverage"]["status"], "unsupported")

    def test_actual_stdio_client_protocol_with_synthetic_transport(self):
        with MCP(command=[sys.executable, str(Path(__file__).parent / "mock_transport.py")]) as client:
            data = client.call("get_activities_by_date", {"start_date": "2026-09-01", "end_date": "2026-09-02"})
            self.assertEqual(data["activities"][0]["id"], "synthetic-transport")
            with self.assertRaises(Invalid): client.call("get_activities_by_date", {"start_date": "2026-09-01", "end_date": "2026-09-02", "password": "should-never-be-an-argument"})

    def test_missing_required_mcp_argument_blocked(self):
        with MCP(command=[sys.executable, str(Path(__file__).parent / "mock_transport.py")]) as client:
            with self.assertRaises(Invalid): client.call("get_activities_by_date", {})


class ValidationTests(StoreCase):
    def test_successful_collect_clears_auth_error_without_replacing_snapshot(self):
        from coach.__main__ import main
        from coach.core import load_json
        with self.store.lock():
            self.store.mark(state="blocked", error="Previous authentication failure",
                            blocked_steps=["Garmin authentication", "Athlete profile confirmation"])
        pointer = self.store.read("current")
        observations = {
            "activities": normalize_activities([], "Europe/Stockholm", "garmin", "2026-09-01", "2026-10-01"),
            "wellness": normalize_wellness([], "garmin", "2026-09-18", "2026-10-01", "unavailable"),
        }
        with patch("sys.argv", ["coach", "--data-dir", self.temp.name, "collect"]), \
             patch("coach.__main__.MCP"), patch("coach.__main__.collect", return_value=observations), \
             patch("builtins.print"):
            self.assertEqual(main(), 0)
        context = load_json(self.store.path / "pending/context.json")
        self.assertEqual(context["run_status"]["state"], "awaiting_coach")
        self.assertIsNone(context["run_status"]["error"])
        self.assertEqual(context["run_status"]["blocked_steps"], ["Athlete profile confirmation"])
        self.assertIsNotNone(context["run_status"]["last_successful_read"])
        self.assertEqual(self.store.read("current"), pointer)

    def test_malformed_output_preserves_snapshot(self):
        pointer = self.store.read("current")
        b = self.store.current(); b["training_plan"]["sessions"][0]["steps"][0]["unit"] = "minutes"
        with self.assertRaises(Invalid):
            with self.store.lock(): self.store.commit(b)
        self.assertEqual(self.store.read("current"), pointer)

    def test_unknown_reference_and_raw_fields_rejected(self):
        b = self.store.current(); b["coach_summary"]["observations"][0]["references"] = ["activity:invented"]
        with self.assertRaises(Invalid): validate_bundle(b, self.store.profile())
        b = self.store.current(); b["activities"]["items"][0]["gps"] = [1, 2]
        with self.assertRaises(Invalid): validate_bundle(b, self.store.profile())

    def test_profile_and_feedback_saved_and_flagged(self):
        p = self.store.profile(); p["display_name"] = "Test runner"; self.store.save_profile(p)
        self.assertEqual(self.store.profile()["display_name"], "Test runner")
        self.store.feedback({"text": "Legs felt heavy", "feeling": "hard"})
        self.assertTrue(self.store.read("run_status")["review_required"])
        self.assertEqual(Store(self.temp.name).read("feedback")["items"][0]["source"], "user_statement")

    def test_profile_confirmation_updates_blockers_without_promoting_old_plan(self):
        pointer = self.store.read("current")
        p = self.store.profile()
        p["profile_confirmed"] = True
        self.store.save_profile(p)
        self.assertEqual(self.store.read("run_status")["blocked_steps"],
                         ["Garmin authentication", "Personalized coaching review"])
        self.assertTrue(self.store.read("run_status")["review_required"])
        self.assertEqual(self.store.read("current"), pointer)
        self.assertTrue(self.store.current()["training_plan"]["illustrative"])
        p["profile_confirmed"] = False
        self.store.save_profile(p)
        self.assertEqual(self.store.read("run_status")["blocked_steps"],
                         ["Garmin authentication", "Athlete profile confirmation"])

    def test_unchanged_profile_does_not_create_new_coaching_review(self):
        self.store.mark(review_required=False)
        self.store.save_profile(self.store.profile())
        self.assertFalse(self.store.read("run_status")["review_required"])

    def test_saved_training_signal_is_citable_without_route_samples(self):
        from coach.dashboard import demo_details
        details = demo_details(self.store.current())
        self.store.write("dashboard_details", details)
        context = prepare(self.store)
        ref = "dashboard:readiness:" + details["training"]["readiness"]["date"]
        self.assertIn(ref, context["allowed_references"])
        self.assertNotIn("activities", context["training_signals"])
        self.assertNotIn("samples", str(context["training_signals"]))
        bundle = self.store.current()
        bundle["coach_summary"]["observations"].append({"kind": "measured", "text": "Saved readiness observation", "references": [ref]})
        with self.store.lock():
            self.store.commit(bundle)
        self.assertEqual(self.store.current()["coach_summary"]["observations"][-1]["references"], [ref])
        bundle = self.store.current()
        bundle["coach_summary"]["observations"][-1]["references"] = ["dashboard:readiness:1900-01-01"]
        pointer = self.store.read("current")
        with self.assertRaises(Invalid):
            with self.store.lock():
                self.store.commit(bundle)
        self.assertEqual(self.store.read("current"), pointer)

    def test_changed_training_signal_rejects_staged_coaching_result(self):
        from coach.dashboard import demo_details
        details = demo_details(self.store.current())
        self.store.write("dashboard_details", details)
        context = prepare(self.store)
        pointer = self.store.read("current")
        details["training"]["readiness"]["value"] += 1
        self.store.write("dashboard_details", details)
        current = self.store.current()
        with self.assertRaisesRegex(Invalid, "Inputs changed"):
            accept(self.store, {"coach_summary": current["coach_summary"], "training_plan": current["training_plan"]}, context)
        self.assertEqual(self.store.read("current"), pointer)

    def test_dashboard_start_failure_does_not_change_coaching_status(self):
        import io
        from contextlib import redirect_stderr
        from coach.__main__ import main
        before = self.store.read("run_status")
        error = io.StringIO()
        with patch.object(sys, "argv", ["coach", "--data-dir", self.temp.name, "serve"]), \
             patch("coach.server.serve", side_effect=PermissionError("Local port unavailable")), \
             redirect_stderr(error):
            self.assertEqual(main(), 1)
        self.assertIn("Could not start dashboard: Local port unavailable", error.getvalue())
        self.assertEqual(self.store.read("run_status"), before)

    def test_timezone_change_marks_real_observations_for_refresh(self):
        self.mocked_real()
        p = self.store.profile()
        p["timezone"] = "UTC"
        self.store.save_profile(p)
        self.assertIn("Garmin data refresh for timezone change", self.store.read("run_status")["blocked_steps"])
        self.assertTrue(self.store.read("run_status")["review_required"])
        self.assertEqual(self.store.current()["activities"]["timezone"], "Europe/Stockholm")
        p["timezone"] = "Europe/Stockholm"
        self.store.save_profile(p)
        self.assertNotIn("Garmin data refresh for timezone change", self.store.read("run_status")["blocked_steps"])

    def test_schedule_changes_and_completion_preserved(self):
        session = self.store.current()["training_plan"]["sessions"][0]
        from datetime import timedelta
        from coach.core import iso_date
        day = (iso_date(session["date"]) + timedelta(days=1)).isoformat()
        self.store.change_session(session["id"], {"date": day, "reason": "Meeting moved"})
        b = self.store.current(); self.assertEqual(b["training_plan"]["sessions"][0]["date"], day)
        self.assertEqual(len(self.store.read("feedback")["schedule_changes"]), 1)
        self.store.change_session(session["id"], {"state": "completed", "reason": "Finished"})
        b = self.store.current(); b["training_plan"]["revision_id"] = "illegal_edit"; b["training_plan"]["sessions"][0]["title"] = "Changed"
        with self.assertRaises(Invalid):
            with self.store.lock(): self.store.commit(b)

    def test_profile_limit_and_availability_enforced(self):
        p = self.store.profile(); p["profile_confirmed"] = True; p["constraints"]["max_session_minutes"] = 10
        b = self.store.current()
        with self.assertRaises(Invalid): validate_bundle(b, p)
        p["constraints"]["max_session_minutes"] = None; p["availability"]["running_days"] = ["monday"]
        with self.assertRaises(Invalid): validate_bundle(b, p)

    def test_revision_and_history(self):
        b = self.store.current(); b["training_plan"]["sessions"][0]["title"] = "Different"
        with self.assertRaises(Invalid):
            with self.store.lock(): self.store.commit(b)
        b["training_plan"]["revision_id"] = "new_plan"; b["training_plan"]["sessions"][0]["revision"] = "new_session"
        with self.store.lock(): self.store.commit(b)
        self.assertEqual(len(list((self.store.path / "snapshots").iterdir())), 2)

    def test_concurrent_feedback_invalidates_agent_output(self):
        context = prepare(self.store)
        b = self.store.current()
        self.store.feedback({"text": "Schedule changed", "feeling": "schedule"})
        with self.assertRaises(Invalid): accept(self.store, {k: b[k] for k in ("coach_summary", "training_plan")}, context)


class WorkoutTests(StoreCase):
    def test_explicit_one_off_exception_preserves_week_and_reuses_remote_ids(self):
        self.mocked_real(); profile = self.store.profile(); before = self.store.current()
        today = datetime.now(ZoneInfo(profile["timezone"])).date()
        monday = today + timedelta(days=(7 - today.weekday()) % 7)
        session = {"id": "oneoff-test-device", "revision": "v1", "date": monday.isoformat(), "title": "Device check",
                   "state": "planned", "rationale": "User-requested device transfer check, not an added training requirement.",
                   "references": ["profile:availability"], "steps": [
                       {"kind": "warmup", "unit": "seconds", "value": 120, "description": "Gentle jog", "target": {"kind": "effort", "value": "comfortable"}},
                       {"kind": "run", "unit": "seconds", "value": 360, "description": "Easy run", "target": {"kind": "effort", "value": "conversational"}},
                       {"kind": "cooldown", "unit": "seconds", "value": 120, "description": "Easy walk", "target": {"kind": "effort", "value": "comfortable"}}]}
        client = FakeGarmin(); writer = Workouts(self.store, client)
        with self.assertRaises(Invalid): writer.stage_one_off(session)
        session["availability_exception"] = "User requested Monday as a one-time device check."
        writer.stage_one_off(session)
        self.assertEqual(self.store.current()["training_plan"], before["training_plan"])
        writer.select(session["id"])
        first = writer.publish(session["id"]); second = writer.publish(session["id"])
        self.assertEqual((first["state"], second["state"], client.uploads, client.schedules), ("scheduled", "scheduled", 1, 1))
        self.assertEqual(first["origin"], "one_off")
        client.calendar[0]["completed"] = True
        writer.reconcile_all()
        with self.store.lock():
            self.store.commit(self.store.current())
        self.assertEqual(self.store.current()["training_plan"], before["training_plan"])

    def test_fixture_unconfirmed_and_draft_policy_block(self):
        sid = self.store.current()["training_plan"]["sessions"][0]["id"]
        with self.assertRaises(Invalid): Workouts(self.store, FakeGarmin()).publish(sid)
        sid = self.mocked_real("draft_only")
        with self.assertRaises(Invalid): Workouts(self.store, FakeGarmin()).publish(sid)

    def test_selection_required_and_edit_invalidates_selection(self):
        sid = self.mocked_real(); client = FakeGarmin(); writer = Workouts(self.store, client)
        with self.assertRaises(Invalid): writer.publish(sid)
        writer.select(sid); self.store.feedback({"text": "New discomfort", "feeling": "discomfort"})
        with self.assertRaises(Invalid): writer.publish(sid)
        self.assertEqual(client.uploads, 0)

    def test_duplicate_prevention_and_separate_watch_evidence(self):
        sid = self.mocked_real(); client = FakeGarmin(); writer = Workouts(self.store, client); writer.select(sid)
        first = writer.publish(sid); second = writer.publish(sid)
        self.assertEqual((client.uploads, client.schedules), (1, 1))
        self.assertEqual(first["scheduled_entry_id"], second["scheduled_entry_id"])
        self.assertIsNone(first["watch_verified_at"])
        writer.watch_verified(sid, "Synthetic test: watch confirmation")
        entry = next(iter(self.store.read("workout_registry")["entries"].values()))
        self.assertEqual(entry["watch_evidence"]["source"], "user_statement")
        self.assertTrue(self.store.read("run_status")["watch_verified"])

    def test_uncertain_upload_reconciles_positive_match(self):
        sid = self.mocked_real(); client = FakeGarmin(); client.timeout_upload = True
        writer = Workouts(self.store, client); writer.select(sid)
        with self.assertRaises(Invalid): writer.publish(sid)
        self.assertEqual(next(iter(self.store.read("workout_registry")["entries"].values()))["state"], "creation_uncertain")
        writer.publish(sid)
        self.assertEqual((client.uploads, client.schedules), (1, 1))

    def test_uncertain_upload_negative_match_never_retries(self):
        sid = self.mocked_real(); client = FakeGarmin(); client.timeout_upload = True
        writer = Workouts(self.store, client); writer.select(sid)
        with self.assertRaises(Invalid): writer.publish(sid)
        client.hide_library = True
        with self.assertRaises(Invalid): writer.publish(sid)
        self.assertEqual(client.uploads, 1)

    def test_uncertain_schedule_and_unrelated_entries(self):
        sid = self.mocked_real(); client = FakeGarmin(); client.timeout_schedule = True
        client.calendar = [{"date": "2026-10-01", "workout_id": 999, "scheduled_workout_id": 999, "completed": True}]
        unrelated = copy.deepcopy(client.calendar[0])
        writer = Workouts(self.store, client); writer.select(sid)
        with self.assertRaises(Invalid): writer.publish(sid)
        writer.publish(sid)
        self.assertEqual(client.schedules, 1); self.assertEqual(client.calendar[0], unrelated)
        self.assertFalse(any("delete" in call[0] for call in client.calls))

    def test_reconcile_clears_transient_calendar_block_after_positive_evidence(self):
        sid = self.mocked_real(); client = FakeGarmin(); writer = Workouts(self.store, client); writer.select(sid)
        original = client.call
        hidden = False
        def delayed_calendar(name, args):
            nonlocal hidden
            if name == "get_scheduled_workouts" and client.schedules == 1 and not hidden:
                hidden = True
                return {"empty": True, "message": "No workouts scheduled between 2026-10-01 and 2026-10-01."}
            return original(name, args)
        client.call = delayed_calendar
        with self.assertRaisesRegex(Invalid, "Calendar entry has not been verified"):
            writer.publish(sid)
        self.store.mark(state="blocked", error="Calendar entry has not been verified", blocked_steps=[])
        writer.reconcile_all()
        entry = next(iter(self.store.read("workout_registry")["entries"].values()))
        self.assertEqual(entry["state"], "scheduled")
        self.assertIsNotNone(entry["scheduled_entry_id"])
        self.assertEqual(self.store.read("run_status")["state"], "success")
        self.assertIsNone(self.store.read("run_status")["error"])
        self.assertEqual((client.uploads, client.schedules), (1, 1))

    def test_ambiguous_calendar_blocks_scheduling(self):
        sid = self.mocked_real(); client = FakeGarmin(); client.ambiguous_calendar = True
        writer = Workouts(self.store, client); writer.select(sid)
        with self.assertRaises(Invalid): writer.publish(sid)
        self.assertEqual(client.schedules, 0)

    def test_steps_explicit_units_and_hr_range(self):
        s = self.store.current()["training_plan"]["sessions"][0]
        s["steps"][1].update(unit="meters", value=1000, target={"kind": "heart_rate_bpm", "low": 130, "high": 145})
        payload = compile_workout(s); step = payload["workoutSegments"][0]["workoutSteps"][1]
        self.assertEqual(step["endCondition"], {"conditionTypeId": 3, "conditionTypeKey": "distance"})
        self.assertEqual(step["targetType"]["workoutTargetTypeId"], 4)
        self.assertEqual(step["targetValueOne"], 130)

    def test_calendar_completion_prevents_recreation_and_plan_deletion(self):
        sid = self.mocked_real(); client = FakeGarmin(); writer = Workouts(self.store, client); writer.select(sid)
        writer.publish(sid); client.calendar[0]["completed"] = True
        writer.reconcile_all(); writer.publish(sid)
        self.assertEqual((client.uploads, client.schedules), (1, 1))
        b = self.store.current(); b["training_plan"]["revision_id"] = "omit_completed"
        b["training_plan"]["sessions"] = [s for s in b["training_plan"]["sessions"] if s["id"] != sid]
        with self.assertRaises(Invalid):
            with self.store.lock(): self.store.commit(b)

    def test_mismatch_readback_cannot_schedule(self):
        sid = self.mocked_real(); client = FakeGarmin(); writer = Workouts(self.store, client); writer.select(sid)
        original = client.call
        def bad_readback(name, args):
            result = original(name, args)
            if name == "get_workout_by_id": result["segments"][0]["steps"][0]["end_condition_value"] = 1
            return result
        client.call = bad_readback
        with self.assertRaises(Invalid): writer.publish(sid)
        self.assertEqual(client.schedules, 0)

    def test_changed_effort_cue_cannot_schedule(self):
        sid = self.mocked_real(); client = FakeGarmin(); writer = Workouts(self.store, client); writer.select(sid)
        original = client.call
        def bad_readback(name, args):
            result = original(name, args)
            if name == "get_workout_by_id": result["segments"][0]["steps"][0]["description"] = "Changed effort"
            return result
        client.call = bad_readback
        with self.assertRaises(Invalid): writer.publish(sid)
        self.assertEqual(client.schedules, 0)


class RunnerTests(StoreCase):
    def test_enabling_timer_preserves_reviewed_plan(self):
        with self.assertRaises(Invalid):
            set_scheduler_enabled(self.store, True)
        profile = self.store.profile()
        profile["profile_confirmed"] = True
        self.store.save_profile(profile)
        self.store.mark(manual_runner_verified=True, review_required=False,
                        last_input_fingerprint=self.store.fingerprint())
        set_scheduler_enabled(self.store, True)
        self.assertTrue(self.store.profile()["automation"]["enabled"])
        status = self.store.read("run_status")
        self.assertFalse(status["review_required"])
        self.assertEqual(status["last_input_fingerprint"], self.store.fingerprint())
        set_scheduler_enabled(self.store, False)
        self.assertFalse(self.store.read("run_status")["review_required"])

    def test_runner_lock(self):
        with self.store.lock("runner"):
            with self.assertRaises(Invalid):
                with self.store.lock("runner", blocking=False): pass

    def test_scheduling_disabled(self):
        with self.assertRaises(Invalid): run(self.store, fixture=True, scheduled=True)

    def test_unchanged_inputs_do_not_invoke_agent(self):
        with patch("coach.runner.invoke_agent") as agent:
            self.assertIn("existing plan retained", run(self.store, fixture=True))
            agent.assert_not_called()

    def test_agent_output_validation_and_success(self):
        b = self.store.current(); self.store.mark(review_required=True)
        result = self.store.path / "pending/runner-result.json"
        def valid(*_):
            b["training_plan"]["revision_id"] = "runner_verified"
            atomic_json(result, {k: b[k] for k in ("coach_summary", "training_plan")})
        with patch("coach.runner.invoke_agent", side_effect=valid):
            self.assertIn("saved", run(self.store, fixture=True))
        self.assertTrue(self.store.read("run_status")["fixture_runner_verified"])
        self.assertNotIn("manual_runner_verified", self.store.read("run_status"))
        pointer = self.store.read("current"); self.store.mark(review_required=True)
        def invalid(*_): atomic_json(result, {"bad": "partial"})
        with patch("coach.runner.invoke_agent", side_effect=invalid):
            with self.assertRaises(Invalid): run(self.store, fixture=True)
        self.assertEqual(self.store.read("current"), pointer)

    def test_timeout_and_overall_deadline(self):
        started = time.monotonic()
        with self.assertRaises(Invalid): invoke_agent([sys.executable, "-c", "import time; time.sleep(10)"], "sample", .1)
        self.assertLess(time.monotonic() - started, 2)
        with self.assertRaises(Invalid):
            with deadline(.03): time.sleep(.2)


class ServerTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.store, 0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True); thread.start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.token = json.loads(urllib.request.urlopen(self.base + "/api/dashboard").read())["csrf_token"]

    def post(self, endpoint, body, token=None, origin=None):
        return urllib.request.urlopen(urllib.request.Request(self.base+endpoint, json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-Coach-Token": token or self.token, "Origin": origin or self.base}))

    def test_only_allowlisted_files_exposed(self):
        for path in ("/data/athlete_profile.json", "/../README.md", "/.env", "/.garminconnect/garmin_tokens.json", "/api/runner"):
            with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(self.base+path)
            self.assertEqual(e.exception.code, 404)
            e.exception.close()

    def test_dashboard_reports_claude_without_exposing_api_key(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "private-test-key"}):
            with urllib.request.urlopen(self.base + "/api/dashboard") as response:
                body = response.read()
        self.assertNotIn(b"private-test-key", body)
        connection = json.loads(body)["coach_connection"]
        self.assertEqual(connection["provider"], "Claude API")
        self.assertTrue(connection["configured"])

    def test_csrf_and_origin_block(self):
        for token, origin in (("bad", self.base), (self.token, "http://example.com")):
            with self.assertRaises(urllib.error.HTTPError) as e: self.post("/api/feedback", {"text": "test", "feeling": "easy"}, token, origin)
            self.assertEqual(e.exception.code, 403)
            e.exception.close()

    def test_http_saves_persist(self):
        p = self.store.profile(); p["display_name"] = "Saved through HTTP"
        with self.post("/api/profile", p) as r: self.assertEqual(r.status, 200)
        with self.post("/api/feedback", {"text": "<script>example data</script>", "feeling": "easy"}) as r: self.assertEqual(r.status, 200)
        self.assertEqual(self.store.profile()["display_name"], "Saved through HTTP")
        self.assertTrue(self.store.read("run_status")["review_required"])

    def test_reviewed_chat_workout_http_path_uses_guarded_mock_garmin(self):
        self.mocked_real()
        old = self.store.current()["training_plan"]["sessions"][0]
        draft = {key: copy.deepcopy(old[key]) for key in ("title", "date", "rationale", "references", "steps")}
        draft["date"] = (iso_date(draft["date"]) + timedelta(days=7)).isoformat()
        chat_id = "b" * 32
        with self.store.lock():
            self.store.write("coach_chat", {"items": [{"id": chat_id, "observation_source": "garmin", "illustrative": False,
                "input_fingerprint": self.store.fingerprint(), "plan_digest": digest(self.store.current()["training_plan"]),
                "result": {"workout": draft}}]})
        fake = FakeGarmin()
        server = make_server(self.store, 0, workout_client_factory=lambda: nullcontext(fake), workout_read_factory=lambda: nullcontext(fake))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/api/dashboard") as response:
            token = json.load(response)["csrf_token"]
        def post(path, body):
            request = urllib.request.Request(base + path, json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-Coach-Token": token, "Origin": base})
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        reviewed = post("/api/proposal/review", {"chat_id": chat_id})
        self.assertTrue(reviewed["selected"])
        self.assertEqual(post("/api/workout/publish", {"session_id": reviewed["session_id"]})["state"], "scheduled")
        self.assertEqual(post("/api/workout/publish", {"session_id": reviewed["session_id"]})["state"], "scheduled")
        self.assertEqual((fake.uploads, fake.schedules), (1, 1))
        self.assertTrue(post("/api/workout/watch-verified", {"session_id": reviewed["session_id"], "note": "Watch test: date and steps visible"})["watch_verified"])
        self.assertTrue(post("/api/workout/reconcile", {})["states"])

    def test_map_preference_is_protected_and_keeps_training_state(self):
        before = (self.store.read('current'), self.store.profile(), self.store.read('run_status'))
        with urllib.request.urlopen(self.base + '/api/dashboard') as response:
            self.assertTrue(json.load(response)['map']['street_map_enabled'])
        for token, origin in (('bad', self.base), (self.token, 'http://example.com')):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post('/api/map/preferences', {'street_map_enabled': True}, token, origin)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        self.assertIsNone(self.store.read('map_preferences'))
        for enabled in (True, False):
            with self.post('/api/map/preferences', {'street_map_enabled': enabled}) as response:
                self.assertEqual(json.load(response)['street_map_enabled'], enabled)
        request = urllib.request.Request(self.base + '/api/map/tile/12/2048/1361.png', headers={'Referer': self.base + '/'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 503)
        error.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.base + '/api/map/tile/12/2048/1361.png')
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        self.assertEqual(before, (self.store.read('current'), self.store.profile(), self.store.read('run_status')))


if __name__ == "__main__":
    unittest.main()
