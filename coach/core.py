"""Validation, normalization and atomic private state. No AI service is embedded."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
OUTPUTS = ("activities", "wellness", "coach_summary", "training_plan")


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=lambda x: (_ for _ in ()).throw(Invalid("Non-finite JSON number")))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    blob = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    fd, temp = tempfile.mkstemp(prefix=".save-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def number(value, name, low=0, high=1e9, nullable=False):
    if value is None and nullable:
        return None
    require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high, f"Invalid {name}")
    return float(value)


def string(value, name, limit=4000, empty=False):
    require(isinstance(value, str) and (empty or bool(value.strip())) and len(value) <= limit, f"Invalid {name}")
    return value


def iso_date(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "Date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise Invalid("Invalid calendar date") from exc


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value)
        require(dt.tzinfo is not None, "Timestamp requires timezone")
        return dt
    except (TypeError, ValueError) as exc:
        raise Invalid("Invalid timestamp with timezone") from exc


def validate_profile(p):
    require(isinstance(p, dict), "Profile must be an object")
    require(set(p) == {"profile_confirmed", "display_name", "language", "timezone", "units", "goal", "baseline", "availability", "constraints", "data", "automation"}, "Unexpected profile fields")
    require(type(p.get("profile_confirmed")) is bool, "Confirmation must be boolean")
    string(p.get("display_name"), "display name", 100)
    require(p.get("units") == "metric", "Only metric units supported")
    require(p.get("language") in ("en", "sv"), "Language must be en or sv")
    try:
        ZoneInfo(p["timezone"])
    except Exception as exc:
        raise Invalid("Unknown IANA timezone") from exc
    goal = p.get("goal", {})
    number(goal.get("distance_km"), "goal distance", 0.1, 200)
    number(goal.get("target_time_seconds"), "goal time", 60, 86400)
    if goal.get("target_date") is not None:
        iso_date(goal["target_date"])
    baseline = p.get("baseline", {})
    number(baseline.get("recent_5k_time_seconds"), "baseline time", 60, 86400, nullable=True)
    if baseline.get("recent_5k_date") is not None:
        iso_date(baseline["recent_5k_date"])
    availability = p.get("availability", {})
    days = availability.get("running_days")
    require(isinstance(days, list) and 1 <= len(days) <= 7 and len(set(days)) == len(days) and all(d in DAYS for d in days), "Choose 1–7 unique running days")
    others = availability.get("other_training")
    require(isinstance(others, list) and len(others) <= 20, "Other training must be a list")
    for item in others:
        string(item, "other training", 300)
    constraints = p.get("constraints", {})
    number(constraints.get("max_session_minutes"), "session limit", 10, 360, nullable=True)
    if constraints.get("current_discomfort") is not None:
        string(constraints["current_discomfort"], "discomfort", 1000, empty=True)
    string(constraints.get("notes", ""), "notes", empty=True)
    for key, cap in (("activity_lookback_days", 42), ("wellness_lookback_days", 14)):
        n = p.get("data", {}).get(key)
        require(type(n) is int and 1 <= n <= cap, f"{key} must be 1–{cap}")
    a = p.get("automation", {})
    require(type(a.get("enabled")) is bool, "Automation flag must be boolean")
    require(a.get("garmin_write_policy") in ("draft_only", "selected_workouts", "automatic"), "Unknown Garmin write policy")
    require(a.get("weekly_plan_day") in DAYS, "Unknown weekly plan day")
    require(isinstance(a.get("daily_local_time"), str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", a["daily_local_time"]), "Invalid daily time")
    number(a.get("max_agent_run_minutes"), "runner timeout", 1, 15)
    return p


def local_timestamp(raw, tz):
    """Taxuspt curated start_time is local, with timezone inferred from profile.

    A DST overlap/gap cannot be resolved from this tool's local-only response.
    Such timestamps remain missing with the original text preserved.
    """
    if not raw:
        return None, "missing"
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None, "invalid"
    zone = ZoneInfo(tz)
    if dt.tzinfo is not None:
        return dt.astimezone(zone).isoformat(), "explicit_offset"
    a, b = dt.replace(tzinfo=zone, fold=0), dt.replace(tzinfo=zone, fold=1)
    if a.utcoffset() != b.utcoffset():
        return None, "ambiguous_or_nonexistent_local_time"
    return a.isoformat(), "profile_timezone_assumed"


def normalize_activities(rows, tz, source, start, end):
    iso_date(start)
    iso_date(end)
    require(source in ("garmin", "fixture"), "Invalid activity source")
    unique = {}
    rejected = 0
    for row in rows:
        require(isinstance(row, dict), "Malformed activity record")
        if row.get("type") not in ("running", "treadmill_running", "trail_running", "virtual_run", "track_running", "indoor_running"):
            continue
        if row.get("id") is None:
            rejected += 1
            continue
        activity_id = str(row["id"])
        string(activity_id, "activity ID", 100)
        raw_time = row.get("start_time")
        started, assumption = local_timestamp(raw_time, tz)
        local_date = started[:10] if started else (str(raw_time)[:10] if raw_time else None)
        if local_date is None:
            rejected += 1
            continue
        iso_date(local_date)
        if not start <= local_date <= end:
            continue
        meters = number(row.get("distance_meters"), "distance", nullable=True)
        seconds = number(row.get("duration_seconds"), "duration", nullable=True)
        km = None if meters is None else meters / 1000
        unique.setdefault(activity_id, {
            "id": activity_id, "name": str(row.get("name") or "Run")[:300], "type": row["type"],
            "source": source, "source_tool": "get_activities_by_date", "source_id": activity_id,
            "started_at": started, "original_start_time": raw_time, "timezone_basis": assumption,
            "local_date": local_date, "distance_km": km, "duration_seconds": seconds,
            "pace_seconds_per_km": round(seconds / km, 2) if km and seconds is not None else None,
            "avg_hr_bpm": number(row.get("avg_hr_bpm"), "average HR", 0, 250, nullable=True),
            "max_hr_bpm": number(row.get("max_hr_bpm"), "maximum HR", 0, 250, nullable=True),
        })
    return {"source": source, "fetched_at": now(), "timezone": tz, "coverage": {"start": start, "end": end, "rejected_without_id_or_date": rejected, "truncated": False},
            "items": sorted(unique.values(), key=lambda x: x["local_date"], reverse=True)}


WELLNESS_FIELDS = {
    "sleep_seconds": ("sleep_seconds", "s", 86400),
    "sleep_score": ("sleep_score", "score/100", 100),
    "hrv_ms": ("avg_overnight_hrv", "ms", 1000),
    "resting_hr_bpm": ("resting_heart_rate_bpm", "bpm", 250),
}


def normalize_wellness(nights, source, start, end, status="available"):
    by_date = {n["date"]: n for n in nights}
    items = []
    current = iso_date(start)
    while current <= iso_date(end):
        day = current.isoformat()
        n = by_date.get(day, {})
        values = {}
        for field, (raw, unit, cap) in WELLNESS_FIELDS.items():
            value = number(n.get(raw), field, 0, cap, nullable=True)
            values[field] = {"value": value, "unit": unit, "source": source,
                             "source_tool": "get_sleep_summary_range", "source_id": day,
                             "missing_reason": None if value is not None else (status if status != "available" else "not_reported")}
        items.append({"date": day, "values": values})
        current += timedelta(days=1)
    return {"source": source, "fetched_at": now(), "coverage": {"start": start, "end": end, "status": status, "note": "The MCP range tool skips failed or empty nights; gaps cannot distinguish unsupported data from transient failure."}, "items": items}


def weekly_distance(activities):
    weeks = {}
    for a in activities["items"]:
        day = iso_date(a["local_date"])
        monday = (day - timedelta(days=day.weekday())).isoformat()
        group = weeks.setdefault(monday, {"week_start": monday, "distance_km": 0, "runs": 0, "missing_distance": 0, "references": []})
        group["runs"] += 1
        group["references"].append("activity:" + a["id"])
        if a["distance_km"] is None:
            group["missing_distance"] += 1
        else:
            group["distance_km"] += a["distance_km"]
    for w in weeks.values():
        w["distance_km"] = round(w["distance_km"], 3)
    return sorted(weeks.values(), key=lambda w: w["week_start"])


def coaching_signals(details):
    """Keep bounded training observations for coaching, never recorded routes."""
    if not details or details.get("source") == "unavailable":
        return None
    return {key: copy.deepcopy(details.get(key, {})) for key in
            ("training", "history", "context", "context_history", "load_focus")}


def dashboard_evidence(details):
    signals = coaching_signals(details)
    if signals is None:
        return set()
    refs = set()
    for group in ("training", "context"):
        refs.update(f"dashboard:{key}:{item['date']}" for key, item in signals[group].items()
                    if item["value"] is not None)
    for group in ("history", "context_history"):
        refs.update(f"dashboard:{key}:{item['date']}" for key, items in signals[group].items()
                    for item in items if item["value"] is not None)
    refs.update(f"dashboard:load_focus:{key}:{item['date']}"
                for key, item in signals["load_focus"]["categories"].items() if item["value"] is not None)
    return refs


def evidence_set(bundle, details=None):
    refs = {"profile:goal", "profile:availability", "profile:constraints"}
    refs.update("activity:" + a["id"] for a in bundle["activities"]["items"])
    refs.update("derived:weekly_distance:" + w["week_start"] for w in weekly_distance(bundle["activities"]))
    for n in bundle["wellness"]["items"]:
        refs.update(f"wellness:{n['date']}:{k}" for k, v in n["values"].items() if v["value"] is not None)
    refs.update("feedback:" + f["id"] for f in bundle.get("feedback", {}).get("items", []))
    if details is not None:
        refs.update(dashboard_evidence(details))
    return refs


def validate_step(step):
    require(isinstance(step, dict), "Step must be an object")
    require(set(step) == {"kind", "unit", "value", "description", "target"}, "Unexpected workout step fields")
    require(step.get("kind") in ("warmup", "run", "recovery", "cooldown"), "Unknown step kind")
    require(step.get("unit") in ("seconds", "meters"), "Step duration unit must be seconds or meters")
    number(step.get("value"), "step length", 1, 21600 if step["unit"] == "seconds" else 100000)
    string(step.get("description"), "step description", 500)
    t = step.get("target", {})
    require(t.get("kind") in ("effort", "heart_rate_bpm"), "Target must be effort or explicit heart_rate_bpm")
    if t["kind"] == "effort":
        require(set(t) == {"kind", "value"}, "Unexpected effort target fields")
        string(t.get("value"), "effort target", 200)
    else:
        require(set(t) == {"kind", "low", "high"}, "Unexpected HR target fields")
        lo = number(t.get("low"), "HR low", 40, 230)
        hi = number(t.get("high"), "HR high", 40, 230)
        require(lo < hi, "HR target bounds reversed")


def validate_bundle(bundle, profile, previous=None, details=None, archived_session_digests=None):
    require(isinstance(bundle, dict) and all(k in bundle for k in OUTPUTS), "Bundle requires activities, wellness, coach_summary, training_plan")
    a, w, summary, plan = (bundle[k] for k in OUTPUTS)
    require(set(a) == {"source", "fetched_at", "timezone", "coverage", "items"}, "Unexpected activity output fields")
    require(set(w) == {"source", "fetched_at", "coverage", "items"}, "Unexpected wellness output fields")
    require(set(summary) == {"source", "generated_at", "headline", "observations", "uncertainty"}, "Unexpected summary fields")
    require(set(plan) == {"source", "generated_at", "revision_id", "revision_reason", "week_start", "illustrative", "sessions"}, "Unexpected plan fields")
    require(a.get("source") in ("fixture", "garmin") and w.get("source") == a["source"], "Mixed or unknown sources")
    for output in (a, w):
        timestamp(output.get("fetched_at"))
        iso_date(output["coverage"]["start"])
        iso_date(output["coverage"]["end"])
        require(isinstance(output.get("items"), list), "Items must be a list")
        require(iso_date(output["coverage"]["start"]) <= iso_date(output["coverage"]["end"]), "Coverage dates reversed")
        cap = 42 if output is a else 14
        require((iso_date(output["coverage"]["end"]) - iso_date(output["coverage"]["start"])).days < cap, "Coverage exceeds bounded lookback")
    require(len(a["items"]) <= 500 and len(w["items"]) <= 14, "Observation bounds exceeded")
    ids = set()
    for row in a["items"]:
        require(set(row) == {"id", "name", "type", "source", "source_tool", "source_id", "started_at", "original_start_time", "timezone_basis", "local_date", "distance_km", "duration_seconds", "pace_seconds_per_km", "avg_hr_bpm", "max_hr_bpm"}, "Unexpected activity fields (GPS/raw data must not be saved)")
        string(row.get("id"), "activity ID", 100)
        require(row["id"] not in ids, "Duplicate activity ID")
        ids.add(row["id"])
        iso_date(row.get("local_date"))
        require(row.get("source") == a["source"], "Activity provenance mismatch")
        require(row.get("source_id") == row["id"] and row.get("source_tool") == "get_activities_by_date", "Activity source identifier missing")
        require(a["coverage"]["start"] <= row["local_date"] <= a["coverage"]["end"], "Activity outside coverage")
        for key in ("distance_km", "duration_seconds", "pace_seconds_per_km", "avg_hr_bpm", "max_hr_bpm"):
            number(row.get(key), key, nullable=True)
        if row.get("started_at"):
            timestamp(row["started_at"])
            require(row["started_at"][:10] == row["local_date"], "Local date mismatch")
        km, sec = row["distance_km"], row["duration_seconds"]
        pace = row["pace_seconds_per_km"]
        if km and sec is not None:
            require(pace is not None and math.isclose(pace, sec / km, abs_tol=.011), "Derived pace differs from distance/duration")
        else:
            require(pace is None, "Pace requires positive distance and measured duration")
    dates = set()
    for night in w["items"]:
        require(set(night) == {"date", "values"} and set(night["values"]) == set(WELLNESS_FIELDS), "Unexpected wellness fields")
        iso_date(night.get("date"))
        require(night["date"] not in dates and w["coverage"]["start"] <= night["date"] <= w["coverage"]["end"], "Wellness duplicate or outside coverage")
        dates.add(night["date"])
        for key, (_, unit, cap) in WELLNESS_FIELDS.items():
            value = night["values"][key]
            require(set(value) == {"value", "unit", "source", "source_tool", "source_id", "missing_reason"}, "Unexpected wellness value fields")
            require(value.get("unit") == unit and value.get("source") == w["source"], "Wellness unit/source mismatch")
            number(value.get("value"), key, 0, cap, nullable=True)
            tools = ('get_sleep_summary_range','get_rhr_day','get_heart_rates_summary') if key == 'resting_hr_bpm' else ('get_sleep_summary_range',)
            require(value.get("source_id") == night["date"] and value.get("source_tool") in tools, "Wellness source identifier missing")
    if details is not None:
        from .dashboard import validate_details
        validate_details(details, bundle)
    refs = evidence_set(bundle, details)
    require(summary.get("source") == a["source"] and plan.get("source") == a["source"], "Coach source must match observations")
    timestamp(summary.get("generated_at"))
    timestamp(plan.get("generated_at"))
    string(summary.get("headline"), "summary headline", 300)
    require(isinstance(summary.get("uncertainty"), list), "Summary requires uncertainty list")
    for u in summary["uncertainty"]:
        string(u, "uncertainty")
    observations = summary.get("observations")
    require(isinstance(observations, list) and 1 <= len(observations) <= 30, "Summary requires observations")
    for obs in observations:
        require(set(obs) == {"kind", "text", "references"}, "Unexpected observation fields")
        require(obs.get("kind") in ("measured", "derived", "user_statement", "ai_suggestion"), "Unknown observation kind")
        string(obs.get("text"), "observation text")
        require(isinstance(obs.get("references"), list) and obs["references"] and all(r in refs for r in obs["references"]), "Unknown or missing observation evidence")
    string(plan.get("revision_id"), "plan revision", 100)
    require(re.fullmatch(r"[A-Za-z0-9_-]+", plan["revision_id"]), "Unsafe revision ID")
    string(plan.get("revision_reason"), "revision reason")
    require(type(plan.get("illustrative")) is bool, "Illustrative flag must be boolean")
    if not profile["profile_confirmed"] or a["source"] == "fixture":
        require(plan["illustrative"], "Unconfirmed or fixture plan must be illustrative")
    week = iso_date(plan.get("week_start"))
    require(week.weekday() == 0, "Week starts on Monday")
    sessions = plan.get("sessions")
    require(isinstance(sessions, list) and 1 <= len(sessions) <= 14, "Week requires 1–14 sessions")
    session_ids = set()
    previous_sessions = {item["id"]: item for item in previous["training_plan"]["sessions"]} if previous else {}
    for s in sessions:
        require(set(s) == {"id", "revision", "date", "title", "state", "rationale", "references", "steps"}, "Unexpected session fields")
        string(s.get("id"), "session ID", 100)
        require(s["id"] not in session_ids, "Duplicate session ID")
        session_ids.add(s["id"])
        string(s.get("revision"), "session revision", 100)
        day = iso_date(s.get("date"))
        require(week <= day <= week + timedelta(days=6), "Session outside plan week")
        require(s.get("state") in ("planned", "completed", "skipped"), "Unknown session state")
        string(s.get("title"), "session title", 150)
        string(s.get("rationale"), "session rationale")
        # An unchanged saved prescription may cite an older observation that
        # has since left the rolling 42-day window. Its prior validated source
        # remains in an archived snapshot; new or edited sessions need current evidence.
        historical = previous_sessions.get(s["id"]) == s or digest(s) in (archived_session_digests or set())
        allowed = refs | set(s["references"]) if historical and isinstance(s.get("references"), list) else refs
        require(isinstance(s.get("references"), list) and s["references"] and all(r in allowed for r in s["references"]), "Unknown or missing session evidence")
        steps = s.get("steps")
        require(isinstance(steps, list) and 3 <= len(steps) <= 100, "Session requires structured steps")
        require(steps[0].get("kind") == "warmup" and steps[-1].get("kind") == "cooldown", "Session requires warmup and cooldown")
        for step in steps:
            validate_step(step)
        if profile["profile_confirmed"] and s["state"] == "planned":
            require(DAYS[day.weekday()] in profile["availability"]["running_days"], "Session does not fit confirmed availability")
            limit = profile["constraints"]["max_session_minutes"]
            if limit is not None:
                require(all(t["unit"] == "seconds" for t in steps), "Use time-based steps to verify confirmed session time limit")
                require(sum(t["value"] for t in steps) <= limit * 60, "Session exceeds confirmed time limit")
    if previous:
        old = previous["training_plan"]
        require(plan["revision_id"] != old["revision_id"] or plan == old, "Changed plan must have a new revision ID")
        by_id = {s["id"]: s for s in sessions}
        for session in old["sessions"]:
            if session["state"] == "completed" and week == iso_date(old["week_start"]):
                require(by_id.get(session["id"]) == session, "Completed sessions are immutable")
            if session["id"] in by_id and by_id[session["id"]] != session:
                require(by_id[session["id"]]["revision"] != session["revision"], "Changed session requires new revision")
    return bundle


class Store:
    def __init__(self, directory=None):
        self.path = Path(directory) if directory else ROOT / "data"
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path, 0o700)

    @contextmanager
    def lock(self, name="state", blocking=True):
        with open(self.path / ("." + name + ".lock"), "a+") as lock:
            os.chmod(lock.name, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise Invalid("Another coach run is already active") from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def read(self, name, default=None):
        path = self.path / (name + ".json")
        return load_json(path) if path.exists() else copy.deepcopy(default)

    def write(self, name, value):
        atomic_json(self.path / (name + ".json"), value)

    def bootstrap(self):
        with self.lock():
            if not self.read("athlete_profile"):
                self.write("athlete_profile", load_json(ROOT / "athlete_profile.example.json"))
            if not self.read("feedback"):
                self.write("feedback", {"items": [], "schedule_changes": []})
            if not self.read("workout_registry"):
                self.write("workout_registry", {"entries": {}, "authorizations": {}})
            if not self.read("run_status"):
                self.write("run_status", {"state": "blocked", "review_required": True, "last_attempt": None, "last_successful_update": None,
                                           "blocked_steps": ["Garmin authentication", "Athlete profile confirmation"], "watch_verified": False})
        validate_profile(self.profile())

    def profile(self):
        return validate_profile(self.read("athlete_profile"))

    def current(self):
        pointer = self.read("current")
        if pointer is None:
            return None
        require(re.fullmatch(r"[a-f0-9]{32}", pointer.get("generation", "")), "Invalid snapshot pointer")
        folder = self.path / "snapshots" / pointer["generation"]
        return {k: load_json(folder / (k + ".json")) for k in OUTPUTS}

    def archived_session_digests(self):
        """Recognize unchanged prescriptions already saved in earlier snapshots."""
        current = self.read("current", {})
        current_generation = current.get("generation")
        found = set()
        for path in (self.path / "snapshots").glob("*/training_plan.json"):
            if path.parent.name == current_generation:
                continue
            plan = load_json(path)
            for session in plan.get("sessions", []):
                found.add(digest(session))
        return found

    def fingerprint(self, observations=None):
        bundle = observations if observations is not None else self.current()
        clean = {}
        if bundle:
            for key in ("activities", "wellness"):
                clean[key] = {k: v for k, v in bundle[key].items() if k != "fetched_at"}
        from .memory import read_memory
        from .planned import read_planned
        planned = read_planned(self)
        return digest({"profile": self.profile(), "feedback": self.read("feedback"), "memory": read_memory(self), "observations": clean,
                       "garmin_planned_runs": {"window": planned.get("window"), "coach_visibility": planned.get("coach_visibility"),
                                               "items": planned["items"]},
                       "training_signals": coaching_signals(self.read("dashboard_details"))})

    def commit(self, bundle, expected=None, details=None):
        """Caller holds state lock. One pointer publishes all four files atomically."""
        if expected is not None:
            require(self.fingerprint() == expected, "Inputs changed during coaching; previous output retained")
        candidate = copy.deepcopy(bundle)
        candidate["feedback"] = self.read("feedback")
        # A calendar read can reveal completion before the agent updates its plan.
        # Preserve that published session's prescription even if agent state lags.
        for entry in self.read("workout_registry", {"entries": {}})["entries"].values():
            if entry["state"] == "completed":
                if entry.get("origin") == "one_off":
                    continue
                planned = next((s for s in candidate["training_plan"]["sessions"] if s["id"] == entry["session_id"]), None)
                week = iso_date(candidate["training_plan"]["week_start"])
                if week <= iso_date(entry["date"]) <= week + timedelta(days=6):
                    require(planned is not None and planned["state"] == "completed", "Completed Garmin session must be retained as completed")
                if planned is not None:
                    comparable = lambda s: {k: v for k, v in s.items() if k not in ("state", "revision")}
                    require(comparable(planned) == comparable(entry["session"]), "Completed Garmin session prescription is immutable")
        if details is None:
            from .dashboard import saved_details
            details = saved_details(self, candidate)
        if details["source"] == "unavailable":
            details = None
        validate_bundle(candidate, self.profile(), self.current(), details)
        generation = uuid.uuid4().hex
        folder = self.path / "snapshots" / generation
        for key in OUTPUTS:
            atomic_json(folder / (key + ".json"), candidate[key])
        text = render_summary(candidate)
        folder.joinpath("summary.md").write_text(text, encoding="utf-8")
        os.chmod(folder / "summary.md", 0o600)
        self.write("current", {"generation": generation})
        status = self.read("run_status", {})
        status.update({"state": "success", "last_successful_update": now(), "last_attempt": now(), "review_required": False,
                       "last_input_fingerprint": self.fingerprint(candidate), "last_plan_revision": candidate["training_plan"]["revision_id"], "error": None,
                       "blocked_steps": (["Garmin authentication"] if candidate["activities"]["source"] == "fixture" else []) + ([] if self.profile()["profile_confirmed"] else ["Athlete profile confirmation"])})
        self.write("run_status", status)

    def mark(self, **updates):
        status = self.read("run_status", {})
        status.update(updates)
        self.write("run_status", status)

    def save_profile(self, p):
        validate_profile(p)
        with self.lock():
            current = self.profile()
            status = self.read("run_status", {})
            snapshot = self.current()
            blocked = [step for step in status.get("blocked_steps", [])
                       if step not in ("Athlete profile confirmation", "Personalized coaching review", "Garmin data refresh for timezone change")]
            if not p["profile_confirmed"]:
                blocked.append("Athlete profile confirmation")
            elif snapshot and snapshot["training_plan"]["illustrative"]:
                blocked.append("Personalized coaching review")
            if snapshot and snapshot["activities"]["source"] == "garmin" and snapshot["activities"]["timezone"] != p["timezone"]:
                blocked.append("Garmin data refresh for timezone change")
            if p != current:
                self.write("athlete_profile", p)
            self.mark(review_required=status.get("review_required", False) or p != current, blocked_steps=blocked)

    def feedback(self, body):
        string(body.get("text"), "feedback", 2000)
        require(body.get("feeling") in ("easy", "as_expected", "hard", "discomfort", "schedule"), "Unknown feedback type")
        session_id = body.get("session_id") or None
        activity_id = body.get("activity_id") or None
        with self.lock():
            bundle = self.current()
            if session_id:
                require(bundle is not None and any(s["id"] == session_id for s in bundle["training_plan"]["sessions"]), "Unknown session")
            if activity_id:
                require(bundle is not None and any(a["id"] == activity_id for a in bundle["activities"]["items"]), "Unknown activity")
            data = self.read("feedback")
            entry = {"id": uuid.uuid4().hex, "source": "user_statement", "created_at": now(), "text": body["text"], "feeling": body["feeling"], "session_id": session_id, "activity_id": activity_id}
            data["items"].append(entry)
            self.write("feedback", data)
            self.mark(review_required=True)
        return entry

    def change_session(self, session_id, body):
        with self.lock():
            bundle = self.current()
            require(bundle is not None, "No plan saved")
            session = next((s for s in bundle["training_plan"]["sessions"] if s["id"] == session_id), None)
            require(session is not None and session["state"] == "planned", "Only planned sessions can change")
            old = copy.deepcopy(session)
            if "date" in body:
                iso_date(body["date"])
                session["date"] = body["date"]
            if "state" in body:
                require(body["state"] in ("completed", "skipped"), "Invalid session transition")
                session["state"] = body["state"]
            string(body.get("reason"), "change reason", 1000)
            session["revision"] = uuid.uuid4().hex[:12]
            plan = bundle["training_plan"]
            plan.update(revision_id="edit_" + uuid.uuid4().hex[:12], generated_at=now(), revision_reason=body["reason"])
            self.commit(bundle)
            feedback = self.read("feedback")
            feedback["schedule_changes"].append({"session_id": session_id, "at": now(), "before": old, "after": copy.deepcopy(session), "reason": body["reason"], "source": "user_statement"})
            self.write("feedback", feedback)
            self.mark(review_required=True)


def render_summary(bundle):
    s, p = bundle["coach_summary"], bundle["training_plan"]
    lines = ["# " + s["headline"], "", "Source: " + s["source"] + (" — SYNTHETIC SAMPLE ONLY" if s["source"] == "fixture" else " — saved Garmin observations"), "", "Plan revision: " + p["revision_id"], "Reason: " + p["revision_reason"], ""]
    for obs in s["observations"]:
        lines.append(f"* [{obs['kind']}] {obs['text']} ({', '.join(obs['references'])})")
    lines.extend(["", "Uncertainty:"] + ["* " + x for x in s["uncertainty"]] + ["", "## " + ("Illustrative " if p["illustrative"] else "Draft ") + "week"])
    for session in p["sessions"]:
        lines.extend(["", f"### {session['date']} · {session['title']} · {session['state']}", session["rationale"], "Evidence: " + ", ".join(session["references"])])
        lines.extend(f"* {step['kind']}: {step['value']} {step['unit']} · {step['description']}" for step in session["steps"])
    return "\n".join(lines) + "\n"
