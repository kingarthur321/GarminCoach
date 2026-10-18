"""One actual Claude API conversation check using synthetic saved data only."""
import json
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from coach.chat import context, run_agent, system_instructions, validate_response
from coach.anthropic_api import PROMPT_SEPARATOR, key_available, model_name
from coach.core import Store, now
from coach.fixtures import make_demo

if not key_available():
    raise SystemExit('Claude API key missing. Add ANTHROPIC_API_KEY to the project .env file first.')

with tempfile.TemporaryDirectory(prefix='running-chat-verification-') as directory:
    store = Store(directory)
    store.bootstrap()
    with store.lock():
        store.commit(make_demo(store.profile()))
    saved = store.current()
    selected = saved['activities']['items'][0]
    payload, refs, profile = context(store, selected['id'])
    prompt = (system_instructions() + PROMPT_SEPARATOR +
              json.dumps({'question': 'What are the date and distance of the selected run? How would Almgren’s controlled-training approach relate to it?', 'context': payload}))
    result = validate_response(run_agent(prompt, 120, threading.Event()), refs, profile)
    assert result['workout'] is None
    assert 'activity:' + selected['id'] in result['references']
    assert 'synthetic' in result['answer'].lower() or 'sample' in result['answer'].lower()
    evidence = {'recorded_at': now(), 'data': 'synthetic fixtures only', 'provider': 'Claude API', 'model': model_name(),
                'checks': ['Actual API answer with editable Almgren-inspired system prompt returned', 'Structured schema and evidence references validated',
                           'No workout proposal or Garmin operation', 'Disposable store; personal data unchanged'], 'result': 'passed'}
    Path('docs/evidence/claude-api-check.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))
