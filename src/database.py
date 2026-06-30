"""
database.py — modelos SQLAlchemy e camada de acesso a dados
===========================================================
Define dois modelos:
  Device — representa uma Raspberry Pi identificada pelo MAC address
  Test   — representa o resultado de um único teste (lan/wlan/boot/usb/hotspot)

A classe Database encapsula todas as operações de leitura e escrita,
mantendo a lógica de negócio fora das rotas Flask.

Inicialização:
  O objeto `db` (SQLAlchemy) deve ser registrado na app Flask via
  db.init_app(app) antes de qualquer operação.
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Device(db.Model):
    """Raspberry Pi registrada no sistema.

    Identificada de forma única pelo MAC address. O campo device_id
    armazena o hostname enviado pelo pitest.py (pode mudar entre runs).
    """

    id          = db.Column(db.Integer, primary_key=True)
    mac_address = db.Column(db.String(32), nullable=False, unique=True)
    device_id   = db.Column(db.String(64), nullable=True)
    name        = db.Column(db.String(64), nullable=True)  # apelido definido no site (sobrepõe o hostname na UI)
    created_at  = db.Column(db.DateTime, nullable=False, default=db.func.now())
    updated_at  = db.Column(db.DateTime, nullable=False, default=db.func.now(), onupdate=db.func.now())
    tests       = db.relationship("Test", backref="device", lazy=True, order_by="Test.created_at.desc()")

    @property
    def display_name(self) -> str | None:
        """Nome exibido na UI: o apelido (name) se definido, senão o hostname."""
        return self.name or self.device_id


class Test(db.Model):
    """Resultado de um teste individual em um Device.

    Campos:
      type      — tipo do teste: lan | wlan | boot | usb | hotspot
      status    — resultado: pass | fail | error
      message   — resumo legível do resultado
      details   — payload JSON completo com métricas e diagnósticos
      elapsed_s — tempo de execução do teste em segundos
    """

    id         = db.Column(db.Integer, primary_key=True)
    type       = db.Column(db.String(32), nullable=False)
    status     = db.Column(db.String(32), nullable=False)
    message    = db.Column(db.Text, nullable=True)  # TEXT: o agente envia mensagens longas (Postgres rejeita VARCHAR estourado)
    details    = db.Column(db.JSON, nullable=True)
    elapsed_s  = db.Column(db.Float, nullable=True)
    device_id  = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=db.func.now())
    updated_at = db.Column(db.DateTime, nullable=False, default=db.func.now(), onupdate=db.func.now())


class Database:
    """Camada de acesso a dados para Device e Test."""

    def commit_info(self, info: dict) -> Exception | None:
        """Persiste um payload completo enviado pelo pitest.py.

        Cria o Device se não existir (lookup por mac_address); caso contrário
        atualiza o hostname. Em seguida cria um registro Test para cada tipo
        presente em info['tests'].

        Retorna None em sucesso ou a Exception em caso de erro (com rollback).
        """
        try:
            mac       = info.get("mac_address")
            hostname  = info.get("device_id")
            tests_raw = info.get("tests", {})

            device = Device.query.filter_by(mac_address=mac).first()
            if not device:
                device = Device(mac_address=mac, device_id=hostname)
                db.session.add(device)
                db.session.flush()  # necessário para obter device.id antes de inserir os testes
            else:
                device.device_id = hostname

            for test_type, test_info in tests_raw.items():
                test = Test(
                    device_id = device.id,
                    type      = test_type,
                    status    = test_info.get("status"),
                    message   = test_info.get("message"),
                    details   = test_info.get("details"),
                    elapsed_s = test_info.get("elapsed_s"),
                )
                db.session.add(test)

            db.session.commit()
            return None
        except Exception as e:
            db.session.rollback()
            return e

    def set_device_name(self, device_id: int, name: str | None) -> Exception | None:
        """Define (ou limpa) o apelido de um device exibido na UI.

        `name` vazio/None limpa o apelido — a UI volta a mostrar o hostname.
        Retorna None em sucesso, ou a Exception em caso de erro (com rollback).
        """
        try:
            device = Device.query.get(device_id)
            if not device:
                return ValueError("device not found")
            device.name = (name or "").strip() or None
            db.session.commit()
            return None
        except Exception as e:
            db.session.rollback()
            return e

    def record_screen_test(self, device_id: int, status: str, note: str | None = None) -> Exception | None:
        """Registra o resultado manual do teste de tela informado pelo usuário no site.

        Cria um Test do tipo 'screen' (append-only, como os demais). `status` deve
        ser 'pass' ou 'fail'; `note` é uma observação opcional do operador.

        Retorna None em sucesso, ou a Exception em caso de erro (com rollback).
        """
        try:
            device = Device.query.get(device_id)
            if not device:
                return ValueError("device not found")

            note = (note or "").strip()
            message = "Tela OK" if status == "pass" else "Tela com problema"
            if note:
                message = f"{message} — {note}"

            test = Test(
                device_id = device_id,
                type      = "screen",
                status    = status,
                message   = message[:256],
                details   = {"note": note, "manual": True},
                elapsed_s = None,
            )
            db.session.add(test)
            db.session.commit()
            return None
        except Exception as e:
            db.session.rollback()
            return e

    def get_all_devices(self) -> list[Device]:
        """Retorna todos os devices ordenados pelo mais recentemente atualizado."""
        return Device.query.order_by(Device.updated_at.desc()).all()

    def get_device(self, device_id: int) -> Device | None:
        """Retorna um Device pelo id primário, ou None se não existir."""
        return Device.query.get(device_id)

    def get_tests(self, device_id: int) -> list[Test]:
        """Retorna todos os testes de um device em ordem decrescente de data."""
        return Test.query.filter_by(device_id=device_id).order_by(Test.created_at.desc()).all()

    def get_latest_overall(self, device_id: int) -> str:
        """Calcula o status geral do device com base no teste mais recente de cada tipo.

        Retorna:
          'pass' — todos os tipos disponíveis passaram
          'fail' — ao menos um tipo falhou ou retornou erro
          'none' — nenhum teste registrado para o device
        """
        types = ("lan", "wlan", "boot", "usb", "hotspot", "screen")
        statuses = []
        for test_type in types:
            t = (
                Test.query
                .filter_by(device_id=device_id, type=test_type)
                .order_by(Test.created_at.desc(), Test.id.desc())
                .first()
            )
            if t:
                statuses.append(t.status)
        if not statuses:
            return "none"
        return "pass" if all(s == "pass" for s in statuses) else "fail"

    def get_latest_tests(self, device_id: int) -> dict:
        """Retorna o teste mais recente de cada tipo para um device.

        Retorna dict: { '<tipo>': Test, ... } — apenas tipos com registro existente.
        """
        latest = {}
        for test_type in ("lan", "wlan", "boot", "usb", "hotspot", "screen"):
            test = (
                Test.query
                .filter_by(device_id=device_id, type=test_type)
                .order_by(Test.created_at.desc(), Test.id.desc())
                .first()
            )
            if test:
                latest[test_type] = test
        return latest
