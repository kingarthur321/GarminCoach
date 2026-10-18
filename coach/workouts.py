"""Policy-gated Garmin writes with a durable registry and conservative recovery."""
import copy
import math
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .core import DAYS, Invalid, digest, evidence_set, iso_date, now, require, validate_step


def session_key(session):
    # Includes date and every prescription field: selection cannot carry to an edit.
    return digest(session)


def compile_workout(session):
    """Mapping inspected in the pinned server's workouts/workout_builders sources."""
    steps = []
    marker = "ARC-" + session_key(session)[:16]
    for index, step in enumerate(session["steps"], 1):
        validate_step(step)
        kind_id, kind = {"warmup": (1, "warmup"), "run": (3, "interval"), "recovery": (4, "recovery"), "cooldown": (2, "cooldown")}[step["kind"]]
        condition_id, condition = (2, "time") if step["unit"] == "seconds" else (3, "distance")
        out = {"type": "ExecutableStepDTO", "stepOrder": index, "stepType": {"stepTypeId": kind_id, "stepTypeKey": kind},
               "description": step["description"], "endCondition": {"conditionTypeId": condition_id, "conditionTypeKey": condition}, "endConditionValue": float(step["value"])}
        target = step["target"]
        if target["kind"] == "effort":
            out["description"] += " · " + target["value"]
            out["targetType"] = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
        else:
            out.update(targetType={"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"}, targetValueOne=target["low"], targetValueTwo=target["high"])
        steps.append(out)
    sport = {"sportTypeId": 1, "sportTypeKey": "running"}
    return {"workoutName": f"[{marker}] {session['title']}"[:120], "description": f"AI Running Coach · session {session['id']} revision {session['revision']} · {session['date']}",
            "sportType": sport, "workoutSegments": [{"segmentOrder": 1, "sportType": sport, "workoutSteps": steps}]}


def verify_details(details, payload):
    require(isinstance(details, dict) and details.get("name") == payload["workoutName"] and details.get("sport") == "running", "Garmin workout identity differs; write retained for reconciliation")
    actual = [s for segment in details.get("segments", []) for s in segment.get("steps", [])]
    expected = payload["workoutSegments"][0]["workoutSteps"]
    require(len(actual) == len(expected), "Garmin workout step count differs")
    for got, wanted in zip(actual, expected):
        require(got.get("type") == wanted["stepType"]["stepTypeKey"] and got.get("end_condition") == wanted["endCondition"]["conditionTypeKey"], "Garmin workout step types differ")
        require(got.get("description") == wanted["description"], "Garmin workout effort cue differs")
        require(isinstance(got.get("end_condition_value"), (int, float)) and math.isclose(got["end_condition_value"], wanted["endConditionValue"], abs_tol=.01), "Garmin duration/distance differs")
        if wanted["targetType"]["workoutTargetTypeId"] == 4:
            require(got.get("target_type") == "heart.rate.zone" and got.get("target_value_low") == wanted["targetValueOne"] and got.get("target_value_high") == wanted["targetValueTwo"], "Garmin HR target differs")
        else:
            require(got.get("target_type") in (None, "no.target"), "Unexpected Garmin target")


class Workouts:
    def __init__(self, store, client):
        self.store, self.client = store, client

    def stage_one_off(self, session, *, select=False, expected_fingerprint=None, reject_date_conflict=False):
        """Keep an explicitly requested extra workout outside the saved weekly plan."""
        with self.store.lock():
            profile, bundle = self.store.profile(), self.store.current()
            require(profile["profile_confirmed"] and bundle is not None and bundle["activities"]["source"] == "garmin"
                    and not bundle["training_plan"]["illustrative"], "A confirmed real plan is required")
            require(not self.store.read("run_status").get("review_required"), "Coach must review changed inputs first")
            if expected_fingerprint is not None:
                require(self.store.fingerprint() == expected_fingerprint, "Coach proposal is stale; ask for a fresh workout after the latest review")
            if select:
                require(profile["automation"]["garmin_write_policy"] == "selected_workouts", "Choose the selected_workouts write policy before approving a chat workout")
            expected = {"id", "revision", "date", "title", "state", "rationale", "references", "steps"}
            require(isinstance(session, dict) and set(session) in (expected, expected | {"availability_exception"}), "Unexpected one-off session fields")
            require(isinstance(session["id"], str) and re.fullmatch(r"oneoff-[a-z0-9-]{1,80}", session["id"]), "Unsafe one-off session ID")
            require(isinstance(session["revision"], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", session["revision"]), "Unsafe one-off revision")
            require(session["state"] == "planned", "One-off workout must be planned")
            day = iso_date(session["date"])
            today = datetime.now(ZoneInfo(profile["timezone"])).date()
            require(today <= day <= today + timedelta(days=30), "One-off workout date must be within 30 days")
            if DAYS[day.weekday()] not in profile["availability"]["running_days"]:
                note = session.get("availability_exception")
                require(isinstance(note, str) and 1 <= len(note) <= 300 and "user requested" in note.lower(), "One-off date needs an explicit user-requested availability exception")
            require(isinstance(session["title"], str) and 1 <= len(session["title"]) <= 120, "Invalid one-off title")
            require(isinstance(session["rationale"], str) and 1 <= len(session["rationale"]) <= 1000, "Invalid one-off rationale")
            from .dashboard import saved_details
            details = saved_details(self.store, bundle)
            refs = evidence_set(bundle, details if details["source"] == bundle["activities"]["source"] else None)
            from .planned import read_planned, reference as planned_reference
            refs.update(planned_reference(item) for item in read_planned(self.store)["items"])
            require(isinstance(session["references"], list) and session["references"] and all(r in refs for r in session["references"]), "Unsupported one-off evidence")
            steps = session["steps"]
            require(isinstance(steps, list) and 3 <= len(steps) <= 100, "One-off workout needs structured steps")
            require(steps[0].get("kind") == "warmup" and steps[-1].get("kind") == "cooldown", "One-off workout requires warmup and cooldown")
            for step in steps:
                validate_step(step)
            limit = profile["constraints"]["max_session_minutes"]
            if limit is not None:
                require(all(step["unit"] == "seconds" for step in steps), "Use time-based steps for the confirmed session limit")
                require(sum(step["value"] for step in steps) <= limit * 60, "One-off workout exceeds confirmed time limit")
            registry = self.store.read("workout_registry")
            require(all(s["id"] != session["id"] for s in bundle["training_plan"]["sessions"]), "One-off ID collides with weekly plan")
            extras = registry.setdefault("one_off_sessions", {})
            if reject_date_conflict:
                from .planned import read_planned
                require(not any(s["date"] == session["date"] for s in bundle["training_plan"]["sessions"]),
                        "A planned workout already uses this date. Ask the coach for another date or review the existing plan.")
                require(not any(s["date"] == session["date"] for s in read_planned(self.store)["items"]),
                        "A Garmin Connect planned run already uses this date. Ask the coach for another date.")
                require(not any(s["date"] == session["date"] and s["id"] != session["id"] for s in extras.values()),
                        "A separate workout draft already uses this date")
                require(not any(e["date"] == session["date"] and e["session_id"] != session["id"] and e["state"] != "draft"
                                for e in registry["entries"].values()), "A tracked Garmin workout already uses this date")
            require(len(extras) < 20 or session["id"] in extras, "Too many one-off sessions")
            require(session["id"] not in extras or extras[session["id"]] == session, "Existing one-off prescription is immutable")
            extras[session["id"]] = copy.deepcopy(session)
            if select:
                registry["authorizations"][session_key(session)] = {"selected_at": now(), "session_id": session["id"],
                    "revision": session["revision"], "input_fingerprint": self.store.fingerprint(),
                    "plan_digest": digest(bundle["training_plan"])}
            self.store.write("workout_registry", registry)
            return session

    def select(self, session_id):
        with self.store.lock():
            profile, bundle = self.store.profile(), self.store.current()
            require(profile["profile_confirmed"], "Confirm athlete profile before authorizing a workout")
            require(bundle is not None and not bundle["training_plan"]["illustrative"] and bundle["activities"]["source"] == "garmin", "Illustrative workouts cannot be authorized")
            registry = self.store.read("workout_registry")
            session = self.find(bundle, session_id, registry)
            require(session["state"] == "planned", "Only planned workouts can be selected")
            require(not self.store.read("run_status").get("review_required"), "Coach must review changed inputs before selection")
            registry["authorizations"][session_key(session)] = {"selected_at": now(), "session_id": session_id, "revision": session["revision"], "input_fingerprint": self.store.fingerprint()}
            self.store.write("workout_registry", registry)

    @staticmethod
    def find(bundle, session_id, registry=None):
        session = next((s for s in bundle["training_plan"]["sessions"] if s["id"] == session_id), None)
        extra = (registry or {}).get("one_off_sessions", {}).get(session_id)
        require(not (session and extra), "Session ID occurs in both plan and one-off registry")
        session = session or extra
        require(session is not None, "Unknown planned session")
        return session

    def publish(self, session_id):
        # Lock covers policy check, write-ahead state and remote call. No second
        # caller can create a duplicate or change the profile during an upload.
        with self.store.lock():
            p, bundle = self.store.profile(), self.store.current()
            require(bundle is not None, "No saved plan")
            registry = self.store.read("workout_registry")
            session = self.find(bundle, session_id, registry)
            require(p["profile_confirmed"] and not bundle["training_plan"]["illustrative"] and bundle["activities"]["source"] == "garmin", "A confirmed profile and real reviewed draft are required")
            require(session["state"] == "planned", "Completed or skipped sessions cannot be published")
            require(iso_date(session["date"]) >= datetime.now(ZoneInfo(p["timezone"])).date(), "Cannot schedule a past workout")
            if session_id in registry.get("one_off_sessions", {}) and DAYS[iso_date(session["date"]).weekday()] not in p["availability"]["running_days"]:
                require(bool(session.get("availability_exception")), "One-off availability exception is missing")
            require(not self.store.read("run_status").get("review_required"), "Coach must review profile, feedback and date changes first")
            policy = p["automation"]["garmin_write_policy"]
            require(policy != "draft_only", "Garmin write policy is draft_only")
            key = session_key(session)
            if policy == "selected_workouts":
                selection = registry["authorizations"].get(key, {})
                require(selection.get("input_fingerprint") == self.store.fingerprint(), "Explicitly select this exact session revision after the latest review")
                if session_id.startswith("oneoff-chat-"):
                    require(selection.get("plan_digest") == digest(bundle["training_plan"]),
                            "Saved training week changed since workout approval. Ask the coach for a fresh proposal.")
            require(all(k == key or e["session_id"] != session_id or e["state"] == "draft" for k, e in registry["entries"].items()), "An earlier revision is published or uncertain; reconcile it manually in Connect before replacing this session")
            payload = compile_workout(session)
            entry = registry["entries"].setdefault(key, {"session_id": session_id, "revision": session["revision"], "session_key": key,
                    "date": session["date"], "state": "draft", "workout_id": None, "scheduled_entry_id": None, "watch_verified_at": None,
                    "name": payload["workoutName"], "session": copy.deepcopy(session),
                    "origin": "one_off" if session_id in registry.get("one_off_sessions", {}) else "plan"})
            def save():
                entry["updated_at"] = now()
                self.store.write("workout_registry", registry)
            try:
                self._reconcile(entry, payload)
                save()
                if entry["state"] in ("scheduled", "completed"):
                    return entry
                # A previous process may have died after submission. Absence from
                # an eventually consistent listing is not permission to resubmit.
                require(entry["state"] not in ("creating", "creation_uncertain", "scheduling", "schedule_uncertain"), "Write outcome remains uncertain; no retry made. Check Connect and reconcile again.")
                if entry["workout_id"] is None:
                    entry["state"] = "creating"
                    save()
                    response = self.client.call("upload_workout", {"workout_data": payload})
                    require(response.get("status") == "success" and type(response.get("workout_id")) is int, "Upload not confirmed")
                    entry.update(workout_id=response["workout_id"], state="created")
                    save()
                details = self.client.call("get_workout_by_id", {"workout_id": entry["workout_id"]})
                verify_details(details, payload)
                entry["connect_verified_at"] = now()
                # Preflight calendar before each scheduling attempt; all unrelated
                # entries are ignored and there are no deletion tools in the client.
                self._calendar(entry)
                save()
                if entry["state"] in ("scheduled", "completed"):
                    return entry
                entry["state"] = "scheduling"
                save()
                response = self.client.call("schedule_workout", {"workout_id": entry["workout_id"], "calendar_date": entry["date"]})
                require(response.get("status") == "success", "Schedule not confirmed")
                self._calendar(entry)
                require(entry["state"] in ("scheduled", "completed") and entry["scheduled_entry_id"] is not None, "Calendar entry has not been verified")
                save()
                return entry
            except Exception:
                if entry["state"] == "creating":
                    entry["state"] = "creation_uncertain"
                elif entry["state"] == "scheduling":
                    entry["state"] = "schedule_uncertain"
                save()
                raise

    def _reconcile(self, entry, payload):
        result = self.client.call("get_workouts", {})
        require(not result.get("empty") or result.get("message") == "No workouts found.", "Ambiguous workout listing")
        items = result.get("workouts", [])
        require(isinstance(items, list), "Malformed workout library")
        matches = [w for w in items if w.get("name") == entry["name"]]
        require(len(matches) <= 1, "Multiple marker matches: inspect Connect before any further write")
        if matches:
            workout_id = matches[0].get("id")
            require(type(workout_id) is int, "Workout ID missing")
            require(entry["workout_id"] in (None, workout_id), "Registry workout ID differs")
            entry["workout_id"] = workout_id
            details = self.client.call("get_workout_by_id", {"workout_id": workout_id})
            verify_details(details, payload)
            entry["connect_verified_at"] = now()
            if entry["state"] in ("creating", "creation_uncertain", "draft"):
                entry["state"] = "created"
        elif entry["workout_id"] is not None:
            # A curated library may be truncated; verify the known ID directly.
            details = self.client.call("get_workout_by_id", {"workout_id": entry["workout_id"]})
            verify_details(details, payload)
            entry["connect_verified_at"] = now()
        if entry["workout_id"] is not None:
            self._calendar(entry)

    def _calendar(self, entry):
        response = self.client.call("get_scheduled_workouts", {"start_date": entry["date"], "end_date": entry["date"]})
        if response.get("empty"):
            require(response.get("message", "").startswith("No workouts scheduled between "), "Calendar read is ambiguous; scheduling blocked")
        rows = response.get("scheduled_workouts", [])
        require(isinstance(rows, list), "Malformed calendar response")
        matches = [r for r in rows if r.get("workout_id") == entry["workout_id"] and r.get("date") == entry["date"]]
        require(len(matches) <= 1, "Duplicate calendar entries; inspect Connect")
        if matches:
            row = matches[0]
            require(row.get("scheduled_workout_id") is not None, "Scheduled entry ID missing")
            entry.update(state="completed" if row.get("completed") else "scheduled", scheduled_entry_id=row["scheduled_workout_id"], calendar_verified_at=now())
        elif entry["state"] in ("scheduled", "completed"):
            raise Invalid("Previously verified calendar entry missing; no new entry created")

    def reconcile_all(self):
        with self.store.lock():
            registry = self.store.read("workout_registry")
            for entry in registry["entries"].values():
                self._reconcile(entry, compile_workout(entry["session"]))
                entry["updated_at"] = now()
                self.store.write("workout_registry", registry)
            status = self.store.read("run_status", {})
            if (registry["entries"] and all(e["state"] in ("scheduled", "completed") for e in registry["entries"].values())
                    and status.get("state") == "blocked" and status.get("error") == "Calendar entry has not been verified"):
                self.store.mark(state="success", error=None, last_attempt=now(), blocked_steps=[])
        return registry

    def watch_verified(self, session_id, note):
        require(isinstance(note, str) and note.strip(), "Record the watch model and visible workout/date")
        with self.store.lock():
            registry = self.store.read("workout_registry")
            matches = [e for e in registry["entries"].values() if e["session_id"] == session_id and e["state"] in ("scheduled", "completed")]
            require(len(matches) == 1, "Verify a scheduled workout first")
            matches[0].update(watch_verified_at=now(), watch_evidence={"source": "user_statement", "note": note[:1000]})
            self.store.write("workout_registry", registry)
            self.store.mark(watch_verified=True)
