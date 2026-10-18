"""One live local Claude workout draft; never approves or sends to Garmin."""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as response:
        return json.load(response)


before = get("/api/dashboard")
assert before["coach_connection"]["configured"]
assert before["snapshot"]["activities"]["source"] == "garmin"
assert before["profile"]["profile_confirmed"]
assert before["profile"]["automation"]["garmin_write_policy"] == "selected_workouts"
tracked = len(before["registry"]["entries"])
extras = len(before["registry"].get("one_off_sessions", {}))
message = "Draft one structured, controlled running workout for my next available running day after my saved training week. Use my goal and recent results. Choose a date without another saved workout."
request = urllib.request.Request(BASE + "/api/coach", data=json.dumps({"message": message}).encode(),
                                 headers={"Content-Type": "application/json", "Origin": BASE,
                                          "X-Coach-Token": before["csrf_token"]}, method="POST")
with urllib.request.urlopen(request, timeout=5) as response:
    job = json.load(response)
for _ in range(120):
    time.sleep(1)
    result = get("/api/coach/job?id=" + urllib.parse.quote(job["id"]))
    if result["state"] != "working":
        break
assert result["state"] == "complete", result.get("error", "Coach job did not finish")
entry = result["result"]
draft = entry["result"]["workout"]
assert draft and draft["steps"] and draft["references"]
assert entry["observation_source"] == "garmin" and not entry["illustrative"]
assert entry.get("input_fingerprint") and entry.get("plan_digest")
after = get("/api/dashboard")
assert len(after["registry"]["entries"]) == tracked
assert len(after["registry"].get("one_off_sessions", {})) == extras
assert not any(s["date"] == draft["date"] for s in after["snapshot"]["training_plan"]["sessions"])
assert not any(s["date"] == draft["date"] for s in after["registry"].get("one_off_sessions", {}).values())
evidence = {"check": "live Sonnet workout draft through localhost dashboard", "result": "passed",
            "structured_workout": True, "evidence_references": True,
            "date_avoids_saved_workouts": True, "chat_saved": any(i["id"] == job["id"] for i in after["coach_chat"]["items"]),
            "garmin_registry_unchanged": True, "no_workout_approval_or_publish": True,
            "private_answer_and_date": "omitted"}
Path("docs/evidence/chat-workout-live-check.json").write_text(json.dumps(evidence, indent=2) + "\n")
print("Live Claude workout draft passed; saved in chat. No Garmin workout was approved or sent.")
