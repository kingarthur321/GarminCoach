"""Prepare context, accept agent output, and keep failed updates out of current."""
import copy
from .core import OUTPUTS, Invalid, atomic_json, coaching_signals, evidence_set, load_json, now, require, validate_bundle, weekly_distance
from .dashboard import saved_details
from .memory import for_context
from .planned import read_planned


def prepare(store, observations=None):
    with store.lock():
        current = store.current()
        if observations is None:
            require(current is not None, "Collect Garmin observations or initialize sample data first")
            observations = {k: current[k] for k in ("activities", "wellness")}
        details = saved_details(store, observations)
        feedback = store.read("feedback")
        planned = read_planned(store)
        context = {"prepared_at": now(), "baseline_fingerprint": store.fingerprint(), "baseline_generation": store.read("current"),
                   "profile": store.profile(), "observations": observations, "derived_weekly_distance": weekly_distance(observations["activities"]),
                   "training_signals": coaching_signals(details),
                   "allowed_references": sorted(evidence_set({**observations, "feedback": feedback}, details if details["source"] != "unavailable" else None)),
                   "feedback": feedback, "athlete_memory": for_context(store, observations["activities"]["source"]), "current_plan": current["training_plan"] if current else None,
                   "garmin_planned_runs": {"source": planned["source"], "imported_at": planned["imported_at"],
                                           "window": planned["window"], "total": len(planned["items"]),
                                           "items": [{key: item.get(key) for key in ("date", "name", "source_kind", "estimated_duration_seconds", "estimated_distance_meters")}
                                                     for item in planned["items"][:100]]},
                   "previous_summary": current["coach_summary"] if current else None, "workout_registry": store.read("workout_registry"), "run_status": store.read("run_status")}
        atomic_json(store.path / "pending" / "context.json", context)
        return context


def accept(store, result, context=None):
    context = context or load_json(store.path / "pending" / "context.json")
    require(isinstance(result, dict) and set(result) == {"coach_summary", "training_plan"}, "Agent result requires exactly coach_summary and training_plan")
    with store.lock():
        require(store.read("current") == context["baseline_generation"], "Another update saved a plan during coaching; previous output retained")
        bundle = {**copy.deepcopy(context["observations"]), **result}
        store.commit(bundle, expected=context["baseline_fingerprint"])
    return bundle
