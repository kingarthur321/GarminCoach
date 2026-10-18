"""Deterministic synthetic examples. Never interpreted as measured athlete data."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .core import DAYS, normalize_activities, normalize_wellness, now, weekly_distance


def step(kind, seconds, description, effort="conversational"):
    return {"kind": kind, "unit": "seconds", "value": seconds, "description": description,
            "target": {"kind": "effort", "value": effort}}


def make_demo(profile):
    today = datetime.now(ZoneInfo(profile["timezone"])).date()
    monday = today - timedelta(days=today.weekday())
    rows = []
    # Four complete synthetic training weeks; deterministic IDs per date.
    for week, distances in enumerate(((5.0, 6.0, 8.0), (5.5, 6.0, 8.5), (6.0, 6.5, 9.0), (5.0, 6.0, 8.0))):
        start = monday - timedelta(weeks=4 - week)
        for day_index, km in zip((1, 3, 6), distances):
            day = start + timedelta(days=day_index)
            rows.append({"id": "sample-" + day.isoformat(), "name": "Sample · " + ("Sunday easy run" if day_index == 6 else "Park loop"), "type": "running", "start_time": day.isoformat() + "T07:15:00", "distance_meters": km * 1000, "duration_seconds": round(km * (345 if day_index != 3 else 332)), "avg_hr_bpm": 143 if day_index != 3 else 151, "max_hr_bpm": 160})
    activities = normalize_activities(rows, profile["timezone"], "fixture", (today - timedelta(days=41)).isoformat(), today.isoformat())
    nights = []
    for i in range(14):
        day = today - timedelta(days=13 - i)
        if i in (3, 10):
            continue
        nights.append({"date": day.isoformat(), "sleep_seconds": (7.2 + (i % 4) * .2) * 3600,
                       "sleep_score": 76 + i % 7, "avg_overnight_hrv": None if i == 5 else 47 + i % 5, "resting_heart_rate_bpm": 51 + i % 3})
    wellness = normalize_wellness(nights, "fixture", (today - timedelta(days=13)).isoformat(), today.isoformat())
    weeks = weekly_distance(activities)
    next_monday = monday + timedelta(days=7)
    sessions = []
    for index, day in enumerate(sorted(profile["availability"]["running_days"], key=DAYS.index)):
        minutes = min((30, 32, 40, 30, 30, 30, 30)[index], profile["constraints"]["max_session_minutes"] or 40)
        warm = min(8 * 60, int(minutes * 60 / 4))
        cool = min(5 * 60, int(minutes * 60 / 4))
        middle = int(minutes * 60) - warm - cool
        date_str = (next_monday + timedelta(days=DAYS.index(day))).isoformat()
        sessions.append({"id": "sample-session-" + date_str, "revision": "sample-v1", "date": date_str, "title": ("Easy reset", "Steady and relaxed", "Longer easy run", "Easy run", "Easy run", "Easy run", "Easy run")[index], "state": "planned",
                         "rationale": "Illustrative conversational session using example availability. Confirm your profile and review actual history before using it.",
                         "references": ["profile:availability", "derived:weekly_distance:" + weeks[-1]["week_start"]],
                         "steps": [step("warmup", warm, "Start gently; comfortable walk or jog"), step("run", middle, "Relaxed running; able to speak in full sentences"), step("cooldown", cool, "Slow jog or walk")]})
    summary = {"source": "fixture", "generated_at": now(), "headline": "A steady rhythm, with room to build",
               "observations": [
                   {"kind": "derived", "text": "Synthetic history shows three runs a week and 19–21.5 km in each complete week.", "references": ["derived:weekly_distance:" + x["week_start"] for x in weeks]},
                   {"kind": "measured", "text": "Sample sleep coverage includes 12 of 14 nights. Two nights and one HRV value are deliberately missing.", "references": ["wellness:" + nights[-1]["date"] + ":sleep_seconds"]},
                   {"kind": "ai_suggestion", "text": "This illustrative week uses conversational effort. The example goal pace is not a daily training target.", "references": ["profile:goal", "profile:availability"]}],
               "uncertainty": ["All observations are synthetic examples, not your Garmin records.", "Goal, availability, other training and constraints need your confirmation.", "Wearable signals describe observations, not diagnoses or performance guarantees."]}
    plan = {"source": "fixture", "generated_at": now(), "revision_id": "sample_" + next_monday.isoformat().replace("-", ""), "revision_reason": "Initial synthetic demonstration; no personal prescription", "week_start": next_monday.isoformat(), "illustrative": True, "sessions": sessions}
    return {"activities": activities, "wellness": wellness, "coach_summary": summary, "training_plan": plan}
