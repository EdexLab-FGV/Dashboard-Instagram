#!/usr/bin/env python3
"""
Coleta dados da conta do Instagram pela Graph API e grava o estado do dashboard.

Grava em data/:
  state.enc.json  -> estado completo criptografado (quando DASHBOARD_PASSWORD existe)
  state.json      -> estado sem criptografia (só quando NÃO há senha; nesse caso
                     comentários e @ de pessoas NÃO são coletados)

Variáveis de ambiente (GitHub Secrets):
  IG_ACCESS_TOKEN     token de acesso (obrigatório)
  IG_USER_ID          ID da conta do Instagram (obrigatório)
  DASHBOARD_PASSWORD  senha do dashboard (recomendado)

Opcionais:
  GRAPH_VERSION  (padrão v26.0)    GRAPH_HOST (padrão graph.facebook.com)
  MAX_POSTS      (padrão 100)
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATE_PLAIN = DATA / "state.json"
STATE_ENC = DATA / "state.enc.json"
LEGACY_HISTORY = DATA / "history.json"
LEGACY_POSTS = DATA / "posts.json"

TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()
USER_ID = os.environ.get("IG_USER_ID", "").strip()
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
VERSION = os.environ.get("GRAPH_VERSION", "v26.0").strip()
HOST = os.environ.get("GRAPH_HOST", "graph.facebook.com").strip()
MAX_POSTS = int(os.environ.get("MAX_POSTS", "100"))

BASE = f"https://{HOST}/{VERSION}"
BRT = timezone(timedelta(hours=-3))  # horário de Brasília
BACKFILL_DAYS = 28          # dias de histórico buscados na 1ª coleta
REFRESH_DAYS = 3            # últimos dias sempre atualizados (a Meta consolida com atraso)
POST_INSIGHT_WINDOW = 45    # posts mais novos que isso têm insights atualizados toda vez
MAX_POST_INSIGHT_CALLS = 60
COMMENT_WINDOW = 60
MAX_COMMENT_POSTS = 40
MAX_HISTORY = 5000

ACCOUNT_METRICS = ["reach", "profile_views", "website_clicks", "accounts_engaged", "views"]
POST_METRICS = ["reach", "saved", "shares", "profile_visits", "follows", "total_interactions", "views"]

# Palavras que indicam interesse comercial em comentários
INTENT_RULES = [
    ("preço", r"\b(valor|valores|pre[cç]os?|quanto custa|custa quanto|mensalidade|investimento|parcel\w*|boleto)\b"),
    ("inscrição", r"(inscri[cç]|inscrev|matr[ií]cul|processo seletivo|\bvagas?\b|como (eu )?(fa[cç]o|faz) (pra|para) (entrar|participar|fazer))"),
    ("bolsa/desconto", r"\b(bolsas?|desconto|cupom|condi[cç][aãõo]+ especia)"),
    ("quer saber mais", r"(mais info|informa[cç](ão|oes|ões|ao)|me chama|chama no (direct|dm|privado|pv)|\binbox\b|\bdm\b|tenho interesse|interessad|quero (fazer|saber|participar|me inscrever)|como funciona|quando come[cç]a|\bin[ií]cio\b|dura[cç][aã]o|pr[oó]xima turma|semipresencial|presencial|\bead\b|online\?)"),
]
INTENT_RES = [(label, re.compile(rx, re.IGNORECASE)) for label, rx in INTENT_RULES]

calls = 0


def log(msg):
    print(f"[coleta] {msg}", flush=True)


def fail(msg):
    log(f"ERRO: {msg}")
    sys.exit(1)


def scrub(text):
    return text.replace(TOKEN, "***") if TOKEN else text


class ApiError(Exception):
    def __init__(self, status, code, subcode, message):
        super().__init__(message)
        self.status, self.code, self.subcode, self.message = status, code, subcode, message

    def __str__(self):
        return f"HTTP {self.status} - código {self.code}: {self.message}"


def request(url, params=None, retries=3):
    """Chama a API. Lança ApiError em erro da Meta; repete em falhas de rede/5xx."""
    global calls
    if params is not None:
        url = f"{url}?{urllib.parse.urlencode({**params, 'access_token': TOKEN})}"
    last = None
    for attempt in range(1, retries + 1):
        calls += 1
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                err = json.loads(body).get("error", {})
            except ValueError:
                err = {"message": body[:300]}
            last = ApiError(e.code, err.get("code"), err.get("error_subcode"), scrub(str(err.get("message", ""))))
            if e.code < 500 and err.get("code") not in (1, 2, 4, 17, 32, 613):
                raise last
        except (urllib.error.URLError, TimeoutError) as e:
            last = ApiError(0, None, None, scrub(str(e)))
        if attempt < retries:
            time.sleep(4 * attempt)
    raise last


def soft(path_or_url, params=None):
    """Versão tolerante: devolve (dados, erro)."""
    try:
        url = path_or_url if path_or_url.startswith("http") else f"{BASE}/{path_or_url}"
        return request(url, params), None
    except ApiError as e:
        return None, e


def is_permission_error(err):
    return err is not None and (err.code in (10, 200, 190) or err.status == 403)


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


# --------------------------------------------------------------------- estado

def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def load_state():
    if STATE_ENC.exists():
        if not PASSWORD:
            fail("existe data/state.enc.json mas o secret DASHBOARD_PASSWORD não está definido")
        try:
            return decrypt(read_json(STATE_ENC, {}))
        except Exception:
            fail("não foi possível abrir os dados com a senha atual. Se você trocou a "
                 "DASHBOARD_PASSWORD, volte a senha antiga ou apague data/state.enc.json "
                 "(isso apaga o histórico).")
    state = read_json(STATE_PLAIN, None)
    if state:
        return state
    # migração da versão 1 (history.json + posts.json)
    state = {"version": 2, "history": read_json(LEGACY_HISTORY, []), "posts": [], "daily": {},
             "comments": [], "flags": {}}
    old = read_json(LEGACY_POSTS, {})
    for p in old.get("posts", []):
        state["posts"].append(p)
    if state["history"]:
        log(f"migrando {len(state['history'])} leituras da versão anterior")
    return state


def save_state(state):
    DATA.mkdir(exist_ok=True)
    if PASSWORD:
        STATE_ENC.write_text(json.dumps(encrypt(state)), encoding="utf-8")
        STATE_PLAIN.unlink(missing_ok=True)
    else:
        STATE_PLAIN.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    LEGACY_HISTORY.unlink(missing_ok=True)
    LEGACY_POSTS.unlink(missing_ok=True)


# ------------------------------------------------------------------- coletas

def fetch_profile():
    try:
        p = request(f"{BASE}/{USER_ID}", {
            "fields": "username,name,biography,website,profile_picture_url,followers_count,follows_count,media_count"})
    except ApiError as e:
        fail(f"não consegui ler o perfil ({e}). Confira o IG_USER_ID e se o token tem acesso à conta.")
    if "followers_count" not in p:
        fail("a resposta não trouxe followers_count; confira as permissões do token")
    return p


def fetch_media():
    fields = ("id,caption,media_type,media_product_type,timestamp,permalink,thumbnail_url,"
              "media_url,like_count,comments_count")
    out = []
    try:
        page = request(f"{BASE}/{USER_ID}/media", {"fields": fields, "limit": 50})
        while True:
            out.extend(page.get("data", []))
            nxt = page.get("paging", {}).get("next")
            if len(out) >= MAX_POSTS or not nxt:
                break
            page = request(nxt)
    except ApiError as e:
        fail(f"não consegui ler os posts ({e})")
    return out[:MAX_POSTS]


def media_kind(p):
    if p.get("media_product_type") == "REELS":
        return "REELS"
    return p.get("media_type", "IMAGE")


def parse_insights(resp):
    vals = {}
    for item in (resp or {}).get("data", []):
        v = None
        if "total_value" in item:
            v = item["total_value"].get("value")
        elif item.get("values"):
            v = item["values"][-1].get("value")
        if isinstance(v, (int, float)):
            vals[item["name"]] = v
    return vals


def discover_post_metrics(flags, kind, media_id):
    """Descobre (uma vez por formato) quais métricas a Meta aceita e guarda em flags."""
    known = flags.setdefault("post_metrics", {})
    if kind in known:
        return known[kind]
    resp, err = soft(f"{media_id}/insights", {"metric": ",".join(POST_METRICS)})
    if err is None:
        known[kind] = POST_METRICS
        return POST_METRICS
    if is_permission_error(err):
        flags["post_insights"] = f"sem permissão: {err.message}"
        known[kind] = []
        return []
    good = []
    for m in POST_METRICS:
        _, e = soft(f"{media_id}/insights", {"metric": m})
        if e is None:
            good.append(m)
    known[kind] = good
    log(f"métricas disponíveis para {kind}: {', '.join(good) or 'nenhuma'}")
    return good


def update_post_insights(state, posts, prev_by_id, now):
    flags = state["flags"]
    budget = MAX_POST_INSIGHT_CALLS
    for p in posts:
        old = prev_by_id.get(p["id"], {})
        p["insights"] = old.get("insights", {})
        p["insights_at"] = old.get("insights_at")
        age_days = (now - datetime.fromisoformat(p["timestamp"].replace("+0000", "+00:00"))).days
        if p["insights"] and age_days > POST_INSIGHT_WINDOW:
            continue  # posts antigos: números já estabilizados
        if budget <= 0:
            continue
        metrics = discover_post_metrics(flags, p["type"], p["id"])
        if not metrics:
            continue
        resp, err = soft(f"{p['id']}/insights", {"metric": ",".join(metrics)})
        budget -= 1
        if err is None:
            p["insights"] = parse_insights(resp)
            p["insights_at"] = now.isoformat()
        elif err.code == 100:
            # alguns posts (ex.: anteriores à conta virar comercial) não têm insights
            p["insights_at"] = now.isoformat()


def day_bounds(day):
    start = datetime(day.year, day.month, day.day, tzinfo=BRT)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


def update_daily(state, now):
    flags = state["flags"]
    daily = state.setdefault("daily", {})
    today = now.astimezone(BRT).date()
    days = []
    for i in range(1, BACKFILL_DAYS + 1):
        d = today - timedelta(days=i)
        if i <= REFRESH_DAYS or d.isoformat() not in daily:
            days.append(d)
    if not days:
        return

    metrics = flags.get("account_metrics")
    if metrics is None:
        since, until = day_bounds(days[0])
        base = {"period": "day", "metric_type": "total_value", "since": since, "until": until}
        _, err = soft(f"{USER_ID}/insights", {**base, "metric": ",".join(ACCOUNT_METRICS)})
        if err is None:
            metrics = ACCOUNT_METRICS
        elif is_permission_error(err):
            flags["account_insights"] = f"sem permissão: {err.message}"
            metrics = []
        else:
            metrics = [m for m in ACCOUNT_METRICS
                       if soft(f"{USER_ID}/insights", {**base, "metric": m})[1] is None]
        flags["account_metrics"] = metrics
        log(f"métricas diárias da conta: {', '.join(metrics) or 'nenhuma'}")

    follows_ok = flags.get("follows_metric", True)
    for d in sorted(days):
        since, until = day_bounds(d)
        row = daily.get(d.isoformat(), {})
        if metrics:
            resp, err = soft(f"{USER_ID}/insights", {"metric": ",".join(metrics), "period": "day",
                                                     "metric_type": "total_value", "since": since, "until": until})
            if err is None:
                row.update(parse_insights(resp))
        if follows_ok:
            resp, err = soft(f"{USER_ID}/insights", {"metric": "follows_and_unfollows", "period": "day",
                                                     "metric_type": "total_value", "breakdown": "follow_type",
                                                     "since": since, "until": until})
            if err is None:
                for item in resp.get("data", []):
                    for br in item.get("total_value", {}).get("breakdowns", []):
                        for res in br.get("results", []):
                            dim = (res.get("dimension_values") or [""])[0].upper()
                            if dim == "FOLLOWER":
                                row["follows"] = res.get("value", 0)
                            elif dim == "NON_FOLLOWER":
                                row["unfollows"] = res.get("value", 0)
                row.setdefault("follows", 0)
                row.setdefault("unfollows", 0)
            else:
                follows_ok = False
                flags["follows_metric"] = False
        if row:
            daily[d.isoformat()] = row
    # guarda até ~2 anos
    for k in sorted(daily)[:-730]:
        del daily[k]


def update_demographics(state, now):
    demo = state.get("demographics") or {}
    last = demo.get("fetched_at")
    if last and (now - datetime.fromisoformat(last)).total_seconds() < 20 * 3600:
        return
    out = {"fetched_at": now.isoformat()}
    for breakdown in ("city", "age", "gender"):
        for timeframe in ("this_month", "last_30_days", "last_90_days"):
            resp, err = soft(f"{USER_ID}/insights", {"metric": "follower_demographics", "period": "lifetime",
                                                     "metric_type": "total_value", "timeframe": timeframe,
                                                     "breakdown": breakdown})
            if err is None:
                rows = []
                for item in resp.get("data", []):
                    for br in item.get("total_value", {}).get("breakdowns", []):
                        for res in br.get("results", []):
                            rows.append([" / ".join(res.get("dimension_values", [])), res.get("value", 0)])
                rows.sort(key=lambda r: -r[1])
                out[breakdown] = rows
                break
    # alcance de seguidores x não seguidores nos últimos 28 dias
    end = datetime(now.astimezone(BRT).year, now.astimezone(BRT).month, now.astimezone(BRT).day, tzinfo=BRT)
    resp, err = soft(f"{USER_ID}/insights", {"metric": "reach", "period": "day", "metric_type": "total_value",
                                             "breakdown": "follow_type",
                                             "since": int((end - timedelta(days=28)).timestamp()),
                                             "until": int(end.timestamp())})
    if err is None:
        split = {}
        for item in resp.get("data", []):
            for br in item.get("total_value", {}).get("breakdowns", []):
                for res in br.get("results", []):
                    split[(res.get("dimension_values") or [""])[0].lower()] = res.get("value", 0)
        if split:
            out["reach_split"] = split
    state["demographics"] = out


def detect_intent(text):
    return [label for label, rx in INTENT_RES if rx.search(text or "")]


def update_comments(state, posts, own_username, now):
    flags = state["flags"]
    if not PASSWORD:
        flags["comments"] = "no_password"
        state["comments"] = []
        return
    if flags.get("comments") == "denied" and flags.get("comments_checked_at"):
        # tenta de novo uma vez por semana, caso a permissão tenha sido liberada
        if (now - datetime.fromisoformat(flags["comments_checked_at"])).days < 7:
            return
    by_id = {c["id"]: c for c in state.get("comments", [])}
    fetched = set(flags.get("comments_fetched", []))
    targets = []
    for p in posts:
        if not p.get("comments"):
            continue
        age = (now - datetime.fromisoformat(p["timestamp"].replace("+0000", "+00:00"))).days
        if age <= COMMENT_WINDOW or p["id"] not in fetched:
            targets.append(p)
    targets = targets[:MAX_COMMENT_POSTS]

    for p in targets:
        url, params = f"{BASE}/{p['id']}/comments", {"fields": "id,text,timestamp,username,like_count", "limit": 50}
        pages = 0
        while url and pages < 5:
            resp, err = soft(url, params)
            pages += 1
            if err is not None:
                if is_permission_error(err) or err.code == 100:
                    flags["comments"] = "denied"
                    flags["comments_checked_at"] = now.isoformat()
                    flags["comments_error"] = err.message
                    log(f"comentários indisponíveis com este token: {err.message}")
                    state["comments"] = list(by_id.values())
                    return
                break
            for c in resp.get("data", []):
                user = c.get("username") or ""
                if not user or user.lower() == (own_username or "").lower():
                    continue
                text = (c.get("text") or "")[:500]
                by_id[c["id"]] = {"id": c["id"], "media_id": p["id"], "user": user, "text": text,
                                  "ts": c.get("timestamp"), "likes": c.get("like_count", 0) or 0,
                                  "intent": detect_intent(text)}
            nxt = resp.get("paging", {}).get("next")
            url, params = (nxt, None) if nxt else (None, None)
        fetched.add(p["id"])
    flags["comments"] = "ok"
    flags["comments_checked_at"] = now.isoformat()
    flags.pop("comments_error", None)
    flags["comments_fetched"] = sorted(fetched)[-500:]
    comments = sorted(by_id.values(), key=lambda c: c.get("ts") or "", reverse=True)
    state["comments"] = comments[:20000]


def update_post_history(state, posts, now):
    """Guarda como cada post evolui nos primeiros 7 dias (para comparar posts novos de forma justa)."""
    hist = state.setdefault("post_hist", {})
    alive = set()
    for p in posts:
        ts = datetime.fromisoformat(p["timestamp"].replace("+0000", "+00:00"))
        age_h = (now - ts).total_seconds() / 3600
        if age_h <= 60 * 24:
            alive.add(p["id"])
        if age_h > 7 * 24:
            continue
        ins = p.get("insights") or {}
        snap = [round(age_h, 1), p.get("likes", 0), p.get("comments", 0), ins.get("reach"), ins.get("saved"), ins.get("shares")]
        rows = hist.setdefault(p["id"], [])
        if not rows or snap[0] - rows[-1][0] >= 1:
            rows.append(snap)
    for pid in list(hist):
        if pid not in alive:
            del hist[pid]


# ---------------------------------------------------------------------- main

def main():
    if not TOKEN or not USER_ID:
        fail("defina os secrets IG_ACCESS_TOKEN e IG_USER_ID")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    state = load_state()
    state.setdefault("flags", {})
    state.setdefault("history", [])
    prev_posts = {p["id"]: p for p in state.get("posts", [])}

    profile = fetch_profile()
    raw = fetch_media()

    posts = []
    for p in raw:
        posts.append({
            "id": p.get("id"),
            "caption": (p.get("caption") or "")[:1200],
            "type": media_kind(p),
            "timestamp": p.get("timestamp"),
            "permalink": p.get("permalink"),
            "thumb": p.get("thumbnail_url") or p.get("media_url"),
            "likes": p.get("like_count", 0) or 0,
            "comments": p.get("comments_count", 0) or 0,
        })

    # curtidas/comentários novos por post desde a última coleta
    gained_likes = gained_comments = 0
    for p in posts:
        old = prev_posts.get(p["id"])
        if old is None:
            if prev_posts:
                gained_likes += p["likes"]
                gained_comments += p["comments"]
        else:
            gained_likes += max(0, p["likes"] - old.get("likes", 0))
            gained_comments += max(0, p["comments"] - old.get("comments", 0))
    last = state["history"][-1] if state["history"] else {}
    state["history"].append({
        "ts": now.isoformat(),
        "followers": profile.get("followers_count", 0),
        "follows": profile.get("follows_count", 0),
        "media_count": profile.get("media_count", 0),
        "likes": sum(p["likes"] for p in posts),
        "comments": sum(p["comments"] for p in posts),
        "likes_gained": last.get("likes_gained", 0) + gained_likes,
        "comments_gained": last.get("comments_gained", 0) + gained_comments,
    })
    state["history"] = state["history"][-MAX_HISTORY:]

    update_post_insights(state, posts, prev_posts, now)
    update_post_history(state, posts, now)
    update_daily(state, now)
    update_demographics(state, now)
    update_comments(state, posts, profile.get("username"), now)

    state.update({
        "version": 2,
        "updated_at": now.isoformat(),
        "profile": {
            "username": profile.get("username"),
            "name": profile.get("name"),
            "biography": profile.get("biography"),
            "website": profile.get("website"),
            "picture": profile.get("profile_picture_url"),
            "followers": profile.get("followers_count", 0),
            "follows": profile.get("follows_count", 0),
            "media_count": profile.get("media_count", 0),
        },
        "posts": posts,
        "encrypted": bool(PASSWORD),
    })
    save_state(state)

    f = state["flags"]
    log(f"ok: @{profile.get('username')} · {profile.get('followers_count')} seguidores · {len(posts)} posts · "
        f"{len(state['daily'])} dias de métricas · {len(state.get('comments', []))} comentários "
        f"(comentários: {f.get('comments')}) · {calls} chamadas à API · "
        f"{'criptografado' if PASSWORD else 'SEM senha'}")


if __name__ == "__main__":
    main()
