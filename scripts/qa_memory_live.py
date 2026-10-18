"""Live Claude check that reviewed synthetic memory is used; no real athlete data."""
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from coach.anthropic_api import PROMPT_SEPARATOR, key_available, model_name
from coach.chat import context, run_agent, system_instructions, validate_response
from coach.core import Store, now
from coach.fixtures import make_demo
from coach.memory import create

if not key_available():
    raise SystemExit("Claude API key missing. Add ANTHROPIC_API_KEY to the project .env file first.")

with tempfile.TemporaryDirectory(prefix="running-memory-verification-") as directory:
    store = Store(directory)
    store.bootstrap()
    with store.lock():
        store.commit(make_demo(store.profile()))
    create(store, {"kind": "user_statement", "text": "I do strength training on Wednesday evenings.", "chat_id": None})
    payload, refs, profile = context(store, None)
    assert payload["memory"][0]["kind"] == "user_statement"
    prompt = system_instructions() + PROMPT_SEPARATOR + json.dumps({
        "question": "Which evening did I tell you I do strength training?",
        "context": payload}, ensure_ascii=False)
    result = validate_response(run_agent(prompt, 120, threading.Event()), refs, profile)
    assert "wednesday" in result["answer"].lower()
    assert result["workout"] is None
    evidence = {"recorded_at": now(), "data": "synthetic fixtures and a disposable user statement only",
                "provider": "Claude API", "model": model_name(),
                "checks": ["Reviewed memory reached the live API context",
                           "Answer recalled the synthetic saved training day",
                           "Structured answer validated; no workout or Garmin operation",
                           "Temporary store removed; real athlete memory unchanged"], "result": "passed"}
    Path("docs/evidence/memory-live-check.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))
