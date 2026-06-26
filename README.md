# PiTest

Ferramenta de diagnóstico de hardware para Raspberry Pi. Executa uma bateria de testes (LAN, WLAN, Boot, USB, Hotspot) e envia os resultados para um servidor web central que exibe o histórico de cada dispositivo.

---

## Estrutura

```
pitest/
├── pitest.py          # script de testes — roda na RPi
├── wifiModule.py      # WifiManager — gerencia hostapd/dnsmasq para o teste de hotspot
├── pitest.service     # unit systemd para executar pitest.py no boot
├── requirements.txt   # dependências do script (requests)
└── src/               # servidor web
    ├── app.py         # Flask + rotas da API
    ├── database.py    # modelos SQLAlchemy (Device, Test)
    ├── seed.py        # popula o banco com dados de exemplo
    ├── requirements.txt
    ├── static/
    │   └── style.css
    └── templates/
        ├── base.html
        ├── index.html  # lista de devices
        └── rasp.html   # detalhes e histórico de um device
```

---

## Testes executados

| Teste | O que verifica |
|---|---|
| **LAN** | Interface `eth*` presente, UP, IP atribuído, ping externo |
| **WLAN** | Interface `wlan*` presente, rfkill desbloqueado, interface sobe, driver responde via `iw list`; coleta SSID/sinal se já associada |
| **Boot** | Partição `/boot` montada, tempo de boot via `systemd-analyze`, units com falha, uptime, versão do kernel e SO |
| **USB** | Dispositivos externos via `lsusb` + topologia de portas via sysfs (filtra hub/Ethernet internos da placa) |
| **Hotspot** | Cria um AP Wi-Fi (hostapd + dnsmasq) e aguarda um cliente conectar (executa por último) |

> O teste WLAN verifica o hardware Wi-Fi sem exigir associação a uma rede. Passa se a interface existe, não está bloqueada por rfkill, consegue subir e o driver responde via `iw list`.

---

## Configuração do script (`pitest.py`)

Edite as constantes no topo do arquivo:

```python
API_URL          = "https://seu-servidor.com/api/pitest"
API_TOKEN        = ""                    # Bearer token; vazio = sem autenticação
DEVICE_ID        = socket.gethostname() # identificador enviado no payload
PING_HOST        = "8.8.8.8"
PING_COUNT       = 4                    # pacotes ICMP por teste de ping
TIMEOUT_S        = 10                   # timeout do POST à API (segundos)

HOTSPOT_SSID     = "PiTest"
HOTSPOT_PASSWORD = "pitest123"
HOTSPOT_TIMEOUT  = 300                  # segundos aguardando cliente no hotspot
HOTSPOT_CON_NAME = "pitest-hotspot"    # nome da conexão nmcli (removida após o teste)
```

---

## Instalação e uso

### Script na Raspberry Pi

```bash
pip install requests
sudo python3 pitest.py           # executa e envia para a API
sudo python3 pitest.py --json    # também imprime o payload no terminal
```

> Requer `sudo` para acessar interfaces de rede e criar o hotspot.
>
> Ferramentas necessárias no sistema: `ip`, `ping`, `iw`, `rfkill`, `lsusb`, `findmnt`, `systemctl`, `systemd-analyze`, `hostapd`, `dnsmasq`.

**Exit code:** `0` se todos os testes passaram, `1` se algum falhou — útil para integração com scripts e cron.

### Primeira execução do hotspot

Na primeira vez, instale as dependências do AP:

```python
from wifiModule import WifiManager
WifiManager().prepare_environment()   # instala hostapd e dnsmasq via apt
```

### Execução automática via systemd

```bash
sudo cp pitest.service /etc/systemd/system/
# edite o caminho em ExecStart/WorkingDirectory se necessário
sudo systemctl daemon-reload
sudo systemctl enable pitest.service   # roda no próximo boot
sudo systemctl start pitest.service    # roda agora
sudo journalctl -u pitest.service      # ver logs
```

O arquivo `pitest.service` executa `pitest.py` como `oneshot` após a rede estar disponível (`network-online.target`).

### Servidor web

```bash
cd src
pip install -r requirements.txt  # ou use um venv

python3 seed.py   # opcional: popula com dados de exemplo
python3 app.py    # inicia em http://localhost:5000
```

O banco SQLite é criado automaticamente em `src/instance/pitest.db` na primeira execução.

---

## API

### `POST /api/pitest`
Recebe o resultado de uma bateria de testes enviado pelo `pitest.py`.

**Body (JSON):**
```json
{
  "device_id":   "rpi-sala",
  "mac_address": "dc:a6:32:11:22:33",
  "timestamp":   "2026-06-25T14:00:00+00:00",
  "overall":     "pass",
  "tests": {
    "lan":  { "status": "pass", "message": "...", "details": {}, "elapsed_s": 1.2 },
    "wlan": { "status": "pass", "message": "...", "details": {}, "elapsed_s": 2.1 },
    "boot": { "status": "pass", "message": "...", "details": {}, "elapsed_s": 0.5 },
    "usb":  { "status": "pass", "message": "...", "details": {}, "elapsed_s": 0.7 },
    "hotspot": { "status": "pass", "message": "...", "details": {}, "elapsed_s": 45.3 }
  }
}
```

**Respostas:** `201 OK` · `400 payload inválido` · `500 erro interno`

---

### `GET /api/devices`
Lista devices com paginação e filtro por MAC.

| Param | Tipo | Default | Descrição |
|---|---|---|---|
| `mac` | string | — | Filtro parcial no MAC (case-insensitive) |
| `page` | int | 1 | Página |
| `per_page` | int | 15 | Itens por página (máx 100) |

---

### `GET /api/devices/<id>/tests`
Retorna o resultado mais recente de cada tipo de teste para o device.

---

### `GET /api/devices/<id>/history`
Histórico paginado de testes com filtro por tipo.

| Param | Tipo | Default | Descrição |
|---|---|---|---|
| `type` | string | all | `lan` · `wlan` · `boot` · `usb` · `hotspot` · `all` |
| `page` | int | 1 | Página |
| `per_page` | int | 10 | Itens por página (máx 100) |

---

## Interface web

| Página | URL | Descrição |
|---|---|---|
| Devices | `/` | Tabela com todos os devices, busca por MAC e resultado do último teste |
| Device | `/rasp/<id>` | Cards com últimos resultados + histórico paginado com filtro por tipo |
