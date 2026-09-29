"""
WLAN — hardware Wi-Fi (interface, rfkill, driver) sem exigir associação
"""

import re

from common import find_iface, run


def run_test() -> dict:
    """Testa o hardware Wi-Fi sem exigir associação a uma rede.

    Critérios de aprovação:
      1. Interface presente no sistema
      2. rfkill desbloqueado
      3. Interface consegue subir (ip link set up)
      4. Driver responde via iw list (suporta modo AP ou managed)

    Coleta adicionalmente: modos suportados, bandas e SSID/sinal se associada.
    """
    result: dict = {"status": "fail", "details": {}}

    iface = find_iface("wlan")
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


if __name__ == "__main__":
    import json
    print(json.dumps(run_test(), indent=2, default=str))
