"""
config.py — configuração do pitest (edite aqui)
"""

import os
import socket

from dotenv import load_dotenv

load_dotenv()

API_URL     = "https://pitest.leogotardo.com.br/api/pitest"  # endpoint que recebe o POST
API_TOKEN   = os.environ.get('PITEST_API_TOKEN', '')                    # Bearer token para autenticação; vazio = sem auth
DEVICE_ID   = socket.gethostname() # identificador do dispositivo enviado no payload
PING_HOST   = "8.8.8.8"            # host usado nos testes de conectividade
PING_COUNT  = 4                     # número de pacotes ICMP por teste de ping
TIMEOUT_S   = 10                    # timeout em segundos para o POST à API

HOTSPOT_SSID     = "PiTest"         # SSID do AP criado no teste de hotspot
HOTSPOT_PASSWORD = "pitest123"      # senha WPA2 do AP
HOTSPOT_TIMEOUT  = 300              # segundos aguardando um cliente conectar
HOTSPOT_CON_NAME = "pitest-hotspot" # nome da conexão nmcli (removida após o teste)

BT_TIMEOUT      = 300              # segundos aguardando um celular conectar via Bluetooth
BT_ALIAS_PREFIX = "PiTest"         # nome visível no celular: "<prefixo> <hostname>"
BT_REMOVE_AFTER = True             # remove o pareamento ao final (RPi fica limpa p/ o próximo teste)

GPIO_PINS        = list(range(2, 28))  # pinos BCM que piscam (2–27 = todos do header de 40 pinos)
GPIO_BLINK_HZ    = 1                   # frequência do pisca
GPIO_BLINK_MIN_S = 60                  # tempo mínimo piscando antes de sair (modo --no-screen)
