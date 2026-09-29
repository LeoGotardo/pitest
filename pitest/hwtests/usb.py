"""
USB — dispositivos conectados via lsusb + topologia sysfs
"""

import os
import re

from common import run


# IDs de dispositivos USB internos da RPi (não são portas físicas externas)
_USB_INTERNAL_IDS = {
    "1d6b:0001", "1d6b:0002", "1d6b:0003",  # Linux Foundation root hubs
    "0424:9514",                               # SMSC SMC9514 hub+ethernet combo
    "0424:ec00",                               # SMSC9512/9514 Ethernet adapter
    "0424:7800",                               # SMSC LAN7800 (RPi 3B+)
    "0bda:8153",                               # Realtek RTL8153 (RPi 4)
}


def run_test() -> dict:
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


if __name__ == "__main__":
    import json
    print(json.dumps(run_test(), indent=2, default=str))
