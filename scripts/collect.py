#!/usr/bin/env python3
"""
Coleta dados da conta do Instagram pela Graph API e grava em data/.

- data/history.json  -> uma leitura por execução (seguidores, curtidas totais...)
- data/posts.json    -> perfil + posts mais recentes com curtidas e comentários

Variáveis de ambiente obrigatórias (configure como GitHub Secrets):
  IG_ACCESS_TOKEN  token de acesso (nunca coloque no código)
  IG_USER_ID       ID da conta do Instagram

Opcionais:
  GRAPH_VERSION    versão da API (padrão v26.0)
  GRAPH_HOST       graph.facebook.com (padrão) ou graph.instagram.com
  MAX_POSTS        quantos posts recentes buscar (padrão 100)

Só usa a biblioteca padrão do Python, sem dependências.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HISTORY_FILE = DATA / "history.json"
POSTS_FILE = DATA / "posts.json"

TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()
USER_ID = os.environ.get("IG_USER_ID", "").strip()
VERSION = os.environ.get("GRAPH_VERSION", "v26.0").strip()
HOST = os.environ.get("GRAPH_HOST", "graph.facebook.com").strip()
MAX_POSTS = int(os.environ.get("MAX_POSTS", "100"))
MAX_HISTORY = 5000  # ~3,4 anos com coleta a cada 6h


def log(msg):
    print(f"[coleta] {msg}", flush=True)


def fail(msg):
    # Nunca imprime o token. Encerra com erro para o Actions marcar a falha.
    log(f"ERRO: {msg}")
    sys.exit(1)


def scrub(text):
    return text.replace(TOKEN, "***") if TOKEN else text


def get(url, params=None, retries=3):
    if params is not None:
        params = {**params, "access_token": TOKEN}
        url = f"{url}?{urllib.parse.urlencode(params)}"
    last = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                err = json.loads(body).get("error", {})
                detail = f"{err.get('type', '')} {err.get('code', '')}: {err.get('message', body)}"
            except ValueError:
                detail = body[:300]
            last = f"HTTP {e.code} - {scrub(detail)}"
            # Erros de token/permissão não adiantam repetir
            if e.code in (400, 401, 403):
                break
        except (urllib.error.URLError, TimeoutError) as e:
            last = scrub(str(e))
        if attempt < retries:
            time.sleep(5 * attempt)
    fail(f"falha ao chamar a API: {last}")


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def main():
    if not TOKEN or not USER_ID:
        fail("defina os secrets IG_ACCESS_TOKEN e IG_USER_ID")

    base = f"https://{HOST}/{VERSION}"

    profile = get(
        f"{base}/{USER_ID}",
        {"fields": "username,name,profile_picture_url,followers_count,follows_count,media_count"},
    )
    if "followers_count" not in profile:
        fail("a resposta não trouxe followers_count; confira o ID e as permissões")

    fields = "id,caption,media_type,media_product_type,timestamp,permalink,thumbnail_url,media_url,like_count,comments_count"
    posts = []
    page = get(f"{base}/{USER_ID}/media", {"fields": fields, "limit": 50})
    while True:
        posts.extend(page.get("data", []))
        nxt = page.get("paging", {}).get("next")
        if len(posts) >= MAX_POSTS or not nxt:
            break
        page = get(nxt)  # a URL "next" já inclui o token
    posts = posts[:MAX_POSTS]

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    clean_posts = []
    for p in posts:
        mtype = p.get("media_type", "")
        if p.get("media_product_type") == "REELS":
            mtype = "REELS"
        clean_posts.append({
            "id": p.get("id"),
            "caption": (p.get("caption") or "")[:280],
            "type": mtype,
            "timestamp": p.get("timestamp"),
            "permalink": p.get("permalink"),
            "thumb": p.get("thumbnail_url") or p.get("media_url"),
            "likes": p.get("like_count", 0) or 0,
            "comments": p.get("comments_count", 0) or 0,
        })

    total_likes = sum(p["likes"] for p in clean_posts)
    total_comments = sum(p["comments"] for p in clean_posts)

    # Curtidas novas = crescimento por post desde a coleta anterior (+ todas as
    # curtidas de posts novos). Assim, posts antigos saindo da janela de busca
    # não geram "perdas" falsas. Guardamos um contador acumulado.
    history = load_json(HISTORY_FILE, [])
    prev_posts = {p["id"]: p for p in load_json(POSTS_FILE, {}).get("posts", [])}
    gained_likes = gained_comments = 0
    for p in clean_posts:
        old = prev_posts.get(p["id"])
        if old is None:
            if prev_posts:  # na 1ª coleta não conta o histórico inteiro como "novo"
                gained_likes += p["likes"]
                gained_comments += p["comments"]
        else:
            gained_likes += max(0, p["likes"] - old.get("likes", 0))
            gained_comments += max(0, p["comments"] - old.get("comments", 0))
    last = history[-1] if history else {}
    history.append({
        "ts": now,
        "followers": profile.get("followers_count", 0),
        "follows": profile.get("follows_count", 0),
        "media_count": profile.get("media_count", 0),
        "likes": total_likes,
        "comments": total_comments,
        "posts_tracked": len(clean_posts),
        "likes_gained": last.get("likes_gained", 0) + gained_likes,
        "comments_gained": last.get("comments_gained", 0) + gained_comments,
    })
    history = history[-MAX_HISTORY:]

    DATA.mkdir(exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    POSTS_FILE.write_text(json.dumps({
        "updated_at": now,
        "profile": {
            "username": profile.get("username"),
            "name": profile.get("name"),
            "picture": profile.get("profile_picture_url"),
            "followers": profile.get("followers_count", 0),
            "follows": profile.get("follows_count", 0),
            "media_count": profile.get("media_count", 0),
        },
        "posts": clean_posts,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    log(f"ok: @{profile.get('username')} - {profile.get('followers_count')} seguidores, "
        f"{len(clean_posts)} posts, {total_likes} curtidas, {len(history)} leituras no histórico")


if __name__ == "__main__":
    main()
