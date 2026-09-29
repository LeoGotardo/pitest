"""
Bluetooth — controlador, rfkill, bluetoothd e pareamento com um celular
"""

import os
import pty
import re
import subprocess
import threading
import time

from common import run
from config import BT_ALIAS_PREFIX, BT_REMOVE_AFTER, BT_TIMEOUT, DEVICE_ID
from progress import notify


# cores ANSI + sequências do readline (limpa linha, marcadores \x01/\x02)
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[\x01\x02]")


def run_test() -> dict:
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
        """Envia um comando ao bluetoothctl (ignora erro se ele já encerrou)."""
        try:
            os.write(master, (cmd + "\n").encode())
        except OSError:
            pass

    passkey: dict = {}

    def reader() -> None:
        """Lê a saída do bluetoothctl e responde 'yes' aos prompts do agente, exibindo o passkey."""
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


if __name__ == "__main__":
    import json
    print(json.dumps(run_test(), indent=2, default=str))
