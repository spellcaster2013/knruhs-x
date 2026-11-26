"""boss.py - the Boss bot: a watchdog over all the notice bots.

Runs on its own schedule (boss.yml), separate from the notice bots, and sends YOU a private Telegram message
(OWNER_CHAT_ID) only when something is wrong - before you notice it yourself. Once a day it sends a short
"all good" report so you know the Boss itself is alive.

What it checks
  1. Runs     - the notice bot's recent runs: failed / cancelled / timed out, too slow, or no good run for a while
  2. Doubles  - the same notice posted more than once on Instagram or the Facebook Page (Telegram follows the
                same memory, so a double there shows up here too)
  3. Tokens   - the Meta (Facebook + Instagram) token still works and is not about to expire
  4. Sources  - each university's bot is still finding notice links (a redesigned website quietly gives 0),
                and has found something new recently
  5. Websites  - visits every university page the bots read and says WHY one fails: address gone (domain
                changed/expired), not responding, server error / maintenance page, page moved (404), redirects
                to a new website, blocking our bot, broken certificate. Reported after 2 failed checks in a row
                (no alarm for a 1-minute blip), and you hear when it is back.
Each problem is reported once, then again only if it is still there 12 hours later (no spam).

Secrets (environment, never in files): TELEGRAM_BOT_TOKEN, OWNER_CHAT_ID, META_PAGE_TOKEN, GITHUB_TOKEN.
"""
import json, os, re, sqlite3, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from firebase_sync import UNI_LABELS
except Exception:
    UNI_LABELS = {}
IST = timezone(timedelta(hours=5, minutes=30))
STATE = os.path.join(HERE, "boss_state.json")
GRAPH = "https://graph.facebook.com/v21.0"
REPEAT_AFTER_H = 12
DIGEST_HOUR_IST = 21          # daily "all good" report around 9 PM
RUN_TOO_SLOW_MIN = 25         # a notice-bot run longer than this is close to its limit
NO_GOOD_RUN_MIN = 75          # no successful run for this long = the bot has stopped
QUIET_DAYS = {"knruhs": 4}    # alert if a university found nothing new for this many days (default below)
QUIET_DAYS_DEFAULT = 12


def now():
    return datetime.now(IST)


def log(*a):
    print(now().strftime("%H:%M:%S"), *a, flush=True)


def http_json(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as ex:
        try:
            return ex.code, json.loads(ex.read() or b"{}")
        except Exception:
            return ex.code, {}


# ----------------------------------------------------------------------------- checks

def check_runs(repo, token):
    """The notice bot's own health, from GitHub's record of its runs."""
    out = []
    if not (repo and token):
        return out, {}
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    code, js = http_json(f"https://api.github.com/repos/{repo}/actions/workflows/pulse.yml/runs?per_page=12", headers=h)
    if code != 200:
        return [("runs-api", f"Could not read the bot's runs from GitHub (HTTP {code}).")], {}
    runs = js.get("workflow_runs", [])
    done = [r for r in runs if r.get("status") == "completed"]
    stats = {"runs": len(done), "ok": sum(1 for r in done if r.get("conclusion") == "success")}
    for r in done[:4]:  # the last ~hour
        c = r.get("conclusion")
        if c in ("failure", "cancelled", "timed_out", "startup_failure"):
            t = datetime.fromisoformat(r["run_started_at"].replace("Z", "+00:00")).astimezone(IST)
            out.append((f"run-{r['id']}", f"Notice-bot run at {t:%H:%M} ended as '{c}'. If it had posted something, "
                                           f"check for doubles.\n{r['html_url']}"))
    for r in done[:4]:
        try:
            a = datetime.fromisoformat(r["run_started_at"].replace("Z", "+00:00"))
            b = datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
            mins = (b - a).total_seconds() / 60
        except Exception:
            continue
        if mins > RUN_TOO_SLOW_MIN:
            out.append((f"slow-{r['id']}", f"A notice-bot run took {mins:.0f} min (limit 40). Something is slow - "
                                            f"a university website may be hanging.\n{r['html_url']}"))
    good = [r for r in done if r.get("conclusion") == "success"]
    if good:
        last = datetime.fromisoformat(good[0]["updated_at"].replace("Z", "+00:00"))
        gap = (datetime.now(timezone.utc) - last).total_seconds() / 60
        if gap > NO_GOOD_RUN_MIN:
            out.append(("no-good-run", f"No successful notice-bot run for {gap:.0f} minutes. The bot may have stopped "
                                       f"(GitHub sometimes pauses schedules). Open Actions and press Run workflow."))
    elif done:
        out.append(("no-good-run", "None of the recent notice-bot runs succeeded."))
    return out, stats


def norm(text):
    first = (text or "").strip().split("\n")[0]
    return re.sub(r"[^a-z0-9]+", " ", first.lower()).strip()[:140]


def doubles(items, label):
    """items: (caption, time, link). The same first line twice within 3 days = a double post."""
    seen, out = {}, []
    for cap, t, link in items:
        k = norm(cap)
        if len(k) < 12:
            continue
        seen.setdefault(k, []).append((t, link))
    for k, hits in seen.items():
        hits.sort()
        if len(hits) > 1 and (hits[-1][0] - hits[0][0]) < timedelta(days=3):
            out.append((f"dup-{label}-{k[:60]}", f"{label}: the same notice is posted {len(hits)} times:\n\"{k[:90]}\"\n"
                                                 + "\n".join(l for _, l in hits[:5])))
    return out


def check_meta(meta, token):
    out, stats = [], {}
    if not token:
        return out, stats
    q = urllib.parse.quote(token)
    # token still valid? (code 190 = expired / revoked)
    code, js = http_json(f"{GRAPH}/me?fields=id,name&access_token={q}")
    if code != 200:
        err = (js.get("error") or {})
        out.append(("meta-token", f"The Facebook/Instagram token stopped working ({err.get('code')}: {err.get('message', '')[:120]}). "
                                  f"Posting to Facebook and Instagram has stopped until it is renewed."))
        return out, stats
    code, js = http_json(f"{GRAPH}/debug_token?input_token={q}&access_token={q}")
    exp = ((js or {}).get("data") or {}).get("expires_at") or 0
    if exp:
        left = (exp - time.time()) / 86400
        if 0 < left < 7:
            out.append(("meta-token-expiry", f"The Facebook/Instagram token expires in {left:.1f} days. Renew it before then."))
    ig = meta.get("instagram_user_id")
    since = now() - timedelta(days=4)
    if ig:
        code, js = http_json(f"{GRAPH}/{ig}/media?fields=caption,timestamp,permalink&limit=40&access_token={q}")
        if code == 200:
            items = []
            for m in js.get("data", []):
                t = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
                if t >= since:
                    items.append((m.get("caption", ""), t, m.get("permalink", "")))
            stats["instagram_today"] = sum(1 for _, t, _ in items if t.astimezone(IST).date() == now().date())
            out += doubles(items, "Instagram")
        else:
            out.append(("ig-read", f"Could not read the Instagram posts (HTTP {code}): {(js.get('error') or {}).get('message', '')[:120]}"))
    page = meta.get("page_id")
    if page:
        code, js = http_json(f"{GRAPH}/{page}/posts?fields=message,created_time,permalink_url&limit=40&access_token={q}")
        if code == 200:
            items = []
            for m in js.get("data", []):
                t = datetime.strptime(m["created_time"], "%Y-%m-%dT%H:%M:%S%z")
                if t >= since:
                    items.append((m.get("message", ""), t, m.get("permalink_url", "")))
            stats["facebook_today"] = sum(1 for _, t, _ in items if t.astimezone(IST).date() == now().date())
            out += doubles(items, "Facebook")
    return out, stats


def check_sources():
    """Each university's bot: still finding links on its website, and finding new notices now and then."""
    out, active = [], []
    for fn in sorted(os.listdir(HERE)):
        if not (fn.startswith("config.") and fn.endswith(".json")):
            continue
        try:
            cfg = json.load(open(os.path.join(HERE, fn), encoding="utf-8"))
        except Exception:
            continue
        uni = cfg.get("university") or ("knruhs" if fn == "config.cloud.json" else fn.split(".")[1])
        if fn == "config.json":
            continue  # the local Mac copy of KNRUHS
        ddir = os.path.join(HERE, cfg.get("data_dir", "data"))
        label = UNI_LABELS.get(uni, uni.upper())
        health = {}
        try:
            health = json.load(open(os.path.join(ddir, "health.json"), encoding="utf-8"))
        except Exception:
            pass
        z = health.get("zero_links_since")
        if z:
            try:
                hrs = (now() - datetime.fromisoformat(z)).total_seconds() / 3600
            except Exception:
                hrs = 0
            if hrs >= 2:
                out.append((f"zero-{uni}", f"{label}: the bot has found NO notice links on the website for {hrs:.0f} hours. "
                                           f"The site may be down, blocking us, or redesigned (the bot then needs fixing)."))
        db = os.path.join(ddir, "seen.db")
        if not os.path.exists(db):
            continue
        try:
            last = sqlite3.connect(db).execute("SELECT MAX(first_seen) FROM seen WHERE processed=1").fetchone()[0]
        except Exception:
            last = None
        if not last:
            continue
        days = (now() - datetime.fromisoformat(last)).total_seconds() / 86400
        limit = QUIET_DAYS.get(uni, QUIET_DAYS_DEFAULT)
        if days > limit:
            out.append((f"quiet-{uni}", f"{label}: nothing new found for {days:.0f} days. Unusual - please check the website "
                                        f"still lists notices where the bot looks."))
        else:
            active.append(label)
    return out, {"active_unis": active}


# ----------------------------------------------------------------------------- website connectivity

MAINT = re.compile(r"under maintenance|site (?:is )?(?:down|under construction)|maintenance mode|temporarily unavailable|"
                   r"be back soon|scheduled maintenance|account (?:has been )?suspended|domain (?:has )?expired|"
                   r"this domain (?:is|may be) for sale|website is under construction", re.I)


def probe(url, cfg):
    """One quick visit to a university page, like the notice bot makes. Returns (ok, reason in plain words)."""
    host = urllib.parse.urlsplit(url).hostname or ""
    try:
        import bot
        ctx = bot.ssl_context(cfg)
    except Exception:
        ctx = None
    req = urllib.request.Request(url, headers={"User-Agent": cfg.get("user_agent", "Mozilla/5.0"), "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=40, context=ctx) as r:
            body = r.read(1_500_000).decode("utf-8", "ignore")
            final = r.geturl()
    except urllib.error.HTTPError as ex:
        why = {403: "the website is now blocking our bot (403 Forbidden)", 404: "the page is gone (404) - the notices page has probably moved to a new address",
               410: "the page was removed (410) - the notices page has moved", 429: "the website is limiting our visits (429 Too many requests)"}.get(ex.code)
        if not why:
            why = f"the website's server is failing ({ex.code}) - likely maintenance or an outage" if ex.code >= 500 else f"the website answered with error {ex.code}"
        return False, why
    except Exception as ex:
        s = f"{type(ex).__name__}: {ex}"
        if re.search(r"Name or service not known|nodename nor servname|getaddrinfo failed|No address associated|gaierror|NXDOMAIN", s):
            return False, f"the address {host} no longer exists (domain changed, expired or DNS down)"
        if "CERTIFICATE" in s or "SSL" in s:
            return False, "the website's security certificate is broken or expired"
        if "timed out" in s.lower() or "TimeoutError" in s:
            return False, "the website is not responding (timed out) - down or overloaded"
        if "refused" in s.lower():
            return False, "the website refused the connection - server down"
        return False, f"could not connect ({s[:120]})"
    fh, oh = (urllib.parse.urlsplit(final).hostname or "").removeprefix("www."), host.removeprefix("www.")
    if fh and fh != oh:
        return False, f"the page now redirects to a different website: {final[:120]} (address changed?)"
    if MAINT.search(body[:200_000]):
        return False, "the website shows a maintenance / suspended page"
    if len(body.strip()) < 300:
        return False, "the page loads but is empty"
    return True, ""


def check_sites(state):
    """Visit every university page the bots read. A problem is reported after it has been seen on two Boss runs in a row
    (so a 1-minute blip does not wake you), and you get a message when the site is back."""
    out, back = [], []
    streak = state.setdefault("site_fail", {})
    alerted = state.setdefault("site_alerted", {})
    seen_now = set()
    for fn in sorted(os.listdir(HERE)):
        if not (fn.startswith("config.") and fn.endswith(".json")) or fn == "config.json":
            continue
        try:
            cfg = json.load(open(os.path.join(HERE, fn), encoding="utf-8"))
        except Exception:
            continue
        uni = cfg.get("university") or ("knruhs" if fn == "config.cloud.json" else fn.split(".")[1])
        label = UNI_LABELS.get(uni, uni.upper())
        for src in cfg.get("sources", []):
            url = src.get("url")
            if not url:
                continue
            key = f"{uni}|{url}"
            seen_now.add(key)
            ok, why = probe(url, cfg)
            if ok:
                streak.pop(key, None)
                if alerted.pop(key, None):
                    back.append(f"✅ {label}: {src.get('name', url)} is reachable again.")
                continue
            streak[key] = streak.get(key, 0) + 1
            log("site problem:", label, url, "-", why, f"(x{streak[key]})")
            if streak[key] >= 2:
                alerted[key] = why
                out.append((f"site-{key}", f"{label} - {src.get('name', 'page')}: {why}.\n{url}\n"
                                           f"Until this is fixed, new {label} notices will be missed."))
    for k in [k for k in streak if k not in seen_now]:
        streak.pop(k, None)
    failing_now = [k for k in seen_now if streak.get(k)]
    if seen_now and len(failing_now) == len(seen_now):
        # every single university failing at once = the Boss's own internet, not the universities
        for k in failing_now:
            streak[k] -= 1
            alerted.pop(k, None) if streak[k] < 2 else None
        return [("boss-network", "The Boss could not reach ANY university website this time - most likely a network problem "
                                 "on GitHub's side, not the universities. Will check again.")], []
    return out, back


# ----------------------------------------------------------------------------- reporting

def tg(token, chat, text):
    if not (token and chat):
        log("no Telegram token/owner chat - would have sent:\n" + text)
        return
    code, js = http_json(f"https://api.telegram.org/bot{token}/sendMessage",
                         data=urllib.parse.urlencode({"chat_id": chat, "text": text[:3900], "disable_web_page_preview": "true"}).encode())
    if code != 200:
        log("telegram send failed:", code, (js or {}).get("description"))


def main():
    e = os.environ.get
    state = {}
    try:
        state = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        pass
    sent = state.setdefault("sent", {})
    problems, stats = [], {}
    cfg = json.load(open(os.path.join(HERE, "config.cloud.json"), encoding="utf-8"))
    for name, fn in (("runs", lambda: check_runs(e("GITHUB_REPOSITORY"), e("GITHUB_TOKEN"))),
                     ("meta", lambda: check_meta(cfg.get("meta", {}), (e("META_PAGE_TOKEN") or "").strip())),
                     ("sources", check_sources)):
        try:
            p, s = fn()
            problems += p
            stats.update(s)
        except Exception as ex:
            problems.append((f"boss-{name}", f"The Boss could not run its '{name}' check: {type(ex).__name__}: {str(ex)[:150]}"))
    try:
        p, back = check_sites(state)
        problems += p
    except Exception as ex:
        back = []
        problems.append(("boss-sites", f"The Boss could not run its website check: {type(ex).__name__}: {str(ex)[:150]}"))
    log(f"{len(problems)} problem(s):", [k for k, _ in problems])

    fresh = []
    t = time.time()
    for key, msg in problems:
        if key.startswith(("dup-", "run-", "slow-")) and key in sent:
            continue  # one-off events: tell once, never repeat
        if t - sent.get(key, 0) > REPEAT_AFTER_H * 3600:
            fresh.append(msg)
            sent[key] = t
    for k in [k for k, v in sent.items() if t - v > 7 * 86400]:
        del sent[k]  # forget very old alerts
    token, owner = (e("TELEGRAM_BOT_TOKEN") or "").strip(), (e("OWNER_CHAT_ID") or "").strip()
    if back:
        tg(token, owner, "\n".join(back))
    if fresh:
        tg(token, owner, "🚨 BOSS BOT - needs your attention\n\n" + "\n\n".join(f"{i + 1}. {m}" for i, m in enumerate(fresh)))

    today = now().strftime("%Y-%m-%d")
    if now().hour >= DIGEST_HOUR_IST and state.get("digest_day") != today:
        lines = [f"📋 BOSS BOT - daily report {now():%d %b}",
                 f"Notice-bot runs (last {stats.get('runs', 0)}): {stats.get('ok', 0)} OK",
                 f"Posted today: Instagram {stats.get('instagram_today', '?')}, Facebook {stats.get('facebook_today', '?')}",
                 f"Universities finding notices: {len(stats.get('active_unis', []))}",
                 ("Open problems: " + str(len(problems))) if problems else "No problems. All good ✅"]
        tg(token, owner, "\n".join(lines))
        state["digest_day"] = today
    if e("BOSS_TEST") == "true":
        tg(token, owner, "Test from the Boss bot: I'm watching your notice bots and will message you here if anything goes wrong.\n"
                         f"Right now: {len(problems)} open problem(s).")
    new = json.dumps(state, indent=1, sort_keys=True)
    old = open(STATE, encoding="utf-8").read() if os.path.exists(STATE) else ""
    if new != old:  # only when an alert or the daily report was sent: no commit noise
        open(STATE, "w", encoding="utf-8").write(new)


if __name__ == "__main__":
    main()
