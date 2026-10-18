"""Refresh future running workouts from Garmin Connect without any Garmin write."""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coach.core import Store
from coach.mcp import MCP
from coach.planned import import_planned


store = Store()
today = datetime.now(ZoneInfo(store.profile()["timezone"])).date()
with MCP(writes=False) as client:
    result = import_planned(store, client, start=today)
print(f"Imported {len(result['items'])} current planned running workouts from Garmin Connect "
      f"({result['window']['start']} through {result['window']['end']}).")
print(f"Garmin Coach check: {result['coach_visibility']} across {len(result['coach_week_dates'])} queried weeks.")
print("No workout was created, scheduled or deleted in Garmin Connect.")
