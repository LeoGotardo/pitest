# PiTest

Diagnóstico de hardware para Raspberry Pi 3B com painel web de histórico.

**Versão atual:** `1.0.0` (definida em `pitest/pitest.py` → `__version__`)

## Sumário

1. [Visão geral](#1-visão-geral)
2. [Ambiente](#2-ambiente)
3. [Operação](#3-operação)
4. [Particularidades](#4-particularidades)
5. [Referência: testes, configuração, API e interface web](#5-referência)
6. [Versionamento e imagens](#6-versionamento-e-imagens)
7. [Decisões técnicas](docs/decisions.md)

---

## 1. Visão geral

### Descrição

O **PiTest** roda em uma Raspberry Pi assim que ela liga. Enquanto isso, a tela
mostra padrões para achar pixels defeituosos e, em paralelo, uma bateria de testes
verifica rede cabeada, Wi-Fi, boot, USB, GPIO, Bluetooth e hotspot. No fim, o
resultado é enviado para um servidor web, que guarda o histórico de cada placa. Os
testes que dependem de olho humano (tela e LEDs dos GPIOs) são confirmados pelo
operador direto no site.

### Função (o que faz e para quem)

- **Para quem:** operador de bancada que precisa validar placas Raspberry Pi (ex.:
  antes de entregar ou depois de um reparo).
- **O que faz:**
  - roda os testes sozinho a cada boot, sem teclado nem SSH;
  - mostra na tela o progresso, o IP, o motivo de cada falha e as instruções ao
    operador (código de pareamento Bluetooth, SSID/senha do hotspot);
  - envia o resultado ao site `https://pitest.leogotardo.com.br`, identificado pelo
    MAC da placa;
  - no site, lista as placas, o último resultado de cada teste e o histórico, e
    recebe o veredito manual de tela e GPIO.

### Componentes

```
pitest/                  (repositório)
├── pitest/              script que roda na RPi → copiado para /home/pi/test/
│   ├── pitest.py        módulo principal: ordem dos testes, tela, envio, __version__
│   ├── config.py        configuração (URL da API, timeouts, SSID, pinos GPIO…)
│   ├── common.py        run(), ping(), interfaces, IP/MAC
│   ├── progress.py      progresso na tela + notify() (avisos ao operador)
│   ├── api.py           envio do resultado ao servidor
│   ├── screenTest.py    teste visual de tela (pygame, KMS/DRM)
│   ├── wifiModule.py    WifiManager — hostapd/dnsmasq para o teste de hotspot
│   └── hwtests/         um módulo por teste; cada um expõe run_test()
│       ├── lan.py  wlan.py  boot.py  usb.py
│       ├── gpio.py      pisca os GPIOs (gpiozero) e confere que cada pino alterna
│       ├── bluetooth.py pareamento com celular (bluetoothctl em pty)
│       └── hotspot.py   AP Wi-Fi + espera um cliente
├── pitest.service       unit systemd (roda o pitest no boot)
├── requirements.txt     dependências do script da RPi
├── src/                 servidor web (Flask) — publicado no Vercel
│   ├── app.py           rotas HTML + API REST
│   ├── database.py      modelos SQLAlchemy (Device, Test)
│   ├── requirements.txt
│   ├── static/style.css
│   └── templates/       base, login, index (lista), rasp (detalhes + testes manuais)
├── docs/
│   └── decisions.md     registro de decisões técnicas
└── Images/              imagens de SD (fora do git) — beta/ stable/ old/
```

---

## 2. Ambiente

### Setup para desenvolvimento

Servidor web (no PC):

```bash
cd src
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python app.py  # http://localhost:5000 (lê o .env da raiz do repositório)
```

Variáveis do `.env` (raiz do repositório, fora do git):

| Variável | Uso |
|---|---|
| `DB_DATABASE_URL` | URL do Postgres (`postgresql://…`; qualquer variante é convertida para psycopg2) |
| `SECRET_KEY` | chave da sessão Flask |
| `PITEST_USER` / `PITEST_PASSWORD` | login do site |
| `PITEST_API_TOKEN` | token que a RPi usa para enviar resultados |

Script (no PC, sem hardware): os módulos rodam isolados; os que só leem o sistema
(`boot`, `usb`) funcionam em qualquer Linux. Não rode `hotspot` nem `bluetooth` no
PC — eles param o NetworkManager / deixam o Bluetooth visível.

```bash
cd pitest
python3 -m hwtests.boot
GPIOZERO_PIN_FACTORY=mock python3 -m hwtests.gpio   # GPIO simulado
python3 pitest.py --no-screen --json                # bateria completa no console
```

### Setup para produção

**Servidor:** projeto `pitest` no Vercel, domínio `pitest.leogotardo.com.br`. As
variáveis da tabela acima ficam em *Settings → Environment Variables*.

**Raspberry Pi** (Raspberry Pi OS Lite bookworm, usuário `pi`):

```bash
sudo apt install -y hostapd dnsmasq bluez python3-venv python3-lgpio python3-gpiozero
mkdir -p /home/pi/test
# copie o CONTEÚDO de pitest/ + requirements.txt + pitest.service para /home/pi/test/
cd /home/pi/test
python3 -m venv --system-site-packages venv && ./venv/bin/pip install -r requirements.txt
echo 'PITEST_API_TOKEN=<mesmo token do Vercel>' > .env
sudo ln -sf /home/pi/test/pitest.service /etc/systemd/system/pitest.service
sudo systemctl daemon-reload && sudo systemctl enable pitest
```

### Como fazer a build do zero (imagem de SD)

1. Grave o Raspberry Pi OS Lite (bookworm) com o Raspberry Pi Imager: hostname,
   usuário `pi`, senha e SSH habilitado.
2. Faça o *Setup para produção* acima e confirme um ciclo completo
   (`sudo reboot` → testes na tela → resultado no site).
3. Com o cartão no PC, gere a imagem e compacte:
   ```bash
   sudo dd if=/dev/sdX of=Images/beta/pitest_v1-0-0_$(date +%d%m%Y).img bs=4M status=progress
   zip -j Images/beta/pitest_v1-0-0_$(date +%d%m%Y).zip Images/beta/pitest_v1-0-0_*.img
   ```
4. Siga o fluxo de imagens da seção 6 (`beta` → `stable` → `old`).

---

## 3. Operação

### Workflow de boot

1. Firmware e kernel → systemd sobe rede (`network-online.target`), SSH e
   `cloud-init.target`.
2. Com o boot concluído, o `pitest.service` (`Type=idle`) assume o `tty1` e inicia
   `pitest.py`.
3. Os GPIOs BCM 2–27 começam a piscar (1 Hz) e ficam piscando até o fim.
4. A tela entra em fullscreen com o teste visual. Os testes rodam em paralelo, na
   ordem: `lan` → `wlan` → `boot` → `usb` → `gpio` → `bluetooth` → `hotspot`.
   - **bluetooth:** a RPi fica visível como `PiTest <hostname>`; ao parear, o código
     de 6 dígitos aparece grande na tela (até 300 s).
   - **hotspot:** a tela mostra SSID `PiTest` / senha `pitest123`; espera um
     celular conectar (até 300 s).
5. O resultado é enviado à API; a tela mostra `API: enviado ✓` ou o erro, e
   `[done — ESC to exit]`.
6. O operador confere tela e LEDs e marca no site (painéis *Screen Test* e *GPIO
   Test* da página do device). ESC fecha a tela e libera os GPIOs.

### Scripts disponíveis

| Comando (em `/home/pi/test`) | O que faz |
|---|---|
| `sudo ./venv/bin/python pitest.py` | bateria completa + tela + envio |
| `… pitest.py --no-screen` | só console (GPIOs piscam por ≥ 60 s) |
| `… pitest.py --json` | também imprime o payload enviado |
| `sudo ./venv/bin/python -m hwtests.<nome>` | um teste isolado (`lan`, `wlan`, `boot`, `usb`, `bluetooth`, `hotspot`) em JSON |
| `sudo ./venv/bin/python -m hwtests.gpio` | testa os GPIOs e segue piscando até Ctrl+C (bom p/ ligar LEDs) |
| `./venv/bin/python screenTest.py` | só o teste de tela |

Exit code do `pitest.py`: `0` todos passaram, `1` algum falhou.

### Update e rollback

**Código na RPi:**

```bash
# update: copie o conteúdo novo de pitest/ para /home/pi/test/ e reinicie
sudo systemctl restart pitest        # ou sudo reboot
# conferir a versão rodando: 2ª linha da tela, console ou campo "version" do payload
```

Rollback: faça checkout da tag anterior (`git checkout vX.Y.Z`) e copie de novo.

**Imagem:** grave no SD a imagem de `Images/stable/`. Para voltar uma versão,
use a mais recente de `Images/old/`.

**Servidor:** rollback pelo painel do Vercel (*Deployments → Promote/Rollback*).

### Comandos de diagnóstico

```bash
sudo journalctl -u pitest -b --no-pager | tail -60   # log do pitest neste boot
systemctl status pitest
systemctl list-jobs                                  # o que está segurando o boot
systemd-analyze critical-chain cloud-init.target
vcgencmd get_throttled                               # ≠ 0x0 = subtensão (fonte/LEDs)
rfkill list ; iw dev ; bluetoothctl show
systemctl is-active hciuart bluetooth ssh NetworkManager
dmesg | grep -iE "brcmf|hci0|under-voltage" | tail
```

Sem SSH e com teclado na RPi: **Ctrl+Alt+F2** abre outro terminal (o `tty1` é do
pitest). Para impedir o pitest de subir, adicione ` systemd.mask=pitest.service` ao
final da linha de `/boot/firmware/cmdline.txt` (dá para editar com o cartão no PC).

### Paths úteis

| Path | Conteúdo |
|---|---|
| `/home/pi/test/` | script do pitest + `venv/` |
| `/home/pi/test/.env` | `PITEST_API_TOKEN` |
| `/etc/systemd/system/pitest.service` | link para `/home/pi/test/pitest.service` |
| `journalctl -u pitest` | logs do pitest |
| `/etc/hostapd/hostapd.conf`, `/etc/dnsmasq.conf` | gerados pelo teste de hotspot |
| `/boot/firmware/cmdline.txt`, `/boot/firmware/config.txt` | boot do kernel / overlays |
| `.env` na raiz do repositório (dev) | variáveis do servidor |

---

## 4. Particularidades

- **Hardware alvo: RPi 3B.** Wi-Fi e Bluetooth são o mesmo chip (BCM43438) e
  funcionam juntos; o Bluetooth fica na UART interna (GPIO 32/33), o Wi-Fi no SDIO
  (GPIO 34–39) — nenhum deles é tocado pelo teste de GPIO.
- **GPIO e energia:** os GPIOs, o chip de rede cabeada e o Wi-Fi/BT dividem o 3,3 V.
  LED sem resistor ou em curto derruba **rede, Wi-Fi e Bluetooth juntos** (SSH dá
  timeout). Sempre: `pino → resistor 330 Ω–1 kΩ → LED (perna longa) → GND`.
  Numeração é **BCM**, não física (GPIO17 = pino 11).
- **GPIO 2/3** têm pull-up na placa: o LED pode brilhar fraco mesmo "apagado".
  **GPIO 14/15** são a serial — o console serial para durante o teste.
- **Hotspot derruba o Wi-Fi cliente:** ele para o NetworkManager. Acompanhe por SSH
  **via cabo**. Se `prepare_environment()` foi usado, `hostapd`/`dnsmasq` ficam
  habilitados no boot.
- **Bluetooth:** use **Android** — o iPhone não lista dispositivos genéricos. O
  `bluetoothctl` precisa rodar num pseudo-terminal (em pipe os prompts não chegam).
- **Service não pode ser `oneshot`:** o boot esperaria o pitest (que fica aberto até
  ESC) e travaria no `cloud-init.target`.
- **Domínio da API:** `pitest-seven.vercel.app` redireciona (301) para
  `pitest.leogotardo.com.br`. O envio segue redirects mantendo o POST; num 301 o
  `requests` trocaria para GET e o resultado se perderia.
- **Cópia para o SD:** ejete o cartão só depois do `sync`/unmount — uma cópia
  interrompida já deixou todos os `.py` com 0 bytes.
- **Sem testes unitários ainda** (pendente, ver guia de práticas).

---

## 5. Referência

### Testes executados

| Teste | O que verifica |
|---|---|
| **LAN** | Interface `eth*` presente, UP, IP atribuído, ping externo |
| **WLAN** | Interface `wlan*` presente, rfkill desbloqueado, interface sobe, driver responde via `iw list`; coleta SSID/sinal se já associada |
| **Boot** | Partição `/boot` montada, tempo de boot via `systemd-analyze`, units com falha, uptime, versão do kernel e SO |
| **USB** | Dispositivos externos via `lsusb` + topologia de portas via sysfs (filtra hub/Ethernet internos da placa) |
| **GPIO** | Automático: lê o nível de cada GPIO BCM 2–27 enquanto piscam e confirma que alterna (pino preso = curto). Manual: o operador marca no site quais LEDs acenderam |
| **Bluetooth** | Controlador `hci*`, rfkill, `bluetooth.service`, controlador liga e um celular pareia (código exibido na tela) |
| **Hotspot** | Cria um AP Wi-Fi (hostapd + dnsmasq) e aguarda um cliente conectar (executa por último) |
| **Screen** (manual) | Operador informa no site se a tela passou |

### Configuração do script (`pitest/config.py`)

| Constante | Default | Uso |
|---|---|---|
| `API_URL` | `https://pitest.leogotardo.com.br/api/pitest` | endpoint do POST |
| `API_TOKEN` | `PITEST_API_TOKEN` do `.env` | Bearer token |
| `PING_HOST` / `PING_COUNT` | `8.8.8.8` / `4` | teste de LAN |
| `TIMEOUT_S` | `10` | timeout do POST |
| `HOTSPOT_SSID` / `HOTSPOT_PASSWORD` / `HOTSPOT_TIMEOUT` | `PiTest` / `pitest123` / `300` | teste de hotspot |
| `BT_TIMEOUT` / `BT_ALIAS_PREFIX` / `BT_REMOVE_AFTER` | `300` / `PiTest` / `True` | teste de Bluetooth |
| `GPIO_PINS` / `GPIO_BLINK_HZ` / `GPIO_BLINK_MIN_S` | BCM 2–27 / `1` / `60` | teste de GPIO (`[]` desativa) |

### API

#### `POST /api/pitest`
Recebe o resultado de uma bateria de testes enviado pelo `pitest.py` (Bearer token).

```json
{
  "device_id":   "rpi-sala",
  "version":     "1.0.0",
  "mac_address": "b8:27:eb:11:22:33",
  "timestamp":   "2026-09-29T14:00:00+00:00",
  "overall":     "pass",
  "tests": {
    "lan":       { "status": "pass", "message": "...", "details": {}, "elapsed_s": 1.2 },
    "gpio":      { "status": "pass", "message": "...", "details": {}, "elapsed_s": 3.0 },
    "bluetooth": { "status": "pass", "message": "...", "details": {}, "elapsed_s": 18.4 }
  }
}
```

**Respostas:** `201 OK` · `400 payload inválido` · `401 token inválido` · `500 erro interno`

#### `POST /api/devices/<id>/screen-test`
Veredito manual da tela: `{ "status": "pass" | "fail", "note": "..." }`.

#### `POST /api/devices/<id>/gpio-test`
Veredito manual dos LEDs: `{ "lit": [17, 27], "not_lit": [22], "note": "..." }` (BCM 2–27).

#### `GET /api/devices`
Lista devices com paginação e filtro por MAC.

| Param | Tipo | Default | Descrição |
|---|---|---|---|
| `mac` | string | — | Filtro parcial no MAC (case-insensitive) |
| `page` | int | 1 | Página |
| `per_page` | int | 15 | Itens por página (máx 100) |

#### `GET /api/devices/<id>/tests`
Resultado mais recente de cada tipo de teste do device.

#### `GET /api/devices/<id>/history`
Histórico paginado com filtro por tipo.

| Param | Tipo | Default | Descrição |
|---|---|---|---|
| `type` | string | all | `lan` · `wlan` · `boot` · `usb` · `gpio` · `bluetooth` · `hotspot` · `screen` · `all` |
| `page` | int | 1 | Página |
| `per_page` | int | 10 | Itens por página (máx 100) |

### Interface web

| Página | URL | Descrição |
|---|---|---|
| Devices | `/` | Tabela com todos os devices, busca por MAC e resultado do último teste |
| Device | `/rasp/<id>` | Cards com últimos resultados, testes manuais (tela e GPIO) e histórico paginado |

---

## 6. Versionamento e imagens

**Código:** versão semântica `MAJOR.MINOR.PATCH` em `pitest/pitest.py`
(`__version__`), com tag git `vX.Y.Z` a cada release. Identificador de build:

```bash
echo "PT_$(git describe --tags --dirty)"   # ex.: PT_v1.0.0-4-gabc1234-dirty
```

Build com `-dirty` ou contador de commits (`-4-…`) **não** é versão oficial e não
vai para produção.

**Imagens:** `pitest_v1-0-0_DDMMAAAA.img` (pontos da versão viram hífens), em:

```
Images/
├── beta/     em teste, ainda não liberadas
├── stable/   imagem atual liberada para produção
└── old/      versões anteriores (rollback)
```

Fluxo: `beta` → (aprovada nos testes) → `stable` → (substituída) → `old`.
