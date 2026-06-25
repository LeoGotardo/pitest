"""
app.py — servidor Flask do PiTest
==================================
Serve as páginas HTML e expõe a API REST consumida pelo pitest.py e pelos
templates front-end.

Rotas HTML:
  GET  /                          — lista de devices (index.html)
  GET  /rasp/<device_id>          — detalhes e histórico de um device (rasp.html)

Rotas API:
  POST /api/pitest                          — recebe payload do pitest.py
  GET  /api/devices                         — lista devices (paginado, filtro por MAC)
  GET  /api/devices/<id>/tests              — último resultado por tipo de teste
  GET  /api/devices/<id>/history            — histórico paginado com filtro por tipo

Banco de dados:
  SQLite em instance/pitest.db (criado automaticamente na primeira execução).
"""

from flask import Flask, jsonify, request, render_template
from database import db, Database, Test

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///pitest.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
database = Database()

with app.app_context():
    db.create_all()


# ─── HTML ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/rasp/<int:device_id>")
def rasp(device_id):
    return render_template("rasp.html", device_id=device_id)


# ─── API ──────────────────────────────────────────────────────────────────────

@app.route("/api/pitest", methods=["POST"])
def pitest():
    """Recebe e persiste o resultado de uma bateria de testes do pitest.py.

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

    Retorna 201 em sucesso, 400 para payload inválido, 500 para erro interno.
    """
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
                "id":          d.id,
                "mac_address": d.mac_address,
                "device_id":   d.device_id,
                "updated_at":  d.updated_at.isoformat() if d.updated_at else None,
                "overall":     database.get_latest_overall(d.id),
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
            "id":          device.id,
            "mac_address": device.mac_address,
            "device_id":   device.device_id,
        },
        "latest_tests": {
            t_type: {
                "status":     t.status,
                "message":    t.message,
                "elapsed_s":  t.elapsed_s,
                "created_at": t.created_at.isoformat(),
            }
            for t_type, t in latest.items()
        },
    })


@app.route("/api/devices/<int:device_id>/history")
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
                "elapsed_s":  t.elapsed_s,
                "created_at": t.created_at.isoformat(),
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
