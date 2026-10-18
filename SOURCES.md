# Primary documentation

Starter checked September 30; build interfaces checked October 1, 2026. Published interfaces are not proof of account-specific integration. The installed clients' help and actual MCP tool schemas were inspected as primary implementation evidence.

* Selected Garmin MCP source, authentication, tool response curators and workout mappings: https://github.com/Taxuspt/garmin_mcp/tree/cfc5d799ab0f165e837f1188a1d093c65838aaf7
* Alternative running-focused MCP candidate: https://github.com/mau240987/garmin-coach-mcp-server
* Claude Code instruction files and imports: https://code.claude.com/docs/en/memory
* Claude Code MCP setup: https://code.claude.com/docs/en/mcp
* Codex project instructions: https://developers.openai.com/codex/guides/agents-md
* Codex MCP configuration: https://developers.openai.com/codex/mcp
* Codex noninteractive execution: https://developers.openai.com/codex/noninteractive (redirects to https://learn.chatgpt.com/docs/non-interactive-mode)
* Garmin send-workout-to-device instructions: https://www8.garmin.com/manuals/webhelp/GUID-F41EAFB3-6CC9-42DE-9C6C-9E358DBB0671/EN-US/GUID-6E339F62-28D2-4C2A-8534-F90D8C27D6E2.html

Tested versions/pins are in dependencies.lock.json and requirements-mcp.lock. Actual tools/list is saved in docs/mcp-tools.json; actual source curators were tested with synthetic API responses. The optional runner uses installed Codex 0.159.3 flags (--ignore-user-config, --sandbox read-only, --output-schema, --output-last-message, --cd, --ephemeral, --skip-git-repo-check and stdin prompt). Manual configuration follows installed Codex and Claude CLI help; no credentials are included.
