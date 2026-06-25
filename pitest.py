#!/usr/bin/env python3
"""
pitest.py — Raspberry Pi hardware diagnostics
Tests: LAN, WLAN, USB, Boot — sends results to external API
"""

import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("Missing: pip install requests")

# ─── CONFIG ───────────────────────────────────────────────────────────────────

API_URL     = "https://your-api.example.com/api/pitest"  # <-- change
API_TOKEN   = ""          # Bearer token, empty = no auth
DEVICE_ID   = socket.gethostname()
PING_HOST   = "8.8.8.8"
PING_COUNT  = 4
TIMEOUT_S   = 10

HOTSPOT_SSID     = "PiTest"
HOTSPOT_PASSWORD = "pitest123"
HOTSPOT_TIMEOUT  = 300        # seconds to wait for a client to connect
HOTSPOT_CON_NAME = "pitest-hotspot"

# ──────────────────────────────────────────────────────────────────────────────


def run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"


def ping(host: str, count: int = PING_COUNT) -> dict:
    code, out, _ = run(["ping", "-c", str(count), "-W", "2", host], timeout=count * 3 + 5)
    result = {"host": host, "reachable": code == 0}
    if code == 0:
        m = re.search(r"(\d+)% packet loss", out)
        if m:
            result["packet_loss_pct"] = int(m.group(1))
        m = re.search(r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)", out)
        if m:
            result["rtt_min_ms"]  = float(m.group(1))
            result["rtt_avg_ms"]  = float(m.group(2))
            result["rtt_max_ms"]  = float(m.group(3))
    return result


def iface_info(iface: str) -> dict:
    """Return IP, MAC, state for a network interface."""
    info: dict = {"name": iface}

    code, out, _ = run(["ip", "addr", "show", iface])
    if code != 0:
        info["present"] = False
        return info

    info["present"] = True
    info["up"] = "state UP" in out or ",UP," in out or "state UNKNOWN" in out

    m = re.search(r"inet ([\d.]+/\d+)", out)
    info["ipv4"] = m.group(1) if m else None

    m = re.search(r"link/ether ([0-9a-f:]+)", out)
    info["mac"] = m.group(1) if m else None

    return info


# ─── LAN ──────────────────────────────────────────────────────────────────────

def test_lan() -> dict:
    result: dict = {"status": "fail", "details": {}}

    iface = _find_iface("eth")
    details = iface_info(iface)
    result["details"]["interface"] = details

    if not details.get("present"):
        result["message"] = f"Interface {iface} not found"
        return result

    if not details.get("up"):
        result["message"] = f"Interface {iface} is down"
        return result

    if not details.get("ipv4"):
        result["message"] = "No IPv4 address assigned"
        return result

    ping_result = ping(PING_HOST)
    result["details"]["ping"] = ping_result

    if ping_result["reachable"]:
        result["status"] = "pass"
        result["message"] = f"LAN OK — {details['ipv4']} — ping {PING_HOST} OK"
    else:
        result["message"] = f"Interface up, IP assigned, but {PING_HOST} unreachable"

    return result


# ─── WLAN ─────────────────────────────────────────────────────────────────────

def test_wlan() -> dict:
    result: dict = {"status": "fail", "details": {}}

    iface = _find_iface("wlan")
    details = iface_info(iface)
    result["details"]["interface"] = details

    if not details.get("present"):
        result["message"] = f"Interface {iface} not found"
        return result

    if not details.get("up"):
        result["message"] = f"Interface {iface} is down"
        return result

    # SSID / signal
    code, out, _ = run(["iwconfig", iface])
    if code == 0:
        m = re.search(r'ESSID:"([^"]+)"', out)
        details["ssid"] = m.group(1) if m else None

        m = re.search(r"Signal level=(-\d+)", out)
        details["signal_dbm"] = int(m.group(1)) if m else None

        m = re.search(r"Bit Rate=([\d.]+\s*\S+)", out)
        details["bit_rate"] = m.group(1).strip() if m else None
    else:
        # try iw
        code2, out2, _ = run(["iw", "dev", iface, "link"])
        if code2 == 0:
            m = re.search(r"SSID: (.+)", out2)
            details["ssid"] = m.group(1).strip() if m else None
            m = re.search(r"signal: (-\d+)", out2)
            details["signal_dbm"] = int(m.group(1)) if m else None

    if not details.get("ipv4"):
        result["message"] = "No IPv4 address — not associated or DHCP failed"
        return result

    ping_result = ping(PING_HOST)
    result["details"]["ping"] = ping_result

    if ping_result["reachable"]:
        result["status"] = "pass"
        ssid = details.get("ssid", "?")
        result["message"] = f"WLAN OK — SSID: {ssid} — {details['ipv4']} — ping OK"
    else:
        result["message"] = "WLAN connected, IP assigned, but gateway unreachable"

    return result


# ─── BOOT ─────────────────────────────────────────────────────────────────────

def test_boot() -> dict:
    result: dict = {"status": "fail", "details": {}}

    # uptime
    code, out, _ = run(["uptime", "-p"])
    result["details"]["uptime"] = out if code == 0 else None

    # last boot
    code, out, _ = run(["who", "-b"])
    if code == 0:
        m = re.search(r"system boot\s+(.+)", out)
        result["details"]["last_boot"] = m.group(1).strip() if m else out.strip()

    # systemd-analyze
    code, out, _ = run(["systemd-analyze"], timeout=15)
    if code == 0:
        m = re.search(r"Startup finished in (.+)", out)
        result["details"]["boot_time"] = m.group(1).strip() if m else out.strip()

    # /boot partition
    code, out, _ = run(["findmnt", "--target", "/boot", "-o", "SOURCE,FSTYPE,SIZE,USED,AVAIL", "-n"])
    result["details"]["boot_partition"] = {
        "mounted": code == 0,
        "info": out if code == 0 else None,
    }

    # critical failed units
    code, out, _ = run(["systemctl", "list-units", "--state=failed", "--no-legend"])
    failed_units = [ln.split()[0] for ln in out.splitlines() if ln.strip()] if out else []
    result["details"]["failed_units"] = failed_units

    # kernel / os
    result["details"]["kernel"] = platform.release()
    result["details"]["os"] = _read_os_release()

    # /boot partition must be mounted, no failed critical units
    critical = [u for u in failed_units if any(k in u for k in ("network", "ssh", "boot", "init"))]
    if not result["details"]["boot_partition"]["mounted"]:
        result["message"] = "/boot not mounted"
    elif critical:
        result["message"] = f"Critical failed units: {', '.join(critical)}"
    else:
        result["status"] = "pass"
        result["message"] = (
            f"Boot OK — {result['details'].get('boot_time', 'n/a')} — "
            f"{len(failed_units)} failed unit(s)"
        )

    return result


def _read_os_release() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


# ─── USB ──────────────────────────────────────────────────────────────────────

def test_usb() -> dict:
    result: dict = {"status": "fail", "details": {"devices": [], "ports": {}}}

    # lsusb
    code, out, _ = run(["lsusb"])
    devices = []
    if code == 0:
        for line in out.splitlines():
            m = re.match(r"Bus (\d+) Device (\d+): ID ([0-9a-f:]+) (.+)", line)
            if m:
                devices.append({
                    "bus": int(m.group(1)),
                    "device": int(m.group(2)),
                    "id": m.group(3),
                    "description": m.group(4).strip(),
                })
    result["details"]["devices"] = devices

    # filter out root hubs (they're always present)
    non_hub = [d for d in devices if "Linux Foundation" not in d["description"]]
    result["details"]["device_count"] = len(devices)
    result["details"]["non_hub_count"] = len(non_hub)

    # physical USB port enumeration via sysfs
    ports = _usb_ports_from_sysfs()
    result["details"]["ports"] = ports

    result["status"] = "pass"
    result["message"] = (
        f"USB OK — {len(non_hub)} device(s) connected "
        f"({len(devices)} total incl. hubs)"
    )

    return result


def _usb_ports_from_sysfs() -> dict:
    """Read USB port topology from sysfs."""
    ports: dict = {}
    usb_path = "/sys/bus/usb/devices"
    if not os.path.isdir(usb_path):
        return ports
    for entry in sorted(os.listdir(usb_path)):
        # top-level ports: usb1, usb2, 1-1, 2-1 etc — skip iface nodes (contain ":")
        if ":" in entry:
            continue
        dev_path = os.path.join(usb_path, entry)
        info: dict = {}
        for attr in ("idVendor", "idProduct", "manufacturer", "product", "speed"):
            try:
                with open(os.path.join(dev_path, attr)) as f:
                    info[attr] = f.read().strip()
            except OSError:
                pass
        ports[entry] = info
    return ports


# ─── HOTSPOT ─────────────────────────────────────────────────────────────────

def test_hotspot() -> dict:
    result: dict = {"status": "fail", "details": {}}
    iface = _find_iface("wlan")

    result["details"].update({
        "iface":      iface,
        "ssid":       HOTSPOT_SSID,
        "timeout_s":  HOTSPOT_TIMEOUT,
    })

    # nmcli creates the hotspot; wlan0 may still be associated — nmcli handles disconnect
    code, out, err = run(
        [
            "nmcli", "device", "wifi", "hotspot",
            "ifname",   iface,
            "ssid",     HOTSPOT_SSID,
            "password", HOTSPOT_PASSWORD,
            "con-name", HOTSPOT_CON_NAME,
        ],
        timeout=20,
    )

    if code != 0:
        result["message"] = f"Failed to start hotspot: {(err or out).splitlines()[0]}"
        return result

    result["details"]["hotspot_up"] = True
    print(f"\n    Hotspot '{HOTSPOT_SSID}' up on {iface}. Waiting up to {HOTSPOT_TIMEOUT}s for a client...",
          flush=True)

    try:
        client_mac = _wait_for_station(iface, HOTSPOT_TIMEOUT)
    finally:
        run(["nmcli", "connection", "down",   HOTSPOT_CON_NAME], timeout=10)
        run(["nmcli", "connection", "delete", HOTSPOT_CON_NAME], timeout=10)

    if client_mac:
        result["status"] = "pass"
        result["details"]["client_mac"] = client_mac
        result["message"] = f"Hotspot OK — client {client_mac} connected"
    else:
        result["message"] = f"Timeout: no client connected within {HOTSPOT_TIMEOUT}s"

    return result


def _wait_for_station(iface: str, timeout: int) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, out, _ = run(["iw", "dev", iface, "station", "dump"], timeout=5)
        if code == 0 and "Station" in out:
            m = re.search(r"Station ([0-9a-f:]{17})", out)
            return m.group(1) if m else "unknown"
        time.sleep(2)
    return None


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_iface(prefix: str) -> str:
    """Return first interface matching prefix, or prefix + '0'."""
    try:
        for name in os.listdir("/sys/class/net"):
            if name.startswith(prefix):
                return name
    except OSError:
        pass
    return f"{prefix}0"


def get_device_mac() -> str | None:
    """Return MAC of eth0 → wlan0 → first non-loopback iface, via sysfs."""
    net_path = "/sys/class/net"
    preferred = [_find_iface("eth"), _find_iface("wlan")]
    try:
        all_ifaces = os.listdir(net_path)
    except OSError:
        return None

    for iface in preferred + sorted(all_ifaces):
        if iface == "lo":
            continue
        try:
            with open(os.path.join(net_path, iface, "address")) as f:
                mac = f.read().strip()
            if mac and mac != "00:00:00:00:00:00":
                return mac
        except OSError:
            continue
    return None


# ─── API SEND ─────────────────────────────────────────────────────────────────

def send_results(payload: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"

    try:
        resp = requests.post(API_URL, json=payload, headers=headers, timeout=TIMEOUT_S)
        return {
            "http_status": resp.status_code,
            "ok": resp.ok,
            "response": resp.text[:500],
        }
    except requests.exceptions.ConnectionError as e:
        return {"ok": False, "error": f"Connection error: {e}"}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": f"Timeout after {TIMEOUT_S}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"[pitest] {DEVICE_ID}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    tests = {
        "lan":     test_lan,
        "wlan":    test_wlan,
        "boot":    test_boot,
        "usb":     test_usb,
        "hotspot": test_hotspot,   # must run last — takes the wlan iface
    }

    results: dict = {}
    overall_pass = True

    for name, fn in tests.items():
        print(f"  [{name.upper()}] running...", end=" ", flush=True)
        t0 = time.monotonic()
        try:
            r = fn()
        except Exception as e:
            r = {"status": "error", "message": str(e), "details": {}}
        elapsed = round(time.monotonic() - t0, 2)
        r["elapsed_s"] = elapsed
        results[name] = r

        icon = "✓" if r["status"] == "pass" else "✗"
        print(f"{icon}  {r.get('message', r['status'])}  ({elapsed}s)")

        if r["status"] != "pass":
            overall_pass = False

    payload = {
        "device_id":   DEVICE_ID,
        "mac_address": get_device_mac(),
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "overall":     "pass" if overall_pass else "fail",
        "tests":       results,
    }

    print(f"\n  Overall: {'PASS' if overall_pass else 'FAIL'}")
    print(f"\n[pitest] Sending to {API_URL} ...", end=" ", flush=True)

    api_result = send_results(payload)
    if api_result["ok"]:
        print(f"OK (HTTP {api_result['http_status']})")
    else:
        print(f"FAILED — {api_result.get('error') or api_result.get('response')}")

    # also dump JSON to stdout for debugging
    if "--json" in sys.argv:
        print("\n" + json.dumps(payload, indent=2, default=str))

    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
