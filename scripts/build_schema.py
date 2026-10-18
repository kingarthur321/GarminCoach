"""Generate the agent's strict structured-output schema (not a runtime dependency)."""
import json
from pathlib import Path


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array(items):
    return {"type": "array", "items": items}


text = {"type": "string"}
num = {"type": "number"}
boolean = {"type": "boolean"}
source = {"type": "string", "enum": ["fixture", "garmin"]}
target = {"anyOf": [obj({"kind": {"type": "string", "enum": ["effort"]}, "value": text}), obj({"kind": {"type": "string", "enum": ["heart_rate_bpm"]}, "low": num, "high": num})]}
step = obj({"kind": {"type": "string", "enum": ["warmup", "run", "recovery", "cooldown"]}, "unit": {"type": "string", "enum": ["seconds", "meters"]}, "value": num, "description": text, "target": target})
session = obj({"id": text, "revision": text, "date": text, "title": text, "state": {"type": "string", "enum": ["planned", "completed", "skipped"]}, "rationale": text, "references": array(text), "steps": array(step)})
observation = obj({"kind": {"type": "string", "enum": ["measured", "derived", "user_statement", "ai_suggestion"]}, "text": text, "references": array(text)})
schema = obj({"coach_summary": obj({"source": source, "generated_at": text, "headline": text, "observations": array(observation), "uncertainty": array(text)}),
              "training_plan": obj({"source": source, "generated_at": text, "revision_id": text, "revision_reason": text, "week_start": text, "illustrative": boolean, "sessions": array(session)})})
folder = Path(__file__).resolve().parent.parent / "schemas"
folder.mkdir(exist_ok=True)
(folder / "coach-output.schema.json").write_text(json.dumps(schema, indent=2) + "\n")
