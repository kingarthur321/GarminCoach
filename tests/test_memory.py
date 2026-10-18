"""Reviewable memory behavior with a disposable synthetic athlete store."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from coach.chat import CoachChat, context as chat_context
from coach.core import Invalid, Store, digest
from coach.fixtures import make_demo
from coach.memory import create, delete, for_context, read_memory, update
from coach.server import make_server
from coach.workflow import accept, prepare


class MemoryCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(temp.name)
        self.store.bootstrap()
        with self.store.lock():
            self.store.commit(make_demo(self.store.profile()))

    def test_reviewed_note_reaches_chat_and_planning_and_can_be_corrected_and_deleted(self):
        original = digest(self.store.current())
        staged = prepare(self.store)
        item = create(self.store, {"kind": "user_statement", "text": "I lift on Mondays.", "chat_id": None})
        self.assertEqual(read_memory(self.store)["items"][0]["source"]["type"], "manual")
        self.assertEqual(chat_context(self.store, None)[0]["memory"][0]["text"], item["text"])
        self.assertEqual(prepare(self.store)["athlete_memory"][0]["text"], item["text"])
        self.assertTrue(self.store.read("run_status")["review_required"])
        self.assertEqual(digest(self.store.current()), original)
        bundle = self.store.current()
        with self.assertRaisesRegex(Invalid, "Inputs changed"):
            accept(self.store, {k: bundle[k] for k in ("coach_summary", "training_plan")}, staged)
        changed = update(self.store, {"id": item["id"], "revision": 1, "text": "I lift on Wednesdays."})
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(for_context(self.store)[0]["text"], "I lift on Wednesdays.")
        with self.assertRaisesRegex(Invalid, "changed"):
            update(self.store, {"id": item["id"], "revision": 1, "text": "stale"})
        delete(self.store, {"id": item["id"], "revision": 2})
        self.assertEqual(read_memory(self.store)["items"], [])
        self.assertEqual(chat_context(self.store, None)[0]["memory"], [])

    def test_coach_insight_keeps_chat_provenance_and_requires_explicit_review(self):
        chat_id = "a" * 32
        with self.store.lock():
            self.store.write("coach_chat", {"items": [{"id": chat_id, "observation_source": "fixture", "result": {"answer": "A controlled workout idea", "references": ["profile:goal"]}}]})
        self.assertEqual(read_memory(self.store)["items"], [])
        item = create(self.store, {"kind": "coach_insight", "text": "Keep Thursday controlled.", "chat_id": chat_id})
        self.assertEqual(item["source"], {"type": "chat", "chat_id": chat_id, "references": ["profile:goal"], "data_source": "fixture"})
        self.assertEqual(for_context(self.store, "garmin"), [])
        with self.assertRaisesRegex(Invalid, "Choose a saved coach answer"):
            create(self.store, {"kind": "coach_insight", "text": "Forged", "chat_id": "bad"})
        with self.assertRaisesRegex(Invalid, "Manual memory"):
            create(self.store, {"kind": "user_statement", "text": "Forged", "chat_id": chat_id})
        self.assertEqual(len(read_memory(self.store)["items"]), 1)

    def test_memory_context_is_bounded_without_erasing_saved_items(self):
        for index in range(25):
            create(self.store, {"kind": "user_statement", "text": f"Training preference {index}", "chat_id": None})
        self.assertEqual(len(read_memory(self.store)["items"]), 25)
        self.assertEqual(len(for_context(self.store)), 20)
        self.assertEqual(for_context(self.store)[0]["text"], "Training preference 24")
        self.assertNotIn("Training preference 0", [item["text"] for item in for_context(self.store)])
        with self.assertRaises(Invalid):
            create(self.store, {"kind": "user_statement", "text": "x" * 601, "chat_id": None})

    def test_memory_edit_during_answer_does_not_save_stale_chat(self):
        first = create(self.store, {"kind": "user_statement", "text": "Gym on Monday", "chat_id": None})
        done = threading.Event()
        def answer(*_):
            update(self.store, {"id": first["id"], "revision": 1, "text": "Gym on Wednesday"})
            done.set()
            return {"answer": "Monday gym", "references": [], "action": "none", "workout": None}
        chat = CoachChat(self.store, executor=answer)
        job = chat.start({"message": "When is gym?"})
        self.assertTrue(done.wait(1))
        for _ in range(100):
            result = chat.get(job["id"])
            if result["state"] != "working":
                break
            threading.Event().wait(.01)
        self.assertEqual(result["state"], "failed")
        self.assertIn("memory changed", result["error"])
        self.assertIsNone(self.store.read("coach_chat"))


class MemoryHttpCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(temp.name)
        self.store.bootstrap()
        with self.store.lock():
            self.store.commit(make_demo(self.store.profile()))
        self.server = make_server(self.store, 0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.token = json.load(urllib.request.urlopen(self.base + "/api/dashboard"))["csrf_token"]

    def post(self, path, body, token=None):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "X-Coach-Token": token or self.token, "Origin": self.base})
        return urllib.request.urlopen(req)

    def test_memory_http_round_trip_and_csrf(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post("/api/memory/create", {"kind": "user_statement", "text": "hello", "chat_id": None}, token="invalid")
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        with self.post("/api/memory/create", {"kind": "user_statement", "text": "Wednesday gym", "chat_id": None}) as response:
            item = json.load(response)
        with urllib.request.urlopen(self.base + "/api/dashboard") as response:
            self.assertEqual(json.load(response)["athlete_memory"]["items"][0]["text"], "Wednesday gym")
        with self.post("/api/memory/update", {"id": item["id"], "revision": 1, "text": "Friday gym"}) as response:
            self.assertEqual(json.load(response)["revision"], 2)
        with self.post("/api/memory/delete", {"id": item["id"], "revision": 2}) as response:
            self.assertTrue(json.load(response)["deleted"])
        self.assertEqual(read_memory(self.store)["items"], [])


if __name__ == "__main__":
    unittest.main()
