"""cloud.py - one headless run for GitHub Actions (no Desk screen).

Each run: check KNRUHS for new notices, build slides + captions, post the clean ones to Telegram (and Instagram if set up),
and send YOU a private Telegram alert for notices that need a human look (scanned PDF, unsure topic, low confidence).
Secrets come from environment variables, never from files in the repo:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (your channel, e.g. @knruhsx), OWNER_CHAT_ID (optional, your own Telegram user id),
  META_PAGE_TOKEN (Facebook Page + Instagram; page/IG ids are in config.cloud.json under meta)
"""
import argparse, json, os, sys, time, urllib.error, urllib.parse, urllib.request
import bot, desk, publish, studio

HERE = os.path.dirname(os.path.abspath(__file__))


def apply_env(cfg):
    e = os.environ.get
    if e("TELEGRAM_BOT_TOKEN"):
        cfg["telegram"] = {"bot_token": e("TELEGRAM_BOT_TOKEN").strip(), "chat_id": e("TELEGRAM_CHAT_ID", "").strip().replace("https://t.me/", "@").replace("t.me/", "@")}
        if e("TELEGRAM_API_BASE"):
            cfg["telegram"]["api_base"] = e("TELEGRAM_API_BASE")
    meta = cfg.get("meta", {})
    page_token = (e("META_PAGE_TOKEN") or "").strip()
    if page_token and meta.get("page_id"):
        cfg["facebook"] = {"page_id": meta["page_id"], "access_token": page_token}
    ig_token = (e("IG_ACCESS_TOKEN") or page_token).strip()
    ig_user = e("IG_USER_ID") or meta.get("instagram_user_id")
    slides_repo = e("SLIDES_REPO") or e("GITHUB_REPOSITORY")
    slides_token = e("SLIDES_TOKEN") or e("GITHUB_TOKEN")
    if ig_token and ig_user and slides_repo and slides_token:
        cfg["instagram"] = {"user_id": ig_user, "access_token": ig_token,
                            "host": {"type": "github", "repo": slides_repo, "token": slides_token, "branch": e("SLIDES_BRANCH", "main"), "folder": "slides"}}
    return cfg


def tg_call(cfg, method, fields):
    tg = cfg.get("telegram") or {}
    if not tg.get("bot_token"):
        return None
    url = f"{tg.get('api_base', 'https://api.telegram.org').rstrip('/')}/bot{tg['bot_token']}/{method}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode()), timeout=30) as r:
            js = json.loads(r.read())
        return js.get("result") if js.get("ok") else None
    except urllib.error.HTTPError as ex:
        try:
            why = json.loads(ex.read()).get("description", "")
        except Exception:
            why = ""
        hint = {401: "the bot token is wrong or was revoked", 400: "check the chat id (for the owner: press Start on the bot; for the channel: the @name must match and the bot must be an admin)", 403: "the bot is blocked or not allowed to write there", 404: "the bot token is wrong"}.get(ex.code, "")
        bot.log(f"telegram {method} failed: HTTP {ex.code} {why} -> {hint}")
    except Exception as ex:
        bot.log(f"telegram {method} failed:", type(ex).__name__)
    return None


def alert_owner(cfg, text):
    owner = os.environ.get("OWNER_CHAT_ID")
    if owner:
        tg_call(cfg, "sendMessage", {"chat_id": owner, "text": text[:3900], "disable_web_page_preview": "true"})


def read_commands(cfg, d, targets, skip=False):
    """You control the cloud bot by messaging it on Telegram: /post ID, /skip ID, /list, /help."""
    owner = os.environ.get("OWNER_CHAT_ID")
    if not owner:
        return
    off = d.state.get("tg_offset", 0)
    ups = tg_call(cfg, "getUpdates", {"offset": off, "timeout": 0, "allowed_updates": json.dumps(["message"])}) or []
    packs = None
    for u in ups:
        d.state["tg_offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        if skip or str((m.get("chat") or {}).get("id")) != str(owner):
            continue  # only YOU can command the bot
        parts = (m.get("text") or "").strip().split()
        if not parts:
            continue
        cmd, arg = parts[0].lower().split("@")[0], (parts[1].lower() if len(parts) > 1 else "")
        if packs is None:
            packs = {p["id"]: p for p in d.packs()}
        if cmd in ("/post", "/skip") and arg not in packs:
            alert_owner(cfg, f"I do not know the code '{arg}'. Send /list to see what is waiting.")
        elif cmd == "/post":
            st = d.item_state(arg)
            st["status"] = "new"
            st["errors"] = {}
            r = d.post(arg, targets)
            alert_owner(cfg, ("Posted to " + " and ".join(r["posted"]) + ": " + packs[arg]["title"]) if r["posted"] else ("Could not post: " + (next(iter(r["errors"].values()), "already posted"))))
        elif cmd == "/skip":
            d.item_state(arg)["status"] = "dismissed"
            alert_owner(cfg, "Skipped: " + packs[arg]["title"])
        elif cmd == "/list":
            wait = [p for p in packs.values() if d.item_state(p["id"])["status"] in ("new", "held") and not d.item_state(p["id"])["posted"]]
            alert_owner(cfg, "\n\n".join(f"{p['id']}  {p['title'][:90]}" for p in wait[-15:]) or "Nothing is waiting.")
        else:
            alert_owner(cfg, "Commands:\n/post CODE  publish a waiting notice\n/skip CODE  drop it\n/list  show what is waiting")
    d.save()


def meta_check(cfg):
    """Diagnostic only: which kind of Meta token is saved, when it expires, and whether the Page and Instagram answer. Never prints the token."""
    fb, ig = cfg.get("facebook") or {}, cfg.get("instagram") or {}
    tok, api = fb["access_token"], "https://graph.facebook.com/v23.0"
    def get(path):
        try:
            with urllib.request.urlopen(f"{api}/{path}{'&' if '?' in path else '?'}access_token={urllib.parse.quote(tok)}", timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as ex:
            try:
                return json.loads(ex.read())
            except Exception:
                return {"error": {"message": f"HTTP {ex.code}"}}
        except Exception as ex:
            return {"error": {"message": type(ex).__name__}}
    me = get("me?fields=id,name")
    kind = "PAGE token" if me.get("id") == fb.get("page_id") else ("USER token" if me.get("id") else "unknown")
    bot.log("meta check: token belongs to", me.get("name", me.get("error", {}).get("message")), "->", kind)
    dbg = (get(f"debug_token?input_token={urllib.parse.quote(tok)}").get("data") or {})
    exp = dbg.get("expires_at")
    bot.log("meta check: expires:", "NEVER" if exp == 0 else (time.strftime("%Y-%m-%d", time.gmtime(exp)) if exp else "unknown"), "| valid:", dbg.get("is_valid"))
    if ig.get("user_id"):
        u = get(f"{ig['user_id']}?fields=username")
        bot.log("meta check: instagram answers as", u.get("username", u.get("error", {}).get("message")))
    alert_owner(cfg, f"Meta check: {kind}, expires {'never' if exp == 0 else 'on a date (see log)' if exp else 'unknown'}.")


def report_to_boss(fb, cfg, d, conf, posted_before):
    """Every worker run ends by filing a short report for the Boss bot (Firestore bot_health/<university>):
    did each website page open (and if not, why, and for how many runs in a row), how many notice links were found,
    what was posted, what the duplicate guard blocked, and any error. The Boss reads these; it never visits websites."""
    uni = cfg.get("university", "knruhs")
    prev = {}
    try:
        import firebase_sync
        doc = fb.get(f"bot_health/{uni}")
        prev = firebase_sync.fields_of(doc) if doc else {}
    except Exception:
        pass
    prev_fail = {s.get("url"): int(s.get("fail_runs") or 0) for s in (prev.get("sources") or []) if isinstance(s, dict)}
    rep = bot.REPORT
    sources = []
    for url, s in rep.get("sources", {}).items():
        sources.append({"url": url, "name": s.get("name", ""), "ok": bool(s.get("ok")), "reason": s.get("reason", ""),
                        "links": int(s.get("links") or 0), "fail_runs": 0 if s.get("ok") else prev_fail.get(url, 0) + 1})
    now = bot.datetime.now(bot.IST).isoformat(timespec="seconds")
    links = int(rep.get("links_found") or 0)
    posted, errors = [], []
    for pk in d.packs():
        st = d.item_state(pk["id"])
        for t, info in st["posted"].items():
            if t not in posted_before.get(pk["id"], set()) and isinstance(info, dict) and info.get("by") in ("auto", "you"):
                posted.append(f"{t}: {pk['title'][:70]}")
        for t, e in (st.get("errors") or {}).items():
            errors.append(f"{t}: {str(e)[:150]}")
    run_url = ""
    if os.environ.get("GITHUB_RUN_ID"):
        run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    data = {"uni": uni, "last_run": now, "run_url": run_url, "links_found": links, "new_found": int(rep.get("new_found") or 0),
            "last_new": rep.get("last_new") or prev.get("last_new") or "", "sources": sources,
            "zero_links_since": (prev.get("zero_links_since") or now) if links == 0 else None,
            "posted": posted[:20], "duplicates_blocked": getattr(d, "dup_blocked", [])[:20],
            "post_errors": errors[:10], "check_error": d.last_error or "",
            "channels": {k: bool(v) for k, v in conf.items()}}
    tok = (cfg.get("facebook") or {}).get("access_token")
    if tok:  # how long the Facebook/Instagram key still works (the Boss warns a week before)
        try:
            q = urllib.parse.quote(tok)
            with urllib.request.urlopen(f"https://graph.facebook.com/v21.0/debug_token?input_token={q}&access_token={q}", timeout=20) as r:
                info = json.loads(r.read()).get("data") or {}
            exp = info.get("expires_at") or 0
            data["meta_token_ok"] = bool(info.get("is_valid", True))
            data["meta_token_days_left"] = round((exp - time.time()) / 86400, 1) if exp else -1  # -1 = never expires
        except urllib.error.HTTPError as ex:
            data["meta_token_ok"] = ex.code != 190 and ex.code != 400
        except Exception:
            pass
    fb.patch(f"bot_health/{uni}", data)
    bad = [s for s in sources if not s["ok"]]
    bot.log("reported to the Boss:", f"{links} links, {len(bad)} page problem(s), {len(posted)} posted, {len(errors)} error(s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.cloud.json"))
    ap.add_argument("--backfill", action="store_true")
    args = ap.parse_args()
    args.notify = False
    cfg = apply_env(bot.load_config(args.config))
    data_dir = os.path.join(HERE, cfg["data_dir"]) if not os.path.isabs(cfg["data_dir"]) else cfg["data_dir"]
    os.makedirs(data_dir, exist_ok=True)
    d = desk.Desk(cfg, args, data_dir, bot.run_once, bot.log)
    conf = publish.configured(cfg)
    bot.log("channels:", {k: ("connected" if v else "NOT connected") for k, v in conf.items()})
    a = d.state["auto"]
    first = not os.path.exists(d.state_path)
    a["delay_minutes"] = int(cfg.get("cloud", {}).get("delay_minutes", 0))
    a["targets"] = [t for t in ("telegram", "facebook", "instagram", "app") if conf.get(t)]
    if cfg.get("cloud", {}).get("max_per_hour"):
        a["max_per_hour"] = int(cfg["cloud"]["max_per_hour"])
    ts = a.setdefault("target_since", {})
    for t in a["targets"]:
        ts.setdefault(t, 0 if t == "telegram" else time.time())  # a newly connected channel starts from now: no flood of old notices
    a["on"] = True
    if os.environ.get("SEND_TEST") == "true":
        bot.log("sending a test message to the owner chat")
        alert_owner(cfg, "Test message from your KNRUHS bot. If you can read this, private alerts work.")
    fb = None
    if os.environ.get("FIREBASE_SA", "").strip():
        try:
            import firebase_sync
            fb = firebase_sync.FirebaseSync(os.environ["FIREBASE_SA"], bot.log, os.environ.get("GITHUB_REPOSITORY"), HERE,
                                            university=cfg.get("university", "knruhs"), slides=not cfg.get("app_only"))
            fb.read_controls()
        except Exception as ex:
            bot.log("firebase: not connected:", type(ex).__name__, str(ex)[:150])
            fb = None
    mode = cfg.get("cloud", {}).get("mode", "auto_all")
    if fb and fb.ok:
        mode = "auto_all" if fb.settings.get("auto_publish", True) else "approve"  # the switch in the admin app wins
    paused = bool(fb and fb.ok and fb.settings.get("paused"))  # "auto_all": every new notice posts itself. "auto": only clean ones (doubtful wait for /post). "approve": everything waits for /post
    a["all"] = (mode == "auto_all")
    if mode == "auto_all":
        mode = "auto"
    posted_before = {pk["id"]: set(d.item_state(pk["id"])["posted"]) for pk in d.packs()}
    read_commands(cfg, d, a["targets"], skip=first)  # first run: ignore old chat messages
    d.check_now()
    if d.last_error:
        bot.log("check problem:", d.last_error)
    if fb:
        fb.import_manual(data_dir, studio.pack_id)
        fb.apply_edits(data_dir, studio.pack_id)
        mine = {pk["id"] for pk in d.packs()}
        for nid, act in fb.actions.items():
            if nid not in mine:
                continue  # a notice of another university
            if act == "hold":
                d.item_state(nid)["status"] = "held"
            elif act == "dismiss":
                d.item_state(nid)["status"] = "dismissed"
        d.save()
    if first and not cfg.get("app_only"):
        a["since"] = time.time()  # notices found on the very first run are NOT posted (no flood of old news)
    if first and cfg.get("app_only"):
        bot.log("first run of an app-only university: its newest notices go into the app (phones are not alerted for them)")
        alert_owner(cfg, "KNRUHS bot is live on GitHub. From now on, new notices are " + ("posted automatically." if mode == "auto" else "sent to you first; reply /post CODE to publish."))
    if os.environ.get("POST_CODE", "").strip() or os.environ.get("SEND_TEST") == "true":
        me = tg_call(cfg, "getMe", {})
        cid = (cfg.get("telegram") or {}).get("chat_id", "")
        bot.log("diagnostic: token belongs to bot", ("@" + me.get("username", "?")) if me else "UNKNOWN (token rejected)")
        bot.log("diagnostic: channel id the bot is using:", repr(cid), "| length", len(cid))
        chat = tg_call(cfg, "getChat", {"chat_id": cid})
        if chat:
            bot.log("diagnostic: Telegram found the channel:", chat.get("title"), "| type", chat.get("type"), "| id", chat.get("id"))
            adm = tg_call(cfg, "getChatMember", {"chat_id": cid, "user_id": (me or {}).get("id", 0)})
            bot.log("diagnostic: bot status in the channel:", (adm or {}).get("status", "unknown"), "| can post:", (adm or {}).get("can_post_messages"))
        else:
            bot.log("diagnostic: Telegram does NOT know a chat called", repr(cid))
    if os.environ.get("SEND_TEST") == "true" and (cfg.get("facebook") or {}).get("access_token"):
        meta_check(cfg)
    codes = [c for c in os.environ.get("POST_CODE", "").replace(",", " ").split() if c]
    if fb and not paused:
        mine = {pk["id"] for pk in d.packs()}
        codes += [nid for nid, act in fb.actions.items() if act == "publish" and nid not in codes and nid in mine]  # "Publish" pressed in the admin app
    if codes:
        known = {p["id"]: p for p in d.packs()}
        for n, code in enumerate(codes):
            if code not in known:
                bot.log(f"POST_CODE {code}: no such notice")
                continue
            if n:
                time.sleep(4)  # be gentle with Telegram's rate limit
            st = d.item_state(code)
            st["status"], st["errors"], st["alerted"] = "new", {}, True
            if os.environ.get("REPOST") == "true":
                st["posted"] = {}  # test: post this notice again on every channel
            r = d.post(code, a["targets"], force=os.environ.get("REPOST") == "true")
            bot.log("manual post:", code, r)
            if not r["posted"] and r["errors"]:
                alert_owner(cfg, "Could not post to the channel: " + "; ".join(r["errors"].values()))
        d.save()
    for pk in d.packs():
        st = d.item_state(pk["id"])
        if st.get("alerted") or st["status"] != "new" or st["posted"] or (mode == "auto" and d.eligible(pk)):
            continue
        try:
            det = bot.datetime.fromisoformat(pk["detected_at"]).timestamp()
        except Exception:
            det = 0
        if det < a.get("since", 0):
            continue
        why = ", ".join(pk.get("flags") or []) or (f"confidence {int(100 * (pk.get('confidence') or 0))}%" if not d.eligible(pk) else "waiting for your OK")
        alert_owner(cfg, f"NEW NOTICE ({why})\n{pk['title']}\n\n{pk.get('summary') or ''}\n\nOriginal: {pk['source_url']}\n\nPublish: /post {pk['id']}\nDrop: /skip {pk['id']}")
        st["alerted"] = True
    if mode == "auto" and not paused:
        before = {pk["id"] for pk in d.packs() if d.item_state(pk["id"])["posted"]}
        d.auto_tick()
        for pk in d.packs():
            if pk["id"] not in before and d.item_state(pk["id"])["posted"] and (pk.get("flags") or (pk.get("confidence") or 0) < 0.85):
                alert_owner(cfg, "Posted automatically (worth a quick look): " + pk["title"][:120] + "\nReason: " + (", ".join(pk.get("flags") or []) or "low confidence") + "\nOriginal: " + pk["source_url"])
    d.save()
    if fb:
        try:
            fb.push(d, d.outbox, {"mode": mode, "paused": paused, "last_error": d.last_error or "",
                                  "channels": {k: bool(v) for k, v in conf.items()}, "notices_total": len(d.drafts())})
        except Exception as ex:
            bot.log("firebase: sync failed:", type(ex).__name__, str(ex)[:200])
    if fb:
        try:
            report_to_boss(fb, cfg, d, conf, posted_before)
        except Exception as ex:
            bot.log("could not report to the Boss:", type(ex).__name__, str(ex)[:150])
    bot.log("run finished")


if __name__ == "__main__":
    main()
