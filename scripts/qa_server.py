"""Disposable synthetic server for browser checks; never edits the real profile."""
import sys
import tempfile
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from coach.core import Store
from coach.fixtures import make_demo
from coach.server import serve


def fixture_chat(prompt, timeout, cancelled):
    """Browser verification uses a mock response, never a model/account call."""
    request = json.loads(prompt[prompt.index('{"question"'):])
    context = request['context']
    reference = 'activity:' + context['recent_runs'][0]['id']
    proposal = None
    if 'workout draft' in request['question'].lower():
        session = context['saved_plan']['sessions'][0]
        proposal = {key: session[key] for key in ('title', 'date', 'rationale', 'references', 'steps')}
    return {'answer': 'Synthetic browser-check response based on the saved sample runs.', 'references': [reference], 'action': 'show_trends', 'workout': proposal}

with tempfile.TemporaryDirectory(prefix="stride-browser-") as directory:
    store = Store(directory); store.bootstrap()
    with store.lock(): store.commit(make_demo(store.profile()))
    serve(store, 8766, chat_executor=fixture_chat)
