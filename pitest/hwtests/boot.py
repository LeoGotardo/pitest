"""
Boot — partição /boot, tempo de boot, units systemd com falha
"""

import platform
import re

from common import run


def run_test() -> dict:
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


if __name__ == "__main__":
    import json
    print(json.dumps(run_test(), indent=2, default=str))
