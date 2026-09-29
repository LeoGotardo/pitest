"""
api.py — envio do resultado para o servidor web
"""

import sys

try:
    import requests
except ImportError:
    sys.exit("Missing: pip install requests")

from config import API_TOKEN, API_URL, TIMEOUT_S


def send_results(payload: dict) -> dict:
    """Envia o payload JSON via POST para API_URL.

    Retorna dict com chaves:
      ok           — True se HTTP 2xx
      http_status  — código de resposta (quando disponível)
      response     — primeiros 500 chars do corpo da resposta
      error        — mensagem de erro em caso de falha de conexão/timeout
    """
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"

    try:
        # Redirects manuais: num 301/302 o requests refaria a chamada como GET
        # e o payload se perderia (ex.: domínio antigo → domínio novo).
        url = API_URL
        for _ in range(3):
            resp = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT_S,
                                 allow_redirects=False)
            if resp.status_code not in (301, 302, 303, 307, 308) or "Location" not in resp.headers:
                break
            url = requests.compat.urljoin(url, resp.headers["Location"])
        return {
            "http_status": resp.status_code,
            "ok": resp.ok,
            "response": resp.text[:500],
        }
    except requests.exceptions.ConnectionError as e:
        return {"ok": False, "error": f"Connection error: {e}"}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": f"Timeout after {TIMEOUT_S}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
