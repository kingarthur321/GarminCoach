"""Run one Claude request in a cancellable child process; never print secrets."""
import json
import sys

from .anthropic_api import request_message
from .core import Invalid


def main():
    try:
        result = request_message(sys.stdin.read(), 110)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, allow_nan=False))
    except Invalid as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
    except Exception:
        print(json.dumps({"ok": False, "error": "Claude API response unavailable. Your saved training data is unchanged."}))


if __name__ == "__main__":
    main()
