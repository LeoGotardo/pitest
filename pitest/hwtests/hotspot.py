"""
Hotspot — cria um AP Wi-Fi e aguarda um cliente conectar

Deve rodar por último: toma controle exclusivo da interface wlan.
"""

from config import HOTSPOT_PASSWORD, HOTSPOT_SSID, HOTSPOT_TIMEOUT
from progress import notify
from wifiModule import WifiManager


def run_test() -> dict:
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


if __name__ == "__main__":
    import json
    print(json.dumps(run_test(), indent=2, default=str))
