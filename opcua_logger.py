#!/usr/bin/env python3
"""
opcua_logger.py — Headless OPC-UA tag logger
Reads a profile saved from the TUI and logs tag values to CSV at a set interval.

Usage:
    python opcua_logger.py                          # uses ~/.opcua_tui_profile.json
    python opcua_logger.py --profile my.json        # custom profile
    python opcua_logger.py --url opc.tcp://192.168.10.254:4840 \
                           --tags "ns=2;s=Tag1" "ns=2;s=Tag2" \
                           --interval 1 --output log.csv

Run as background process:
    nohup python opcua_logger.py &> logger.log &
    echo $! > logger.pid          # save PID to stop it later
    kill $(cat logger.pid)        # stop it

Run as a systemd service: see --print-service flag.
"""

import argparse
import asyncio
import csv
import json
import signal
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_PROFILE = Path(__file__).parent / "opcua_tui_profile.json"


def _parse_nodeid(node_id_str: str) -> str:
    """
    Convert any node ID format to proper OPC-UA string (ns=X;s=...).
    Handles both proper format and the legacy Python repr:
      NodeId(Identifier='Root.Kiln...', NamespaceIndex=4, NodeIdType=...)
    """
    import re
    s = str(node_id_str).strip()
    if s.startswith("ns=") or s.startswith("i=") or s.startswith("s="):
        return s
    if "Identifier=" in s and "NamespaceIndex=" in s:
        ident = re.search(r"Identifier='([^']+)'", s)
        ns    = re.search(r"NamespaceIndex=(\d+)", s)
        itype = re.search(r"NodeIdType=<NodeIdType\.(\w+)", s)
        if ident and ns:
            t = itype.group(1).lower() if itype else "string"
            prefix = "i" if "numeric" in t else "s"
            return f"ns={ns.group(1)};{prefix}={ident.group(1)}"
    return s


try:
    from asyncua import Client
    from asyncua.ua import NodeClass
except ImportError:
    print("ERROR: asyncua not installed.  Run: pip install asyncua")
    sys.exit(1)


# ─── Config ────────────────────────────────────────────────────────────────────

def load_profile(path: Path) -> dict:
    if not path.exists():
        print(f"ERROR: Profile not found: {path}")
        print("  Save a profile first using the TUI monitor screen.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def build_config(args: argparse.Namespace) -> dict:
    """Merge CLI args over a loaded profile (CLI wins)."""
    profile_path = Path(args.profile) if args.profile else DEFAULT_PROFILE

    if profile_path.exists():
        cfg = load_profile(profile_path)
        print(f"[config] Loaded profile: {profile_path}")
    else:
        cfg = {"tags": [], "log_interval": 1.0, "log_path": "", "server_url": ""}

    if args.url:
        cfg["server_url"] = args.url
    if args.interval is not None:
        cfg["log_interval"] = args.interval
    if args.output:
        cfg["log_path"] = args.output
    if args.tags:
        # Override tags with CLI node IDs (names will be the node_id strings)
        cfg["tags"] = [{"node_id": t, "name": t, "dtype": ""} for t in args.tags]

    # Validate
    if not cfg.get("server_url"):
        print("ERROR: No server URL. Set it in the profile or use --url")
        sys.exit(1)
    if not cfg.get("tags"):
        print("ERROR: No tags to log. Set them in the profile or use --tags")
        sys.exit(1)
    if not cfg.get("log_path"):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        cfg["log_path"] = f"opcua_log_{ts}.csv"

    return cfg


# ─── Logger ────────────────────────────────────────────────────────────────────

class OpcUaLogger:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.url: str = cfg["server_url"]
        self.interval: float = float(cfg.get("log_interval", 1.0))
        self.output: str = cfg["log_path"]
        self.tag_defs: list[dict] = cfg["tags"]
        self.client: Client = None
        self.nodes: list = []
        self.names: list[str] = []
        self._running = False
        self._rows_written = 0

    async def connect(self) -> None:
        print(f"[connect] Connecting to {self.url} ...")
        self.client = Client(url=self.url, timeout=30)

        auth = self.cfg.get("auth", {})
        mode     = auth.get("mode", "none_anon")
        username = auth.get("username")
        password = auth.get("password")
        cert     = auth.get("cert")
        key      = auth.get("key")

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
                print(f"[auth] Security: Basic256Sha256 / {encrypt}")
            if username:
                self.client.set_user(username)
                self.client.set_password(password or "")
                print(f"[auth] Username: {username}")
            else:
                print("[auth] Anonymous")
        except Exception as e:
            print(f"[auth] Warning: {e}")

        await self.client.connect()
        print(f"[connect] Connected")

        # Resolve node objects from node_id strings
        self.nodes = []
        self.names = []
        for tag in self.tag_defs:
            node_id = tag.get("node_id", "")
            name = tag.get("name", node_id)
            try:
                clean_id = _parse_nodeid(node_id)
                node = self.client.get_node(clean_id)
                # Quick read to verify it's readable
                await node.read_value()
                self.nodes.append(node)
                self.names.append(name)
                print(f"  [ok] {name}  ({node_id})")
            except Exception as exc:
                print(f"  [skip] {name} ({node_id}) — {exc}")

        if not self.nodes:
            print("ERROR: No readable nodes found. Check your profile.")
            await self.client.disconnect()
            sys.exit(1)

        print(f"[connect] {len(self.nodes)} tag(s) ready to log")

    async def run(self) -> None:
        import os
        write_header = not os.path.exists(self.output)

        with open(self.output, "a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(["timestamp"] + self.names)
                f.flush()
                print(f"[log] Created {self.output}")
            else:
                print(f"[log] Appending to {self.output}")

            print(f"[log] Interval: {self.interval}s  |  Tags: {len(self.nodes)}")
            print(f"[log] Press Ctrl+C to stop\n")

            self._running = True
            while self._running:
                loop_start = asyncio.get_event_loop().time()
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

                values = []
                for node in self.nodes:
                    try:
                        val = await node.read_value()
                        values.append(str(val))
                    except Exception as exc:
                        values.append(f"ERR:{type(exc).__name__}")

                writer.writerow([ts] + values)
                f.flush()
                self._rows_written += 1

                # Live status line (overwrite in place)
                status = f"\r[{ts}]  row {self._rows_written:>6}  |  " + \
                         "  ".join(f"{n}={v}" for n, v in zip(self.names[:4], values[:4]))
                if len(self.names) > 4:
                    status += f"  (+{len(self.names)-4} more)"
                print(status, end="", flush=True)

                # Sleep for remainder of interval
                elapsed = asyncio.get_event_loop().time() - loop_start
                sleep_time = max(0.0, self.interval - elapsed)
                await asyncio.sleep(sleep_time)

        print(f"\n[log] Stopped. {self._rows_written} rows written to {self.output}")

    def stop(self) -> None:
        print("\n[log] Stopping...")
        self._running = False

    async def disconnect(self) -> None:
        if self.client:
            try:
                await self.client.disconnect()
                print("[connect] Disconnected")
            except Exception:
                pass


# ─── systemd service generator ─────────────────────────────────────────────────

def print_systemd_service(args: argparse.Namespace) -> None:
    profile = Path(args.profile).resolve() if args.profile else DEFAULT_PROFILE.resolve()
    python = sys.executable
    script = Path(__file__).resolve()
    user = __import__("os").getenv("USER", "pi")

    svc = f"""[Unit]
Description=OPC-UA Tag Logger
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={script.parent}
ExecStart={python} {script} --profile {profile}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    print(svc)
    print("# To install:")
    print(f"# sudo cp opcua-logger.service /etc/systemd/system/")
    print(f"# sudo systemctl daemon-reload")
    print(f"# sudo systemctl enable --now opcua-logger")
    print(f"# sudo journalctl -fu opcua-logger   # follow logs")


# ─── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Headless OPC-UA tag logger. Reads a TUI-saved profile.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--profile", metavar="PATH",
                   help=f"Profile JSON path (default: {DEFAULT_PROFILE})")
    p.add_argument("--url", metavar="URL",
                   help="Override server URL, e.g. opc.tcp://192.168.10.254:4840")
    p.add_argument("--tags", metavar="NODE_ID", nargs="+",
                   help="Override tags by Node ID, e.g. ns=2;s=GlobalVars.Kiln.Temp")
    p.add_argument("--interval", metavar="SECONDS", type=float,
                   help="Override log interval in seconds (default: from profile or 1.0)")
    p.add_argument("--output", metavar="FILE",
                   help="Override output CSV file path")
    p.add_argument("--print-service", action="store_true",
                   help="Print a systemd .service unit file and exit")
    p.add_argument("--show-profile", action="store_true",
                   help="Print the loaded profile config and exit (dry run)")
    return p.parse_args()


async def main() -> None:
    args = parse_args()

    if args.print_service:
        print_systemd_service(args)
        return

    cfg = build_config(args)

    if args.show_profile:
        print(json.dumps(cfg, indent=2))
        return

    print("=" * 60)
    print("  OPC-UA Headless Logger")
    print("=" * 60)
    print(f"  Server   : {cfg['server_url']}")
    print(f"  Tags     : {len(cfg['tags'])}")
    print(f"  Interval : {cfg['log_interval']}s")
    print(f"  Output   : {cfg['log_path']}")
    print("=" * 60)

    logger = OpcUaLogger(cfg)

    # Handle Ctrl+C and SIGTERM cleanly
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, logger.stop)

    try:
        await logger.connect()
        await logger.run()
    except Exception as exc:
        print(f"\nFATAL: {type(exc).__name__}: {exc}")
    finally:
        await logger.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
