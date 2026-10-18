"""Small, isolated Claude Messages API client for the localhost coach chat."""
import json
import os
import urllib.error
import urllib.request

from .core import ROOT, Invalid, load_json, require

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5-5"
PROMPT_SEPARATOR = "\n<coach_context_json>\n"


def load_local_env():
    """Read only the two expected values from the ignored project .env file."""
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if separator and name in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL") and not os.environ.get(name):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ[name] = value


def key_available():
    load_local_env()
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def model_name():
    load_local_env()
    return os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def request_message(prompt, timeout, opener=None, *, schema_path=None, max_tokens=6000):
    load_local_env()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    require(key, "Claude API key is missing. Set ANTHROPIC_API_KEY before launching the dashboard.")
    system, separator, context = prompt.partition(PROMPT_SEPARATOR)
    require(separator and system.strip(), "Invalid coach request")
    try:
        user_input = json.loads(context)
        require(isinstance(user_input, dict) and set(user_input) == {"question", "context"}, "Invalid coach context")
    except (ValueError, TypeError) as exc:
        raise Invalid("Invalid coach context") from exc
    payload = {
        "model": model_name(),
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": json.dumps(user_input, ensure_ascii=False, allow_nan=False)}],
        "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": load_json(schema_path or ROOT / "schemas/chat-output.schema.json")}},
    }
    request = urllib.request.Request(API_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json", "anthropic-version": API_VERSION, "x-api-key": key}, method="POST")
    try:
        with (opener or urllib.request.urlopen)(request, timeout=max(1, timeout)) as response:
            raw = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 401:
            message = "Claude API rejected the key. Check ANTHROPIC_API_KEY."
        elif exc.code == 403:
            message = "Claude API access is denied for this key or model."
        elif exc.code == 429:
            message = "Claude API rate or spend limit reached. Try again after checking your Anthropic account."
        elif exc.code >= 500:
            message = "Claude API is temporarily unavailable. Try again later."
        else:
            message = f"Claude API request failed (HTTP {exc.code}). Check the configured model and account."
        raise Invalid(message) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise Invalid("Could not reach Claude API. Check the connection and try again.") from None
    require(len(raw) <= 1024 * 1024, "Claude API response exceeded the size limit")
    try:
        response = json.loads(raw)
        require(response.get("type") == "message" and response.get("stop_reason") == "end_turn", "Claude API answer was incomplete")
        blocks = response.get("content")
        require(isinstance(blocks, list) and blocks and all(isinstance(block, dict) and block.get("type") in ("text", "thinking", "redacted_thinking") for block in blocks), "Claude API returned unexpected content")
        answer = "".join(block.get("text", "") for block in blocks if block["type"] == "text")
        require(answer, "Claude API returned no structured answer")
        result = json.loads(answer)
        require(isinstance(result, dict), "Claude API answer must be an object")
        return result
    except Invalid:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise Invalid("Claude API returned an invalid structured answer") from None
