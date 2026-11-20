"""cloud.py - one headless run for GitHub Actions (no Desk screen).

Each run: check KNRUHS for new notices, build slides + captions, post the clean ones to Telegram (and Instagram if set up),
and send YOU a private Telegram alert for notices that need a human look (scanned PDF, unsure topic, low confidence).
Secrets come from environment variables, never from files in the repo:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (your channel, e.g. @knruhsx), OWNER_CHAT_ID (optional, your own Telegram user id),
  IG_USER_ID, IG_ACCESS_TOKEN, SLIDES_REPO, SLIDES_TOKEN (optional, Instagram)
"""
import argparse, json, os, sys, time, urllib.error, urllib.parse, urllib.request
import bot, desk, publish

HERE = os.path.dirname(os.path.abspath(__file__))


def apply_env(cfg):
    e = os.environ.get
    if e("TELEGRAM_BOT_TOKEN"):
        cfg["telegram"] = {"bot_token": e("TELEGRAM_BOT_TOKEN").strip(), "chat_id": e("TELEGRAM_CHAT_ID", "").strip().replace("https://t.me/", "@").replace("t.me/", "@")}
        if e("TELEGRAM_API_BASE"):
            cfg["telegram"]["api_base"] = e("TELEGRAM_API_BASE")
    if e("IG_USER_ID") and e("IG_ACCESS_TOKEN") and e("SLIDES_REPO"):
        cfg["instagram"] = {"user_id": e("IG_USER_ID"), "access_token": e("IG_ACCESS_TOKEN"),
                            "host": {"type": "github", "repo": e("SLIDES_REPO"), "token": e("SLIDES_TOKEN", e("GITHUB_TOKEN", "")), "branch": e("SLIDES_BRANCH", "main")}}
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
    a["targets"] = [t for t in ("telegram", "instagram") if conf.get(t)]
    a["on"] = True
    if os.environ.get("SEND_TEST") == "true":
        bot.log("sending a test message to the owner chat")
        alert_owner(cfg, "Test message from your KNRUHS bot. If you can read this, private alerts work.")
    mode = cfg.get("cloud", {}).get("mode", "auto_all")  # "auto_all": every new notice posts itself. "auto": only clean ones (doubtful wait for /post). "approve": everything waits for /post
    a["all"] = (mode == "auto_all")
    if mode == "auto_all":
        mode = "auto"
    read_commands(cfg, d, a["targets"], skip=first)  # first run: ignore old chat messages
    d.check_now()
    if d.last_error:
        bot.log("check problem:", d.last_error)
    if first:
        a["since"] = time.time()  # notices found on the very first run are NOT posted (no flood of old news)
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
    codes = [c for c in os.environ.get("POST_CODE", "").replace(",", " ").split() if c]
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
            r = d.post(code, a["targets"])
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
    if mode == "auto":
        before = {pk["id"] for pk in d.packs() if d.item_state(pk["id"])["posted"]}
        d.auto_tick()
        for pk in d.packs():
            if pk["id"] not in before and d.item_state(pk["id"])["posted"] and (pk.get("flags") or (pk.get("confidence") or 0) < 0.85):
                alert_owner(cfg, "Posted automatically (worth a quick look): " + pk["title"][:120] + "\nReason: " + (", ".join(pk.get("flags") or []) or "low confidence") + "\nOriginal: " + pk["source_url"])
    d.save()
    bot.log("run finished")


if __name__ == "__main__":
    main()
