"""Bounded optional dashboard observations, separate from the coaching snapshot.

Real records are never completed with synthetic routes or wearable measurements.
Imports populate this sidecar without mixing measured and synthetic records.
"""
import math
from datetime import timedelta
from .core import Invalid, iso_date, now, number, require, string, timestamp

CATEGORIES = ("low_aerobic", "high_aerobic", "anaerobic")
METRICS = {"readiness": ("score/100", 100), "training_status": ("status", None),
           "acute_load": ("load", 100000), "vo2_max": ("mL/kg/min", 150)}
CONTEXT_METRICS = {"chronic_load": ("load", 100000), "load_ratio": ("ratio", 100),
                   "load_status": ("status", None), "load_focus_feedback": ("status", None),
                   "readiness_level": ("status", None), "readiness_feedback": ("status", None),
                   "recovery_hours": ("hours", 336), "weekly_hrv": ("ms", 300),
                   **{key + "_factor": ("percent", 100) for key in ("sleep", "recovery", "load", "hrv", "stress_history", "sleep_history")}}


def observation(value, unit, day, source_tool, recommended_range=None, missing_reason=None):
    return {"value": value, "unit": unit, "date": day, "source_tool": source_tool,
            "source_id": day, "recommended_range": recommended_range, "missing_reason": missing_reason}


def empty_details(reason="not_imported"):
    return {"version": 1, "source": "unavailable", "fetched_at": None, "activities": {},
            "training": {key: observation(None, unit, None, None, missing_reason=reason)
                         for key, (unit, _) in METRICS.items()},
            "load_focus": {"start": None, "end": None, "source_tool": None, "categories": {
                key: observation(None, "load", None, None, missing_reason=reason) for key in CATEGORIES}},
            "history": {key: [] for key in METRICS}}


def validate_observation(item, unit, cap):
    required = {"value", "unit", "date", "source_tool", "source_id", "recommended_range", "missing_reason"}
    optional = {"recorded_at", "original_timestamp", "timezone_basis", "source_method", "kind", "raw_value"}
    require(isinstance(item, dict) and required <= set(item) <= required | optional, "Invalid dashboard observation fields")
    require(item["unit"] == unit, "Dashboard observation unit mismatch")
    value = item["value"]
    if item["date"] is not None:
        iso_date(item["date"])
    if value is not None:
        if cap is None:
            string(value, "training status", 100)
        else:
            number(value, "dashboard observation", 0, cap)
        iso_date(item["date"])
        string(item["source_tool"], "observation tool", 100)
        string(item["source_id"], "observation identifier", 150)
        require(item["missing_reason"] is None, "Measured observation cannot be missing")
    else:
        string(item["missing_reason"], "missing reason", 300)
    bounds = item["recommended_range"]
    if bounds is not None:
        require(value is not None and cap is not None and isinstance(bounds, list) and len(bounds) == 2, "Invalid load target range")
        low, high = [number(x, "load bound", 0, cap) for x in bounds]
        require(low < high, "Load range must be increasing")
    if item.get("recorded_at") is not None:
        require(timestamp(item['recorded_at']).date().isoformat() == item['date'], 'Observation timestamp/date mismatch')
    for field in ('original_timestamp','timezone_basis','source_method'):
        if field in item:
            string(item[field], 'observation provenance', 150)
    if 'kind' in item:
        require(item['kind'] in ('garmin_reported','garmin_estimate','derived_from_activity'), 'Unknown observation kind')
    if 'raw_value' in item:
        require(isinstance(item['raw_value'],str) and len(item['raw_value']) <= 150, 'Invalid original observation value')


def validate_details(details, bundle):
    required = {"version", "source", "fetched_at", "activities", "training", "load_focus", "history"}
    require(isinstance(details, dict) and required <= set(details) <= required | {'context','context_history'}, "Invalid dashboard details")
    require(details["version"] == 1 and details["source"] == bundle["activities"]["source"], "Dashboard details must match snapshot source")
    timestamp(details["fetched_at"])
    activities = {a["id"]: a for a in bundle["activities"]["items"]}
    require(isinstance(details["activities"], dict) and len(details["activities"]) <= 50, "Dashboard activity limit exceeded")
    for activity_id, detail in details["activities"].items():
        require(activity_id in activities and {"source_tool", "source_id", "samples", "splits"} <= set(detail) <= {"source_tool", "source_id", "samples", "splits", "elapsed_duration_seconds", "record_count"}, "Unknown activity details")
        require(detail["source_id"] == activity_id, "Activity detail identifier mismatch")
        string(detail["source_tool"], "activity detail tool", 100)
        require(isinstance(detail["samples"], list) and len(detail["samples"]) <= 4000, "Too many activity samples")
        previous = -1
        previous_distance = -1
        duration = detail.get("elapsed_duration_seconds", activities[activity_id]["duration_seconds"])
        number(duration, "recorded elapsed duration", 0, 172800, nullable=True)
        if "record_count" in detail:
            number(detail["record_count"], "record count", len(detail["samples"]), 25000)
        for point in detail["samples"]:
            require(set(point) == {"time_seconds", "distance_km", "latitude", "longitude", "heart_rate_bpm", "pace_seconds_per_km", "elevation_m"}, "Invalid activity sample fields")
            elapsed = number(point["time_seconds"], "sample time", 0, 172800)
            require(elapsed > previous and (duration is None or elapsed <= duration + 2), "Sample times must increase within activity")
            previous = elapsed
            km = number(point["distance_km"], "sample distance", 0, 300, nullable=True)
            if km is not None:
                require(km >= previous_distance, "Sample distance must not decrease")
                previous_distance = km
            require((point["latitude"] is None) == (point["longitude"] is None), "Route coordinate requires latitude and longitude")
            number(point["latitude"], "latitude", -90, 90, nullable=True)
            number(point["longitude"], "longitude", -180, 180, nullable=True)
            number(point["heart_rate_bpm"], "sample heart rate", 0, 250, nullable=True)
            number(point["pace_seconds_per_km"], "sample pace", 20, 3600, nullable=True)
            number(point["elevation_m"], "elevation", -500, 10000, nullable=True)
        require(isinstance(detail["splits"], list) and len(detail["splits"]) <= 300, "Invalid activity splits")
        for index, split in enumerate(detail["splits"], 1):
            require(set(split) == {"index", "distance_km", "duration_seconds", "avg_hr_bpm"} and split["index"] == index, "Invalid split sequence")
            number(split["distance_km"], "split distance", .001, 300)
            number(split["duration_seconds"], "split duration", .01, 172800)
            number(split["avg_hr_bpm"], "split HR", 0, 250, nullable=True)
    require(set(details["training"]) == set(METRICS) and set(details["history"]) == set(METRICS), "Unexpected training metrics")
    for key, (unit, cap) in METRICS.items():
        validate_observation(details["training"][key], unit, cap)
        history = details["history"][key]
        require(isinstance(history, list) and len(history) <= 90, "Training history limit exceeded")
        dates = []
        for item in history:
            validate_observation(item, unit, cap)
            require(item["date"] is not None, "History requires observation date")
            dates.append(item["date"])
        require(dates == sorted(set(dates)), "Training history dates must be unique and sorted")
    focus = details["load_focus"]
    require({'start','end','source_tool','categories'} <= set(focus) <= {'start','end','source_tool','categories','period'} and set(focus["categories"]) == set(CATEGORIES), "Invalid load focus")
    if focus["start"] is not None:
        require(iso_date(focus["start"]) <= iso_date(focus["end"]), "Invalid load focus dates")
    elif focus['end'] is not None:
        iso_date(focus['end'])
    if 'period' in focus:
        require(focus['period'] == 'trailing_month', 'Unknown load-focus period')
    for category in focus["categories"].values():
        validate_observation(category, "load", 100000)
    context = details.get('context', {})
    require(isinstance(context,dict) and set(context) <= set(CONTEXT_METRICS), 'Unknown training context')
    for key,item in context.items():
        validate_observation(item,*CONTEXT_METRICS[key])
    history = details.get('context_history', {})
    require(isinstance(history,dict) and set(history) <= set(CONTEXT_METRICS), 'Unknown context history')
    for key,items in history.items():
        require(isinstance(items,list) and len(items) <= 90, 'Context history limit exceeded')
        dates=[]
        for item in items:
            validate_observation(item,*CONTEXT_METRICS[key])
            require(item['date'] is not None, 'History requires observation date')
            dates.append(item['date'])
        require(dates == sorted(set(dates)), 'Context history dates must be unique and sorted')
    return details


def demo_details(bundle):
    """Every coordinate/sample/target in this function is explicitly synthetic."""
    details = empty_details()
    details.update(source="fixture", fetched_at=now())
    day = bundle["activities"]["coverage"]["end"]
    values = {"readiness": 78, "training_status": "productive", "acute_load": 742, "vo2_max": 54}
    for key, value in values.items():
        unit = METRICS[key][0]
        bounds = [600, 850] if key == "acute_load" else None
        details["training"][key] = observation(value, unit, day, "synthetic_fixture", bounds)
        for index in range(28):
            date = (iso_date(day) - timedelta(days=27 - index)).isoformat()
            measured = value if isinstance(value, str) or index == 27 else round(value + math.sin(index / 3) * (8 if key != "vo2_max" else 1.5), 1)
            details["history"][key].append(observation(measured, unit, date, "synthetic_fixture", bounds))
    details["load_focus"].update(start=(iso_date(day) - timedelta(days=27)).isoformat(), end=day, source_tool="synthetic_fixture")
    for key, value, bounds in zip(CATEGORIES, (640, 320, 90), ([500, 800], [250, 450], [80, 160])):
        details["load_focus"]["categories"][key] = observation(value, "load", day, "synthetic_fixture", bounds)
    for run_index, activity in enumerate(bundle["activities"]["items"][:50]):
        duration, km = activity["duration_seconds"], activity["distance_km"]
        if duration is None or not km:
            continue
        points = []
        for index in range(161):
            f = index / 160
            angle = f * math.tau
            radius = 1 + .15 * math.sin(angle * 3 + run_index / 4)
            points.append({"time_seconds": round(duration * f, 3), "distance_km": round(km * f, 4),
                           "latitude": 52 + math.sin(angle) * radius * .008,
                           "longitude": .15 + math.cos(angle) * radius * .012,
                           "heart_rate_bpm": round(112 + 35 * min(1, f * 5) + 7 * math.sin(index * .12 + run_index) + 3 * math.sin(index * 1.7)),
                           "pace_seconds_per_km": round(activity["pace_seconds_per_km"] + 10 * math.sin(index * .14) + 4 * math.sin(index * 1.6), 1),
                           "elevation_m": round(25 + 8 * math.sin(angle * 2) + 2 * math.sin(angle * 7), 1)})
        splits = []
        remaining = km
        while remaining > .0001:
            length = min(1, remaining)
            splits.append({"index": len(splits) + 1, "distance_km": length,
                           "duration_seconds": round(length * duration / km, 2), "avg_hr_bpm": activity["avg_hr_bpm"]})
            remaining -= length
        details["activities"][activity["id"]] = {"source_tool": "synthetic_fixture", "source_id": activity["id"], "samples": points, "splits": splits}
    return validate_details(details, bundle)


def saved_details(store, bundle):
    if bundle is None:
        return empty_details()
    details = store.read("dashboard_details")
    if details is not None:
        try:
            return validate_details(details, bundle)
        except (Invalid, KeyError, TypeError, ValueError):
            return empty_details("saved_details_invalid_or_outdated")
    if bundle["activities"]["source"] == "fixture":
        return demo_details(bundle)
    return empty_details()
