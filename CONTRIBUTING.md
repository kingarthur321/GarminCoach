# Contributing

Thanks for helping improve the AI running coach. Forks, experiments and pull requests are all welcome.

## Getting started

1. Fork and clone the repository, then run `./launch.sh` to see the dashboard with sample data.
2. Read [AGENTS.md](AGENTS.md), [BUILD_SPEC.md](BUILD_SPEC.md) and [STATUS.md](STATUS.md). They are written for both humans and coding agents, so you can point Claude Code or Codex at them directly.
3. Make your change and run the checks:

```bash
python3 -m unittest discover -v
node --check web/app.js && node --check web/map.js
node tests/map-geometry.cjs
```

Browser checks (`node scripts/qa-dashboard.cjs` against `python3 scripts/qa_server.py`) need Playwright and Chrome; see [docs/LAUNCH.md](docs/LAUNCH.md).

## Ground rules

* **Never commit personal data.** That includes Garmin exports, real screenshots, tokens, `.env`, anything under `data/` and real activity or workout IDs. Use the synthetic fixtures for tests and screenshots. Keep personal agent notes in a git-ignored `CLAUDE.local.md`.
* **Keep the app dependency-free.** The app runtime uses only the Python standard library and plain HTML/CSS/JS. The Garmin MCP server is pinned in `dependencies.lock.json` and `requirements-mcp.lock`.
* **Preserve the safety rules.** Keep the write policies, duplicate prevention, missing-data honesty and the separation between measured data, user statements and AI suggestions.
* **Label evidence truthfully.** Say which checks used fixtures and which used a real account or device.

## Reporting issues

Include your OS, Python version, the command you ran and the error output. Remove email addresses, tokens, activity IDs and GPS data first.
