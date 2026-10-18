"""Dry run exact review of the latest real-source chat proposal in a temporary store."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coach.core import Store, digest
from coach.proposals import review_chat_proposal


real = Store()
history = real.read("coach_chat", {"items": []})["items"]
entry = next(item for item in reversed(history) if item.get("observation_source") == "garmin" and item["result"].get("workout"))
before = digest(real.read("workout_registry"))
with tempfile.TemporaryDirectory(prefix="stride-review-dry-run-") as directory:
    test = Store(directory)
    test.bootstrap()
    with test.lock():
        test.write("athlete_profile", real.read("athlete_profile"))
        test.write("dashboard_details", real.read("dashboard_details"))
        test.commit(real.current())
        for name in ("feedback", "workout_registry", "run_status", "athlete_memory", "coach_chat"):
            value = real.read(name)
            if value is not None:
                test.write(name, value)
    assert test.fingerprint() == real.fingerprint()
    reviewed = review_chat_proposal(test, entry["id"])
    registry = test.read("workout_registry")
    assert reviewed["id"] in registry["one_off_sessions"]
    assert len(registry["authorizations"]) == len(real.read("workout_registry")["authorizations"]) + 1
assert digest(real.read("workout_registry")) == before
evidence = {"check": "latest live Claude proposal can pass exact local review", "result": "passed",
            "temporary_store_only": True, "real_registry_unchanged": True,
            "garmin_uploads": 0, "private_proposal": "omitted"}
Path("docs/evidence/chat-workout-review-check.json").write_text(json.dumps(evidence, indent=2) + "\n")
print("Latest live proposal passed exact-review dry run; real registry and Garmin unchanged.")
