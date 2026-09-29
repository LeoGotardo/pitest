"""
common.py — utilitários compartilhados pelos módulos de teste
"""

import os
import re
import subprocess
import time

from config import PING_COUNT


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


def find_iface(prefix: str) -> str:
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


def get_ip_addresses() -> dict:
    """Retorna o IPv4 de cada interface não-loopback: {iface: ip}."""
    ips: dict = {}
    code, out, _ = run(["ip", "-4", "-o", "addr", "show"])
    if code == 0:
        for m in re.finditer(r"^\d+:\s+(\S+)\s+inet ([\d.]+)", out, re.M):
            if m.group(1) != "lo":
                ips.setdefault(m.group(1), m.group(2))
    return ips


_ip_cache: dict = {"at": 0.0, "text": ""}


def ip_line(max_age: float = 3) -> str:
    """Linha "IP eth0 x.x.x.x  wlan0 y.y.y.y" p/ a tela, com cache de `max_age` s
    (o overlay é redesenhado a 60 fps e o IP muda durante o hotspot)."""
    now = time.monotonic()
    if now - _ip_cache["at"] >= max_age:
        ips = get_ip_addresses()
        _ip_cache["text"] = "IP " + ("  ".join(f"{i} {ip}" for i, ip in ips.items()) or "sem endereço")
        _ip_cache["at"] = now
    return _ip_cache["text"]


def get_device_mac() -> str | None:
    """Retorna o MAC address da RPi lido diretamente do sysfs.

    Ordem de preferência: eth* → wlan* → primeira interface não-loopback.
    Ignora endereços nulos (00:00:00:00:00:00).
    """
    net_path = "/sys/class/net"
    preferred = [find_iface("eth"), find_iface("wlan")]
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
