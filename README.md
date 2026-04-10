# OPC-UA Tag Browser & Logger

A terminal-based OPC-UA tag browser, live monitor, and headless CSV logger built for industrial use with Opto22 groov EPIC and compatible OPC-UA servers.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

### TUI Browser (`opcua_tui.py`)
- Connect to any OPC-UA server by hostname or IP with configurable port
- Anonymous, username/password, and certificate-based (Basic256Sha256) authentication
- Auto-navigates to a configurable default path on connect
- Browse the full address space tree with expandable folders
- Click any variable node to see its live value, type, and Node ID
- Folder nodes show child counts and are never confused with variable nodes
- **`W`** watch a tag | **`U`** unwatch | **`Ctrl+A`** recursively watch all children at any depth
- **`Ctrl+X`** clear watch list | **`M`** open live monitor
- Live monitor refreshes values from the server every 3 seconds
- Log all watched tags to CSV at a configurable interval (minimum 0.1s, default 1s)
- Save/load watch profiles to `opcua_tui_profile.json` (same directory as the script)
- **`R`** refresh tree | **`Ctrl+R`** hard reconnect (disconnect + reconnect same URL)
- **`Ctrl+H`** jump to server root | **`Esc`** disconnect

### Headless Logger (`opcua_logger.py`)
- Reads a saved TUI profile and logs tags to CSV with no UI
- Supports all auth modes saved in the profile (anonymous, username/password, certificates)
- Run as a background process or systemd service for continuous data collection
- CLI flags to override any profile setting without editing files
- Handles legacy and modern Node ID string formats automatically

---

## Requirements

- Python 3.10+
- Linux / macOS / Windows (WSL recommended on Windows)

---

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/mitchsowa/opcua-tag-browser.git
cd opcua-tag-browser

# 2. Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Both scripts auto-activate the `.venv` directory if it exists, so you can run them directly without manually activating the virtual environment first.

**Optional convenience alias** (add to `~/.bashrc`):
```bash
alias opcua='python ~/opcua-tag-browser/opcua_tui.py'
```

---

## Usage

### TUI Browser

```bash
python opcua_tui.py
```

#### Connect Screen

| Field | Description |
|---|---|
| Hostname / IP | Server address (e.g. `192.168.10.254`) |
| Port | OPC-UA port — defaults to `4840` |
| Endpoint path | Optional path suffix (e.g. `/OPCUA/SimulationServer`) |
| Security Mode | See table below |
| Username / Password | Shown for user-auth and certificate modes |
| Client Certificate | Path to `.pem` or `.der` file (certificate modes only) |
| Private Key | Path to `.pem` private key file (certificate modes only) |

**Security modes:**

| Mode | Description |
|---|---|
| None (Anonymous) | No credentials — most common for local networks |
| None (Username/Password) | Credentials sent unencrypted — use on trusted networks only |
| Basic256Sha256 — Sign | Messages signed with client certificate |
| Basic256Sha256 — Sign & Encrypt | Messages signed and encrypted (most secure) |

#### Browser keyboard shortcuts

| Key | Action |
|-----|--------|
| `W` | Watch selected variable tag |
| `U` | Unwatch selected tag |
| `Ctrl+A` | Recursively watch all variable children of selected folder (all levels deep) |
| `Ctrl+X` | Clear entire watch list |
| `M` | Open live monitor / logger |
| `R` | Refresh tree |
| `Ctrl+R` | Hard reconnect — disconnect and reconnect to same server |
| `Ctrl+H` | Jump to server root |
| `Ctrl+L` | Clear activity log |
| `Esc` | Disconnect and return to connect screen |

#### Monitor screen

| Key / Button | Action |
|---|---|
| `L` / ▶ Start Logging | Start CSV logging at the configured interval |
| `D` / Unwatch | Remove selected row from watch list |
| `Ctrl+X` / Clear All | Clear all watched tags (also stops logging) |
| 💾 Save Profile | Save current tags, server, auth, and settings to `opcua_tui_profile.json` |
| 📂 Load Profile | Load a saved profile and reconnect nodes against the live server |
| `Esc` / Close | Close monitor (logging stops automatically) |

---

### Headless Logger

```bash
# Use saved profile (default: opcua_tui_profile.json next to the script)
python opcua_logger.py

# Override settings at runtime
python opcua_logger.py --interval 5 --output /var/log/kiln.csv
python opcua_logger.py --url opc.tcp://192.168.1.100:4840
python opcua_logger.py --show-profile     # dry run — print resolved config and exit

# Background process
nohup python opcua_logger.py &> logger.log &
echo $! > logger.pid
kill $(cat logger.pid)   # stop it
```

#### CLI flags

| Flag | Description |
|------|-------------|
| `--profile PATH` | Path to profile JSON (default: `opcua_tui_profile.json` next to script) |
| `--url URL` | Override server URL |
| `--tags ID [ID ...]` | Override tags by Node ID (e.g. `ns=4;s=Root.Kiln.Temp`) |
| `--interval SECONDS` | Override log interval in seconds |
| `--output FILE` | Override CSV output path |
| `--show-profile` | Print resolved config and exit |
| `--print-service` | Print a systemd `.service` unit file and exit |

---

### Run as a systemd service (auto-start on boot)

```bash
python opcua_logger.py --print-service | sudo tee /etc/systemd/system/opcua-logger.service
sudo systemctl daemon-reload
sudo systemctl enable --now opcua-logger
sudo journalctl -fu opcua-logger    # follow live logs
```

### Copy logger to a remote device

```bash
scp opcua_logger.py requirements.txt opcua_tui_profile.json user@192.168.10.254:~/
```

On the remote device, create the `.venv` and install dependencies — after that the logger auto-activates the venv on its own:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
python opcua_logger.py
```

---

## Profile format

Profiles are saved as `opcua_tui_profile.json` in the same directory as the scripts. You can edit this file manually or use the **💾 Save Profile** button in the monitor.

```json
{
  "server_url": "opc.tcp://192.168.10.254:4840",
  "auth": {
    "mode": "none_user",
    "username": "admin",
    "password": "secret",
    "cert": null,
    "key": null
  },
  "log_interval": 1.0,
  "log_path": "kiln_log.csv",
  "tags": [
    {
      "node_id": "ns=4;s=Root.Kiln.KilnReadings.FWD_Drybulb",
      "name": "FWD_Drybulb",
      "dtype": "float"
    },
    {
      "node_id": "ns=4;s=Root.Kiln.KilnReadings.REV_Drybulb",
      "name": "REV_Drybulb",
      "dtype": "float"
    }
  ]
}
```

> **Note:** The profile stores passwords in plaintext. Keep it out of version control — it is excluded from git by default via `.gitignore`.

---

## CSV output format

```
timestamp,FWD_Drybulb,REV_Drybulb,FWD_Wetbulb,...
2026-04-02 13:18:57.123,72.4,68.1,65.2,...
2026-04-02 13:18:58.124,72.5,68.2,65.3,...
```

Each row is written and flushed immediately so data is not lost if the process is interrupted.

---

## Default navigation path

On connect the TUI auto-navigates to:

```
Objects → DeviceSet → Opto22-Cortex-Linux → Resources → Application → GlobalVars
```

If the path is not found it falls back to the server root automatically.

To change the default, edit `DEFAULT_PATH` near the top of `opcua_tui.py`:

```python
DEFAULT_PATH = [
    "Objects",
    "DeviceSet",
    "Opto22-Cortex-Linux",
    "Resources",
    "Application",
    "GlobalVars",
]
```

---

## Tested with

- Opto22 groov EPIC (firmware R10+)
- Standard OPC-UA servers (Unified Automation UaExpert verified)

---

## License

MIT
