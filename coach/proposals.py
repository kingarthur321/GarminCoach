"""Promote one saved, current chat proposal to an exact selected local workout."""
import copy
import re

from .core import Invalid, digest, require
from .workouts import Workouts


def session_id_for_chat(chat_id):
    require(isinstance(chat_id, str) and re.fullmatch(r"[a-f0-9]{32}", chat_id), "Invalid coach answer ID")
    return "oneoff-chat-" + chat_id


def review_chat_proposal(store, chat_id, *, auto_request=False):
    session_id = session_id_for_chat(chat_id)
    with store.lock():
        history = store.read("coach_chat", {"items": []})["items"]
        chat = next((item for item in history if item["id"] == chat_id), None)
        require(chat is not None, "Coach answer is no longer in saved chat history. Ask for a fresh workout.")
        if auto_request:
            require(chat.get("auto_send_requested") is True, "This question did not authorize automatic Garmin delivery")
        require(chat.get("observation_source") == "garmin" and not chat.get("illustrative"),
                "Only a proposal based on confirmed Garmin observations can be approved")
        proposal = chat["result"].get("workout")
        require(isinstance(proposal, dict), "This coach answer has no structured workout")
        fingerprint = chat.get("input_fingerprint")
        require(isinstance(fingerprint, str) and re.fullmatch(r"[a-f0-9]{64}", fingerprint),
                "This proposal predates reviewable workout approval. Ask the coach for a fresh workout.")
        plan_digest = chat.get("plan_digest")
        require(isinstance(plan_digest, str) and re.fullmatch(r"[a-f0-9]{64}", plan_digest),
                "This proposal predates saved-plan verification. Ask the coach for a fresh workout.")
        require(digest(store.current()["training_plan"]) == plan_digest,
                "Saved training week changed since this proposal. Ask the coach for a fresh workout.")
        draft = copy.deepcopy(proposal)
    session = {"id": session_id, "revision": "chat-" + digest(draft)[:16], "date": draft["date"],
               "title": draft["title"], "state": "planned", "rationale": draft["rationale"],
               "references": draft["references"], "steps": draft["steps"]}
    return Workouts(store, None).stage_one_off(session, select=True, expected_fingerprint=fingerprint,
                                               reject_date_conflict=True)
