"""Disposable browser rehearsal of chat approval and Garmin scheduling."""
import copy
import sys
import tempfile
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coach.core import Store, iso_date
from coach.fixtures import make_demo
from coach.server import make_server
from tests.test_coach import FakeGarmin


with tempfile.TemporaryDirectory(prefix="stride-proposal-browser-") as directory:
    store = Store(directory)
    store.bootstrap()
    with store.lock():
        store.commit(make_demo(store.profile()))
    profile = store.profile()
    profile["profile_confirmed"] = True
    profile["automation"]["garmin_write_policy"] = "selected_workouts"
    store.save_profile(profile)
    bundle = store.current()
    for key in ("activities", "wellness", "coach_summary", "training_plan"):
        bundle[key]["source"] = "garmin"
    for activity in bundle["activities"]["items"]:
        activity["source"] = "garmin"
    for day in bundle["wellness"]["items"]:
        for value in day["values"].values():
            value["source"] = "garmin"
    bundle["training_plan"].update(illustrative=False, revision_id="simulated_garmin_browser")
    with store.lock():
        store.commit(bundle)

    session = bundle["training_plan"]["sessions"][0]
    draft = {key: copy.deepcopy(session[key]) for key in ("title", "date", "rationale", "references", "steps")}
    draft["date"] = (iso_date(session["date"]) + timedelta(days=7)).isoformat()
    answer = {"answer": "A controlled workout for your next open running day. Review the date and steps before saving it.",
              "references": draft["references"], "action": "none", "workout": draft}
    garmin = FakeGarmin()
    server = make_server(store, 8767, chat_executor=lambda *_: copy.deepcopy(answer),
                         workout_client_factory=lambda: nullcontext(garmin),
                         workout_read_factory=lambda: nullcontext(garmin))
    try:
        print("Disposable simulated-Garmin rehearsal at http://127.0.0.1:8767", flush=True)
        server.serve_forever()
    finally:
        server.server_close()
