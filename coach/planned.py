"""Read-only import of the running workouts Garmin Connect currently exposes."""
from datetime import date, timedelta

from .core import Invalid, iso_date, now, require

EMPTY = {"version": 1, "source": "not_imported", "imported_at": None, "window": None,
         "coach_week_dates": [], "coach_visibility": "not_checked", "items": []}
MAX_ITEMS = 500


def reference(item):
    return "garmin_planned:" + item["date"] + ":" + str(item["workout_id"] or item["workout_uuid"])


def read_planned(store):
    value = store.read("garmin_planned_runs", EMPTY)
    require(isinstance(value, dict) and value.get("version") == 1 and value.get("source") in ("not_imported", "garmin"),
            "Saved Garmin planned runs are invalid")
    require(isinstance(value.get("items"), list) and len(value["items"]) <= MAX_ITEMS,
            "Saved Garmin planned runs exceed the supported limit")
    for item in value["items"]:
        require(isinstance(item, dict) and isinstance(item.get("date"), str)
                and isinstance(item.get("name"), str) and item.get("source_kind") in ("calendar", "garmin_coach"),
                "Saved Garmin planned run is invalid")
        iso_date(item["date"])
    return value


def _rows(response, key, empty_message):
    require(isinstance(response, dict), "Garmin planned workout response is malformed")
    if response.get("empty"):
        require(str(response.get("message", "")).startswith(empty_message),
                "Garmin planned workout read was ambiguous; previous import retained")
        return []
    rows = response.get(key)
    require(isinstance(rows, list) and response.get("count") == len(rows),
            "Garmin planned workout response is incomplete; previous import retained")
    return rows


def _running(row):
    if row.get("is_rest_day") or row.get("completed"):
        return False
    sport = row.get("sport")
    return isinstance(sport, str) and "run" in sport.lower()


def _clean(row, source_kind):
    require(isinstance(row, dict), "Garmin planned workout row is malformed")
    day = iso_date(row.get("date"))
    name = row.get("name")
    require(isinstance(name, str) and 1 <= len(name) <= 200, "Garmin planned workout name is missing")
    ident = row.get("workout_id") or row.get("workout_uuid")
    require((type(ident) is int and ident > 0) or (isinstance(ident, str) and 1 <= len(ident) <= 100),
            "Garmin planned workout identifier is missing")
    return {"date": day.isoformat(), "name": name, "sport": row["sport"],
            "source_kind": source_kind, "workout_id": row.get("workout_id"),
            "workout_uuid": row.get("workout_uuid"), "scheduled_workout_id": row.get("scheduled_workout_id"),
            "training_plan": row.get("training_plan"), "workout_type": row.get("workout_type"),
            "is_race_day": bool(row.get("is_race_day")),
            "estimated_duration_seconds": row.get("estimated_duration_seconds"),
            "estimated_distance_meters": row.get("estimated_distance_meters"),
            "completed": False, "description": None, "segments": []}


def import_planned(store, client, *, start=None, horizon_days=365, coach_weeks=5):
    """Replace the local calendar snapshot only after every required read succeeds."""
    require(1 <= horizon_days <= 366 and 1 <= coach_weeks <= 8, "Invalid planned run import window")
    start = start or date.today()
    end = start + timedelta(days=horizon_days)
    calendar = client.call("get_scheduled_workouts", {"start_date": start.isoformat(), "end_date": end.isoformat()})
    calendar_rows = _rows(calendar, "scheduled_workouts", "No workouts scheduled between ")
    if calendar_rows:
        require(calendar.get("date_range") == {"start": start.isoformat(), "end": end.isoformat()},
                "Garmin calendar date range does not match the request")
    items = {}
    for row in calendar_rows:
        require(isinstance(row, dict), "Garmin calendar row is malformed")
        if not _running(row):
            continue
        item = _clean(row, "calendar")
        require(start <= iso_date(item["date"]) <= end, "Garmin calendar returned an out-of-range date")
        key = (item["date"], str(item["workout_id"] or item["workout_uuid"]))
        require(key not in items, "Garmin calendar returned duplicate planned runs")
        items[key] = item
    week_dates = [(start + timedelta(days=7 * n)).isoformat() for n in range(coach_weeks)]
    coach_rows_seen = False
    for day in week_dates:
        response = client.call("get_garmin_coach_workouts", {"calendar_date": day})
        rows = _rows(response, "workouts", "No training plan workouts scheduled for ")
        coach_rows_seen = coach_rows_seen or bool(rows)
        for row in rows:
            require(isinstance(row, dict), "Garmin Coach row is malformed")
            if not _running(row):
                continue
            item = _clean(row, "garmin_coach")
            if not start <= iso_date(item["date"]) <= end:
                continue
            key = (item["date"], str(item["workout_id"] or item["workout_uuid"]))
            items.setdefault(key, item)  # Calendar entry takes precedence over plan duplicate.
    require(len(items) <= MAX_ITEMS, "Garmin returned too many planned runs for a complete import")
    for item in items.values():
        ident = item["workout_id"] or item["workout_uuid"]
        detail = client.call("get_workout_by_id", {"workout_id": ident})
        require(isinstance(detail, dict) and (detail.get("id") == ident or detail.get("uuid") == ident),
                "Garmin planned workout detail did not match its identifier")
        require(isinstance(detail.get("segments", []), list), "Garmin planned workout steps are malformed")
        item["description"] = detail.get("description") if isinstance(detail.get("description"), str) else None
        item["segments"] = detail.get("segments", [])
    result = {"version": 1, "source": "garmin", "imported_at": now(),
              "window": {"start": start.isoformat(), "end": end.isoformat()},
              "coach_week_dates": week_dates, "coach_visibility": "workouts_found" if coach_rows_seen else "no_workouts_reported",
              "items": sorted(items.values(), key=lambda item: (item["date"], item["name"], str(item["workout_id"] or item["workout_uuid"])))}
    with store.lock():
        store.write("garmin_planned_runs", result)
    return result
