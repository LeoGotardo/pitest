"""
LAN — interface Ethernet, IP, ping externo
"""

from common import find_iface, iface_info, ping
from config import PING_HOST


def run_test() -> dict:
    """Testa a interface Ethernet (eth*).

    Critérios de aprovação (em ordem):
      1. Interface presente no sistema
      2. Interface com estado UP
      3. Endereço IPv4 atribuído
      4. PING_HOST alcançável
    """
    result: dict = {"status": "fail", "details": {}}

    iface = find_iface("eth")
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


if __name__ == "__main__":
    import json
    print(json.dumps(run_test(), indent=2, default=str))
