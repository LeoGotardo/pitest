#!/usr/bin/env python3
"""
seed.py — popula o banco com dados de teste para visualização da UI
====================================================================
Apaga e recria todas as tabelas, então insere 3 devices com 3 rodadas
de testes cada (spread nas últimas 24h), totalizando 45 registros Test.

Os templates de resultado cobrem cenários de pass e fail para todos
os tipos de teste (lan, wlan, boot, usb, hotspot), permitindo verificar
a renderização dos badges, filtros e paginação na interface.

Uso:
  python3 seed.py           (a partir do diretório src/)
  ../venv/bin/python seed.py
"""

import random
from datetime import datetime, timedelta
from app import app
from database import db, Device, Test

# devices fictícios com MACs no range da Raspberry Pi Foundation (dc:a6:32, e4:5f:01)
DEVICES = [
    {"mac_address": "dc:a6:32:11:22:33", "device_id": "rpi-sala"},
    {"mac_address": "dc:a6:32:44:55:66", "device_id": "rpi-cozinha"},
    {"mac_address": "e4:5f:01:ab:cd:ef", "device_id": "rpi-escritorio"},
]

# três variantes (pass/fail/pass) por tipo — rotacionadas entre devices e rodadas
TEST_TEMPLATES = {
    "lan": [
        ("pass",  "LAN OK — 192.168.1.101/24 — ping 8.8.8.8 OK",  {"interface": {"name": "eth0", "present": True, "up": True, "ipv4": "192.168.1.101/24"}, "ping": {"host": "8.8.8.8", "reachable": True, "packet_loss_pct": 0, "rtt_avg_ms": 4.2}}),
        ("fail",  "Interface eth0 is down",                        {"interface": {"name": "eth0", "present": True, "up": False, "ipv4": None}}),
        ("pass",  "LAN OK — 192.168.1.105/24 — ping 8.8.8.8 OK",  {"interface": {"name": "eth0", "present": True, "up": True, "ipv4": "192.168.1.105/24"}, "ping": {"host": "8.8.8.8", "reachable": True, "packet_loss_pct": 0, "rtt_avg_ms": 3.8}}),
    ],
    "wlan": [
        ("pass",  "WLAN OK — SSID: HomeNet — 192.168.1.102/24 — ping OK", {"interface": {"name": "wlan0", "present": True, "up": True, "ipv4": "192.168.1.102/24", "ssid": "HomeNet", "signal_dbm": -52, "bit_rate": "72.2 Mb/s"}, "ping": {"host": "8.8.8.8", "reachable": True, "packet_loss_pct": 0, "rtt_avg_ms": 8.1}}),
        ("fail",  "No IPv4 address — not associated or DHCP failed",       {"interface": {"name": "wlan0", "present": True, "up": True, "ipv4": None, "ssid": None}}),
        ("pass",  "WLAN OK — SSID: LabWifi — 10.0.0.23/24 — ping OK",     {"interface": {"name": "wlan0", "present": True, "up": True, "ipv4": "10.0.0.23/24", "ssid": "LabWifi", "signal_dbm": -67, "bit_rate": "54 Mb/s"}, "ping": {"host": "8.8.8.8", "reachable": True, "packet_loss_pct": 25, "rtt_avg_ms": 22.4}}),
    ],
    "boot": [
        ("pass",  "Boot OK — 12.401s (kernel) + 18.003s (userspace) — 0 failed unit(s)", {"uptime": "up 2 hours, 14 minutes", "boot_time": "12.401s (kernel) + 18.003s (userspace)", "failed_units": [], "boot_partition": {"mounted": True, "info": "/dev/mmcblk0p1 vfat 256M 52M 204M"}, "kernel": "6.1.21-v8+", "os": "Raspberry Pi OS 12 (bookworm)"}),
        ("fail",  "Critical failed units: networking.service",               {"uptime": "up 5 minutes", "boot_time": "15.2s (kernel) + 42.1s (userspace)", "failed_units": ["networking.service"], "boot_partition": {"mounted": True, "info": "/dev/mmcblk0p1 vfat 256M 52M 204M"}, "kernel": "6.1.21-v8+", "os": "Raspberry Pi OS 12 (bookworm)"}),
        ("pass",  "Boot OK — 10.812s (kernel) + 15.449s (userspace) — 0 failed unit(s)", {"uptime": "up 47 minutes", "boot_time": "10.812s (kernel) + 15.449s (userspace)", "failed_units": [], "boot_partition": {"mounted": True, "info": "/dev/mmcblk0p1 vfat 256M 48M 208M"}, "kernel": "6.1.21-v8+", "os": "Raspberry Pi OS 12 (bookworm)"}),
    ],
    "usb": [
        ("pass",  "USB OK — 2 device(s) connected (4 total incl. hubs)", {"device_count": 4, "non_hub_count": 2, "devices": [{"bus": 1, "device": 2, "id": "0781:5583", "description": "SanDisk Corp. Ultra Fit"}, {"bus": 1, "device": 3, "id": "046d:c52b", "description": "Logitech, Inc. Unifying Receiver"}, {"bus": 1, "device": 1, "id": "1d6b:0002", "description": "Linux Foundation 2.0 root hub"}, {"bus": 2, "device": 1, "id": "1d6b:0003", "description": "Linux Foundation 3.0 root hub"}]}),
        ("pass",  "USB OK — 0 device(s) connected (2 total incl. hubs)",  {"device_count": 2, "non_hub_count": 0, "devices": [{"bus": 1, "device": 1, "id": "1d6b:0002", "description": "Linux Foundation 2.0 root hub"}, {"bus": 2, "device": 1, "id": "1d6b:0003", "description": "Linux Foundation 3.0 root hub"}]}),
        ("pass",  "USB OK — 1 device(s) connected (3 total incl. hubs)",  {"device_count": 3, "non_hub_count": 1, "devices": [{"bus": 1, "device": 2, "id": "0951:1666", "description": "Kingston Technology DataTraveler 100 G3"}, {"bus": 1, "device": 1, "id": "1d6b:0002", "description": "Linux Foundation 2.0 root hub"}, {"bus": 2, "device": 1, "id": "1d6b:0003", "description": "Linux Foundation 3.0 root hub"}]}),
    ],
    "hotspot": [
        ("pass",  "Hotspot OK — client b8:27:eb:99:aa:bb connected", {"iface": "wlan0", "ssid": "PiTest", "timeout_s": 300, "hotspot_up": True, "client_mac": "b8:27:eb:99:aa:bb"}),
        ("fail",  "Timeout: no client connected within 300s",         {"iface": "wlan0", "ssid": "PiTest", "timeout_s": 300, "hotspot_up": True}),
        ("pass",  "Hotspot OK — client a4:c3:f0:12:34:56 connected", {"iface": "wlan0", "ssid": "PiTest", "timeout_s": 300, "hotspot_up": True, "client_mac": "a4:c3:f0:12:34:56"}),
    ],
}

# faixas realistas de tempo de execução por tipo de teste (segundos)
ELAPSED = {
    "lan":     (0.8,  2.5),
    "wlan":    (1.2,  3.1),
    "boot":    (0.3,  0.9),
    "usb":     (0.4,  1.1),
    "hotspot": (15.0, 280.0),
}


def seed():
    with app.app_context():
        db.drop_all()
        db.create_all()

        now = datetime.utcnow()

        for i, dev_data in enumerate(DEVICES):
            device = Device(**dev_data)
            db.session.add(device)
            db.session.flush()

            # 3 rodadas por device, distribuídas nas últimas 24h (0h, 8h e 16h atrás)
            for round_idx in range(3):
                ts = now - timedelta(hours=24 - round_idx * 8, minutes=random.randint(0, 30))
                variant = (i + round_idx) % 3  # rotaciona entre as 3 variantes

                for test_type, templates in TEST_TEMPLATES.items():
                    status, message, details = templates[variant]
                    lo, hi = ELAPSED[test_type]
                    test = Test(
                        device_id  = device.id,
                        type       = test_type,
                        status     = status,
                        message    = message,
                        details    = details,
                        elapsed_s  = round(random.uniform(lo, hi), 2),
                        created_at = ts,
                        updated_at = ts,
                    )
                    db.session.add(test)
                    db.session.commit()

        print(f"Seeded {len(DEVICES)} devices × 3 rounds × 5 test types = {len(DEVICES) * 3 * 5} test records.")


if __name__ == "__main__":
    seed()
