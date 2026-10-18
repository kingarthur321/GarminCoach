"""Allowlisted source distribution. Never traverse private state or token stores."""
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP = [".gitignore", ".env.example", "AGENTS.md", "BUILD_SPEC.md", "CLAUDE.md", "COACHING.md", "README.md", "SOURCES.md", "START_HERE.txt", "STATUS.md", "LICENSE", "CONTRIBUTING.md", "athlete_profile.example.json", "pyproject.toml", "dependencies.lock.json", "requirements-mcp.lock", "launch.sh", "prompts/coach-update.md", "prompts/coach-chat.md", "prompts/coach-plan-api.md"]
FOLDERS = ["coach", "web", "scripts", "schemas", "tests"]
DOCS = ["docs/LAUNCH.md", "docs/VERIFICATION.md", "docs/WATCH_SYNC.md", "docs/COACH_PHILOSOPHY.md", "docs/ATHLETE_MEMORY.md", "docs/CHAT_WORKOUT_FLOW.md", "docs/PLANNED_RUNS_IMPORT.md", "docs/mcp-tools.json"]
DOCS += ["docs/evidence/" + name for name in ("browser-checks.json", "chat-workout-checks.json", "claude-api-check.json", "coach-chat-check.json", "map-checks.json", "mcp-contract-checks.json", "memory-checks.json", "memory-live-check.json", "unit-checks.json", "dashboard-fixture-desktop.png", "dashboard-fixture-mobile.png")]
DOCS += [f"docs/evidence/dashboard-{view}-fixture-{size}.png" for view in ("runs", "plan", "trends", "settings") for size in ("desktop", "mobile")]
paths = [ROOT / p for p in TOP + DOCS]
for folder in FOLDERS:
    paths.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".json", ".html", ".css", ".js", ".cjs"))
assert all(p.is_file() for p in paths), "Required release file missing"
browser = json.loads((ROOT / "docs/evidence/browser-checks.json").read_text())
assert browser["data"] == "synthetic fixtures only", "Do not release private screenshots"
prefix = "ai-running-coach-prototype"
dest = ROOT / "dist"; dest.mkdir(exist_ok=True)
archive = dest / (prefix + ".zip")
manifest = {"version": "1.0.0-prototype", "verification": "See docs/VERIFICATION.md for fixture checks and the summarized real-account results.",
            "excluded": ["private data", "credentials and tokens", "local client config", "logs", "virtual environments", "downloaded upstream source"],
            "files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}}
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(set(paths)):
        z.write(p, prefix + "/" + str(p.relative_to(ROOT)))
    z.writestr(prefix + "/release_manifest.json", json.dumps(manifest, indent=2) + "\n")
with tempfile.TemporaryDirectory(prefix="stride-release-") as temp:
    with zipfile.ZipFile(archive) as z:
        assert all(not any(part in ("data", ".venv-mcp", ".build", ".codex", ".garminconnect", "logs", "__pycache__", "private") for part in Path(n).parts) for n in z.namelist())
        assert all(Path(n).name != ".env" for n in z.namelist()), "Private API key file must not be packaged"
        z.extractall(temp)
    extracted = Path(temp) / prefix
    assert (extracted / "prompts/coach-update.md").is_file(), "Runner coaching prompt missing"
    assert (extracted / "prompts/coach-chat.md").is_file(), "Chat coaching prompt missing"
    assert (extracted / "docs/ATHLETE_MEMORY.md").is_file(), "Athlete memory guide missing"
    assert (extracted / "docs/CHAT_WORKOUT_FLOW.md").is_file(), "Chat workout guide missing"
    assert (extracted / "docs/PLANNED_RUNS_IMPORT.md").is_file(), "Garmin planned-run guide missing"
    assert (extracted / "schemas/chat-output.schema.json").is_file(), "Chat output schema missing"
    for args in (["init"], ["demo"], ["validate"]):
        subprocess.run([sys.executable, "-m", "coach", *args], cwd=extracted, check=True)
    assert json.loads((extracted / "data/athlete_profile.json").read_text())["profile_confirmed"] is False
    assert json.loads((extracted / "data/athlete_profile.json").read_text())["automation"]["enabled"] is False
sha = hashlib.sha256(archive.read_bytes()).hexdigest()
(dest / (archive.name + ".sha256")).write_text(sha + "  " + archive.name + "\n")
print(f"Clean archive: {archive}\nSHA-256: {sha}\nFresh extraction init/demo/validate: passed")
