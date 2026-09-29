# Registro de decisões

Decisões técnicas do PiTest: **por que** algo foi feito, não só **o que** foi feito.
Mais recentes primeiro.

---

## Service systemd `Type=idle` em vez de `oneshot`

* Status: aceita
* Data: 2026-09-29

### Contexto e Problema
O boot da RPi travava no `cloud-init.target`. Com `Type=oneshot`, o
`multi-user.target` só conclui quando o pitest termina, e o `cloud-init.target` vem
depois dele. O pitest fica aberto até ESC e espera até 300 s no Bluetooth e no
hotspot, então o boot nunca terminava.

### Critérios de Decisão
* Boot tem que concluir independente do pitest
* O pitest continua dono do `tty1` para a tela (KMS/DRM)

### Opções Consideradas
* `Type=oneshot` com `TimeoutStartSec`
* `Type=simple`
* `Type=idle`

### Decisão
Opção escolhida: "`Type=idle`", porque não bloqueia os targets e ainda adia o start
até os outros jobs do boot terminarem, o que deixa o console limpo para a tela.

#### Consequências
* Bom, porque o boot conclui e o SSH fica disponível durante os testes
* Ruim, porque o boot não "espera" o resultado (quem precisa saber se passou lê o
  site ou o `journalctl`)

---

## GPIO com gpiozero (`LEDBoard`) em vez do CLI `pinctrl`

* Status: aceita (substitui "GPIO via pinctrl/raspi-gpio", obsoleta)
* Data: 2026-09-29

### Contexto e Problema
O teste de GPIO precisa piscar os pinos BCM 2–27 durante toda a execução e ler o
nível real de cada um para detectar pinos presos em curto.

### Critérios de Decisão
* Simplicidade de código e manutenção
* Funcionar no Raspberry Pi OS atual
* Permitir ler o nível real do pino (detecção de curto)
* Testável fora da RPi

### Opções Consideradas
* `pinctrl`/`raspi-gpio` via `subprocess` com uma thread de pisca manual
* gpiozero `LEDBoard`
* RPi.GPIO

### Decisão
Opção escolhida: "gpiozero `LEDBoard`", porque `board.blink()` pisca todos os pinos
juntos numa thread interna, `led.pin.state` lê o nível do hardware, `close()` devolve
os pinos a input, e a `MockFactory` permite testar sem RPi. É a biblioteca padrão
das práticas do projeto.

#### Consequências
* Bom, porque o módulo ficou bem menor e sem parsing de saída de CLI
* Ruim, porque adiciona dependência (`gpiozero` + `lgpio`) ao venv da RPi; o `lgpio`
  pode precisar vir do apt (`python3-lgpio`)

---

## Um módulo por teste (`hwtests/`)

* Status: aceita
* Data: 2026-09-29

### Contexto e Problema
O `pitest.py` tinha ~1000 linhas com todos os testes, config, rede, tela e envio.
Era difícil achar código e impossível rodar um teste isolado na bancada.

### Critérios de Decisão
* Cada teste roda sozinho (`python3 -m hwtests.<nome>`)
* O `pitest.service` não pode mudar
* Mudança sem reescrever a lógica dos testes

### Opções Consideradas
* Manter arquivo único com flag `--only`
* Pacote `hwtests/` com `run_test()` por módulo + `config`, `common`, `progress`, `api`

### Decisão
Opção escolhida: "pacote `hwtests/`", porque isola cada teste, centraliza a
configuração em `config.py` e deixa a ordem de execução explícita no dicionário
`TESTS` do `pitest.py`.

#### Consequências
* Bom, porque cada teste é depurável na bancada e o código foi movido sem alteração
* Ruim, porque a cópia para a RPi agora envolve vários arquivos e uma pasta

---

## Bluetooth: `bluetoothctl` em pseudo-terminal com agente `DisplayYesNo`

* Status: aceita
* Data: 2026-09-29

### Contexto e Problema
O teste precisa que um celular pareie com a RPi. Rodando o `bluetoothctl` em pipe,
a saída ficava em buffer: os prompts de confirmação e os eventos de conexão nunca
chegavam, e o teste esperava o timeout inteiro.

### Critérios de Decisão
* Operador vê e confirma o código de pareamento na tela da RPi
* Sem dependências Python novas
* Detecção confiável do pareamento

### Opções Consideradas
* `bluetoothctl` em pipe (rejeitada: buffering)
* Agente via D-Bus (`dbus-python` + GLib main loop)
* `bluetoothctl` em `pty` + polling de `bluetoothctl info`

### Decisão
Opção escolhida: "`bluetoothctl` em `pty`", porque no pty a saída é imediata, o
agente `DisplayYesNo` gera o código de 6 dígitos, que o pitest mostra na tela e
confirma sozinho, e o polling de `Paired: yes` não depende de parsing do stream.

#### Consequências
* Bom, porque funciona com o bluez padrão do SO
* Ruim, porque depende do formato de texto do `bluetoothctl`; o iPhone não lista a
  RPi (usar Android)

---

## Testes visuais (tela e LEDs) confirmados no site

* Status: aceita
* Data: 2026-09-28

### Contexto e Problema
Não dá para verificar automaticamente se a tela exibe as cores certas ou se um LED
acendeu. A RPi de bancada não tem teclado nem mouse para o operador responder.

### Critérios de Decisão
* Operador responde sem periférico na RPi
* O resultado fica no histórico junto com os testes automáticos

### Opções Consideradas
* Resposta por teclado na própria RPi
* Painéis manuais no site (`screen-test`, `gpio-test`)

### Decisão
Opção escolhida: "painéis no site", porque o operador já usa o site para ver o
resultado e o veredito vira um `Test` normal (`screen`, `gpio`) no histórico.

#### Consequências
* Bom, porque não precisa de periférico e fica auditável
* Ruim, porque o teste automático e o manual de GPIO compartilham o tipo `gpio`
  (o card mostra o mais recente)

---

## Envio à API seguindo redirects manualmente

* Status: aceita
* Data: 2026-09-29

### Contexto e Problema
`pitest-seven.vercel.app` passou a redirecionar (301) para
`pitest.leogotardo.com.br`. Num 301, o `requests` reenvia como GET, a API responde
405 e o resultado se perde sem aviso na tela.

### Critérios de Decisão
* Resultado nunca pode se perder em mudança de domínio
* Falha de envio visível para o operador

### Opções Consideradas
* Só trocar a `API_URL`
* Trocar a `API_URL` e seguir redirects mantendo o POST

### Decisão
Opção escolhida: "trocar e seguir redirects", porque protege contra a próxima troca
de domínio. O resultado do envio também passou a aparecer na tela.

#### Consequências
* Bom, porque um redirect futuro não quebra o envio
* Ruim, porque o POST é refeito a cada salto (máx. 3)

---

## URL do banco normalizada para psycopg2

* Status: aceita
* Data: 2026-09-29

### Contexto e Problema
No Vercel, a `DB_DATABASE_URL` chegava como `postgresql+psycopg://` (psycopg 3), mas
só o `psycopg2-binary` está instalado: o app nem importava.

### Critérios de Decisão
* Funcionar com qualquer variante de URL (`postgres://`, `postgresql://`, `+psycopg`)
* Não adicionar dependência

### Opções Consideradas
* Instalar `psycopg[binary]`
* Normalizar a URL para `postgresql+psycopg2://` em `app.py`

### Decisão
Opção escolhida: "normalizar a URL", porque resolve todas as variantes sem mexer
nas dependências nem na configuração do Vercel.

#### Consequências
* Bom, porque o deploy não depende de como a URL foi gerada
* Ruim, porque fixa o driver psycopg2 (trocar de driver exige mudar `_db_url`)
