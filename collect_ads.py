#!/usr/bin/env python3
"""
Coleta os dados dos anúncios (Meta Ads) e grava o estado do painel de campanhas.

Grava em data/:
  ads.enc.json        -> estado criptografado (quando DASHBOARD_PASSWORD existe)
  ads.json            -> estado sem criptografia (sem senha)
  ads/thumbs/*.jpg    -> miniaturas dos criativos (os links da Meta expiram,
                         por isso as imagens são baixadas e guardadas)

Variáveis de ambiente (GitHub Secrets):
  AD_ACCOUNT_ID       ID da conta de anúncios (com ou sem "act_")      obrigatório
  META_ADS_TOKEN      token com ads_read (se não existir, usa IG_ACCESS_TOKEN)
  DASHBOARD_PASSWORD  senha do painel (a mesma do painel do Instagram)

Opcional: GRAPH_VERSION (padrão v26.0)
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

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
THUMBS = DATA / "ads" / "thumbs"
STATE_PLAIN = DATA / "ads.json"
STATE_ENC = DATA / "ads.enc.json"

TOKEN = (os.environ.get("META_ADS_TOKEN") or os.environ.get("IG_ACCESS_TOKEN") or "").strip()
ACCOUNT = os.environ.get("AD_ACCOUNT_ID", "").strip().replace("act_", "")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
VERSION = os.environ.get("GRAPH_VERSION", "v26.0").strip()
BASE = f"https://graph.facebook.com/{VERSION}"
ACT = f"act_{ACCOUNT}"

BACKFILL_DAYS = 180      # histórico buscado na primeira coleta
REFRESH_DAYS = 7         # últimos dias sempre atualizados (a Meta ajusta a atribuição)
KEEP_DAYS = 400
PERIODS = (7, 30, 90)
CHUNK_DAYS = 30
MAX_THUMBS_PER_RUN = 120

# Tipos de ação que contam como "contato"
LEAD_FALLBACK = ["onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead", "leadgen_grouped", "onsite_web_lead"]
MSG_TYPES = ["onsite_conversion.messaging_conversation_started_7d"]

calls = 0


def log(msg):
    print(f"[anúncios] {msg}", flush=True)


def fail(msg):
    log(f"ERRO: {msg}")
    sys.exit(1)


def scrub(text):
    return text.replace(TOKEN, "***") if TOKEN else text


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def __str__(self):
        return f"HTTP {self.status} - código {self.code}: {self.message}"


def request(url, params=None, retries=3):
    global calls
    if params is not None:
        url = f"{url}?{urllib.parse.urlencode({**params, 'access_token': TOKEN})}"
    last = None
    for attempt in range(1, retries + 1):
        calls += 1
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                err = json.loads(body).get("error", {})
            except ValueError:
                err = {"message": body[:300]}
            last = ApiError(e.code, err.get("code"), scrub(str(err.get("message", ""))))
            # 1, 2, 4, 17, 32, 613, 80000+ = instabilidade/limite de chamadas: vale repetir
            code = err.get("code") or 0
            if e.code < 500 and code not in (1, 2, 4, 17, 32, 613) and not (80000 <= code < 81000):
                raise last
        except (urllib.error.URLError, TimeoutError) as e:
            last = ApiError(0, None, scrub(str(e)))
        if attempt < retries:
            time.sleep(6 * attempt)
    raise last


def soft(path, params=None):
    try:
        url = path if path.startswith("http") else f"{BASE}/{path}"
        return request(url, params), None
    except ApiError as e:
        return None, e


def paged(path, params, max_pages=60):
    out, url, p = [], f"{BASE}/{path}", params
    for _ in range(max_pages):
        resp = request(url, p)
        out.extend(resp.get("data", []))
        nxt = resp.get("paging", {}).get("next")
        if not nxt:
            break
        url, p = nxt, None
    return out


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


def decrypt(env):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    d = lambda x: base64.b64decode(x)
    pt = AESGCM(_kdf(d(env["salt"]), env["iter"])).decrypt(d(env["iv"]), d(env["data"]), None)
    return json.loads(pt.decode("utf-8"))


def load_state():
    if STATE_ENC.exists():
        if not PASSWORD:
            fail("existe data/ads.enc.json mas o secret DASHBOARD_PASSWORD não está definido")
        try:
            return decrypt(json.loads(STATE_ENC.read_text(encoding="utf-8")))
        except Exception:
            fail("não foi possível abrir data/ads.enc.json com a senha atual (a DASHBOARD_PASSWORD mudou?)")
    if STATE_PLAIN.exists():
        try:
            return json.loads(STATE_PLAIN.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def save_state(state):
    DATA.mkdir(exist_ok=True)
    if PASSWORD:
        STATE_ENC.write_text(json.dumps(encrypt(state)), encoding="utf-8")
        STATE_PLAIN.unlink(missing_ok=True)
    else:
        STATE_PLAIN.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


# ---------------------------------------------------------------- utilidades

def parse_actions(actions):
    a = {}
    for x in actions or []:
        try:
            a[x.get("action_type")] = float(x.get("value", 0) or 0)
        except (TypeError, ValueError):
            pass
    leads = a.get("lead")
    if leads is None:
        leads = max([a.get(k, 0) for k in LEAD_FALLBACK] + [0])
    msgs = max([a.get(k, 0) for k in MSG_TYPES] + [0])
    return int(round(leads)), int(round(msgs)), int(round(a.get("landing_page_view", 0)))


def compact(row):
    """Linha de insights -> dicionário curto (omite zeros para economizar espaço)."""
    l, m, v = parse_actions(row.get("actions"))
    out = {
        "s": round(float(row.get("spend", 0) or 0), 2),
        "i": int(float(row.get("impressions", 0) or 0)),
        "c": int(float(row.get("inline_link_clicks", 0) or 0)),
        "l": l, "m": m, "v": v,
    }
    return {k: v for k, v in out.items() if v}


def tr(since, until):
    return json.dumps({"since": since.isoformat(), "until": until.isoformat()})


def is_permission_error(err):
    return err is not None and (err.code in (10, 190, 200, 270, 272, 278, 294) or err.status == 403)


# ------------------------------------------------------------------- coletas

def fetch_account():
    try:
        return request(f"{BASE}/{ACT}", {"fields": "name,currency,timezone_name,account_status,amount_spent"})
    except ApiError as e:
        if is_permission_error(e) or e.code == 100:
            fail(f"o token não consegue ler a conta de anúncios {ACT} ({e.message}). Confira: (1) o AD_ACCOUNT_ID; "
                 "(2) a conta de anúncios foi atribuída ao usuário do sistema; (3) o token foi gerado com ads_read.")
        fail(f"não consegui ler a conta de anúncios ({e})")


def update_daily(state, today):
    daily = state.setdefault("daily", {})
    meta = state.setdefault("ads", {})
    camps = state.setdefault("campaigns", {})
    if daily:
        last = max(date.fromisoformat(k) for k in daily)
        since = min(today - timedelta(days=REFRESH_DAYS - 1), last + timedelta(days=1))
    else:
        since = today - timedelta(days=BACKFILL_DAYS - 1)
    fields = "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,spend,impressions,inline_link_clicks,actions"
    start = since
    while start <= today:
        end = min(start + timedelta(days=CHUNK_DAYS - 1), today)
        rows = paged(f"{ACT}/insights", {"level": "ad", "time_increment": 1, "time_range": tr(start, end),
                                         "fields": fields, "limit": 500})
        d = start
        while d <= end:
            daily.pop(d.isoformat(), None)
            d += timedelta(days=1)
        for r in rows:
            day = r.get("date_start")
            ad = r.get("ad_id")
            if not day or not ad:
                continue
            row = compact(r)
            if row:
                daily.setdefault(day, {})[ad] = row
            m = meta.setdefault(ad, {})
            m["name"] = r.get("ad_name") or m.get("name")
            m["campaign_id"] = r.get("campaign_id") or m.get("campaign_id")
            m["adset_id"] = r.get("adset_id") or m.get("adset_id")
            m["adset_name"] = r.get("adset_name") or m.get("adset_name")
            if r.get("campaign_id"):
                c = camps.setdefault(r["campaign_id"], {})
                c.setdefault("name", r.get("campaign_name"))
        log(f"insights diários {start} → {end}: {len(rows)} linhas")
        start = end + timedelta(days=1)
    for k in sorted(daily)[:-KEEP_DAYS]:
        del daily[k]


def update_campaigns(state):
    camps = state.setdefault("campaigns", {})
    fields = "id,name,objective,effective_status,daily_budget,lifetime_budget,start_time,stop_time,created_time"
    try:
        rows = paged(f"{ACT}/campaigns", {"fields": fields, "limit": 200})
    except ApiError as e:
        log(f"não consegui listar campanhas: {e}")
        return
    for c in rows:
        cur = camps.setdefault(c["id"], {})
        cur.update({
            "name": c.get("name"),
            "objective": c.get("objective"),
            "status": c.get("effective_status"),
            "daily_budget": float(c["daily_budget"]) / 100 if c.get("daily_budget") else None,
            "lifetime_budget": float(c["lifetime_budget"]) / 100 if c.get("lifetime_budget") else None,
            "start": c.get("start_time"),
            "stop": c.get("stop_time"),
        })


def _creative_text(cr):
    oss = cr.get("object_story_spec") or {}
    ld, vd = oss.get("link_data") or {}, oss.get("video_data") or {}
    afs = cr.get("asset_feed_spec") or {}
    first = lambda arr: (arr or [{}])[0].get("text") if arr else None
    title = cr.get("title") or ld.get("name") or vd.get("title") or first(afs.get("titles"))
    body = cr.get("body") or ld.get("message") or vd.get("message") or first(afs.get("bodies"))
    return title, body


def update_ad_details(state, today):
    meta = state.setdefault("ads", {})
    daily = state.get("daily", {})
    recent_cut = (today - timedelta(days=30)).isoformat()
    spent_recent, spent_any = set(), set()
    for day, ads in daily.items():
        for ad, row in ads.items():
            if row.get("s") or row.get("i"):
                spent_any.add(ad)
                if day >= recent_cut:
                    spent_recent.add(ad)
    ids = sorted(spent_recent | {a for a in spent_any if "creative_id" not in meta.get(a, {}) and not meta.get(a, {}).get("gone")})
    if not ids:
        return
    cr_fields = ("id,title,body,thumbnail_url,image_url,object_type,video_id,call_to_action_type,"
                 "instagram_permalink_url,object_story_spec,asset_feed_spec")
    base_fields = "id,name,effective_status,campaign_id,adset_id,created_time"
    variants = [f"{base_fields},creative.thumbnail_width(480).thumbnail_height(480){{{cr_fields}}}",
                f"{base_fields},creative{{{cr_fields}}}"]
    use = 0
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        resp, err = soft(BASE + "/", {"ids": ",".join(chunk), "fields": variants[use]})
        if err is not None and err.code == 100 and use == 0:
            use = 1
            resp, err = soft(BASE + "/", {"ids": ",".join(chunk), "fields": variants[use]})
        if err is not None:
            # anúncio apagado derruba o lote inteiro: tenta um a um
            resp = {}
            for ad in chunk:
                r1, e1 = soft(ad, {"fields": variants[use]})
                if e1 is None:
                    resp[ad] = r1
                elif e1.code == 100:
                    meta.setdefault(ad, {})["gone"] = True
        for ad, a in (resp or {}).items():
            m = meta.setdefault(ad, {})
            cr = a.get("creative") or {}
            title, body = _creative_text(cr)
            m.update({
                "name": a.get("name") or m.get("name"),
                "status": a.get("effective_status"),
                "campaign_id": a.get("campaign_id") or m.get("campaign_id"),
                "adset_id": a.get("adset_id") or m.get("adset_id"),
                "created": a.get("created_time"),
                "creative_id": cr.get("id"),
                "title": (title or "")[:200] or None,
                "body": (body or "")[:1500] or None,
                "cta": cr.get("call_to_action_type"),
                "kind": "video" if cr.get("video_id") or (cr.get("object_type") == "VIDEO") else "image",
                "permalink": cr.get("instagram_permalink_url"),
                "image_src": cr.get("image_url") or cr.get("thumbnail_url"),
            })
    log(f"detalhes de {len(ids)} anúncios atualizados")


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (painel-edexlab)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        ctype = r.headers.get("Content-Type", "")
        data = r.read()
    if not ctype.startswith("image/") or len(data) < 200:
        raise ValueError(f"não é imagem ({ctype})")
    path.write_bytes(data)


def update_thumbs(state):
    THUMBS.mkdir(parents=True, exist_ok=True)
    done = 0
    for ad, m in state.get("ads", {}).items():
        cid, src = m.get("creative_id"), m.get("image_src")
        if not cid:
            continue
        path = THUMBS / f"{cid}.jpg"
        if path.exists():
            m["thumb"] = f"data/ads/thumbs/{cid}.jpg"
            continue
        if not src or done >= MAX_THUMBS_PER_RUN or m.get("thumb_failed", 0) >= 3:
            continue
        try:
            download(src, path)
            m["thumb"] = f"data/ads/thumbs/{cid}.jpg"
            m.pop("thumb_failed", None)
        except Exception as e:  # noqa: BLE001
            m["thumb_failed"] = m.get("thumb_failed", 0) + 1
            log(f"miniatura do anúncio {ad} não baixou: {e}")
        done += 1
    if done:
        log(f"{done} miniaturas processadas")


def update_periods(state, yday):
    periods = {}
    for n in PERIODS:
        since = yday - timedelta(days=n - 1)
        p = {"since": since.isoformat(), "until": yday.isoformat(), "account": {}, "campaign": {}, "ad": {}}
        resp, err = soft(f"{ACT}/insights", {"time_range": tr(since, yday), "fields": "reach,frequency,impressions"})
        if err is None and resp.get("data"):
            r = resp["data"][0]
            p["account"] = {"reach": int(float(r.get("reach", 0) or 0)), "frequency": float(r.get("frequency", 0) or 0)}
        for level in ("campaign", "ad"):
            try:
                rows = paged(f"{ACT}/insights", {"level": level, "time_range": tr(since, yday),
                                                 "fields": f"{level}_id,reach,frequency", "limit": 500})
            except ApiError:
                rows = []
            for r in rows:
                p[level][r.get(f"{level}_id")] = [int(float(r.get("reach", 0) or 0)), round(float(r.get("frequency", 0) or 0), 2)]
        periods[str(n)] = p
    state["periods"] = periods


BREAKDOWNS = [
    ("age_gender", "age,gender"),
    ("region", "region"),
    ("placement", "publisher_platform,platform_position"),
    ("hour", "hourly_stats_aggregated_by_advertiser_time_zone"),
]


def update_breakdowns(state, yday):
    out = {}
    for n in PERIODS:
        since = yday - timedelta(days=n - 1)
        per = {}
        for key, bd in BREAKDOWNS:
            try:
                rows = paged(f"{ACT}/insights", {"time_range": tr(since, yday), "breakdowns": bd,
                                                 "fields": "spend,impressions,inline_link_clicks,actions", "limit": 500})
            except ApiError as e:
                log(f"quebra '{key}' indisponível: {e.message}")
                continue
            dims = bd.split(",")
            res = []
            for r in rows:
                c = compact(r)
                vals = [r.get(d, "") for d in dims]
                if key == "hour":
                    try:
                        vals = [int(str(vals[0])[:2])]
                    except ValueError:
                        continue
                res.append(vals + [c.get("s", 0), c.get("i", 0), c.get("c", 0), c.get("l", 0), c.get("m", 0)])
            per[key] = res
        out[str(n)] = per
    state["breakdowns"] = out


# ---------------------------------------------------------------------- main

def main():
    if not ACCOUNT:
        fail("defina o secret AD_ACCOUNT_ID")
    if not TOKEN:
        fail("defina o secret IG_ACCESS_TOKEN (ou META_ADS_TOKEN) com a permissão ads_read")
    state = load_state()
    acc = fetch_account()
    tz = timezone(timedelta(hours=-3))
    if ZoneInfo and acc.get("timezone_name"):
        try:
            tz = ZoneInfo(acc["timezone_name"])
        except Exception:  # noqa: BLE001
            pass
    now = datetime.now(timezone.utc).replace(microsecond=0)
    today = now.astimezone(tz).date()
    yday = today - timedelta(days=1)

    try:
        update_daily(state, today)
    except ApiError as e:
        fail(f"não consegui ler os resultados dos anúncios ({e})")
    update_campaigns(state)
    update_ad_details(state, today)
    update_thumbs(state)
    update_periods(state, yday)
    update_breakdowns(state, yday)

    state.update({
        "version": 1,
        "updated_at": now.isoformat(),
        "today": today.isoformat(),
        "yesterday": yday.isoformat(),
        "account": {"id": ACT, "name": acc.get("name"), "currency": acc.get("currency") or "BRL",
                    "timezone": acc.get("timezone_name"), "status": acc.get("account_status")},
    })
    save_state(state)
    spend = sum(r.get("s", 0) for ads in state["daily"].values() for r in ads.values())
    log(f"ok: {acc.get('name')} · {len(state['daily'])} dias · {len(state.get('ads', {}))} anúncios · "
        f"{len(state.get('campaigns', {}))} campanhas · R$ {spend:,.2f} no histórico · {calls} chamadas · "
        f"{'criptografado' if PASSWORD else 'SEM senha'}")


if __name__ == "__main__":
    main()
