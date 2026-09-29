"""
GPIO — pisca os pinos (gpiozero) e confirma, por leitura, que cada um alterna

Duas partes:
  GpioBlinker — pisca GPIO_PINS durante toda a execução do pitest, p/ o
                operador conferir os LEDs e informar no site quais acenderam
                (parte manual).
  run_test()  — parte automática: enquanto os pinos piscam, lê o nível real
                de cada um várias vezes. Um pino que não alterna está preso
                (ex.: LED sem resistor / curto p/ GND prende em "lo").

Rodar isolado (pisca, testa e segue piscando até Ctrl+C):

    sudo python3 -m hwtests.gpio
"""

import time

from config import GPIO_BLINK_HZ, GPIO_BLINK_MIN_S, GPIO_PINS

try:
    from gpiozero import LEDBoard
except ImportError:
    LEDBoard = None

# blinker iniciado pelo pitest — run_test() amostra os pinos enquanto ele pisca
_active: "GpioBlinker | None" = None


class GpioBlinker:
    """Pisca os pinos GPIO_PINS juntos (gpiozero LEDBoard, thread interna)."""

    def __init__(self, pins: list[int] = GPIO_PINS, hz: float = GPIO_BLINK_HZ):
        """Prepara o blinker; os pinos só são abertos em start()."""
        self.pins    = list(pins)
        self.hz      = hz
        self.board   = None
        self.started = None
        self.error   = ""

    def start(self) -> bool:
        """Inicia o pisca. Retorna False (e preenche `error`) se não conseguir."""
        global _active
        if not self.pins:
            self.error = "GPIO_PINS vazio — teste desativado"
            return False
        if LEDBoard is None:
            self.error = "gpiozero não instalado (pip install gpiozero lgpio)"
            print(f"[pitest] GPIO: {self.error}")
            return False
        try:
            self.board = LEDBoard(*self.pins)
        except Exception as e:
            self.error = f"falha ao abrir pinos ({e})"
            print(f"[pitest] GPIO: {self.error}")
            return False
        half = 1 / (2 * self.hz)
        self.board.blink(on_time=half, off_time=half)
        self.started = time.monotonic()
        _active = self
        print(f"[pitest] GPIO {self.pins} piscando — informe no site quais LEDs acenderam\n")
        return True

    def levels(self) -> dict[int, str]:
        """Nível real ("hi" | "lo") de cada pino — lido do hardware, não o valor pedido."""
        return {p: "hi" if led.pin.state else "lo" for p, led in zip(self.pins, self.board.leds)}

    def wait_min(self, seconds: float = GPIO_BLINK_MIN_S) -> None:
        """Bloqueia até o pisca ter rodado ao menos `seconds`."""
        if self.started is None:
            return
        remaining = seconds - (time.monotonic() - self.started)
        if remaining > 0:
            print(f"[pitest] GPIO piscando por mais {int(remaining)}s...", flush=True)
            time.sleep(remaining)

    def stop(self) -> None:
        """Para o pisca e libera os pinos (voltam a input)."""
        global _active
        if self.board is not None:
            self.board.close()
            self.board = None
        if _active is self:
            _active = None


def run_test() -> dict:
    """Confirma que cada pino de GPIO_PINS alterna entre hi e lo.

    Usa o blinker já ativo (iniciado pelo pitest) ou inicia um temporário.
    Amostra os níveis por ~3 ciclos do pisca.

    Critérios de aprovação:
      1. gpiozero disponível e pinos abertos
      2. Todos os pinos foram vistos em hi E em lo

    A confirmação visual (LED acendeu) é feita pelo operador no site.
    """
    result: dict = {"status": "fail", "details": {"pins": list(GPIO_PINS)}}

    blinker, own = _active, None
    if blinker is None:
        own = blinker = GpioBlinker()
        if not blinker.start():
            result["message"] = blinker.error
            return result
    result["details"]["pin_factory"] = type(blinker.board.pin_factory).__name__

    seen: dict[int, set] = {p: set() for p in blinker.pins}
    try:
        deadline = time.monotonic() + max(2.5, 3 / blinker.hz)
        while time.monotonic() < deadline:
            for pin, lvl in blinker.levels().items():
                seen[pin].add(lvl)
            time.sleep(0.1)
    finally:
        if own:
            own.stop()

    stuck    = {p: next(iter(s)) for p, s in seen.items() if len(s) == 1}
    toggling = [p for p, s in seen.items() if len(s) == 2]
    result["details"].update({"toggling": toggling, "stuck": stuck})

    if stuck:
        parts = ", ".join(f"{p}={lvl}" for p, lvl in stuck.items())
        result["message"] = f"Pinos sem alternar: {parts} (lo = curto p/ GND?)"
    else:
        result["status"] = "pass"
        result["message"] = f"GPIO OK — {len(toggling)} pinos alternando (confirme os LEDs no site)"
    return result


if __name__ == "__main__":
    import json

    blinker = GpioBlinker()
    if blinker.start():
        try:
            print(json.dumps(run_test(), indent=2, default=str))
            print("\nPiscando — Ctrl+C para sair")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            blinker.stop()
    else:
        print(json.dumps(run_test(), indent=2, default=str))
