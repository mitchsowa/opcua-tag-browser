# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OPC-UA Tag Browser & Logger — a Python TUI for browsing OPC-UA address spaces (targeting Opto22 groov EPIC controllers) and a headless CSV logger. Two standalone scripts, no package structure.

## Setup & Running

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt    # textual, asyncua
```

Run the TUI: `python opcua_tui.py`
Run the headless logger: `python opcua_logger.py` (requires a saved profile from the TUI)

There are no tests, no linter config, and no build step.

## Architecture

The entire app is two files:

- **`opcua_tui.py`** (~1400 lines) — Textual-based TUI with three screens:
  - `ConnectScreen` — server URL, auth mode selection (anonymous, username/password, certificate/Basic256Sha256)
  - `BrowserScreen` — tree widget browsing the OPC-UA address space; watch/unwatch tags; auto-navigates `DEFAULT_PATH` on connect
  - `MonitorModal` — live value display for watched tags, CSV logging, profile save/load
  - `OpcUaApp` — thin shell that pushes `ConnectScreen` on mount

- **`opcua_logger.py`** (~340 lines) — headless counterpart:
  - `OpcUaLogger` class handles connect, auth, read loop, CSV write
  - `build_config()` merges CLI args over a JSON profile (CLI wins)
  - Can generate a systemd unit file via `--print-service`

## Key Concepts

- **Profiles** (`opcua_tui_profile.json`): JSON files storing server URL, auth credentials, log interval, CSV path, and watched tag list. Shared between TUI and logger. Contains plaintext passwords — excluded via `.gitignore`.
- **Node IDs**: OPC-UA node identifiers (e.g. `ns=4;s=Root.Kiln.Temp`). The logger's `_parse_nodeid()` also handles legacy Python repr format from older profiles.
- **`DEFAULT_PATH`**: Hardcoded navigation path in `opcua_tui.py` for Opto22 groov EPIC address space layout. Falls back to server root if not found.
- **asyncua**: The async OPC-UA client library. All server communication is async. The TUI uses Textual's `@work` decorator for background OPC-UA operations.

## Dependencies

- `textual` — TUI framework (only used by `opcua_tui.py`)
- `asyncua` — async OPC-UA client (used by both files)
