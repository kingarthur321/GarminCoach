"""User-reviewed athlete memory, separate from measured Garmin data and chat history."""
import re
import uuid

from .core import Invalid, now, require, string, timestamp

MAX_ITEMS = 80
CONTEXT_ITEMS = 20
MAX_TEXT = 600
EMPTY = {"version": 1, "items": []}


def read_memory(store):
    data = store.read("athlete_memory", EMPTY)
    require(isinstance(data, dict) and set(data) == {"version", "items"} and data["version"] == 1,
            "Saved athlete memory is invalid")
    items = data["items"]
    require(isinstance(items, list) and len(items) <= MAX_ITEMS, "Saved athlete memory exceeds its limit")
    ids = set()
    for item in items:
        require(isinstance(item, dict) and set(item) == {"id", "kind", "text", "source", "created_at", "updated_at", "revision"},
                "Saved athlete memory entry is invalid")
        require(isinstance(item["id"], str) and re.fullmatch(r"[a-f0-9]{32}", item["id"]) and item["id"] not in ids,
                "Saved athlete memory ID is invalid")
        ids.add(item["id"])
        require(item["kind"] in ("user_statement", "coach_insight"), "Saved athlete memory type is invalid")
        string(item["text"], "athlete memory", MAX_TEXT)
        require(isinstance(item["source"], dict) and set(item["source"]) == {"type", "chat_id", "references", "data_source"},
                "Saved athlete memory source is invalid")
        source = item["source"]
        require((item["kind"] == "user_statement" and source["type"] == "manual" and source["chat_id"] is None)
                or (item["kind"] == "coach_insight" and source["type"] == "chat" and isinstance(source["chat_id"], str)
                    and re.fullmatch(r"[a-f0-9]{32}", source["chat_id"])), "Saved athlete memory provenance is invalid")
        require(isinstance(source["references"], list) and len(source["references"]) <= 30
                and all(isinstance(ref, str) and len(ref) <= 200 for ref in source["references"]),
                "Saved athlete memory references are invalid")
        if source["type"] == "manual":
            require(source["references"] == [] and source["data_source"] is None,
                    "Manual memory cannot claim measured references")
        else:
            require(source["data_source"] in ("fixture", "garmin", "unknown"), "Coach memory data source is invalid")
        timestamp(item["created_at"])
        timestamp(item["updated_at"])
        require(type(item["revision"]) is int and item["revision"] >= 1, "Saved athlete memory revision is invalid")
    return data


def for_context(store, observation_source=None):
    """Bounded latest reviewed memories; never includes arbitrary chat transcript."""
    items = [item for item in reversed(read_memory(store)["items"])
             if not (observation_source == "garmin" and item["kind"] == "coach_insight"
                     and item["source"]["data_source"] != "garmin")]
    return [{"id": item["id"], "kind": item["kind"], "text": item["text"],
             "source": item["source"], "updated_at": item["updated_at"]}
            for item in items[:CONTEXT_ITEMS]]


def create(store, body):
    require(set(body) == {"kind", "text", "chat_id"}, "Unexpected athlete memory fields")
    kind = body["kind"]
    require(kind in ("user_statement", "coach_insight"), "Unknown athlete memory type")
    value = string(body["text"], "athlete memory", MAX_TEXT).strip()
    with store.lock():
        data = read_memory(store)
        require(len(data["items"]) < MAX_ITEMS, "Athlete memory is full; delete an old entry first")
        if kind == "coach_insight":
            chat_id = body["chat_id"]
            require(isinstance(chat_id, str) and re.fullmatch(r"[a-f0-9]{32}", chat_id), "Choose a saved coach answer")
            chat = next((entry for entry in store.read("coach_chat", {"items": []})["items"] if entry["id"] == chat_id), None)
            require(chat is not None, "Coach answer is no longer in recent history")
            source = {"type": "chat", "chat_id": chat_id, "references": list(chat["result"]["references"]),
                      "data_source": chat.get("observation_source", "unknown")}
        else:
            require(body["chat_id"] is None, "Manual memory cannot claim a coach answer")
            source = {"type": "manual", "chat_id": None, "references": [], "data_source": None}
        item = {"id": uuid.uuid4().hex, "kind": kind, "text": value, "source": source,
                "created_at": now(), "updated_at": now(), "revision": 1}
        data["items"].append(item)
        store.write("athlete_memory", data)
        store.mark(review_required=True)
        return item


def update(store, body):
    require(set(body) == {"id", "revision", "text"}, "Unexpected athlete memory fields")
    value = string(body["text"], "athlete memory", MAX_TEXT).strip()
    with store.lock():
        data = read_memory(store)
        item = next((row for row in data["items"] if row["id"] == body["id"]), None)
        require(item is not None, "Athlete memory entry no longer exists")
        require(type(body["revision"]) is int and item["revision"] == body["revision"], "Athlete memory changed; reload before editing")
        if item["text"] != value:
            item.update(text=value, updated_at=now(), revision=item["revision"] + 1)
            data["items"].remove(item)
            data["items"].append(item)
            store.write("athlete_memory", data)
            store.mark(review_required=True)
        return item


def delete(store, body):
    require(set(body) == {"id", "revision"}, "Unexpected athlete memory fields")
    with store.lock():
        data = read_memory(store)
        item = next((row for row in data["items"] if row["id"] == body["id"]), None)
        require(item is not None, "Athlete memory entry no longer exists")
        require(type(body["revision"]) is int and item["revision"] == body["revision"], "Athlete memory changed; reload before deleting")
        data["items"].remove(item)
        store.write("athlete_memory", data)
        store.mark(review_required=True)
