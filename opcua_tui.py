#!/usr/bin/env python3
"""
OPC-UA Tag Browser & Monitor — Terminal UI
Requires: pip install textual asyncua
"""

import os
import sys
from pathlib import Path

# Auto-activate the .venv next to this script if not already running inside it
_venv_dir = Path(__file__).resolve().parent / ".venv"
_venv_python = _venv_dir / "bin" / "python"
if _venv_python.exists() and os.environ.get("VIRTUAL_ENV") != str(_venv_dir):
    os.environ["VIRTUAL_ENV"] = str(_venv_dir)
    os.execv(str(_venv_python), [str(_venv_python)] + sys.argv)

import asyncio
import json
from datetime import datetime
from typing import Optional

DEFAULT_PROFILE_PATH = Path(__file__).parent / "opcua_tui_profile.json"

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.screen import Screen, ModalScreen
from textual.widgets import (
    Button, DataTable, Footer, Header, Input, Label,
    Static, Tree, LoadingIndicator, RichLog, Select, Switch
)
from textual.widgets import TabbedContent, TabPane
from textual.widgets.tree import TreeNode
from textual import work, on
from rich.text import Text
from rich.panel import Panel

try:
    from asyncua import Client, Node
    from asyncua.common.node import Node as OpcNode
    from asyncua.ua import NodeClass
    ASYNCUA_AVAILABLE = True
except ImportError:
    ASYNCUA_AVAILABLE = False


# Default path to auto-navigate on connect (from your address space screenshot)
DEFAULT_PATH = [
    "Objects",
    "DeviceSet",
    "Opto22-Cortex-Linux",
    "Resources",
    "Application",
    "GlobalVars",
]
DEFAULT_PATH_LABEL = " → ".join(DEFAULT_PATH)


def _nodeid_to_str(nodeid) -> str:
    """Convert an asyncua NodeId object to proper OPC-UA string format (ns=X;s=...)."""
    try:
        ns = nodeid.NamespaceIndex
        ident = nodeid.Identifier
        id_type = str(nodeid.NodeIdType)
        if "String" in id_type:
            return f"ns={ns};s={ident}"
        elif "Numeric" in id_type:
            return f"ns={ns};i={ident}"
        elif "Guid" in id_type:
            return f"ns={ns};g={ident}"
        else:
            return f"ns={ns};b={ident}"
    except Exception:
        return str(nodeid)


# ─── Connect Screen ────────────────────────────────────────────────────────────

class ConnectScreen(Screen):
    """Initial connection screen with auth options."""

    CSS = """
    ConnectScreen {
        align: center middle;
        background: $background;
    }

    #connect-box {
        width: 68;
        height: auto;
        border: double $primary;
        padding: 2 4;
        background: $surface;
    }

    #title {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 0;
    }

    #subtitle {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }

    .field-label {
        color: $text;
        margin-bottom: 0;
    }

    .section-label {
        color: $primary;
        text-style: bold;
        margin-top: 1;
        margin-bottom: 0;
    }

    Input {
        margin-bottom: 1;
    }

    #host-row {
        height: auto;
        margin-bottom: 0;
    }

    #host-row Input:first-of-type {
        width: 1fr;
        margin-right: 1;
    }

    #host-row Input:last-of-type {
        width: 10;
    }

    #security-row {
        height: auto;
        margin-bottom: 1;
        align: left middle;
    }

    #security-row Label {
        width: auto;
        margin-right: 1;
        content-align: left middle;
    }

    Select {
        width: 1fr;
        margin-bottom: 1;
    }

    #cert-fields {
        display: none;
        height: auto;
    }

    #cert-fields.visible {
        display: block;
    }

    #error-msg {
        color: $error;
        text-align: center;
        height: 1;
        margin-top: 1;
    }

    #btn-row {
        align: center middle;
        margin-top: 1;
    }

    Button {
        margin: 0 1;
    }

    TabbedContent {
        height: auto;
        margin-bottom: 0;
    }

    TabPane {
        padding: 1 0;
    }
    """

    SECURITY_MODES = [
        ("None (Anonymous)", "none_anon"),
        ("None (Username/Password)", "none_user"),
        ("Basic256Sha256 — Sign", "basic256_sign"),
        ("Basic256Sha256 — Sign & Encrypt", "basic256_signencrypt"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="connect-box"):
            yield Label("⚙  OPC-UA Tag Browser", id="title")
            yield Label("Connect to an OPC-UA server", id="subtitle")

            # ── Server ──────────────────────────────────────────────
            yield Label("Hostname / IP", classes="field-label")
            with Horizontal(id="host-row"):
                yield Input(placeholder="192.168.1.100", value="192.168.10.254", id="host-input")
                yield Input(placeholder="Port", value="4840", id="port-input")
            yield Label("Endpoint path  (optional)", classes="field-label")
            yield Input(placeholder="leave blank for default", id="path-input")

            # ── Auth / Security ──────────────────────────────────────
            yield Label("Security Mode", classes="section-label")
            yield Select(
                [(label, val) for label, val in self.SECURITY_MODES],
                value="none_anon",
                id="security-select",
            )

            # Username/password — shown for none_user + cert modes
            with Container(id="userpass-fields"):
                yield Label("Username", classes="field-label")
                yield Input(placeholder="leave blank for anonymous", id="username-input")
                yield Label("Password", classes="field-label")
                yield Input(placeholder="", password=True, id="password-input")

            # Certificate fields — shown for cert-based modes
            with Container(id="cert-fields"):
                yield Label("Client Certificate (.pem or .der)", classes="field-label")
                yield Input(placeholder="/path/to/client_cert.pem", id="cert-input")
                yield Label("Private Key (.pem)", classes="field-label")
                yield Input(placeholder="/path/to/private_key.pem", id="key-input")

            yield Label("", id="error-msg")
            with Horizontal(id="btn-row"):
                yield Button("Connect", variant="primary", id="btn-connect")
                yield Button("Demo Mode", variant="default", id="btn-demo")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#host-input").focus()
        self._update_auth_fields("none_anon")

    def _update_auth_fields(self, mode: str) -> None:
        userpass = self.query_one("#userpass-fields")
        cert = self.query_one("#cert-fields")
        if mode == "none_anon":
            userpass.display = False
            cert.display = False
        elif mode == "none_user":
            userpass.display = True
            cert.display = False
        else:
            # cert-based modes support optional username too
            userpass.display = True
            cert.display = True

    @on(Select.Changed, "#security-select")
    def on_security_changed(self, event: Select.Changed) -> None:
        self._update_auth_fields(str(event.value))

    @on(Button.Pressed, "#btn-connect")
    def do_connect(self) -> None:
        host = self.query_one("#host-input", Input).value.strip()
        port = self.query_one("#port-input", Input).value.strip() or "4840"
        path = self.query_one("#path-input", Input).value.strip()
        security = str(self.query_one("#security-select", Select).value)
        username = self.query_one("#username-input", Input).value.strip()
        password = self.query_one("#password-input", Input).value
        cert_path = self.query_one("#cert-input", Input).value.strip()
        key_path  = self.query_one("#key-input", Input).value.strip()

        if not host:
            self.query_one("#error-msg").update("Please enter a hostname or IP address.")
            return

        # Validate cert fields when required
        if security in ("basic256_sign", "basic256_signencrypt"):
            if not cert_path or not key_path:
                self.query_one("#error-msg").update("Certificate and key paths are required for this security mode.")
                return

        if not host.startswith("opc.tcp://"):
            url = f"opc.tcp://{host}:{port}"
            if path:
                url += f"/{path.lstrip('/')}"
        else:
            url = host

        auth = {
            "mode": security,
            "username": username or None,
            "password": password or None,
            "cert": cert_path or None,
            "key": key_path or None,
        }
        self.app.push_screen(BrowserScreen(url, auth=auth))

    @on(Button.Pressed, "#btn-demo")
    def do_demo(self) -> None:
        self.app.push_screen(BrowserScreen(None))


# ─── Tag Monitor Modal ──────────────────────────────────────────────────────────

class MonitorModal(ModalScreen):
    """Shows live-updating values for watched tags with unwatch/clear/logging controls."""

    BINDINGS = [
        Binding("d", "unwatch_selected", "Unwatch"),
        Binding("ctrl+x", "clear_all", "Clear All"),
        Binding("l", "toggle_logging", "Toggle Log"),
        Binding("escape", "close_modal", "Close"),
    ]

    CSS = """
    MonitorModal {
        align: center middle;
    }

    #monitor-box {
        width: 92%;
        height: 90%;
        border: double $accent;
        background: $surface;
        padding: 1 2;
    }

    #monitor-title {
        text-align: center;
        color: $accent;
        text-style: bold;
        margin-bottom: 0;
    }

    #monitor-hint {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }

    DataTable {
        height: 1fr;
    }

    #log-controls {
        height: auto;
        layout: horizontal;
        align: left middle;
        margin-top: 1;
        margin-bottom: 0;
    }

    #log-controls Label {
        width: auto;
        padding: 0 1;
        content-align: center middle;
    }

    #interval-input {
        width: 8;
    }

    #log-path-input {
        width: 1fr;
    }

    #log-status {
        height: 1;
        color: $text-muted;
        margin-bottom: 0;
    }

    #monitor-btn-row {
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    #monitor-btn-row Button {
        margin: 0 1;
    }

    #tag-count {
        color: $text-muted;
        height: 1;
    }

    .logging-active {
        color: $success;
        text-style: bold;
    }
    """

    def __init__(self, watched: list[dict], client=None) -> None:
        super().__init__()
        self.watched = watched
        self.client = client
        self._logging = False
        self._log_file = None
        self._log_writer = None
        self._log_interval: float = 1.0
        self._log_timer = None
        self._log_row_count = 0

    def compose(self) -> ComposeResult:
        default_path = f"opcua_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with Container(id="monitor-box"):
            yield Label("📡  Live Tag Monitor", id="monitor-title")
            yield Label(
                "[dim][D] unwatch  ·  [Ctrl+X] clear all  ·  [L] toggle logging  ·  [Esc] close[/dim]",
                id="monitor-hint"
            )
            yield Label(f"  {len(self.watched)} tag(s) watched", id="tag-count")
            tbl = DataTable(id="monitor-table", cursor_type="row")
            tbl.add_columns("#", "Name", "Value", "Type", "Node ID", "Updated")
            yield tbl

            # ── Logging controls ─────────────────────────────────────
            with Horizontal(id="log-controls"):
                yield Label("Interval (s):")
                yield Input(value="1", id="interval-input", placeholder="1.0")
                yield Label("  Log file:")
                yield Input(value=default_path, id="log-path-input", placeholder="log.csv")
                yield Button("▶ Start Logging  [L]", id="btn-log-toggle", variant="success")

            yield Label("  Not logging.", id="log-status")

            with Horizontal(id="monitor-btn-row"):
                yield Button("Unwatch  [D]", id="btn-unwatch", variant="warning")
                yield Button("Clear All  [^X]", id="btn-clear", variant="error")
                yield Button("💾 Save Profile", id="btn-save-profile", variant="default")
                yield Button("📂 Load Profile", id="btn-load-profile", variant="default")
                yield Button("Close  [Esc]", id="btn-close", variant="primary")

    def on_mount(self) -> None:
        self._populate()
        self.set_interval(3.0, self._refresh_values)

    # ── Table ────────────────────────────────────────────────────────────────────

    def _populate(self) -> None:
        tbl = self.query_one("#monitor-table", DataTable)
        tbl.clear()
        for i, tag in enumerate(self.watched, 1):
            tbl.add_row(
                str(i),
                tag.get("name", ""),
                str(tag.get("value", "—")),
                tag.get("dtype", ""),
                tag.get("node_id", ""),
                tag.get("ts", "—"),
                key=tag.get("node_id"),
            )
        self.query_one("#tag-count").update(f"  {len(self.watched)} tag(s) watched")

    def _refresh_values(self) -> None:
        tbl = self.query_one("#monitor-table", DataTable)
        now = datetime.now().strftime("%H:%M:%S")
        for i, tag in enumerate(self.watched):
            try:
                if self.client and tag.get("node"):
                    asyncio.create_task(self._read_and_update(tbl, i, tag, now))
                else:
                    tbl.update_cell_at((i, 5), now)
            except Exception:
                pass

    async def _read_and_update(self, tbl: DataTable, row: int, tag: dict, ts: str) -> None:
        try:
            val = await tag["node"].read_value()
            tag["value"] = val
            tbl.update_cell_at((row, 2), str(val))
            tbl.update_cell_at((row, 5), ts)
        except Exception:
            pass

    # ── Logging ──────────────────────────────────────────────────────────────────

    def _start_logging(self) -> bool:
        import csv, os
        try:
            interval_str = self.query_one("#interval-input", Input).value.strip()
            self._log_interval = max(0.1, float(interval_str or "1"))
        except ValueError:
            self.notify("Invalid interval — using 1s", severity="warning")
            self._log_interval = 1.0

        path = self.query_one("#log-path-input", Input).value.strip()
        if not path:
            path = f"opcua_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        write_header = not os.path.exists(path)
        try:
            self._log_file = open(path, "a", newline="")
            self._log_writer = csv.writer(self._log_file)
            if write_header:
                self._log_writer.writerow(
                    ["timestamp"] + [t.get("name", t.get("node_id", "")) for t in self.watched]
                )
                self._log_file.flush()
            self._log_row_count = 0
            self._logging = True
            self._log_timer = self.set_interval(self._log_interval, self._write_log_row)
            self.query_one("#log-status").update(
                f"  [green]● Logging[/green] → [cyan]{path}[/cyan]  every {self._log_interval}s"
            )
            self.query_one("#btn-log-toggle").label = "⏹ Stop Logging  [L]"
            self.query_one("#btn-log-toggle").variant = "error"
            self.notify(f"Logging started → {path}", severity="information")
            return True
        except Exception as exc:
            self.notify(f"Could not open log file: {exc}", severity="error")
            return False

    def _stop_logging(self) -> None:
        self._logging = False
        if self._log_timer:
            self._log_timer.stop()
            self._log_timer = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None
            self._log_writer = None
        self.query_one("#log-status").update(
            f"  [yellow]■ Stopped[/yellow]  ({self._log_row_count} rows written)"
        )
        self.query_one("#btn-log-toggle").label = "▶ Start Logging  [L]"
        self.query_one("#btn-log-toggle").variant = "success"
        self.notify(f"Logging stopped — {self._log_row_count} rows written", severity="warning")

    def _write_log_row(self) -> None:
        if not self._logging or self._log_writer is None:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        row = [ts] + [str(t.get("value", "")) for t in self.watched]
        try:
            self._log_writer.writerow(row)
            self._log_file.flush()
            self._log_row_count += 1
            self.query_one("#log-status").update(
                f"  [green]● Logging[/green]  {self._log_row_count} rows  "
                f"last: {ts}  →  {self.query_one('#log-path-input', Input).value}"
            )
            # Also trigger a live OPC-UA read so logged values are fresh
            self._refresh_values()
        except Exception as exc:
            self._stop_logging()
            self.notify(f"Logging error: {exc}", severity="error")

    # ── Profile save / load ──────────────────────────────────────────────────────

    def _build_profile(self) -> dict:
        """Serialize current monitor state to a JSON-serialisable dict."""
        interval_str = self.query_one("#interval-input", Input).value.strip()
        log_path = self.query_one("#log-path-input", Input).value.strip()
        try:
            interval = float(interval_str)
        except ValueError:
            interval = 1.0
        return {
            "server_url": getattr(self, "_server_url", ""),
            "auth": getattr(self, "_auth", {}),
            "log_interval": interval,
            "log_path": log_path,
            "tags": [
                {
                    "node_id": t.get("node_id", ""),
                    "name": t.get("name", ""),
                    "dtype": t.get("dtype", ""),
                }
                for t in self.watched
            ],
        }

    def _save_profile(self, path: Path = None) -> None:
        path = path or DEFAULT_PROFILE_PATH
        try:
            profile = self._build_profile()
            path.write_text(json.dumps(profile, indent=2))
            self.notify(f"Profile saved → {path}", severity="information")
            self.query_one("#log-status").update(
                f"  [green]✓ Profile saved[/green] → [cyan]{path}[/cyan]"
            )
        except Exception as exc:
            self.notify(f"Save failed: {exc}", severity="error")

    def _load_profile(self, path: Path = None) -> None:
        path = path or DEFAULT_PROFILE_PATH
        if not path.exists():
            self.notify(f"No profile found at {path}", severity="warning")
            self.query_one("#log-status").update(
                f"  [red]Profile not found:[/red] {path}"
            )
            return
        try:
            profile = json.loads(path.read_text())

            # Restore interval + log path
            self.query_one("#interval-input", Input).value = str(profile.get("log_interval", 1.0))
            lp = profile.get("log_path", "")
            if lp:
                self.query_one("#log-path-input", Input).value = lp

            # Restore watched tags — resolve live nodes if client is available
            tag_defs = profile.get("tags", [])
            added, skipped = 0, 0
            existing_ids = {w["node_id"] for w in self.watched}
            for tag in tag_defs:
                node_id = tag.get("node_id", "")
                if not node_id or node_id in existing_ids:
                    skipped += 1
                    continue
                entry = {
                    "node_id": node_id,
                    "name": tag.get("name", node_id),
                    "dtype": tag.get("dtype", ""),
                    "value": "—",
                    "ts": "—",
                    "node": None,
                }
                # Attach live node object if we have a connected client
                if self.client:
                    try:
                        node = self.client.get_node(node_id)
                        entry["node"] = node
                    except Exception:
                        pass
                self.watched.append(entry)
                existing_ids.add(node_id)
                added += 1

            self._populate()
            msg = (f"Profile loaded — {added} tag(s) added"
                   + (f", {skipped} skipped" if skipped else "")
                   + f"  ({profile.get('log_interval', 1)}s interval)")
            self.notify(msg, severity="information")
            self.query_one("#log-status").update(
                f"  [green]✓ Profile loaded[/green] from [cyan]{path}[/cyan]  "
                f"({len(self.watched)} tags total)"
            )
        except Exception as exc:
            self.notify(f"Load failed: {exc}", severity="error")
            self.query_one("#log-status").update(f"  [red]Load failed:[/red] {exc}")

    @on(Button.Pressed, "#btn-save-profile")
    def on_btn_save_profile(self) -> None:
        self._save_profile()

    @on(Button.Pressed, "#btn-load-profile")
    def on_btn_load_profile(self) -> None:
        self._load_profile()

    def on_unmount(self) -> None:
        if self._logging:
            self._stop_logging()

    # ── Actions & events ─────────────────────────────────────────────────────────

    def action_toggle_logging(self) -> None:
        if self._logging:
            self._stop_logging()
        else:
            self._start_logging()

    def action_unwatch_selected(self) -> None:
        tbl = self.query_one("#monitor-table", DataTable)
        if tbl.cursor_row is not None:
            self._unwatch_row(tbl.cursor_row)

    def action_clear_all(self) -> None:
        if self._logging:
            self._stop_logging()
        self.watched.clear()
        self._populate()
        self.notify("Watch list cleared", severity="warning")

    def action_close_modal(self) -> None:
        if self._logging:
            self._stop_logging()
        self.dismiss()

    def _unwatch_row(self, row_index: int) -> None:
        if 0 <= row_index < len(self.watched):
            removed = self.watched.pop(row_index)
            self._populate()
            self.notify(f"Unwatched: {removed.get('name', '')}", severity="warning")

    @on(Button.Pressed, "#btn-log-toggle")
    def on_btn_log_toggle(self) -> None:
        self.action_toggle_logging()

    @on(Button.Pressed, "#btn-unwatch")
    def on_btn_unwatch(self) -> None:
        self.action_unwatch_selected()

    @on(Button.Pressed, "#btn-clear")
    def on_btn_clear(self) -> None:
        self.action_clear_all()

    @on(Button.Pressed, "#btn-close")
    def on_btn_close(self) -> None:
        self.action_close_modal()


# ─── Main Browser Screen ────────────────────────────────────────────────────────

class BrowserScreen(Screen):
    """Node tree browser + tag value panel."""

    BINDINGS = [
        Binding("r", "refresh", "Refresh Tree"),
        Binding("ctrl+r", "reconnect", "Reconnect"),
        Binding("w", "watch_tag", "Watch"),
        Binding("u", "unwatch_tag", "Unwatch"),
        Binding("ctrl+a", "watch_children", "Watch Children"),
        Binding("ctrl+x", "clear_watchlist", "Clear Watchlist"),
        Binding("m", "open_monitor", "Monitor"),
        Binding("escape", "go_back", "Disconnect"),
        Binding("ctrl+l", "clear_log", "Clear Log"),
        Binding("ctrl+h", "goto_root", "Root"),
    ]

    CSS = """
    BrowserScreen {
        layout: vertical;
    }

    #main-area {
        layout: horizontal;
        height: 1fr;
    }

    #left-pane {
        width: 40%;
        border-right: solid $primary-darken-2;
    }

    #right-pane {
        width: 60%;
        layout: vertical;
    }

    #pane-title-tree, #pane-title-detail, #pane-title-log {
        background: $primary-darken-3;
        color: $text;
        padding: 0 1;
        text-style: bold;
    }

    #breadcrumb {
        color: $text-muted;
        background: $surface-darken-1;
        padding: 0 1;
        text-style: italic;
        margin-bottom: 0;
    }

    Tree {
        height: 1fr;
        padding: 0 1;
    }

    #detail-box {
        height: 50%;
        border-bottom: solid $primary-darken-2;
        padding: 1;
        overflow-y: auto;
    }

    #log-box {
        height: 50%;
        padding: 0 1;
    }

    #status-bar {
        height: 1;
        background: $primary-darken-3;
        padding: 0 1;
        color: $text-muted;
    }

    .detail-row {
        margin-bottom: 0;
    }

    .detail-key {
        color: $primary;
        text-style: bold;
    }

    .watch-badge {
        color: $success;
    }

    #loading-overlay {
        align: center middle;
        display: none;
    }

    .dim {
        color: $text-muted;
    }
    """

    connected = reactive(False)
    status_text = reactive("Not connected")

    def __init__(self, url: Optional[str], auth: Optional[dict] = None) -> None:
        super().__init__()
        self.url = url
        self.auth = auth or {"mode": "none_anon", "username": None, "password": None, "cert": None, "key": None}
        self.client: Optional["Client"] = None
        self.watched_tags: list[dict] = []
        self._selected_node_data: Optional[dict] = None
        self.demo_mode = (url is None)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="main-area"):
            with Horizontal():
                with Vertical(id="left-pane"):
                    yield Label(f" 🌳  {DEFAULT_PATH[-1]}", id="pane-title-tree")
                    yield Label(f" {DEFAULT_PATH_LABEL}", id="breadcrumb")
                    yield Tree("GlobalVars", id="node-tree")
                with Vertical(id="right-pane"):
                    yield Label(" 🏷  Tag Details", id="pane-title-detail")
                    yield ScrollableContainer(
                        Static("Select a node from the tree to inspect.", id="detail-content"),
                        id="detail-box"
                    )
                    yield Label(" 📋  Activity Log", id="pane-title-log")
                    yield RichLog(id="log-box", highlight=True, markup=True, max_lines=200)
        yield Static(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._update_status("Connecting…")
        self.connect_and_load()

    # ── Connection ──────────────────────────────────────────────────────────────

    @work(exclusive=True)
    async def connect_and_load(self) -> None:
        log = self.query_one("#log-box", RichLog)

        if self.demo_mode:
            log.write("[bold yellow]DEMO MODE[/] — showing synthetic data (no real OPC-UA server)")
            self._update_status("Demo mode  |  Press [W] to watch a tag  |  [M] to open monitor")
            self._populate_demo_tree()
            return

        if not ASYNCUA_AVAILABLE:
            log.write("[red]asyncua not installed.[/] Run: [bold]pip install asyncua[/]")
            self._update_status("Missing dependency: asyncua")
            return

        log.write(f"Connecting to [cyan]{self.url}[/]…")

        # Clean up any previous dead client
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None
        self.connected = False

        # --- TCP reachability pre-check ---
        import socket
        try:
            host_part = self.url.split("://")[-1].split("/")[0]
            tcp_host, tcp_port = (host_part.rsplit(":", 1) + ["4840"])[:2]
            sock = socket.create_connection((tcp_host, int(tcp_port)), timeout=5)
            sock.close()
            log.write(f"[green]✓ TCP reachable[/] {tcp_host}:{tcp_port}")
        except Exception as tcp_err:
            tcp_msg = str(tcp_err) or repr(tcp_err)
            log.write(f"[red]✗ TCP unreachable:[/] {tcp_host}:{tcp_port} — {tcp_msg}")
            log.write("[yellow]Check:[/] Is the server powered on? Is the IP correct? Is port 4840 open?")
            self._update_status(f"TCP unreachable — {tcp_host}:{tcp_port}")
            self.client = None
            return

        # Use a generous timeout - Opto22 can be slow to respond
        self.client = Client(url=self.url, timeout=30)

        # Apply security / auth settings
        mode = self.auth.get("mode", "none_anon")
        username = self.auth.get("username")
        password = self.auth.get("password")
        cert     = self.auth.get("cert")
        key      = self.auth.get("key")

        try:
            if mode in ("basic256_sign", "basic256_signencrypt") and cert and key:
                from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
                encrypt = "SignAndEncrypt" if "signencrypt" in mode else "Sign"
                await self.client.set_security(
                    SecurityPolicyBasic256Sha256,
                    certificate=cert,
                    private_key=key,
                    mode=encrypt,
                )
                log.write(f"[dim]Security: Basic256Sha256 / {encrypt}[/dim]")
            if username:
                self.client.set_user(username)
                self.client.set_password(password or "")
                log.write(f"[dim]Auth: username={username}[/dim]")
            else:
                log.write("[dim]Auth: Anonymous[/dim]")
        except Exception as sec_err:
            log.write(f"[yellow]Security setup warning:[/] {sec_err}")

        try:
            await self.client.connect()
            self.connected = True
            log.write(f"[green]✓ Connected[/] to {self.url}")
            self._update_status(f"Connected → {self.url}  |  [R] refresh  [W] watch  [M] monitor")
            await self._load_tree()
        except Exception as exc:
            err = str(exc) or repr(exc) or type(exc).__name__
            log.write(f"[red]✗ Connection failed:[/] [bold]{type(exc).__name__}[/]: {err}")
            if "BadSessionId" in err or "BadSession" in err:
                log.write("[yellow]Hint:[/] Server rejected the session. Try a different endpoint path.")
            if "TimeoutError" in type(exc).__name__ or "timed out" in err.lower():
                log.write("[yellow]Hint:[/] OPC-UA handshake timed out. The server may need security=None or a specific endpoint path (e.g. add /OPCUA/SimulationServer to the URL).")
            elif "refused" in err.lower():
                log.write("[yellow]Hint:[/] Connection refused. Check IP and that port 4840 is open.")
            self._update_status(f"Connection failed — {type(exc).__name__}: {err[:80]}")
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

    async def _load_tree(self) -> None:
        tree = self.query_one("#node-tree", Tree)
        log = self.query_one("#log-box", RichLog)
        tree.clear()
        try:
            log.write(f"Navigating to [cyan]{DEFAULT_PATH_LABEL}[/] …")
            target_node = await self._walk_path(DEFAULT_PATH)
            if target_node is None:
                log.write("[yellow]⚠ Default path not found — falling back to server root.[/]")
                target_node = self.client.get_root_node()
                self.query_one("#breadcrumb").update(" Server Root")
            else:
                log.write(f"[green]✓ Landed at[/] [cyan]{DEFAULT_PATH[-1]}[/]")

            root_node = tree.root
            root_node.data = {
                "node": target_node,
                "node_id": str(target_node.nodeid),
                "name": DEFAULT_PATH[-1],
            }
            root_node.expand()
            await self._add_children(root_node, target_node)
            log.write("[green]✓ Tags loaded[/]")
        except Exception as exc:
            log.write(f"[red]Tree load error:[/] {exc}")

    async def _walk_path(self, path: list[str]) -> Optional["OpcNode"]:
        """Walk the OPC-UA address space by display name OR browse name, return final node or None."""
        if self.client is None:
            return None
        node = self.client.get_root_node()
        for segment in path:
            children = await node.get_children()
            found = None
            for child in children:
                try:
                    display = (await child.read_display_name()).Text or ""
                    browse = (await child.read_browse_name()).Name or ""
                    if display == segment or browse == segment:
                        found = child
                        break
                except Exception:
                    continue
            if found is None:
                return None
            node = found
        return node

    async def _add_children(self, tree_node: TreeNode, opc_node: "OpcNode", depth: int = 0) -> None:
        if depth > 6:
            return
        try:
            children = await opc_node.get_children()
            for child in children[:200]:  # cap to avoid huge servers freezing
                try:
                    name = (await child.read_display_name()).Text or str(child.nodeid)
                    node_class = await child.read_node_class()
                    icon = "📁" if node_class == NodeClass.Object else "🏷"
                    branch = tree_node.add(f"{icon} {name}", data={
                        "node": child,
                        "node_id": _nodeid_to_str(child.nodeid),
                        "name": name,
                        "node_class": str(node_class),
                    })
                    if node_class == NodeClass.Object:
                        branch.allow_expand = True
                except Exception:
                    pass
        except Exception:
            pass

    # ── Demo tree ───────────────────────────────────────────────────────────────

    def _populate_demo_tree(self) -> None:
        tree = self.query_one("#node-tree", Tree)
        tree.clear()

        # Mirrors the address space seen in the screenshot:
        # Objects > DeviceSet > Opto22-Cortex-Linux > Resources > Application > GlobalVars
        demo_structure = {
            "📁 HMI_DB": {
                "🏷 SystemReady":     {"value": True,   "dtype": "Boolean", "unit": ""},
                "🏷 AlarmCount":      {"value": 3,      "dtype": "Int32",   "unit": ""},
                "🏷 OperatorMessage": {"value": "Run",  "dtype": "String",  "unit": ""},
            },
            "📁 Master_Recieve_1A": {
                "🏷 Temperature":     {"value": 72.4,   "dtype": "Float",   "unit": "°F"},
                "🏷 Pressure":        {"value": 14.7,   "dtype": "Float",   "unit": "psi"},
                "🏷 FlowRate":        {"value": 120.5,  "dtype": "Float",   "unit": "L/min"},
                "🏷 RunningState":    {"value": True,   "dtype": "Boolean", "unit": ""},
                "🏷 CycleCount":      {"value": 48210,  "dtype": "Int32",   "unit": ""},
            },
            "📁 Master_Recieve_1B": {
                "🏷 Temperature":     {"value": 68.9,   "dtype": "Float",   "unit": "°F"},
                "🏷 Pressure":        {"value": 15.1,   "dtype": "Float",   "unit": "psi"},
                "🏷 FlowRate":        {"value": 98.3,   "dtype": "Float",   "unit": "L/min"},
                "🏷 RunningState":    {"value": False,  "dtype": "Boolean", "unit": ""},
                "🏷 CycleCount":      {"value": 31004,  "dtype": "Int32",   "unit": ""},
            },
            "📁 DeviceFeatures": {
                "🏷 FirmwareVersion": {"value": "R10.4b", "dtype": "String", "unit": ""},
                "🏷 SerialNumber":    {"value": "SN-00421", "dtype": "String", "unit": ""},
                "🏷 Uptime_s":        {"value": 864000,  "dtype": "Int64",  "unit": "s"},
            },
        }

        def add_nodes(parent: TreeNode, structure: dict) -> None:
            for label, content in structure.items():
                if isinstance(content, dict) and "value" not in content:
                    branch = parent.add(label, data={"name": label, "is_folder": True})
                    add_nodes(branch, content)
                else:
                    node_id = f"ns=2;s=GlobalVars.{label.split(' ', 1)[-1]}"
                    parent.add_leaf(label, data={
                        "name": label.split(" ", 1)[-1],
                        "node_id": node_id,
                        "value": content["value"],
                        "dtype": content["dtype"],
                        "unit": content["unit"],
                        "is_folder": False,
                        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })

        add_nodes(tree.root, demo_structure)
        tree.root.expand()


    # ── Tree interaction ────────────────────────────────────────────────────────

    @on(Tree.NodeSelected, "#node-tree")
    async def on_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if not data:
            return
        self._selected_node_data = data

        if self.demo_mode or not ASYNCUA_AVAILABLE:
            self._show_demo_details(data)
        else:
            await self._show_live_details(data)
            if not event.node.children and data.get("node"):
                await self._add_children(event.node, data["node"], depth=1)

    def _show_demo_details(self, data: dict) -> None:
        if data.get("is_folder"):
            self.query_one("#detail-content", Static).update(
                "[dim]Folder node — expand to see children[/dim]"
            )
            return

        watched = any(w["node_id"] == data.get("node_id") for w in self.watched_tags)
        watch_txt = "  [green]● WATCHING[/green]" if watched else ""

        lines = [
            f"[bold cyan]{data.get('name', 'Unknown')}[/bold cyan]{watch_txt}\n",
            f"[bold]Node ID:[/bold]    {data.get('node_id', '—')}",
            f"[bold]Value:[/bold]      [yellow]{data.get('value', '—')}[/yellow]  {data.get('unit', '')}",
            f"[bold]Data Type:[/bold]  {data.get('dtype', '—')}",
            f"[bold]Timestamp:[/bold]  {data.get('ts', '—')}",
            "",
            "[dim]Press [W] to add to monitor[/dim]",
        ]
        self.query_one("#detail-content", Static).update("\n".join(lines))

    async def _show_live_details(self, data: dict) -> None:
        content = self.query_one("#detail-content", Static)
        node: OpcNode = data.get("node")
        if not node:
            return

        name = data.get("name", str(node.nodeid))
        node_id = data.get("node_id", "—")

        # Always read node class live — stored value may be stale/missing
        try:
            nc = await node.read_node_class()
            is_object = (nc == NodeClass.Object)
        except Exception:
            is_object = False

        if is_object or data.get("is_folder"):
            child_count = ""
            try:
                children = await node.get_children()
                var_count = sum(1 for _ in [c for c in children])
                # quick class scan
                v, o = 0, 0
                for c in children:
                    try:
                        cnc = await c.read_node_class()
                        if cnc == NodeClass.Variable:
                            v += 1
                        else:
                            o += 1
                    except Exception:
                        pass
                child_count = f"\n[bold]Children:[/bold]   {o} folder(s), {v} variable(s)"
            except Exception:
                pass
            lines = [
                f"[bold cyan]{name}[/bold cyan]  [dim](folder)[/dim]\n",
                f"[bold]Node ID:[/bold]    {node_id}",
                f"[bold]Node Class:[/bold] Object",
                child_count,
                "",
                "[dim][Ctrl+A] watch all variable children[/dim]",
            ]
            content.update("\n".join(lines))
            return

        # Variable node — try to read value
        try:
            val = await node.read_value()
            dtype = type(val).__name__
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            data.update({"value": val, "dtype": dtype, "ts": ts, "name": name})

            watched = any(w["node_id"] == node_id for w in self.watched_tags)
            watch_txt = "  [green]● WATCHING[/green]" if watched else ""
            hint = "[dim][U] unwatch[/dim]" if watched else "[dim][W] watch  ·  [Ctrl+A] watch folder[/dim]"

            lines = [
                f"[bold cyan]{name}[/bold cyan]{watch_txt}\n",
                f"[bold]Node ID:[/bold]    {node_id}",
                f"[bold]Value:[/bold]      [yellow]{val}[/yellow]",
                f"[bold]Data Type:[/bold]  {dtype}",
                f"[bold]Timestamp:[/bold]  {ts}",
                "",
                hint,
            ]
            content.update("\n".join(lines))
        except Exception as exc:
            err = str(exc)
            # BadAttributeIdInvalid means it's a non-variable node — show as folder
            if "BadAttributeId" in err or "BadNodeId" in err:
                lines = [
                    f"[bold cyan]{name}[/bold cyan]  [dim](object/method node)[/dim]\n",
                    f"[bold]Node ID:[/bold]    {node_id}",
                    "",
                    "[dim]This node type does not have a readable value.[/dim]",
                    "[dim][Ctrl+A] to watch all variable children[/dim]",
                ]
                content.update("\n".join(lines))
            else:
                content.update(
                    f"[bold cyan]{name}[/bold cyan]\n"
                    f"[bold]Node ID:[/bold] {node_id}\n\n"
                    f"[red]Read error:[/red] {err}"
                )

    # ── Actions ─────────────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        log = self.query_one("#log-box", RichLog)
        log.write(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] Refreshing tree…")
        self.connect_and_load()

    def action_reconnect(self) -> None:
        """Hard reconnect — disconnect cleanly then reconnect to the same URL."""
        log = self.query_one("#log-box", RichLog)
        log.write(f"[yellow]⟳ Reconnecting to {self.url}…[/yellow]")
        self._update_status("Reconnecting…")
        self.connected = False
        # Invalidate node references in watch list so stale nodes aren't used
        for tag in self.watched_tags:
            tag["node"] = None
            tag["value"] = "—"
        self.connect_and_load()

    def action_watch_tag(self) -> None:
        data = self._selected_node_data
        log = self.query_one("#log-box", RichLog)
        if not data or "Object" in data.get("node_class", "") or data.get("is_folder"):
            log.write("[yellow]Select a variable node to watch. For folders use [Ctrl+A].[/]")
            return
        node_id = data.get("node_id", "")
        if any(w["node_id"] == node_id for w in self.watched_tags):
            log.write(f"[yellow]{data.get('name')}[/] already in watch list.")
            return
        self.watched_tags.append({
            "node_id": node_id,
            "name": data.get("name", node_id),
            "value": data.get("value", "—"),
            "dtype": data.get("dtype", ""),
            "ts": data.get("ts", ""),
            "node": data.get("node"),
        })
        log.write(f"[green]+[/] Watching [cyan]{data.get('name')}[/]  ({len(self.watched_tags)} total)")

    def action_unwatch_tag(self) -> None:
        data = self._selected_node_data
        log = self.query_one("#log-box", RichLog)
        if not data:
            log.write("[yellow]Select a watched node first.[/]")
            return
        node_id = data.get("node_id", "")
        before = len(self.watched_tags)
        self.watched_tags = [w for w in self.watched_tags if w["node_id"] != node_id]
        if len(self.watched_tags) < before:
            log.write(f"[red]−[/] Unwatched [cyan]{data.get('name')}[/]  ({len(self.watched_tags)} remaining)")
        else:
            log.write(f"[yellow]{data.get('name')}[/] was not in the watch list.")

    def action_clear_watchlist(self) -> None:
        count = len(self.watched_tags)
        self.watched_tags.clear()
        self.query_one("#log-box", RichLog).write(f"[red]✕[/] Cleared watch list ({count} tag(s) removed)")

    @work(exclusive=False)
    async def action_watch_children(self) -> None:
        data = self._selected_node_data
        log = self.query_one("#log-box", RichLog)
        if not data:
            log.write("[yellow]Select a folder node first.[/]")
            return
        node: OpcNode = data.get("node")
        if node is None:
            log.write("[yellow]No OPC-UA node attached (demo mode?).[/]")
            return

        log.write(f"Recursively scanning [cyan]{data.get('name')}[/] …")
        added = 0
        skipped = 0
        existing_ids = {w["node_id"] for w in self.watched_tags}

        async def recurse(n: OpcNode, depth: int = 0) -> None:
            nonlocal added, skipped
            if depth > 10:
                return
            try:
                children = await n.get_children()
            except Exception:
                return
            for child in children:
                try:
                    nc = await child.read_node_class()
                    if nc == NodeClass.Variable:
                        node_id = str(child.nodeid)
                        if node_id in existing_ids:
                            skipped += 1
                            continue
                        name = (await child.read_display_name()).Text or node_id
                        try:
                            val = await child.read_value()
                            dtype = type(val).__name__
                        except Exception:
                            val = "—"
                            dtype = "unknown"
                        self.watched_tags.append({
                            "node_id": node_id,
                            "name": name,
                            "value": val,
                            "dtype": dtype,
                            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "node": child,
                        })
                        existing_ids.add(node_id)
                        added += 1
                        # Log progress every 10 tags
                        if added % 10 == 0:
                            log.write(f"[dim]  … {added} tags found so far[/dim]")
                    elif nc == NodeClass.Object:
                        await recurse(child, depth + 1)
                except Exception:
                    pass

        try:
            await recurse(node)
        except Exception as exc:
            log.write(f"[red]Error during scan:[/] {exc}")
            return

        msg = f"[green]+{added}[/] variable(s) added from [cyan]{data.get('name')}[/] (all levels)"
        if skipped:
            msg += f"  ([dim]{skipped} already watched[/dim])"
        msg += f"  ({len(self.watched_tags)} total)"
        log.write(msg)

    def action_open_monitor(self) -> None:
        modal = MonitorModal(self.watched_tags, client=self.client)
        modal._server_url = self.url or ""
        modal._auth = self.auth
        self.app.push_screen(modal)

    def action_go_back(self) -> None:
        if self.client:
            asyncio.create_task(self.client.disconnect())
        self.app.pop_screen()

    def action_clear_log(self) -> None:
        self.query_one("#log-box", RichLog).clear()

    def action_goto_root(self) -> None:
        """Reset tree to server root instead of default path."""
        log = self.query_one("#log-box", RichLog)
        if self.demo_mode:
            log.write("[yellow]Root navigation not available in demo mode.[/]")
            return
        self.query_one("#breadcrumb").update(" Server Root")
        log.write("[dim]Navigating to server root…[/dim]")
        self._load_from_root()

    @work(exclusive=True)
    async def _load_from_root(self) -> None:
        tree = self.query_one("#node-tree", Tree)
        log = self.query_one("#log-box", RichLog)
        if self.client is None or not self.connected:
            log.write("[red]Not connected. Press [R] to reconnect first.[/]")
            return
        tree.clear()
        try:
            root = self.client.get_root_node()
            root_node = tree.root
            root_node.set_label("Root")
            root_node.data = {"node": root, "node_id": str(root.nodeid), "name": "Root"}
            root_node.expand()
            await self._add_children(root_node, root)
            log.write("[green]✓ Root tree loaded[/]")
        except Exception as exc:
            log.write(f"[red]Root load error:[/] {exc}")


    # ── Helpers ─────────────────────────────────────────────────────────────────

    def _update_status(self, msg: str) -> None:
        try:
            self.query_one("#status-bar", Static).update(f" {msg}")
        except Exception:
            pass


# ─── App ────────────────────────────────────────────────────────────────────────

class OpcUaApp(App):
    """OPC-UA Tag Browser TUI."""

    TITLE = "OPC-UA Tag Browser"
    CSS = """
    Header { background: $primary-darken-3; }
    Footer { background: $primary-darken-3; }
    """

    def on_mount(self) -> None:
        self.push_screen(ConnectScreen())


# ─── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not ASYNCUA_AVAILABLE:
        print("\n⚠  asyncua is not installed.")
        print("   Run:  pip install asyncua\n")
        print("   The app will still launch in DEMO MODE.\n")

    app = OpcUaApp()
    app.run()
