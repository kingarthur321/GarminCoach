"""Synthetic stdio MCP protocol fixture, never a Garmin account server."""
import json
import sys
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message["method"]
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "synthetic-test", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "get_activities_by_date", "inputSchema": {"type": "object", "required": ["start_date", "end_date"], "properties": {"start_date": {"type": "string"}, "end_date": {"type": "string"}}}}]}
    else:
        payload = {"activities": [{"id": "synthetic-transport", "type": "running", "distance_meters": 5000}], "has_more": False}
        result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
