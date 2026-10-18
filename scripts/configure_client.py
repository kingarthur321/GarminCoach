"""User-run client configuration. Stores a token-only read connection, no secrets."""
import argparse
import subprocess
import sys
from pathlib import Path
root = Path(__file__).resolve().parent.parent
p = argparse.ArgumentParser()
p.add_argument("client", choices=["codex", "claude"])
args = p.parse_args()
server = str(root / "scripts/garmin_server.py")
if args.client == "codex":
    command = ["codex", "mcp", "add", "garmin", "--", sys.executable, server]
else:
    command = ["claude", "mcp", "add", "--transport", "stdio", "--scope", "local", "garmin", "--", sys.executable, server]
subprocess.run(command, cwd=root, check=True)
print("Read-only Garmin MCP configured without credentials. Restart the coding-agent session and inspect its MCP connection.")
