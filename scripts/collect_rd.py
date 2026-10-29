#!/usr/bin/env python3
"""
Coleta os negócios do RD Station CRM para a aba Comercial.

NÃO guarda dados pessoais: nome, telefone, e-mail e contatos dos alunos ficam de fora.
Cada negócio vira uma linha com datas, etapa, fonte, campanha, praça e status.

Grava em data/:
  rd.enc.json  -> criptografado (quando DASHBOARD_PASSWORD existe)
  rd.json      -> sem criptografia (sem senha)

Variáveis de ambiente (GitHub Secrets):
  RD_CRM_TOKEN        token do RD Station CRM (obrigatório)
  DASHBOARD_PASSWORD  senha do painel (a mesma dos outros painéis)

Opcionais:
  RD_HIST_START       início do histórico (padrão 2024-01-01)
  RD_POR_CONSULTOR    "1" para guardar o responsável de cada negócio (ranking por consultor)
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATE_PLAIN = DATA / "rd.json"
STATE_ENC = DATA / "rd.enc.json"

TOKEN = os.environ.get("RD_CRM_TOKEN", "").strip()
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
BASE = "https://crm.rdstation.com/api/v1"
HIST_START = date.fromisoformat(os.environ.get("RD_HIST_START", "2024-01-01"))
POR_CONSULTOR = os.environ.get("RD_POR_CONSULTOR", "") == "1"
PAGE_LIMIT = 200
PAUSE = 0.55  # a API aceita 120 requisições por minuto

calls = 0


def log(msg):
    print(f"[rd] {msg}", flush=True)


def fail(msg):
    log(f"ERRO: {msg}")
    sys.exit(1)


def scrub(text):
    return text.replace(TOKEN, "***") if TOKEN else text


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message

    def __str__(self):
        return f"HTTP {self.status}: {self.message}"


def request(path, params=None, retries=4):
    global calls
    q = dict(params or {})
    q["token"] = TOKEN
    url = f"{BASE}/{path}?{urllib.parse.urlencode(q)}"
    last = None
    for attempt in range(1, retries + 1):
        calls += 1
        time.sleep(PAUSE)
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = scrub(e.read().decode("utf-8", "replace"))[:300]
            last = ApiError(e.code, body)
            if e.code in (401, 403):
                fail("o RD recusou o token (401/403). Confira o secret RD_CRM_TOKEN.")
            if e.code == 429:
                time.sleep(30)
                continue
            if e.code < 500:
                raise last
        except (urllib.error.URLError, TimeoutError) as e:
            last = ApiError(0, scrub(str(e)))
        if attempt < retries:
            time.sleep(5 * attempt)
    raise last


# ----------------------------------------------------------------- criptografia

def _kdf(salt, iterations):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    return PBKDF2HMAC(hashes.SHA256(), 32, salt, iterations).derive(PASSWORD.encode("utf-8"))


def encrypt(obj):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv, it = os.urandom(16), os.urandom(12), 250_000
    ct = AESGCM(_kdf(salt, it)).encrypt(iv, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), None)
    b = lambda x: base64.b64encode(x).decode("ascii")
    return {"v": 1, "alg": "AES-256-GCM", "kdf": "PBKDF2-SHA256", "iter": it, "salt": b(salt), "iv": b(iv), "data": b(ct)}


def save(state):
    DATA.mkdir(exist_ok=True)
    if PASSWORD:
        STATE_ENC.write_text(json.dumps(encrypt(state)), encoding="utf-8")
        STATE_PLAIN.unlink(missing_ok=True)
    else:
        STATE_PLAIN.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


# ------------------------------------------------------------------- leitura

def as_list(resp, *keys):
    if isinstance(resp, list):
        return resp
    for k in keys:
        if isinstance(resp, dict) and isinstance(resp.get(k), list):
            return resp[k]
    return []


def name_of(obj):
    if isinstance(obj, dict):
        return obj.get("name") or obj.get("label") or obj.get("title")
    if isinstance(obj, str):
        return obj
    return None


def day(value):
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            d = datetime.strptime(v.replace("Z", "+0000") if fmt.endswith("%z") else v, fmt)
            if d.tzinfo:
                d = d.astimezone(timezone(timedelta(hours=-3)))
            return d.date().isoformat()
        except ValueError:
            continue
    return v[:10] if len(v) >= 10 and v[4] == "-" else None


def fetch_stages():
    stages = {}
    try:
        resp = request("deal_stages", {"limit": 200})
    except ApiError as e:
        log(f"não consegui ler as etapas: {e}")
        return stages
    for i, s in enumerate(as_list(resp, "deal_stages", "data")):
        stages[str(s.get("id") or s.get("_id"))] = {
            "name": s.get("name"),
            "order": s.get("order", i),
            "pipeline": name_of(s.get("deal_pipeline")) or s.get("deal_pipeline_id"),
        }
    log(f"{len(stages)} etapas")
    return stages


def custom_fields(deal):
    out = {}
    for f in deal.get("deal_custom_fields") or []:
        label = name_of(f.get("custom_field")) or f.get("label") or ""
        out[label.strip()] = f.get("value")
    return out


def pick_field(fields, *needles):
    for label, value in fields.items():
        low = label.lower()
        if any(n in low for n in needles) and value not in (None, "", []):
            return value if not isinstance(value, list) else ", ".join(map(str, value))
    return None


def compact(deal, stages):
    stage = deal.get("deal_stage") or {}
    sid = str(stage.get("id") or stage.get("_id") or deal.get("deal_stage_id") or "")
    fields = custom_fields(deal)
    win = deal.get("win")
    row = {
        "id": str(deal.get("id") or deal.get("_id")),
        "cr": day(deal.get("created_at")),
        "cl": day(deal.get("closed_at")),
        "w": True if win is True else False if win is False else None,
        "h": bool(deal.get("hold")),
        "st": sid,
        "stn": stage.get("name") or (stages.get(sid) or {}).get("name"),
        "src": name_of(deal.get("deal_source")),
        "cmp": name_of(deal.get("campaign")),
        "pr": pick_field(fields, "praça", "praca", "cidade", "unidade"),
        "cu": pick_field(fields, "curso", "mba", "programa", "produto"),
        "prd": ", ".join(filter(None, (name_of(p) or name_of((p or {}).get("product")) for p in (deal.get("deal_products") or []))))[:200] or None,
        "dv": day(pick_field(fields, "data da venda", "data venda", "matrícula", "matricula")),
        "lr": name_of(deal.get("deal_lost_reason")),
        "rt": deal.get("rating"),
        "la": day(deal.get("last_activity_at") or deal.get("interactions_updated_at") or deal.get("updated_at")),
        "v": deal.get("amount_total") or None,
    }
    if POR_CONSULTOR:
        row["u"] = name_of(deal.get("user"))
    return {k: v for k, v in row.items() if v not in (None, "", False) or k == "w"}


LABELS = {}


def log_field_summary(deals):
    """Mostra (só nomes e contagens, nunca valores) quais campos personalizados vieram preenchidos."""
    if LABELS:
        top = sorted(LABELS.items(), key=lambda kv: -kv[1])[:25]
        log("campos personalizados preenchidos: " + "; ".join(f"{k} ({v})" for k, v in top))
    else:
        log("nenhum campo personalizado preenchido nos negócios")
    rows = list(deals.values())
    n = len(rows) or 1
    def cnt(key):
        return sum(1 for r in rows if r.get(key))
    log(f"com praça/cidade: {cnt('pr')} · com data da venda: {cnt('dv')} · com fonte: {cnt('src')} · com campanha: {cnt('cmp')} · com curso: {cnt('cu')} · com produto: {cnt('prd')} (de {n})")
    srcs = {}
    for r in rows:
        k = r.get("src") or "(sem fonte)"
        srcs[k] = srcs.get(k, 0) + 1
    log("fontes: " + "; ".join(f"{k} ({v})" for k, v in sorted(srcs.items(), key=lambda kv: -kv[1])[:20]))


def fetch_deals(stages, today):
    """Busca mês a mês (a API devolve no máximo 10 mil negócios por listagem)."""
    deals, sample_logged = {}, False
    start = date(HIST_START.year, HIST_START.month, 1)
    while start <= today:
        nxt = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        end = min(nxt - timedelta(days=1), today)
        page, next_token, got = 1, None, 0
        while True:
            params = {"limit": PAGE_LIMIT, "created_at_period": "true",
                      "start_date": f"{start.isoformat()}T00:00:00", "end_date": f"{end.isoformat()}T23:59:59"}
            if next_token:
                params["next_page"] = next_token
            else:
                params["page"] = page
            resp = request("deals", params)
            items = as_list(resp, "deals", "data")
            if items and not sample_logged:
                d0 = items[0]
                log("campos do negócio: " + ", ".join(sorted(d0.keys())))
                sample_logged = True
            for d in items:
                for lb, val in custom_fields(d).items():
                    if lb and val not in (None, "", []):
                        LABELS[lb] = LABELS.get(lb, 0) + 1
                row = compact(d, stages)
                deals[row["id"]] = row
            got += len(items)
            more = resp.get("has_more") if isinstance(resp, dict) else None
            next_token = resp.get("next_page") if isinstance(resp, dict) else None
            if not items or (more is False) or (more is None and not next_token and len(items) < PAGE_LIMIT):
                break
            page += 1
            if page > 60:
                log(f"aviso: {start:%m/%Y} tem mais de {60 * PAGE_LIMIT} negócios; parte pode ter ficado de fora")
                break
        log(f"{start:%m/%Y}: {got} negócios")
        start = nxt
    return deals


def main():
    if not TOKEN:
        fail("defina o secret RD_CRM_TOKEN")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    today = now.astimezone(timezone(timedelta(hours=-3))).date()
    stages = fetch_stages()
    try:
        deals = fetch_deals(stages, today)
    except ApiError as e:
        fail(f"não consegui ler os negócios ({e})")
    log_field_summary(deals)
    rows = sorted(deals.values(), key=lambda r: r.get("cr") or "")
    state = {
        "version": 1,
        "updated_at": now.isoformat(),
        "today": today.isoformat(),
        "stages": stages,
        "por_consultor": POR_CONSULTOR,
        "deals": rows,
    }
    save(state)
    won = sum(1 for r in rows if r.get("w") is True)
    lost = sum(1 for r in rows if r.get("w") is False)
    log(f"ok: {len(rows)} negócios desde {HIST_START:%m/%Y} · {won} vendas · {lost} perdas · "
        f"{len(rows) - won - lost} em andamento · {calls} chamadas · {'criptografado' if PASSWORD ? 'criptografado' : 'SEM senha'}")


if __name__ == "__main__":
    main()
