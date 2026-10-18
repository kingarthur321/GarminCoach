"""Validate Claude's planning response against a disposable synthetic store."""
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coach.core import Store
from coach.fixtures import make_demo
from coach.runner import request_claude_plan
from coach.workflow import accept, prepare


with tempfile.TemporaryDirectory(prefix="stride-claude-planner-") as directory:
    store = Store(directory)
    store.bootstrap()
    with store.lock():
        store.commit(make_demo(store.profile()))
    context = prepare(store)
    result = request_claude_plan(context, False, 120)
    saved = accept(store, result, context)
    assert saved["activities"]["source"] == "fixture"
    assert saved["training_plan"]["illustrative"] is True
    print("Claude planning response validated and saved in a disposable synthetic store.")
