"""
hwtests — um módulo por teste de hardware.

Cada módulo expõe `run_test() -> dict` com as chaves status ("pass" | "fail"),
message e details. Rodar um teste isolado (na RPi, da pasta do projeto):

    sudo python3 -m hwtests.bluetooth
"""
