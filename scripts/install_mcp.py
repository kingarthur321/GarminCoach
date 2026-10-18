"""Install the inspected upstream commit with tested dependency pins, no secrets."""
import hashlib
import json
import subprocess
import sys
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
lock = json.loads((ROOT / "dependencies.lock.json").read_text())
commit = lock["garmin_mcp_commit"]
folder = ROOT / ".build"
folder.mkdir(exist_ok=True)
archive = folder / "garmin-mcp.tar.gz"
url = f"https://codeload.github.com/Taxuspt/garmin_mcp/tar.gz/{commit}"
with urllib.request.urlopen(url, timeout=90) as response:
    archive.write_bytes(response.read(8_000_000))
if hashlib.sha256(archive.read_bytes()).hexdigest() != lock["source_archive_sha256"]:
    raise SystemExit("Upstream archive hash differs; installation stopped")
environment = ROOT / ".venv-mcp"
python = environment / "bin/python"
if not python.exists():
    venv.EnvBuilder(with_pip=True, symlinks=True).create(environment)
subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements-mcp.lock")], check=True)
subprocess.run([str(python), "-m", "pip", "install", "--no-deps", "--no-build-isolation", str(archive)], check=True)
print("Pinned MCP installed. Authenticate yourself in a terminal:")
print("./.venv-mcp/bin/garmin-mcp-auth")
