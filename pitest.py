#!/usr/bin/env python3
"""
pitest.py — Raspberry Pi hardware diagnostics
==============================================
Executa seis baterias de testes no hardware da RPi e envia os resultados
em JSON para uma API externa via HTTP POST.

Testes executados (nessa ordem):
  lan     — interface Ethernet, IP, ping externo
  wlan    — interface Wi-Fi, SSID, sinal, ping externo
  boot    — partição /boot, tempo de boot, units systemd com falha
  usb     — dispositivos USB conectados via lsusb + sysfs
  bluetooth — controlador hci, rfkill, bluetoothd, power on e aguarda um celular conectar
  hotspot — cria um AP Wi-Fi e aguarda um cliente conectar (executa por último
             pois toma controle exclusivo da interface wlan)

GPIO: durante toda a execução os pinos de GPIO_PINS piscam (LED + resistor
entre o pino e o GND). O operador informa no site quais acenderam.

Uso:
  sudo python3 pitest.py           # executa e envia para a API
  sudo python3 pitest.py --json    # também imprime o payload JSON no terminal

Exit code:
  0 — todos os testes passaram
  1 — ao menos um teste falhou ou retornou erro

Dependências:
  pip install requests
  Ferramentas do sistema: ip, ping, iwconfig/iw, lsusb, findmnt,
                          systemctl, systemd-analyze, nmcli, bluetoothctl
"""

import json
import os
import platform
import pty
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("Missing: pip install requests")

from wifiModule import WifiManager
from dotenv import load_dotenv

try:
    import screenTest
except Exception:
    screenTest = None

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────────────────────────

API_URL     = "https://pitest-seven.vercel.app/api/pitest"  # endpoint que recebe o POST
API_TOKEN   = os.environ.get('PITEST_API_TOKEN', '')                    # Bearer token para autenticação; vazio = sem auth
DEVICE_ID   = socket.gethostname() # identificador do dispositivo enviado no payload
PING_HOST   = "8.8.8.8"            # host usado nos testes de conectividade
PING_COUNT  = 4                     # número de pacotes ICMP por teste de ping
TIMEOUT_S   = 10                    # timeout em segundos para o POST à API

HOTSPOT_SSID     = "PiTest"         # SSID do AP criado no teste de hotspot
HOTSPOT_PASSWORD = "pitest123"      # senha WPA2 do AP
HOTSPOT_TIMEOUT  = 300              # segundos aguardando um cliente conectar
HOTSPOT_CON_NAME = "pitest-hotspot" # nome da conexão nmcli (removida após o teste)

BT_TIMEOUT      = 300              # segundos aguardando um celular conectar via Bluetooth
BT_ALIAS_PREFIX = "PiTest"         # nome visível no celular: "<prefixo> <hostname>"
BT_REMOVE_AFTER = True             # remove o pareamento ao final (RPi fica limpa p/ o próximo teste)

GPIO_PINS        = list(range(2, 28))  # pinos BCM que piscam (2–27 = todos do header de 40 pinos)
GPIO_BLINK_HZ    = 1                   # frequência do pisca
GPIO_BLINK_MIN_S = 60                  # tempo mínimo piscando antes de sair (modo --no-screen)

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
    """Testa o hardware Wi-Fi sem exigir associação a uma rede.

    Critérios de aprovação:
      1. Interface presente no sistema
      2. rfkill desbloqueado
      3. Interface consegue subir (ip link set up)
      4. Driver responde via iw list (suporta modo AP ou managed)

    Coleta adicionalmente: modos suportados, bandas e SSID/sinal se associada.
    """
    result: dict = {"status": "fail", "details": {}}

    iface = _find_iface("wlan")
    result["details"]["iface"] = iface

    # 1. interface presente
    code, out, _ = run(["ip", "link", "show", iface])
    if code != 0:
        result["message"] = f"Interface {iface} not found"
        return result
    result["details"]["present"] = True

    # 2. rfkill
    _, rfkill_out, _ = run(["rfkill", "list"])
    soft_blocked = "Soft blocked: yes" in rfkill_out
    hard_blocked = "Hard blocked: yes" in rfkill_out
    result["details"]["rfkill"] = {
        "soft_blocked": soft_blocked,
        "hard_blocked": hard_blocked,
    }
    if hard_blocked:
        result["message"] = "Wi-Fi hard blocked (hardware switch)"
        return result
    if soft_blocked:
        run(["rfkill", "unblock", "wifi"])

    # 3. bring up
    up_code, _, up_err = run(["ip", "link", "set", iface, "up"])
    if up_code != 0:
        result["message"] = f"Cannot bring {iface} up: {up_err}"
        return result
    result["details"]["link_up"] = True

    # 4. driver / capabilities via iw list
    cap_code, cap_out, _ = run(["iw", "list"], timeout=5)
    if cap_code != 0:
        result["message"] = "iw list failed — driver not responding"
        return result

    modes = re.findall(r"\*\s+(\S+)", cap_out[cap_out.find("Supported interface modes"):]) if "Supported interface modes" in cap_out else []
    bands = re.findall(r"Band (\w+):", cap_out)
    result["details"]["capabilities"] = {
        "supported_modes": modes,
        "bands": bands,
        "ap_supported": "AP" in modes,
    }

    # opcional: coleta SSID/sinal se já associada
    _, iw_out, _ = run(["iw", "dev", iface, "link"])
    if iw_out and "Not connected" not in iw_out:
        m = re.search(r"SSID: (.+)", iw_out)
        if m:
            result["details"]["ssid"] = m.group(1).strip()
        m = re.search(r"signal: (-\d+)", iw_out)
        if m:
            result["details"]["signal_dbm"] = int(m.group(1))

    result["status"] = "pass"
    modes_str = ", ".join(modes) if modes else "unknown"
    result["message"] = f"WLAN OK — {iface} up — modes: {modes_str}"

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


# IDs de dispositivos USB internos da RPi (não são portas físicas externas)
_USB_INTERNAL_IDS = {
    "1d6b:0001", "1d6b:0002", "1d6b:0003",  # Linux Foundation root hubs
    "0424:9514",                               # SMSC SMC9514 hub+ethernet combo
    "0424:ec00",                               # SMSC9512/9514 Ethernet adapter
    "0424:7800",                               # SMSC LAN7800 (RPi 3B+)
    "0bda:8153",                               # Realtek RTL8153 (RPi 4)
}

# ─── USB ──────────────────────────────────────────────────────────────────────

def test_usb() -> dict:
    """Enumera dispositivos USB externos nas portas físicas da RPi.

    Filtra dispositivos internos conhecidos (root hubs, chip Ethernet/hub
    SMSC soldado na placa) para reportar apenas periféricos externos.

    Sempre passa — objetivo é inventário, não bloquear por ausência de periféricos.
    """
    result: dict = {"status": "fail", "details": {"devices": [], "external_devices": []}}

    code, out, _ = run(["lsusb"])
    devices = []
    if code == 0:
        for line in out.splitlines():
            m = re.match(r"Bus (\d+) Device (\d+): ID ([0-9a-f:]+) (.+)", line)
            if m:
                devices.append({
                    "bus":         int(m.group(1)),
                    "device":      int(m.group(2)),
                    "id":          m.group(3),
                    "description": m.group(4).strip(),
                })

    external = [d for d in devices if d["id"] not in _USB_INTERNAL_IDS]

    result["details"]["devices"] = devices
    result["details"]["external_devices"] = external
    result["details"]["ports"] = _usb_ports_from_sysfs()

    result["status"] = "pass"
    result["message"] = f"USB OK — {len(external)} external device(s) connected"

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


# ─── BLUETOOTH ────────────────────────────────────────────────────────────────

# cores ANSI + sequências do readline (limpa linha, marcadores \x01/\x02)
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[\x01\x02]")


def test_bluetooth() -> dict:
    """Testa o Bluetooth onboard (BCM43438 via UART na RPi 3B).

    Critérios de aprovação (em ordem):
      1. Controlador hci* presente em /sys/class/bluetooth
      2. rfkill bluetooth desbloqueado
      3. bluetooth.service ativo (tenta iniciar se parado)
      4. Controlador liga (bluetoothctl power on) e reporta Powered: yes
      5. Um celular pareia/conecta à RPi em até BT_TIMEOUT s
         (a RPi fica visível como "PiTest <hostname>"; pareamento aceito
         automaticamente, sem PIN)

    Requer: bluez (bluetoothctl), rfkill, execução com sudo.
    """
    result: dict = {"status": "fail", "details": {}}

    # 1. controlador presente
    try:
        controllers = sorted(n for n in os.listdir("/sys/class/bluetooth") if n.startswith("hci"))
    except OSError:
        controllers = []
    result["details"]["controllers"] = controllers
    if not controllers:
        # na RPi 3B o controlador é anexado pelo hciuart.service
        _, hciuart, _ = run(["systemctl", "is-active", "hciuart"])
        result["details"]["hciuart"] = hciuart or None
        result["message"] = f"No Bluetooth controller found (hciuart: {hciuart or 'n/a'})"
        return result
    hci = controllers[0]

    # 2. rfkill
    _, rfkill_out, _ = run(["rfkill", "list", "bluetooth"])
    soft_blocked = "Soft blocked: yes" in rfkill_out
    hard_blocked = "Hard blocked: yes" in rfkill_out
    result["details"]["rfkill"] = {
        "soft_blocked": soft_blocked,
        "hard_blocked": hard_blocked,
    }
    if hard_blocked:
        result["message"] = "Bluetooth hard blocked"
        return result
    if soft_blocked:
        run(["rfkill", "unblock", "bluetooth"])

    # 3. serviço bluez
    _, svc, _ = run(["systemctl", "is-active", "bluetooth"])
    if svc != "active":
        run(["systemctl", "start", "bluetooth"], timeout=15)
        time.sleep(2)
        _, svc, _ = run(["systemctl", "is-active", "bluetooth"])
    result["details"]["service"] = svc
    if svc != "active":
        result["message"] = f"bluetooth.service not active ({svc})"
        return result

    # 4. power on + info do controlador
    run(["bluetoothctl", "power", "on"])
    _, show, _ = run(["bluetoothctl", "show"])
    show = _ANSI_RE.sub("", show)
    m = re.search(r"Controller ([0-9A-F:]{17})", show)
    controller = {
        "hci":     hci,
        "address": m.group(1) if m else None,
        "powered": "Powered: yes" in show,
    }
    m = re.search(r"Name: (.+)", show)
    if m:
        controller["name"] = m.group(1).strip()
    result["details"]["controller"] = controller
    if not controller["powered"]:
        result["message"] = f"{hci} does not power on"
        return result

    # 5. aguarda o celular conectar
    alias = f"{BT_ALIAS_PREFIX} {DEVICE_ID}"
    result["details"].update({"alias": alias, "timeout_s": BT_TIMEOUT})
    notify(f"Bluetooth: pareie o celular com '{alias}' ({BT_TIMEOUT}s)")

    try:
        station = _bt_wait_for_connection(alias, BT_TIMEOUT)
    finally:
        notify("")
    if station is None:
        result["message"] = "bluetoothctl could not start"
        return result
    if not station:
        result["message"] = f"Timeout: no phone paired within {BT_TIMEOUT}s"
        return result

    result["details"]["station"] = station
    result["status"] = "pass"
    name = f" ({station['name']})" if station.get("name") else ""
    result["message"] = f"Bluetooth OK — {station['mac']}{name} paired with {hci}"
    return result


def _bt_wait_for_connection(alias: str, timeout: int) -> dict | None:
    """Deixa a RPi visível/pareável e espera um celular parear.

    Mantém um `bluetoothctl` interativo aberto num pseudo-terminal (em pipe ele
    bufferiza a saída e os prompts do agente nunca chegam). O agente é
    DisplayYesNo: o celular mostra um código de 6 dígitos, o mesmo código é
    exibido na tela da RPi (notify) e confirmado automaticamente.

    O sucesso é detectado por polling (`bluetoothctl info`): um dispositivo que
    não estava pareado antes passa a Paired: yes.

    Retorna dict {mac, name?, connected, passkey?} do dispositivo, {} em
    timeout, ou None se o bluetoothctl não puder ser iniciado. Ao final desliga
    discoverable/pairable, restaura o alias e (se BT_REMOVE_AFTER) remove o
    pareamento.
    """
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(["bluetoothctl", "--agent", "DisplayYesNo"],
                                stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    except FileNotFoundError:
        os.close(master)
        os.close(slave)
        return None
    os.close(slave)

    def send(cmd: str) -> None:
        try:
            os.write(master, (cmd + "\n").encode())
        except OSError:
            pass

    passkey: dict = {}

    def reader() -> None:
        buf = ""
        while True:
            try:
                chunk = os.read(master, 1024)
            except OSError:  # EIO quando o bluetoothctl encerra
                break
            if not chunk:
                break
            buf = (buf + _ANSI_RE.sub("", chunk.decode(errors="replace")))[-2048:]
            # prompts do agente: "Confirm passkey 123456 (yes/no):",
            # "Authorize service ... (yes/no):", "Accept pairing (yes/no):"
            if "(yes/no)" in buf:
                m = re.search(r"[Pp]asskey (\d+)", buf)
                if m:
                    passkey["code"] = m.group(1).zfill(6)
                    notify(f"Bluetooth: confirme no celular o codigo  {passkey['code']}")
                send("yes")
                buf = ""
            elif "\n" in buf:
                buf = buf.rsplit("\n", 1)[1]

    threading.Thread(target=reader, daemon=True).start()

    for cmd in ("power on", f"system-alias {alias}", "default-agent", "pairable on",
                "discoverable-timeout 0", "discoverable on"):
        send(cmd)
        time.sleep(0.3)

    baseline = {mac for mac, st in _bt_devices().items() if st["paired"]}
    station: dict = {}
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and proc.poll() is None:
            for mac, st in _bt_devices().items():
                if st["paired"] and mac not in baseline:
                    station = {"mac": mac, "connected": st["connected"]}
                    if st.get("name"):
                        station["name"] = st["name"]
                    if passkey:
                        station["passkey"] = passkey["code"]
                    break
            if station:
                break
            time.sleep(1)
    finally:
        send("discoverable off")
        send("pairable off")
        send("reset-alias")
        if station and BT_REMOVE_AFTER:
            send(f"remove {station['mac']}")
        time.sleep(1)
        send("quit")
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        os.close(master)

    return station


def _bt_devices() -> dict:
    """Estado dos dispositivos conhecidos pelo bluez: {mac: {name, paired, connected}}."""
    devices: dict = {}
    _, out, _ = run(["bluetoothctl", "devices"])
    for mac in re.findall(r"Device ([0-9A-F:]{17})", _ANSI_RE.sub("", out)):
        _, info, _ = run(["bluetoothctl", "info", mac])
        info = _ANSI_RE.sub("", info)
        m = re.search(r"Name: (.+)", info)
        devices[mac] = {
            "name":      m.group(1).strip() if m else None,
            "paired":    "Paired: yes" in info,
            "connected": "Connected: yes" in info,
        }
    return devices


# ─── HOTSPOT ─────────────────────────────────────────────────────────────────

def test_hotspot() -> dict:
    """Cria um AP Wi-Fi via hostapd/dnsmasq e aguarda um cliente se conectar.

    Usa WifiManager (wifiModule.py) que:
      - desbloqueia rfkill
      - para NetworkManager/wpa_supplicant
      - configura hostapd + dnsmasq
      - polling via iw station dump

    Executa por último pois assume controle exclusivo da interface wlan.
    Requer: hostapd, dnsmasq, iw, execução com sudo.
    """
    result: dict = {"status": "fail", "details": {}}

    wifi = WifiManager(ssid=HOTSPOT_SSID, password=HOTSPOT_PASSWORD)

    result["details"].update({
        "iface":     wifi.interface,
        "ssid":      HOTSPOT_SSID,
        "timeout_s": HOTSPOT_TIMEOUT,
    })

    try:
        wifi.startHotspot()
    except RuntimeError as exc:
        result["message"] = f"Failed to start hotspot: {exc}"
        return result

    result["details"]["hotspot_up"] = True
    notify(f"Wi-Fi: conecte em '{HOTSPOT_SSID}' senha '{HOTSPOT_PASSWORD}' ({HOTSPOT_TIMEOUT}s)")

    try:
        station = wifi.wait_for_station(HOTSPOT_TIMEOUT)
    finally:
        notify("")
        wifi.stopHotspot()

    if station:
        result["status"] = "pass"
        result["details"]["station"] = station
        signal = f" — signal {station['signal_dbm']} dBm" if "signal_dbm" in station else ""
        result["message"] = f"Hotspot OK — client {station['mac']} connected{signal}"
    else:
        result["message"] = f"Timeout: no client connected within {HOTSPOT_TIMEOUT}s"

    return result


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


# ─── GPIO ─────────────────────────────────────────────────────────────────────

class GpioBlinker:
    """Pisca os pinos GPIO_PINS em thread de fundo p/ o teste manual de GPIO.

    Usa o CLI `pinctrl` (Raspberry Pi OS bookworm) ou `raspi-gpio` (legado) —
    ambos aceitam `set <lista> op dh|dl` e `set <lista> ip`. Ao parar, devolve
    os pinos para input (pulls originais mantidos).
    """

    def __init__(self, pins: list[int] = GPIO_PINS, hz: float = GPIO_BLINK_HZ):
        self.pins    = ",".join(str(p) for p in pins)
        self.half    = 1 / (2 * hz)
        self.tool    = shutil.which("pinctrl") or shutil.which("raspi-gpio")
        self.started = None
        self._stop   = threading.Event()
        self._thread = None

    def start(self) -> bool:
        """Inicia o pisca. Retorna False se nenhuma ferramenta estiver disponível."""
        if not self.tool:
            print("[pitest] GPIO: pinctrl/raspi-gpio não encontrado — pinos não vão piscar")
            return False
        code, _, err = run([self.tool, "set", self.pins, "op", "dl"])
        if code != 0:
            print(f"[pitest] GPIO: falha ao configurar pinos ({err})")
            return False
        self.started = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print(f"[pitest] GPIO {self.pins} piscando — informe no site quais LEDs acenderam\n")
        return True

    def _loop(self) -> None:
        level = "dh"
        while not self._stop.is_set():
            run([self.tool, "set", self.pins, level])
            level = "dl" if level == "dh" else "dh"
            self._stop.wait(self.half)

    def wait_min(self, seconds: float = GPIO_BLINK_MIN_S) -> None:
        """Bloqueia até o pisca ter rodado ao menos `seconds`."""
        if self.started is None:
            return
        remaining = seconds - (time.monotonic() - self.started)
        if remaining > 0:
            print(f"[pitest] GPIO piscando por mais {int(remaining)}s...", flush=True)
            time.sleep(remaining)

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join()
        run([self.tool, "set", self.pins, "ip"])


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


# ─── PROGRESS ───────────────────────────────────────────────────────────────

# Ordem dos testes — hotspot por último (toma controle exclusivo da wlan).
TEST_ORDER = ("lan", "wlan", "boot", "usb", "bluetooth", "hotspot")


class Progress:
    """Estado compartilhado do progresso dos diagnósticos.

    A thread de diagnósticos chama start()/finish(); o loop do screen_test lê
    line() p/ desenhar o overlay. Protegido por lock por ser lido de outra thread.
    """

    def __init__(self, names: tuple[str, ...] = TEST_ORDER):
        self._names  = names
        self._status = {n: "·" for n in names}   # · pendente · … rodando · ✓/✗ feito
        self._lock   = threading.Lock()

    def start(self, name: str) -> None:
        with self._lock:
            self._status[name] = "…"

    def finish(self, name: str, status: str) -> None:
        with self._lock:
            self._status[name] = "✓" if status == "pass" else "✗"

    def line(self) -> str:
        with self._lock:
            return "  ".join(f"{n.upper()}:{self._status[n]}" for n in self._names)

    def set_notice(self, text: str) -> None:
        with self._lock:
            self._notice = text

    def notice(self) -> str:
        with self._lock:
            return getattr(self, "_notice", "")


_progress: "Progress | None" = None


def notify(text: str) -> None:
    """Mostra uma instrução ao operador: console + destaque na tela (se ativa).

    Texto vazio limpa o destaque da tela.
    """
    if text:
        print(f"\n    >> {text}", flush=True)
    if _progress:
        _progress.set_notice(text)


# ─── DIAGNOSTICS ──────────────────────────────────────────────────────────────

def run_diagnostics(progress: "Progress | None" = None) -> tuple[dict, bool]:
    """Executa a bateria de testes e devolve (payload, overall_pass).

    Pode rodar em thread de fundo (enquanto o screen_test ocupa a tela). Se
    `progress` for passado, atualiza-o a cada teste p/ alimentar o overlay.

    Payload retornado:
      {
        "device_id":   "<hostname>",
        "mac_address": "<MAC>",
        "timestamp":   "<ISO 8601 UTC>",
        "overall":     "pass" | "fail",
        "tests": { "<tipo>": {status, message, details, elapsed_s}, ... }
      }
    """
    print(f"[pitest] {DEVICE_ID}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    tests = {
        "lan":     test_lan,
        "wlan":    test_wlan,
        "boot":    test_boot,
        "usb":     test_usb,
        "bluetooth": test_bluetooth,
        "hotspot": test_hotspot,   # deve ser o último — toma controle da interface wlan
    }

    global _progress
    _progress = progress

    results: dict = {}
    overall_pass = True

    for name, fn in tests.items():
        if progress:
            progress.start(name)
        print(f"  [{name.upper()}] running...", end=" ", flush=True)
        t0 = time.monotonic()
        try:
            r = fn()
        except Exception as e:
            r = {"status": "error", "message": str(e), "details": {}}
        elapsed = round(time.monotonic() - t0, 2)
        r["elapsed_s"] = elapsed
        results[name] = r
        if progress:
            progress.finish(name, r["status"])

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
    return payload, overall_pass


def report_and_send(payload: dict, overall_pass: bool) -> None:
    """Imprime o resumo, envia o payload à API e (com --json) imprime o JSON."""
    print(f"\n  Overall: {'PASS' if overall_pass else 'FAIL'}")
    print(f"\n[pitest] Sending to {API_URL} ...", end=" ", flush=True)

    api_result = send_results(payload)
    if api_result["ok"]:
        print(f"OK (HTTP {api_result['http_status']})")
    else:
        print(f"FAILED — {api_result.get('error') or api_result.get('response')}")

    if "--json" in sys.argv:
        print("\n" + json.dumps(payload, indent=2, default=str))


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    """Ponto de entrada.

    Por padrão abre o teste visual de tela (screen_test) em fullscreen enquanto
    os diagnósticos rodam em uma thread de fundo. Os GPIOs piscam durante toda
    a execução (no modo --no-screen, por no mínimo GPIO_BLINK_MIN_S). Ao terminarem, os resultados
    são enviados automaticamente, mas a tela CONTINUA rodando — fecha só quando
    o usuário pressiona ESC/Q. O overlay mostra "[done — ESC to exit]" quando
    os testes acabam. Use --no-screen p/ rodar só no console (ou quando não há
    pygame/display, o fallback é automático).
    """
    use_screen = screenTest is not None and "--no-screen" not in sys.argv

    gpio = GpioBlinker()
    gpio_on = gpio.start()

    if use_screen:
        progress = Progress()
        holder: dict = {}
        done = threading.Event()

        def worker():
            # Roda os diagnósticos e já envia os resultados, sem fechar a tela.
            try:
                payload, ok = run_diagnostics(progress)
                holder["payload"], holder["ok"] = payload, ok
                report_and_send(payload, ok)
            finally:
                done.set()

        def status() -> str:
            line = progress.line() + ("  GPIO:blink" if gpio_on else "")
            return line + "  [done — ESC to exit]" if done.is_set() else line

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        try:
            # Sem stop_event: a tela roda até o usuário sair (ESC/Q/quit),
            # mesmo depois de os diagnósticos terminarem.
            screenTest.run(status_fn=status, notice_fn=progress.notice)
        except Exception as e:
            print(f"[pitest] screen test indisponível ({e}); seguindo no console.")
        t.join()
        overall_pass = holder.get("ok", False)
    else:
        payload, overall_pass = run_diagnostics()
        report_and_send(payload, overall_pass)
        gpio.wait_min()

    gpio.stop()
    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()