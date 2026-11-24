"""publish.py - send a finished pack to Telegram, the Facebook Page and Instagram. WhatsApp is copy-and-paste (see README).

Telegram: official Bot API (needs a bot token from @BotFather and your channel).
Instagram: official Instagram Graph API (needs a Professional account linked to a Facebook Page, a Meta app and an access token).
           Instagram downloads each image from a public URL, so slides are first uploaded to a public host (GitHub repo or your own URL).
"""
import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class PublishError(Exception):
    pass


def _http(req, timeout=60):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, (json.loads(body) if body[:1] in (b"{", b"[") else body)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode("utf-8", "ignore")[:300]
    except Exception as e:
        raise PublishError(f"network error: {e}")


def _multipart(fields, files):
    boundary = "----pulse" + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fname, blob) in files.items():
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + blob + b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


# ----------------------------------------------------------------------------- Telegram
def post_telegram(cfg, pack, folder):
    tg = cfg.get("telegram") or {}
    token, chat = tg.get("bot_token"), tg.get("chat_id")
    if not token or not chat:
        raise PublishError("Telegram is not set up (bot_token / chat_id missing in config.json)")
    base = tg.get("api_base", "https://api.telegram.org").rstrip("/") + f"/bot{token}"
    caps = pack["captions"]
    slides = pack["slides"][:10]

    def call(method, fields, files=None):
        if files:
            body, ctype = _multipart(fields, files)
            req = urllib.request.Request(f"{base}/{method}", data=body, headers={"Content-Type": ctype})
        else:
            req = urllib.request.Request(f"{base}/{method}", data=urllib.parse.urlencode(fields).encode())
        code, js = _http(req)
        if code != 200 or not (isinstance(js, dict) and js.get("ok")):
            raise PublishError(f"Telegram {method} failed: {js if not isinstance(js, dict) else js.get('description', js)}")
        return js["result"]

    def blob(name):
        with open(os.path.join(folder, name), "rb") as f:
            return f.read()

    if len(slides) == 1:
        res = call("sendPhoto", {"chat_id": chat, "caption": caps["telegram_caption"], "parse_mode": "HTML"}, {"photo": (slides[0], blob(slides[0]))})
        ids = [res["message_id"]]
    else:
        media, files = [], {}
        for i, s in enumerate(slides):
            item = {"type": "photo", "media": f"attach://f{i}"}
            if i == 0:
                item.update({"caption": caps["telegram_caption"], "parse_mode": "HTML"})
            media.append(item)
            files[f"f{i}"] = (s, blob(s))
        res = call("sendMediaGroup", {"chat_id": chat, "media": json.dumps(media)}, files)
        ids = [m["message_id"] for m in res]
    if caps["telegram"] != caps["telegram_caption"]:  # long text: send the full version as a follow-up message
        res = call("sendMessage", {"chat_id": chat, "text": caps["telegram"], "parse_mode": "HTML", "disable_web_page_preview": "true"})
        ids.append(res["message_id"])
    return {"message_ids": ids}


# ----------------------------------------------------------------------------- Instagram
def _host_github(host, pid, name, blob):
    """Upload one image to a public GitHub repo and return its raw URL (Instagram fetches this URL)."""
    repo, branch, folder, token = host["repo"], host.get("branch", "main"), host.get("folder", "pulse").strip("/"), host["token"]
    api = host.get("api_base", "https://api.github.com").rstrip("/")
    path = f"{folder}/{pid}/{name}"
    url = f"{api}/repos/{repo}/contents/{urllib.parse.quote(path)}"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "knruhs-pulse-studio", "Content-Type": "application/json"}
    payload = {"message": f"slide {pid}/{name}", "content": base64.b64encode(blob).decode(), "branch": branch}
    code, js = _http(urllib.request.Request(url, data=json.dumps(payload).encode(), headers=hdr, method="PUT"))
    if code in (409, 422):  # already exists: fetch its sha and overwrite
        c2, cur = _http(urllib.request.Request(url + f"?ref={branch}", headers=hdr))
        if c2 == 200 and isinstance(cur, dict):
            payload["sha"] = cur["sha"]
            code, js = _http(urllib.request.Request(url, data=json.dumps(payload).encode(), headers=hdr, method="PUT"))
    if code not in (200, 201):
        raise PublishError(f"GitHub upload failed ({code}): {js}")
    raw = host.get("raw_base", "https://raw.githubusercontent.com").rstrip("/")
    return f"{raw}/{repo}/{branch}/{path}"


def _public_urls(ig, pack, folder):
    host = ig.get("host") or {}
    kind = host.get("type")
    urls = []
    for name in pack["slides"][:10]:
        if kind == "github":
            with open(os.path.join(folder, name), "rb") as f:
                urls.append(_host_github(host, pack["id"], name, f.read()))
        elif kind == "base_url":
            urls.append(host["url"].rstrip("/") + f"/{pack['id']}/{name}")
        else:
            raise PublishError("Instagram needs a public place for the images: set instagram.host in config.json (see README)")
    return urls


def post_instagram(cfg, pack, folder):
    ig = cfg.get("instagram") or {}
    uid, token = ig.get("user_id"), ig.get("access_token")
    if not uid or not token:
        raise PublishError("Instagram is not set up (user_id / access_token missing in config.json)")
    api = ig.get("api_base", "https://graph.facebook.com/v23.0").rstrip("/")
    urls = _public_urls(ig, pack, folder)
    time.sleep(ig.get("host_settle_seconds", 4))  # give the raw file host a moment before Instagram fetches it
    caption = pack["captions"]["instagram"]

    def post(path, fields):
        fields = dict(fields, access_token=token)
        code, js = _http(urllib.request.Request(f"{api}/{path}", data=urllib.parse.urlencode(fields).encode()))
        if code != 200 or not isinstance(js, dict) or "id" not in js:
            raise PublishError(f"Instagram {path.split('/')[-1]} failed: {js.get('error', {}).get('message', js) if isinstance(js, dict) else js}")
        return js["id"]

    def wait_ready(cid, tries=30):
        for _ in range(tries):
            code, js = _http(urllib.request.Request(f"{api}/{cid}?fields=status_code&access_token={urllib.parse.quote(token)}"))
            st = js.get("status_code") if isinstance(js, dict) else None
            if st == "FINISHED":
                return
            if st in ("ERROR", "EXPIRED"):
                raise PublishError(f"Instagram could not process the image (status {st})")
            time.sleep(ig.get("poll_seconds", 2))
        raise PublishError("Instagram took too long to process the images")

    if len(urls) == 1:
        container = post(f"{uid}/media", {"image_url": urls[0], "caption": caption})
    else:
        kids = []
        for u in urls:
            kid = post(f"{uid}/media", {"image_url": u, "is_carousel_item": "true"})
            wait_ready(kid)
            kids.append(kid)
        container = post(f"{uid}/media", {"media_type": "CAROUSEL", "children": ",".join(kids), "caption": caption})
    wait_ready(container)
    media_id = post(f"{uid}/media_publish", {"creation_id": container})
    return {"media_id": media_id}


# ----------------------------------------------------------------------------- Facebook Page
def post_facebook(cfg, pack, folder):
    """Upload the slides straight to the Page (no public host needed), then make one post with all of them."""
    fb = cfg.get("facebook") or {}
    page, token = fb.get("page_id"), fb.get("access_token")
    if not page or not token:
        raise PublishError("Facebook is not set up (page_id / access_token missing)")
    api = fb.get("api_base", "https://graph.facebook.com/v23.0").rstrip("/")
    message = pack["captions"].get("facebook") or pack["captions"]["instagram"]
    # Posting must be done "as the Page". If the saved token is a user token, swap it for the Page's own token.
    code, js = _http(urllib.request.Request(f"{api}/{page}?fields=access_token&access_token={urllib.parse.quote(token)}"))
    if code == 200 and isinstance(js, dict) and js.get("access_token"):
        token = js["access_token"]

    def call(path, fields, files=None):
        fields = dict(fields, access_token=token)
        if files:
            body, ctype = _multipart(fields, files)
            req = urllib.request.Request(f"{api}/{path}", data=body, headers={"Content-Type": ctype})
        else:
            req = urllib.request.Request(f"{api}/{path}", data=urllib.parse.urlencode(fields).encode())
        code, js = _http(req, timeout=120)
        if code != 200 or not isinstance(js, dict) or "id" not in js:
            raise PublishError(f"Facebook {path.split('/')[-1]} failed: {js.get('error', {}).get('message', js) if isinstance(js, dict) else js}")
        return js["id"]

    photo_ids = []
    for name in pack["slides"][:10]:
        with open(os.path.join(folder, name), "rb") as f:
            photo_ids.append(call(f"{page}/photos", {"published": "false"}, {"source": (name, f.read())}))
    fields = {"message": message}
    for i, pid in enumerate(photo_ids):
        fields[f"attached_media[{i}]"] = json.dumps({"media_fbid": pid})
    return {"post_id": call(f"{page}/feed", fields)}


def post_app(cfg, pack, folder):
    """App-only universities: 'posting' just marks the notice published, so it goes into the app's feed and phone alerts."""
    return {"to": "app"}


TARGETS = {"telegram": post_telegram, "facebook": post_facebook, "instagram": post_instagram, "app": post_app}


def configured(cfg):
    tg, ig, fb = cfg.get("telegram") or {}, cfg.get("instagram") or {}, cfg.get("facebook") or {}
    return {"telegram": bool(tg.get("bot_token") and tg.get("chat_id")),
            "facebook": bool(fb.get("page_id") and fb.get("access_token")),
            "instagram": bool(ig.get("user_id") and ig.get("access_token") and (ig.get("host") or {}).get("type")),
            "app": bool(cfg.get("app_only"))}
