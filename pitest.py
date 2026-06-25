#!/usr/bin/env python3
"""
pitest.py — Raspberry Pi hardware diagnostics
==============================================
Executa cinco baterias de testes no hardware da RPi e envia os resultados
em JSON para uma API externa via HTTP POST.

Testes executados (nessa ordem):
  lan     — interface Ethernet, IP, ping externo
  wlan    — interface Wi-Fi, SSID, sinal, ping externo
  boot    — partição /boot, tempo de boot, units systemd com falha
  usb     — dispositivos USB conectados via lsusb + sysfs
  hotspot — cria um AP Wi-Fi e aguarda um cliente conectar (executa por último
             pois toma controle exclusivo da interface wlan)

Uso:
  sudo python3 pitest.py           # executa e envia para a API
  sudo python3 pitest.py --json    # também imprime o payload JSON no terminal

Exit code:
  0 — todos os testes passaram
  1 — ao menos um teste falhou ou retornou erro

Dependências:
  pip install requests
  Ferramentas do sistema: ip, ping, iwconfig/iw, lsusb, findmnt,
                          systemctl, systemd-analyze, nmcli
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

API_URL     = "https://your-api.example.com/api/pitest"  # endpoint que recebe o POST
API_TOKEN   = ""                    # Bearer token para autenticação; vazio = sem auth
DEVICE_ID   = socket.gethostname() # identificador do dispositivo enviado no payload
PING_HOST   = "8.8.8.8"            # host usado nos testes de conectividade
PING_COUNT  = 4                     # número de pacotes ICMP por teste de ping
TIMEOUT_S   = 10                    # timeout em segundos para o POST à API

HOTSPOT_SSID     = "PiTest"         # SSID do AP criado no teste de hotspot
HOTSPOT_PASSWORD = "pitest123"      # senha WPA2 do AP
HOTSPOT_TIMEOUT  = 300              # segundos aguardando um cliente conectar
HOTSPOT_CON_NAME = "pitest-hotspot" # nome da conexão nmcli (removida após o teste)

# ──────────────────────────────────────────────────────────────────────────────


def run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Executa um subprocesso e retorna (returncode, stdout, stderr).

    Retorna (-1, "", mensagem) em caso de timeout ou comando não encontrado,
    evitando que exceções interrompam o fluxo dos testes.
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"


def ping(host: str, count: int = PING_COUNT) -> dict:
    """Envia `count` pacotes ICMP para `host` e retorna métricas de latência.

    Retorna dict com chaves: host, reachable, packet_loss_pct,
    rtt_min_ms, rtt_avg_ms, rtt_max_ms (as três últimas apenas se reachable).
    """
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
    """Retorna estado, IPv4 e MAC de uma interface de rede via `ip addr show`.

    Chaves retornadas: name, present, up, ipv4, mac.
    """
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
    """Testa a interface Ethernet (eth*).

    Critérios de aprovação (em ordem):
      1. Interface presente no sistema
      2. Interface com estado UP
      3. Endereço IPv4 atribuído
      4. PING_HOST alcançável
    """
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
    """Testa a interface Wi-Fi (wlan*).

    Além dos critérios do LAN, coleta SSID e força do sinal via
    iwconfig (fallback para iw se iwconfig não estiver disponível).

    Critérios de aprovação:
      1. Interface presente e UP
      2. Endereço IPv4 atribuído (associação + DHCP bem-sucedidos)
      3. PING_HOST alcançável
    """
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

    # tenta iwconfig primeiro; fallback para iw
    code, out, _ = run(["iwconfig", iface])
    if code == 0:
        m = re.search(r'ESSID:"([^"]+)"', out)
        details["ssid"] = m.group(1) if m else None

        m = re.search(r"Signal level=(-\d+)", out)
        details["signal_dbm"] = int(m.group(1)) if m else None

        m = re.search(r"Bit Rate=([\d.]+\s*\S+)", out)
        details["bit_rate"] = m.group(1).strip() if m else None
    else:
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
    """Verifica a integridade do processo de boot.

    Coleta:
      - Uptime atual (uptime -p)
      - Timestamp do último boot (who -b)
      - Tempo de boot do systemd (systemd-analyze)
      - Estado de montagem de /boot (findmnt)
      - Units systemd com falha (systemctl list-units --state=failed)
      - Versão do kernel e nome do SO

    Critérios de aprovação:
      - /boot montado
      - Nenhuma unit crítica com falha (network, ssh, boot, init)
    """
    result: dict = {"status": "fail", "details": {}}

    code, out, _ = run(["uptime", "-p"])
    result["details"]["uptime"] = out if code == 0 else None

    code, out, _ = run(["who", "-b"])
    if code == 0:
        m = re.search(r"system boot\s+(.+)", out)
        result["details"]["last_boot"] = m.group(1).strip() if m else out.strip()

    code, out, _ = run(["systemd-analyze"], timeout=15)
    if code == 0:
        m = re.search(r"Startup finished in (.+)", out)
        result["details"]["boot_time"] = m.group(1).strip() if m else out.strip()

    code, out, _ = run(["findmnt", "--target", "/boot", "-o", "SOURCE,FSTYPE,SIZE,USED,AVAIL", "-n"])
    result["details"]["boot_partition"] = {
        "mounted": code == 0,
        "info": out if code == 0 else None,
    }

    code, out, _ = run(["systemctl", "list-units", "--state=failed", "--no-legend"])
    failed_units = [ln.split()[0] for ln in out.splitlines() if ln.strip()] if out else []
    result["details"]["failed_units"] = failed_units

    result["details"]["kernel"] = platform.release()
    result["details"]["os"] = _read_os_release()

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
    """Lê PRETTY_NAME de /etc/os-release; fallback para platform.system()."""
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
    """Enumera dispositivos USB via lsusb e sysfs.

    Sempre passa (status='pass') — o objetivo é inventariar o que está
    conectado, não bloquear o fluxo por ausência de periféricos.

    details inclui:
      devices       — lista completa de dispositivos (bus, device, id, description)
      device_count  — total de entradas lsusb
      non_hub_count — dispositivos excluindo root hubs Linux Foundation
      ports         — topologia física lida de /sys/bus/usb/devices
    """
    result: dict = {"status": "fail", "details": {"devices": [], "ports": {}}}

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

    non_hub = [d for d in devices if "Linux Foundation" not in d["description"]]
    result["details"]["device_count"] = len(devices)
    result["details"]["non_hub_count"] = len(non_hub)

    result["details"]["ports"] = _usb_ports_from_sysfs()

    result["status"] = "pass"
    result["message"] = (
        f"USB OK — {len(non_hub)} device(s) connected "
        f"({len(devices)} total incl. hubs)"
    )

    return result


def _usb_ports_from_sysfs() -> dict:
    """Lê topologia USB de /sys/bus/usb/devices.

    Retorna dict keyed pelo nome da entrada (ex: '1-1', 'usb2').
    Nós de interface (contêm ':') são ignorados.
    Atributos coletados por porta: idVendor, idProduct, manufacturer,
    product, speed.
    """
    ports: dict = {}
    usb_path = "/sys/bus/usb/devices"
    if not os.path.isdir(usb_path):
        return ports
    for entry in sorted(os.listdir(usb_path)):
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
    """Cria um AP Wi-Fi e aguarda um cliente se conectar.

    Fluxo:
      1. Cria hotspot via `nmcli device wifi hotspot` (desconecta automaticamente
         qualquer rede Wi-Fi associada na interface)
      2. Polling a cada 2s via `iw dev <iface> station dump`
      3. Ao detectar a primeira estação associada → status='pass'
      4. Se HOTSPOT_TIMEOUT for atingido sem cliente → status='fail'
      5. Sempre remove a conexão nmcli ao final (bloco finally)

    Executa por último pois assume controle exclusivo da interface wlan.
    Requer: nmcli, iw, execução com sudo.
    """
    result: dict = {"status": "fail", "details": {}}
    iface = _find_iface("wlan")

    result["details"].update({
        "iface":      iface,
        "ssid":       HOTSPOT_SSID,
        "timeout_s":  HOTSPOT_TIMEOUT,
    })

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
        # garante remoção do hotspot independente do resultado
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
    """Aguarda até `timeout` segundos por uma estação Wi-Fi associada.

    Faz polling via `iw dev <iface> station dump` a cada 2 segundos.
    Retorna o MAC do primeiro cliente detectado, ou None se expirar.
    """
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
    """Retorna o nome da primeira interface em /sys/class/net que inicia com `prefix`.

    Fallback para '<prefix>0' se nenhuma for encontrada.
    """
    try:
        for name in os.listdir("/sys/class/net"):
            if name.startswith(prefix):
                return name
    except OSError:
        pass
    return f"{prefix}0"


def get_device_mac() -> str | None:
    """Retorna o MAC address da RPi lido diretamente do sysfs.

    Ordem de preferência: eth* → wlan* → primeira interface não-loopback.
    Ignora endereços nulos (00:00:00:00:00:00).
    """
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
    """Envia o payload JSON via POST para API_URL.

    Retorna dict com chaves:
      ok           — True se HTTP 2xx
      http_status  — código de resposta (quando disponível)
      response     — primeiros 500 chars do corpo da resposta
      error        — mensagem de erro em caso de falha de conexão/timeout
    """
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
    """Ponto de entrada: executa todos os testes e envia os resultados.

    Payload enviado:
      {
        "device_id":   "<hostname>",
        "mac_address": "<MAC>",
        "timestamp":   "<ISO 8601 UTC>",
        "overall":     "pass" | "fail",
        "tests": {
          "<tipo>": {
            "status":    "pass" | "fail" | "error",
            "message":   "<resumo legível>",
            "details":   { ... },
            "elapsed_s": <float>
          },
          ...
        }
      }
    """
    print(f"[pitest] {DEVICE_ID}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    tests = {
        "lan":     test_lan,
        "wlan":    test_wlan,
        "boot":    test_boot,
        "usb":     test_usb,
        "hotspot": test_hotspot,   # deve ser o último — toma controle da interface wlan
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

    if "--json" in sys.argv:
        print("\n" + json.dumps(payload, indent=2, default=str))

    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
