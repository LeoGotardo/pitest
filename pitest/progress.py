"""
progress.py — estado do progresso exibido na tela + avisos ao operador
"""

import threading


class Progress:
    """Estado compartilhado do progresso dos diagnósticos.

    A thread de diagnósticos chama start()/finish(); o loop do screen_test lê
    line() p/ desenhar o overlay. Protegido por lock por ser lido de outra thread.
    """

    def __init__(self, names: tuple[str, ...]):
        """Cria o progresso com todos os testes de `names` pendentes."""
        self._names  = names
        self._status = {n: "·" for n in names}   # · pendente · … rodando · ✓/✗ feito
        self._lock   = threading.Lock()

    def start(self, name: str) -> None:
        """Marca o teste `name` como em execução."""
        with self._lock:
            self._status[name] = "…"

    def finish(self, name: str, status: str, message: str = "") -> None:
        """Marca o teste `name` como concluído e guarda o motivo se falhou."""
        with self._lock:
            self._status[name] = "✓" if status == "pass" else "✗"
            if status != "pass":
                self._errors = getattr(self, "_errors", {})
                self._errors[name] = message or status

    def line(self) -> str:
        """Linha de progresso + uma linha por teste que falhou (motivo visível na tela)."""
        with self._lock:
            text = "  ".join(f"{n.upper()}:{self._status[n]}" for n in self._names)
            for name, msg in getattr(self, "_errors", {}).items():
                text += f"\n✗ {name.upper()}: {msg[:110]}"
            return text

    def set_notice(self, text: str) -> None:
        """Define a instrução em destaque na tela (vazio = sem destaque)."""
        with self._lock:
            self._notice = text

    def notice(self) -> str:
        """Retorna a instrução em destaque atual."""
        with self._lock:
            return getattr(self, "_notice", "")


_progress: "Progress | None" = None


def attach(progress: "Progress | None") -> None:
    """Define qual Progress recebe as mensagens de notify()."""
    global _progress
    _progress = progress


def notify(text: str) -> None:
    """Mostra uma instrução ao operador: console + destaque na tela (se ativa).

    Texto vazio limpa o destaque da tela.
    """
    if text:
        print(f"\n    >> {text}", flush=True)
    if _progress:
        _progress.set_notice(text)
