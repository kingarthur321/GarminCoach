"""Read-only MCP entry point shared by Codex and Claude Code. No credentials."""
import os
from pathlib import Path

root = Path(__file__).resolve().parent.parent
for key in ("GARMIN_EMAIL", "GARMIN_PASSWORD", "GARMIN_EMAIL_FILE", "GARMIN_PASSWORD_FILE"):
    os.environ.pop(key, None)
os.environ["GARMIN_ENABLED_TOOLS"] = "get_activities_by_date,get_sleep_summary_range,get_workouts,get_workout_by_id,get_scheduled_workouts,get_activity_fit_messages,get_activity_splits,get_training_readiness,get_training_status,get_training_load_balance,get_training_load_trend,get_vo2max_trend,get_rhr_day,get_heart_rates_summary,get_recovery_time_remaining"
os.environ["GARMIN_MCP_TRANSPORT"] = "stdio"
os.environ["GARMIN_MCP_CALL_TIMEOUT"] = "45"
executable = root / ".venv-mcp/bin/garmin-mcp"
os.execv(str(executable), [str(executable)])
