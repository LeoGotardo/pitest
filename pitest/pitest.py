#!/usr/bin/env python3
"""
pitest.py — Raspberry Pi hardware diagnostics
==============================================
Executa sete baterias de testes no hardware da RPi e envia os resultados
em JSON para uma API externa via HTTP POST.

Testes executados (nessa ordem):
  lan       — interface Ethernet, IP, ping externo
  wlan      — interface Wi-Fi, rfkill, driver
  boot      — partição /boot, tempo de boot, units systemd com falha
  usb       — dispositivos USB conectados via lsusb + sysfs
  gpio      — confirma, por leitura, que cada pino de GPIO_PINS alterna
  bluetooth — controlador hci, rfkill, bluetoothd, power on e aguarda um celular parear
  hotspot   — cria um AP Wi-Fi e aguarda um cliente conectar (executa por último
              pois toma controle exclusivo da interface wlan)

GPIO: durante toda a execução os pinos de GPIO_PINS piscam (LED + resistor
entre o pino e o GND). O operador informa no site quais LEDs acenderam.

Estrutura:
  config.py    — configuração (URL da API, timeouts, SSID, pinos GPIO…)
  common.py    — run(), ping(), interfaces, IP/MAC
  progress.py  — progresso na tela + notify()
  api.py       — envio do resultado
  hwtests/     — um módulo por teste (lan, wlan, boot, usb, bluetooth,
                 hotspot, gpio); rode isolado com `sudo python3 -m hwtests.<nome>`

Uso:
  sudo python3 pitest.py              # executa e envia para a API
  sudo python3 pitest.py --json       # também imprime o payload JSON no terminal
  sudo python3 pitest.py --no-screen  # só console, sem o teste de tela

Exit code:
  0 — todos os testes passaram
  1 — ao menos um teste falhou ou retornou erro

Dependências:
  pip install -r requirements.txt   (requests, python-dotenv, pygame, gpiozero, lgpio)
  Ferramentas do sistema: ip, ping, iw, rfkill, lsusb, findmnt, systemctl,
                          systemd-analyze, bluetoothctl, hostapd, dnsmasq
"""

# Versão semântica (MAJOR.MINOR.PATCH) — atualizar junto com a tag git vX.Y.Z.
__version__ = "1.0.0"

import json
import sys
import threading
import time
from datetime import datetime, timezone

from api import send_results
from common import get_device_mac, ip_line
from config import API_URL, DEVICE_ID
from hwtests import bluetooth, boot, gpio, hotspot, lan, usb, wlan
from hwtests.gpio import GpioBlinker
from progress import Progress, attach

try:
    import screenTest
except Exception:
    screenTest = None


# Ordem de execução — hotspot por último (toma controle exclusivo da wlan).
TESTS = {
    "lan":       lan.run_test,
    "wlan":      wlan.run_test,
    "boot":      boot.run_test,
    "usb":       usb.run_test,
    "gpio":      gpio.run_test,       # confirma que os pinos alternam; LEDs → site
    "bluetooth": bluetooth.run_test,
    "hotspot":   hotspot.run_test,
}


# ─── DIAGNOSTICS ──────────────────────────────────────────────────────────────

def run_diagnostics(progress: "Progress | None" = None) -> tuple[dict, bool]:
    """Executa a bateria de testes e devolve (payload, overall_pass).

    Pode rodar em thread de fundo (enquanto o screen_test ocupa a tela). Se
    `progress` for passado, atualiza-o a cada teste p/ alimentar o overlay.

    Payload retornado:
      {
        "device_id":   "<hostname>",
        "version":     "<__version__>",
        "mac_address": "<MAC>",
        "timestamp":   "<ISO 8601 UTC>",
        "overall":     "pass" | "fail",
        "tests": { "<tipo>": {status, message, details, elapsed_s}, ... }
      }
    """
    print(f"[pitest] v{__version__}  {DEVICE_ID}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {ip_line(0)}\n")

    attach(progress)

    results: dict = {}
    overall_pass = True

    for name, fn in TESTS.items():
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
            progress.finish(name, r["status"], r.get("message", ""))

        icon = "✓" if r["status"] == "pass" else "✗"
        print(f"{icon}  {r.get('message', r['status'])}  ({elapsed}s)")

        if r["status"] != "pass":
            overall_pass = False

    payload = {
        "device_id":   DEVICE_ID,
        "version":     __version__,
        "mac_address": get_device_mac(),
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "overall":     "pass" if overall_pass else "fail",
        "tests":       results,
    }
    return payload, overall_pass


def report_and_send(payload: dict, overall_pass: bool) -> str:
    """Imprime o resumo, envia o payload à API e (com --json) imprime o JSON.

    Retorna uma linha curta com o resultado do envio (exibida na tela).
    """
    print(f"\n  Overall: {'PASS' if overall_pass else 'FAIL'}")
    print(f"\n[pitest] Sending to {API_URL} ...", end=" ", flush=True)

    api_result = send_results(payload)
    if api_result["ok"]:
        summary = f"API: enviado ✓ (HTTP {api_result['http_status']})"
    else:
        http = f"HTTP {api_result['http_status']} " if "http_status" in api_result else ""
        summary = f"✗ API: {http}{api_result.get('error') or api_result.get('response')}"
    print(summary)

    if "--json" in sys.argv:
        print("\n" + json.dumps(payload, indent=2, default=str))
    return summary


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

    blinker = GpioBlinker()
    blinker.start()

    if use_screen:
        progress = Progress(tuple(TESTS))
        holder: dict = {}
        done = threading.Event()

        def worker():
            # Roda os diagnósticos e já envia os resultados, sem fechar a tela.
            """Roda os diagnósticos e envia o resultado, sem fechar a tela."""
            try:
                payload, ok = run_diagnostics(progress)
                holder["payload"], holder["ok"] = payload, ok
                holder["api"] = report_and_send(payload, ok)
            finally:
                done.set()

        def status() -> str:
            """Texto do overlay: progresso, IP, falhas e resultado do envio."""
            first, *errors = progress.line().split("\n")
            if done.is_set():
                first += "  [done — ESC to exit]"
            if "api" in holder:
                errors.append(holder["api"][:120])
            return "\n".join([first, f"v{__version__}  {ip_line()}", *errors])

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
        blinker.wait_min()

    blinker.stop()
    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
