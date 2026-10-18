"""Bounded JSON-RPC stdio client for the existing pinned Garmin MCP server."""
import json
import os
import queue
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import ROOT, Invalid, atomic_json, normalize_activities, normalize_wellness, require

READ_TOOLS = ("get_activities_by_date", "get_sleep_summary_range", "get_workouts", "get_workout_by_id", "get_scheduled_workouts", "get_garmin_coach_workouts", "get_activity_fit_messages", "get_activity_splits",
              "get_training_readiness", "get_training_status", "get_training_load_balance", "get_training_load_trend", "get_vo2max_trend", "get_rhr_day", "get_heart_rates_summary", "get_recovery_time_remaining")
WRITE_TOOLS = ("upload_workout", "schedule_workout")
COMMIT = "cfc5d799ab0f165e837f1188a1d093c65838aaf7"


class MCP:
    def __init__(self, command=None, writes=False, timeout=55):
        executable = ROOT / ".venv-mcp" / "bin" / "garmin-mcp"
        require(command is not None or executable.exists(), "Install MCP first: python3 scripts/install_mcp.py")
        env = dict(os.environ)
        # Never inherit credentials or secret-file configuration into our wrapper.
        for key in ("GARMIN_EMAIL", "GARMIN_PASSWORD", "GARMIN_EMAIL_FILE", "GARMIN_PASSWORD_FILE"):
            env.pop(key, None)
        env.update(GARMIN_ENABLED_TOOLS=",".join(READ_TOOLS + (WRITE_TOOLS if writes else ())), GARMIN_MCP_CALL_TIMEOUT="45", GARMIN_MCP_TRANSPORT="stdio")
        self.process = subprocess.Popen(command or [str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env, start_new_session=True)
        self.responses = queue.Queue()
        self.timeout = timeout
        self.seq = 0
        self.schemas = {}
        def pump():
            try:
                for line in self.process.stdout:
                    try:
                        self.responses.put(json.loads(line))
                    except json.JSONDecodeError:
                        self.responses.put({"_failure": "Invalid MCP transport response"})
            finally:
                self.responses.put({"_failure": "MCP process stopped"})
        threading.Thread(target=pump, daemon=True).start()

    def request(self, method, params=None):
        self.seq += 1
        request_id = self.seq
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            require(time.monotonic() < deadline, "MCP call deadline exceeded; uncertain writes require reconciliation")
            try:
                message = self.responses.get(timeout=max(0.001, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise Invalid("MCP call timed out; a write outcome must be reconciled before retrying") from exc
            require("_failure" not in message, message.get("_failure", ""))
            if message.get("id") != request_id:
                continue
            require("error" not in message, "MCP protocol error; see the authentication tool in your terminal")
            return message.get("result", {})

    def discover(self):
        self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "ai-running-coach", "version": "1.0.0"}})
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.process.stdin.flush()
        result = self.request("tools/list")
        self.schemas = {t["name"]: t["inputSchema"] for t in result["tools"]}
        return result["tools"]

    def call(self, name, arguments):
        require(name in self.schemas, "Tool unavailable: " + name)
        schema = self.schemas[name]
        require(set(schema.get("required", [])) <= set(arguments), "Required MCP arguments missing")
        require(set(arguments) <= set(schema.get("properties", {})), "Unknown MCP arguments")
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        require(not result.get("isError"), "Garmin tool failed; verify authentication in your terminal")
        if isinstance(result.get("structuredContent"), dict):
            value = result["structuredContent"]
            if "result" not in value:
                return value
        texts = [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
        require(bool(texts), "Empty Garmin tool response")
        payload = "\n".join(texts)
        for _ in range(3):
            if not isinstance(payload, str):
                return payload
            if payload.startswith(("Error", "Invalid", "Date range", "end_date")):
                # Do not print upstream errors that can contain personal details.
                raise Invalid("Garmin read/write failed. Verify tokens with garmin-mcp-auth --verify; retain previous output.")
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                if payload.startswith("No "):
                    return {"empty": True, "message": payload}
                raise Invalid("Unexpected Garmin response shape") from None
        require(isinstance(payload, (dict, list)), "Unexpected Garmin result")
        return payload

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()

    def __enter__(self):
        try:
            self.discover()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_):
        self.close()


def collect(client, profile):
    today = datetime.now(ZoneInfo(profile["timezone"])).date()
    end = today.isoformat()
    start = (today - timedelta(days=profile["data"]["activity_lookback_days"] - 1)).isoformat()
    rows = []
    has_more = False
    for page in range(5):
        result = client.call("get_activities_by_date", {"start_date": start, "end_date": end, "activity_type": "running", "page": page, "page_size": 100})
        require(isinstance(result, dict) and isinstance(result.get("activities"), list), "Malformed activity page")
        rows.extend(result["activities"])
        has_more = result.get("has_more", False)
        if not has_more:
            break
    activities = normalize_activities(rows, profile["timezone"], "garmin", start, end)
    activities["coverage"].update(truncated=bool(has_more), page_limit=5, page_size=100)
    start_w = (today - timedelta(days=profile["data"]["wellness_lookback_days"] - 1)).isoformat()
    status = "unsupported"
    nights = []
    if "get_sleep_summary_range" in client.schemas:
        try:
            result = client.call("get_sleep_summary_range", {"start_date": start_w, "end_date": end})
            if not result.get("empty"):
                require(isinstance(result.get("nights"), list), "Malformed wellness range")
                nights = result["nights"]
            status = "available" if nights else "unavailable"
        except Invalid:
            status = "read_failed"
    wellness = normalize_wellness(nights, "garmin", start_w, end, status)
    return {"activities": activities, "wellness": wellness}
