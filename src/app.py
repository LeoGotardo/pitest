"""
app.py — servidor Flask do PiTest
==================================
Serve as páginas HTML e expõe a API REST consumida pelo pitest.py e pelos
templates front-end.

Rotas HTML:
  GET  /login                     — página de login
  POST /login                     — autenticação
  GET  /logout                    — encerra sessão
  GET  /                          — lista de devices (index.html)
  GET  /rasp/<device_id>          — detalhes e histórico de um device (rasp.html)

Rotas API:
  POST /api/pitest                          — recebe payload do pitest.py (Bearer se PITEST_API_TOKEN configurado)
  GET  /api/devices                         — lista devices (paginado, filtro por MAC)
  GET  /api/devices/<id>/tests              — último resultado por tipo de teste
  GET  /api/devices/<id>/history            — histórico paginado com filtro por tipo

Banco de dados:
  SQLite em instance/pitest.db (criado automaticamente na primeira execução).

Variáveis de ambiente:
  SECRET_KEY       — chave para assinar cookies de sessão (obrigatória em produção)
  PITEST_USER      — login (default: admin)
  PITEST_PASSWORD  — senha (obrigatória)
  PITEST_API_TOKEN — Bearer token para acesso à API (opcional; se vazio, /api/pitest fica aberto)
"""

import functools
import os
import time

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from database import db, Database, Test
from dotenv import load_dotenv

load_dotenv()

def _resolve_db_uri() -> str:
    """Resolve a URI do banco a partir das envs, com fallback para SQLite.

    Aceita tanto o DATABASE_URL clássico quanto o conjunto da integração Neon
    no Vercel (que neste projeto usa prefixo DB_, ex.: DB_DATABASE_URL). Usa a
    primeira env não-vazia na ordem de preferência e normaliza o esquema
    postgres:// → postgresql:// exigido pelo SQLAlchemy. Sem nenhuma env de
    Postgres, cai para SQLite local (efêmero no Vercel — /tmp).
    """
    for key in (
        "DATABASE_URL",                 # var clássica / .env local
        "DB_DATABASE_URL",              # integração Neon (pooled) — prefixo DB_
        "DB_POSTGRES_URL",
        "POSTGRES_URL",                 # integração sem prefixo
        "DB_DATABASE_URL_UNPOOLED",     # conexões diretas (não-pooled)
        "DB_POSTGRES_URL_NON_POOLING",
    ):
        url = os.environ.get(key, "").strip()
        if url:
            if url.startswith("postgres://"):
                url = "postgresql://" + url[len("postgres://"):]
            return url
    return "sqlite:////tmp/pitest.db" if os.environ.get("VERCEL") else "sqlite:///pitest.db"


app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = _resolve_db_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Neon encerra conexões ociosas; pre_ping evita usar uma conexão morta.
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")

db.init_app(app)
database = Database()

def _ensure_schema() -> None:
    """Migração leve: adiciona colunas novas em DBs já existentes.

    db.create_all() só cria tabelas faltantes, não altera colunas. Garante a
    coluna `name` (apelido do device) em bancos criados antes dessa feature.
    """
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    cols = [c["name"] for c in insp.get_columns("device")]
    if "name" not in cols:
        db.session.execute(text("ALTER TABLE device ADD COLUMN name VARCHAR(64)"))
        db.session.commit()


with app.app_context():
    db.create_all()
    _ensure_schema()

_LOGIN_USER = os.environ.get("PITEST_USER", "admin")
_LOGIN_PASS = os.environ.get("PITEST_PASSWORD", "")
_API_TOKEN  = os.environ.get("PITEST_API_TOKEN", "")

# ─── BRUTE FORCE ──────────────────────────────────────────────────────────────

_MAX_ATTEMPTS = 5
_LOCKOUT_S    = 15 * 60  # 15 minutes
_attempts: dict[str, dict] = {}  # ip -> {count, locked_until}


def _get_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


def _check_lockout(ip: str) -> tuple[bool, int]:
    rec = _attempts.get(ip)
    if not rec:
        return False, 0
    remaining = int(rec["locked_until"] - time.time())
    if remaining > 0:
        return True, remaining
    return False, 0


def _record_failure(ip: str) -> int:
    rec = _attempts.setdefault(ip, {"count": 0, "locked_until": 0.0})
    rec["count"] += 1
    if rec["count"] >= _MAX_ATTEMPTS:
        rec["locked_until"] = time.time() + _LOCKOUT_S
        rec["count"] = 0
        return 0
    return _MAX_ATTEMPTS - rec["count"]


def _reset_attempts(ip: str) -> None:
    _attempts.pop(ip, None)


# ─── AUTH ─────────────────────────────────────────────────────────────────────

def _bearer_token() -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _bearer_valid() -> bool:
    token = _bearer_token()
    return bool(_API_TOKEN and token == _API_TOKEN)


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("logged_in") or _bearer_valid():
            return f(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        return redirect(url_for("login", next=request.path))
    return wrapper


def _safe_next(next_url: str | None) -> str:
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return url_for("index")


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("index"))

    error = None
    locked_for = None

    if request.method == "POST":
        ip = _get_ip()
        is_locked, secs = _check_lockout(ip)

        if is_locked:
            mins = (secs + 59) // 60
            error = f"Muitas tentativas. Tente novamente em {mins} minuto(s)."
            locked_for = secs
        else:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")

            if username == _LOGIN_USER and password == _LOGIN_PASS and _LOGIN_PASS:
                _reset_attempts(ip)
                session["logged_in"] = True
                session.permanent = False
                return redirect(_safe_next(request.form.get("next")))
            else:
                remaining = _record_failure(ip)
                if remaining == 0:
                    mins = _LOCKOUT_S // 60
                    error = f"Conta bloqueada por {mins} minutos após muitas tentativas."
                else:
                    error = f"Usuário ou senha incorretos. {remaining} tentativa(s) restante(s)."

    return render_template("login.html", error=error, locked_for=locked_for,
                           next=request.args.get("next", ""))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── HTML ─────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/rasp/<int:device_id>")
@login_required
def rasp(device_id):
    return render_template("rasp.html", device_id=device_id)


# ─── API ──────────────────────────────────────────────────────────────────────

@app.route("/api/pitest", methods=["POST"])
def pitest():
    """Recebe e persiste o resultado de uma bateria de testes do pitest.py.

    Se PITEST_API_TOKEN estiver configurado, exige Authorization: Bearer <token>.

    Body esperado (JSON):
      {
        "device_id":   "<hostname>",
        "mac_address": "<MAC>",
        "timestamp":   "<ISO 8601>",
        "overall":     "pass" | "fail",
        "tests": {
          "<tipo>": { "status": ..., "message": ..., "details": ..., "elapsed_s": ... },
          ...
        }
      }

    Retorna 201 em sucesso, 400 para payload inválido, 401 para token inválido, 500 para erro interno.
    """
    if _API_TOKEN and not _bearer_valid():
        return jsonify({"status": "error", "message": "unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "invalid JSON"}), 400
    if not data.get("mac_address"):
        return jsonify({"status": "error", "message": "mac_address required"}), 400

    error = database.commit_info(data)
    if error:
        return jsonify({"status": "error", "message": str(error)}), 500
    return jsonify({"status": "ok"}), 201


@app.route("/api/devices")
@login_required
def api_devices():
    """Lista devices com paginação e filtro parcial por MAC address.

    Query params:
      mac      — filtro parcial case-insensitive no mac_address (opcional)
      page     — página (default 1)
      per_page — itens por página, máx 100 (default 15)

    Retorna:
      { devices: [...], pagination: { page, per_page, total, pages } }
    """
    from database import Device
    mac      = request.args.get("mac", "").strip()
    page     = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 15, type=int), 100)

    query = Device.query
    if mac:
        query = query.filter(Device.mac_address.ilike(f"%{mac}%"))

    paginated = query.order_by(Device.updated_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        "devices": [
            {
                "id":           d.id,
                "mac_address":  d.mac_address,
                "device_id":    d.device_id,
                "name":         d.name,
                "display_name": d.display_name,
                "updated_at":   d.updated_at.isoformat() + "Z" if d.updated_at else None,
                "overall":      database.get_latest_overall(d.id),
            }
            for d in paginated.items
        ],
        "pagination": {
            "page":     paginated.page,
            "per_page": paginated.per_page,
            "total":    paginated.total,
            "pages":    paginated.pages,
        },
    })


@app.route("/api/devices/<int:device_id>/tests")
@login_required
def api_device_tests(device_id):
    """Retorna o resultado mais recente de cada tipo de teste para um device.

    Retorna 404 se o device não existir.

    Retorna:
      {
        device: { id, mac_address, device_id },
        latest_tests: {
          "<tipo>": { status, message, elapsed_s, created_at },
          ...
        }
      }
    """
    device = database.get_device(device_id)
    if not device:
        return jsonify({"status": "error", "message": "device not found"}), 404

    latest = database.get_latest_tests(device_id)
    return jsonify({
        "device": {
            "id":           device.id,
            "mac_address":  device.mac_address,
            "device_id":    device.device_id,
            "name":         device.name,
            "display_name": device.display_name,
        },
        "latest_tests": {
            t_type: {
                "status":     t.status,
                "message":    t.message,
                "elapsed_s":  t.elapsed_s,
                "created_at": t.created_at.isoformat() + "Z",
            }
            for t_type, t in latest.items()
        },
    })


@app.route("/api/devices/<int:device_id>/name", methods=["PUT"])
@login_required
def api_set_device_name(device_id):
    """Define o apelido exibido na UI para um device.

    Body (JSON): { "name": "<apelido>" }. Nome vazio/null limpa o apelido,
    voltando a exibir o hostname.

    Retorna 200 com o device atualizado, 404 se não existir, 500 em erro.
    """
    device = database.get_device(device_id)
    if not device:
        return jsonify({"status": "error", "message": "device not found"}), 404

    data = request.get_json(silent=True) or {}
    name = data.get("name")

    error = database.set_device_name(device_id, name)
    if error:
        return jsonify({"status": "error", "message": str(error)}), 500

    device = database.get_device(device_id)
    return jsonify({
        "status":       "ok",
        "name":         device.name,
        "display_name": device.display_name,
    })


@app.route("/api/devices/<int:device_id>/screen-test", methods=["POST"])
@login_required
def api_screen_test(device_id):
    """Registra o resultado manual do teste de tela informado pelo usuário.

    O teste de tela é visual e roda na Raspberry (sem teclado/mouse), então o
    veredito é dado aqui no site.

    Body (JSON): { "status": "pass" | "fail", "note": "<observação opcional>" }

    Retorna 201 em sucesso, 400 para status inválido, 404 se o device não
    existir, 500 em erro interno.
    """
    device = database.get_device(device_id)
    if not device:
        return jsonify({"status": "error", "message": "device not found"}), 404

    data   = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in ("pass", "fail"):
        return jsonify({"status": "error", "message": "status must be 'pass' or 'fail'"}), 400

    error = database.record_screen_test(device_id, status, data.get("note"))
    if error:
        return jsonify({"status": "error", "message": str(error)}), 500
    return jsonify({"status": "ok"}), 201


@app.route("/api/devices/<int:device_id>/history")
@login_required
def api_device_history(device_id):
    """Retorna o histórico paginado de testes de um device com filtro por tipo.

    Query params:
      type     — tipo de teste (lan/wlan/boot/usb/hotspot/'all'); default 'all'
      page     — página (default 1)
      per_page — itens por página, máx 100 (default 10)

    Retorna 404 se o device não existir.

    Retorna:
      {
        tests: [ { id, type, status, message, elapsed_s, created_at }, ... ],
        pagination: { page, per_page, total, pages }
      }
    """
    device = database.get_device(device_id)
    if not device:
        return jsonify({"status": "error", "message": "device not found"}), 404

    test_type = request.args.get("type")
    page      = request.args.get("page", 1, type=int)
    per_page  = min(request.args.get("per_page", 10, type=int), 100)

    query = Test.query.filter_by(device_id=device_id)
    if test_type and test_type != "all":
        query = query.filter_by(type=test_type)

    paginated = query.order_by(Test.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        "tests": [
            {
                "id":         t.id,
                "type":       t.type,
                "status":     t.status,
                "message":    t.message,
                "details":    t.details,
                "elapsed_s":  t.elapsed_s,
                "created_at": t.created_at.isoformat() + "Z",
            }
            for t in paginated.items
        ],
        "pagination": {
            "page":     paginated.page,
            "per_page": paginated.per_page,
            "total":    paginated.total,
            "pages":    paginated.pages,
        },
    })


if __name__ == "__main__":
    app.run(debug=True)
