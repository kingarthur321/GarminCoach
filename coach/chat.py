"""On-demand, bounded Claude API conversation with guarded local workout delivery.

Uses a local API key, supplies saved observations through an isolated worker,
and validates the response before saving local conversation/proposal history.
"""
import copy
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from .core import ROOT, DAYS, Invalid, digest, evidence_set, iso_date, now, require, string, validate_step, weekly_distance
from .dashboard import saved_details
from .anthropic_api import PROMPT_SEPARATOR, key_available
from .memory import for_context
from .planned import read_planned, reference as planned_reference

CHAT_PROMPT = ROOT / "prompts" / "coach-chat.md"


def wants_workout_created(message):
    """Only an explicit creation request can authorize automatic delivery."""
    if re.search(r"\b(draft|proposal|idea|suggest|suggestion|utkast|förslag|idé|don't send|do not send|without sending)\b", message, re.I):
        return False
    return bool(re.search(r"^\s*(?:(?:please|coach[,]?|hey coach[,]?)\s+)*(?:(?:can|could|would)\s+you\s+|i want you to\s+|i'd like you to\s+|(?:kan|skulle)\s+du\s+|jag vill att du\s+)?(?:please\s+)?(?:create|build|make|schedule|skapa|schemalägg)\b.{0,100}\b(?:workouts?|training sessions?|runs?|träningspass|löppass)\b", message, re.I))


def system_instructions():
    return CHAT_PROMPT.read_text(encoding="utf-8").strip() + "\n"


def command():
    return [sys.executable, "-m", "coach.anthropic_worker"]


def run_agent(prompt, timeout, cancelled):
    require(key_available(), "Claude API key is missing. Add ANTHROPIC_API_KEY to the project .env file, then retry.")
    process = subprocess.Popen(command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, cwd=ROOT, start_new_session=True)
    started = time.monotonic()
    supplied = False
    try:
        while True:
            if cancelled.is_set():
                raise Invalid("Coach request cancelled")
            if time.monotonic() - started > timeout:
                raise Invalid("Coach response timed out. Your saved training data is unchanged.")
            try:
                output, _ = process.communicate(prompt if not supplied else None, timeout=.2)
                break
            except subprocess.TimeoutExpired:
                supplied = True
        require(process.returncode == 0, "Claude API worker failed. Your saved training data is unchanged.")
        try:
            envelope = json.loads(output)
            require(isinstance(envelope, dict) and type(envelope.get("ok")) is bool, "Invalid Claude API worker response")
            if not envelope["ok"]:
                raise Invalid(envelope.get("error", "Claude API response unavailable."))
            require(isinstance(envelope.get("result"), dict), "Invalid Claude API worker response")
            return envelope["result"]
        except (ValueError, TypeError):
            raise Invalid("Claude API response unavailable. Your saved training data is unchanged.") from None
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def context(store, activity_id):
    with store.lock():
        bundle = store.current()
        require(bundle is not None, "Save a training snapshot before asking the coach")
        profile = store.profile()
        selected = next((a for a in bundle["activities"]["items"] if a["id"] == activity_id), None)
        require(activity_id is None or selected is not None, "Unknown selected activity")
        feedback = store.read("feedback")
        bundle["feedback"] = feedback
        details = saved_details(store, bundle)
        refs = evidence_set(bundle)
        for key, item in details["training"].items():
            if item["value"] is not None:
                refs.add("dashboard:" + key + ":" + item["date"])
        for key,item in details.get('context',{}).items():
            if item['value'] is not None:
                refs.add('dashboard:'+key+':'+item['date'])
        for key,item in details['load_focus']['categories'].items():
            if item['value'] is not None:
                refs.add('dashboard:load_focus:'+key+':'+item['date'])
        history = store.read("coach_chat", {"items": []})["items"][-6:]
        registry = store.read("workout_registry", {"entries": {}, "one_off_sessions": {}})
        garmin_planned = read_planned(store)
        refs.update(planned_reference(item) for item in garmin_planned["items"])
        occupied = {s["date"] for s in bundle["training_plan"]["sessions"]}
        occupied.update(s["date"] for s in registry.get("one_off_sessions", {}).values())
        occupied.update(e["date"] for e in registry.get("entries", {}).values())
        occupied.update(item["date"] for item in garmin_planned["items"])
        payload = {"source": bundle["activities"]["source"], "profile_confirmed": profile["profile_confirmed"],
                   "input_fingerprint": store.fingerprint(),
                   "timezone": profile["timezone"], "goal": profile["goal"], "availability": profile["availability"],
                   "constraints": profile["constraints"], "selected_run": selected,
                   "recent_runs": bundle["activities"]["items"][:12], "weekly_distance": weekly_distance(bundle["activities"]),
                   "wellness": bundle["wellness"], "training_metrics": details["training"], "load_focus": details["load_focus"],
                   "training_context": details.get("context", {}),
                   "saved_summary": bundle["coach_summary"], "saved_plan": bundle["training_plan"],
                   "garmin_planned_runs": {"source": garmin_planned["source"], "imported_at": garmin_planned["imported_at"],
                                           "window": garmin_planned["window"], "total": len(garmin_planned["items"]),
                                           "items": [{**{key: item.get(key) for key in ("date", "name", "sport", "source_kind", "estimated_duration_seconds", "estimated_distance_meters")},
                                                      "reference": planned_reference(item)}
                                                     for item in garmin_planned["items"][:60]]},
                   "existing_workout_dates": sorted(occupied),
                   "feedback": feedback["items"][-10:], "memory": for_context(store, bundle["activities"]["source"]),
                   "conversation": history, "allowed_references": sorted(refs), "now": now()}
    return payload, refs, profile


def validate_response(result, refs, profile):
    require(isinstance(result, dict) and set(result) == {"answer", "references", "action", "workout"}, "Invalid coach answer fields")
    string(result["answer"], "coach answer", 6000)
    require(isinstance(result["references"], list) and len(result["references"]) <= 30 and all(r in refs for r in result["references"]), "Coach answer cites unknown evidence")
    require(result["action"] in ("none", "show_trends", "show_plan", "compare_runs"), "Unknown coach view action")
    draft = result["workout"]
    if draft is not None:
        require(isinstance(draft, dict) and set(draft) == {"title", "date", "rationale", "references", "steps"}, "Invalid coach workout proposal")
        string(draft["title"], "draft title", 150)
        day = iso_date(draft["date"])
        string(draft["rationale"], "draft rationale", 4000)
        require(isinstance(draft["references"], list) and draft["references"] and all(r in refs for r in draft["references"]), "Unknown workout evidence")
        require(isinstance(draft["steps"], list) and 3 <= len(draft["steps"]) <= 100, "Draft requires structured steps")
        require(draft["steps"][0]["kind"] == "warmup" and draft["steps"][-1]["kind"] == "cooldown", "Draft requires warmup and cooldown")
        for step in draft["steps"]:
            validate_step(step)
        if profile["profile_confirmed"]:
            require(DAYS[day.weekday()] in profile["availability"]["running_days"], "Proposal does not fit confirmed running days")
            limit = profile["constraints"]["max_session_minutes"]
            if limit is not None:
                require(all(s["unit"] == "seconds" for s in draft["steps"]) and sum(s["value"] for s in draft["steps"]) <= limit * 60, "Proposal exceeds confirmed session limit")
    return result


class CoachChat:
    def __init__(self, store, executor=None, timeout=120, workout_publisher=None):
        self.store, self.executor, self.timeout = store, executor or run_agent, timeout
        self.workout_publisher = workout_publisher
        self.lock = threading.Lock()
        self.jobs = {}
        self.active = None

    def start(self, body):
        require(set(body) <= {"message", "activity_id", "auto_send"}, "Unexpected coach request fields")
        message = string(body.get("message"), "coach question", 2000)
        auto_send = body.get("auto_send", False)
        require(type(auto_send) is bool, "Automatic send must be a boolean")
        require(not auto_send or wants_workout_created(message), "Ask the coach explicitly to create a workout before automatic sending")
        payload, refs, profile = context(self.store, body.get("activity_id"))
        with self.lock:
            require(self.active is None, "The coach is answering another question. Wait or cancel it first.")
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "state": "working", "message": message, "created_at": now(), "result": None, "error": None,
                   "auto_send_requested": auto_send, "phase": "answering"}
            event = threading.Event()
            self.jobs[job_id] = (job, event)
            self.active = job_id
            while len(self.jobs) > 20:
                self.jobs.pop(next(iter(self.jobs)))
        thread = threading.Thread(target=self._work, args=(job_id, message, payload, refs, profile), daemon=True)
        thread.start()
        return copy.deepcopy(job)

    def _work(self, job_id, message, payload, refs, profile):
        job, event = self.jobs[job_id]
        try:
            instructions = system_instructions()
            if job["auto_send_requested"]:
                instructions += "\nThe athlete asked to create and send one workout. Return one structured workout if the saved context supports it. The app handles Garmin delivery after validation; do not claim it was sent or ask for another approval.\n"
            result = self.executor(instructions + PROMPT_SEPARATOR + json.dumps({"question": message, "context": payload}, ensure_ascii=False), self.timeout, event)
            validate_response(result, refs, profile)
            require(not event.is_set(), "Coach request cancelled")
            entry = {"id": job_id, "source": "ai_suggestion", "observation_source": payload["source"], "created_at": now(), "question": message,
                     "input_fingerprint": payload["input_fingerprint"], "plan_digest": digest(payload["saved_plan"]),
                     "activity_id": (payload["selected_run"] or {}).get("id"), "result": result,
                     "illustrative": not profile["profile_confirmed"] or payload["source"] == "fixture",
                     "auto_send_requested": job["auto_send_requested"]}
            with self.store.lock():
                require(digest(for_context(self.store, payload["source"])) == digest(payload["memory"]),
                        "Athlete memory changed while the coach answered. Ask again for an up-to-date answer.")
                require(self.store.fingerprint() == payload["input_fingerprint"],
                        "Training inputs changed while the coach answered. Ask again for an up-to-date answer.")
                require(digest(self.store.current()["training_plan"]) == entry["plan_digest"],
                        "Saved training week changed while the coach answered. Ask again for an up-to-date answer.")
                history = self.store.read("coach_chat", {"items": []})
                history["items"] = (history["items"] + [entry])[-40:]
                self.store.write("coach_chat", history)
            if job["auto_send_requested"]:
                delivery = {"state": "blocked", "message": "The coach did not return a structured workout."}
                if result["workout"] is not None and self.workout_publisher is not None and not event.is_set():
                    with self.lock:
                        job["phase"] = "publishing"
                    try:
                        published = self.workout_publisher(job_id)
                        delivery = {"state": published["state"], "session_id": published["session_id"]}
                    except Exception as exc:
                        delivery = {"state": "blocked", "message": str(exc) if isinstance(exc, Invalid) else
                                    "Garmin connection unavailable. Check the saved workout status before retrying."}
                with self.store.lock():
                    history = self.store.read("coach_chat", {"items": []})
                    saved = next((item for item in history["items"] if item["id"] == job_id), None)
                    if saved is not None:
                        saved["delivery"] = delivery
                        self.store.write("coach_chat", history)
                        entry = saved
            with self.lock:
                job.update(state="complete", result=entry)
        except Exception as exc:
            with self.lock:
                job.update(state="cancelled" if event.is_set() else "failed", error=str(exc) if isinstance(exc, Invalid) else "Coach response unavailable or invalid. Your saved training data is unchanged.")
        finally:
            with self.lock:
                self.active = None

    def current_job(self):
        with self.lock:
            return copy.deepcopy(self.jobs[self.active][0]) if self.active else None

    def get(self, job_id):
        require(isinstance(job_id, str) and re.fullmatch(r"[a-f0-9]{32}", job_id), "Invalid coach request ID")
        with self.lock:
            require(job_id in self.jobs, "Coach request not found; it may belong to a previous dashboard session")
            return copy.deepcopy(self.jobs[job_id][0])

    def cancel(self, job_id):
        self.get(job_id)
        with self.lock:
            require(self.jobs[job_id][0]["phase"] == "answering", "Garmin delivery has started; check its status instead of cancelling")
            self.jobs[job_id][1].set()

    def close(self):
        with self.lock:
            for _, event in self.jobs.values():
                event.set()
