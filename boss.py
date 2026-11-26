"""boss.py - the Boss bot. It manages the worker bots; it never visits university websites itself.

Every worker (one per university, run by pulse.yml) checks its own website on each run and files a report with the
Boss (Firestore bot_health/<university>). The Boss runs on its own schedule (boss.yml), reads those reports and the
workers' run records on GitHub, and sends YOU a private Telegram message (OWNER_CHAT_ID) only when something is wrong.
Once a day (9 PM) it sends a short roll-call of all workers, so you also know the Boss itself is alive.

What the Boss watches
  Runs        a worker run failed / was cancelled / timed out / ran too long, or no good run for over an hour
  Silence     a worker has not reported for over an hour
  Websites    a worker says its university page will not open, and why: address gone (domain changed/expired),
              not responding, server error / maintenance page, page moved (404), now redirects to another
              website, blocking the bot, broken certificate - after 2 failed runs in a row (no alarm for a blip).
              You also hear when it is back.
  Redesign    the page opens but the worker finds 0 notice links (site redesigned - the worker needs a fix)
  Quiet       a university with nothing new for many days
  Posting     post errors, notices the duplicate guard stopped, the Facebook/Instagram key expiring
Each problem is reported once, then again only if it is still there 12 hours later (no spam).

Secrets (environment): TELEGRAM_BOT_TOKEN, OWNER_CHAT_ID, GITHUB_TOKEN, FIREBASE_SA.
"""
import hashlib, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from firebase_sync import UNI_LABELS
except Exception:
    UNI_LABELS = {}
IST = timezone(timedelta(hours=5, minutes=30))
STATE = os.path.join(HERE, "boss_state.json")
REPEAT_AFTER_H = 12
DIGEST_HOUR_IST = 21
RUN_TOO_SLOW_MIN = 25
SILENT_MIN = 75
PAGE_FAIL_RUNS = 2
ZERO_LINKS_HOURS = 2
QUIET_DAYS = {"knruhs": 4}
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


def h(text):
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def workers():
    """Every worker the notice bot runs: one per config file (config.cloud.json is KNRUHS)."""
    out = {}
    for fn in sorted(os.listdir(HERE)):
        if fn.startswith("config.") and fn.endswith(".json") and fn != "config.json":
            try:
                cfg = json.load(open(os.path.join(HERE, fn), encoding="utf-8"))
            except Exception:
                continue
            uni = cfg.get("university") or ("knruhs" if fn == "config.cloud.json" else fn.split(".")[1])
            out[uni] = UNI_LABELS.get(uni, uni.upper())
    return out


# ----------------------------------------------------------------------------- 1. worker runs (GitHub's record)

def check_runs(repo, token):
    out, stats = [], {}
    if not (repo and token):
        return out, stats
    hd = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    code, js = http_json(f"https://api.github.com/repos/{repo}/actions/workflows/pulse.yml/runs?per_page=12", headers=hd)
    if code != 200:
        return [("runs-api", f"Could not read the workers' runs from GitHub (HTTP {code}).")], stats
    done = [r for r in js.get("workflow_runs", []) if r.get("status") == "completed"]
    stats["runs"], stats["runs_ok"] = len(done), sum(1 for r in done if r.get("conclusion") == "success")
    for r in done[:4]:
        t = datetime.fromisoformat(r["run_started_at"].replace("Z", "+00:00")).astimezone(IST)
        c = r.get("conclusion")
        try:
            ran = (datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00")) - datetime.fromisoformat(r["run_started_at"].replace("Z", "+00:00"))).total_seconds()
        except Exception:
            ran = 999
        if c == "cancelled" and ran < 90:
            continue  # a queued run that GitHub replaced with a newer one - it never did anything
        if c in ("failure", "cancelled", "timed_out", "startup_failure"):
            out.append((f"run-{r['id']}", f"Worker run at {t:%H:%M} ended as '{c}'.\n{r['html_url']}"))
        try:
            mins = (datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00")) - datetime.fromisoformat(r["run_started_at"].replace("Z", "+00:00"))).total_seconds() / 60
        except Exception:
            mins = 0
        if mins > RUN_TOO_SLOW_MIN:
            out.append((f"slow-{r['id']}", f"A worker run at {t:%H:%M} took {mins:.0f} min (limit 40) - a university website may be hanging.\n{r['html_url']}"))
    good = [r for r in done if r.get("conclusion") == "success"]
    if good:
        gap = (datetime.now(timezone.utc) - datetime.fromisoformat(good[0]["updated_at"].replace("Z", "+00:00"))).total_seconds() / 60
        if gap > SILENT_MIN:
            out.append(("no-good-run", f"No successful worker run for {gap:.0f} minutes. GitHub may have paused the schedule - "
                                       f"open Actions > KNRUHS notice bot > Run workflow."))
    elif done:
        out.append(("no-good-run", "None of the recent worker runs succeeded."))
    return out, stats


# ----------------------------------------------------------------------------- 2. worker reports

def read_reports(sa_json):
    from firebase_sync import FirebaseSync, fields_of
    fs = FirebaseSync(sa_json, log, os.environ.get("GITHUB_REPOSITORY"), HERE)
    reps = {}
    for doc in fs.list_all("bot_health"):
        f = fields_of(doc)
        reps[f.get("uni") or doc["name"].rsplit("/", 1)[-1]] = f
    return reps


def check_reports(reps, state):
    out, back, roll = [], [], []
    alerted = state.setdefault("page_alerted", {})
    t_now = now()
    for uni, label in workers().items():
        r = reps.get(uni)
        if not r:
            roll.append(f"❔ {label}: no report yet")  # a brand-new worker; its first run will file one
            continue
        try:
            age = (t_now - datetime.fromisoformat(r["last_run"])).total_seconds() / 60
        except Exception:
            age = 1e9
        if age > SILENT_MIN:
            out.append((f"silent-{uni}", f"{label}: the worker has not reported for {age / 60:.1f} hours - it may be crashing before it finishes."))
        bad = [s for s in (r.get("sources") or []) if isinstance(s, dict) and not s.get("ok")]
        for s in r.get("sources") or []:
            if not isinstance(s, dict):
                continue
            key = f"page-{uni}-{h(s.get('url', ''))}"
            if not s.get("ok") and int(s.get("fail_runs") or 0) >= PAGE_FAIL_RUNS:
                alerted[key] = s.get("reason", "")
                out.append((key, f"{label} - {s.get('name') or 'page'}: {s.get('reason')}.\n{s.get('url')}\n"
                                 f"Failed {s.get('fail_runs')} runs in a row. Until it is fixed, new {label} notices are missed."))
            elif s.get("ok") and alerted.pop(key, None) is not None:
                back.append(f"✅ {label} - {s.get('name') or 'page'} is reachable again.")
        z = r.get("zero_links_since")
        if z and not bad:
            try:
                hrs = (t_now - datetime.fromisoformat(z)).total_seconds() / 3600
            except Exception:
                hrs = 0
            if hrs >= ZERO_LINKS_HOURS:
                out.append((f"zero-{uni}", f"{label}: the website opens, but the worker has found 0 notice links for {hrs:.0f} hours. "
                                           f"The site was probably redesigned - the worker needs updating."))
        if r.get("last_new"):
            try:
                days = (t_now - datetime.fromisoformat(r["last_new"])).total_seconds() / 86400
            except Exception:
                days = 0
            if days > QUIET_DAYS.get(uni, QUIET_DAYS_DEFAULT):
                out.append((f"quiet-{uni}", f"{label}: nothing new for {days:.0f} days. Unusual - check the website still lists notices where the worker looks."))
        for e in r.get("post_errors") or []:
            out.append((f"posterr-{uni}-{h(e)}", f"{label}: posting failed - {e}"))
        if r.get("check_error"):
            out.append((f"checkerr-{uni}-{h(r['check_error'])}", f"{label}: the worker hit an error - {r['check_error'][:200]}"))
        for t in r.get("duplicates_blocked") or []:
            out.append((f"dupblock-{uni}-{h(t)}", f"{label}: the duplicate guard stopped a repeat post (it was already on the channel): {t}. "
                                                  f"Nothing to do - a run lost its memory and the guard caught it."))
        if r.get("db_ok") is False:
            out.append(("db-reads", "The app database (Firestore) refused the workers' reads (429 Too Many Requests) - usually the "
                                    "daily free limit is used up. Admin actions are ignored and students may fail to sign in or "
                                    "save settings until it resets (around 1:30 PM IST)."))
        if r.get("meta_token_ok") is False:
            out.append(("meta-token", "The Facebook/Instagram key stopped working - nothing is being posted there until it is renewed."))
        dl = r.get("meta_token_days_left")
        if isinstance(dl, (int, float)) and 0 <= dl < 7:
            out.append(("meta-token-expiry", f"The Facebook/Instagram key expires in {dl:.1f} days - renew it before then."))
        mark = "✅" if not bad and age <= SILENT_MIN and not r.get("zero_links_since") else "⚠️"
        roll.append(f"{mark} {label}: {r.get('links_found', 0)} links" + (f", {len(bad)} page problem(s)" if bad else ""))
    return out, back, roll


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
    try:
        state = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        state = {}
    sent = state.setdefault("sent", {})
    problems, stats, back, roll = [], {}, [], []
    try:
        p, stats = check_runs(e("GITHUB_REPOSITORY"), e("GITHUB_TOKEN"))
        problems += p
    except Exception as ex:
        problems.append(("boss-runs", f"The Boss could not read the workers' runs: {type(ex).__name__}: {str(ex)[:150]}"))
    if (e("FIREBASE_SA") or "").strip():
        try:
            p, back, roll = check_reports(read_reports(e("FIREBASE_SA")), state)
            problems += p
        except Exception as ex:
            problems.append(("boss-reports", f"The Boss could not read the workers' reports: {type(ex).__name__}: {str(ex)[:150]}"))
    else:
        problems.append(("boss-reports", "The Boss has no access to the workers' reports (FIREBASE_SA secret missing)."))
    log(f"{len(problems)} problem(s):", [k for k, _ in problems])

    fresh, t = [], time.time()
    for key, msg in problems:
        if key.startswith(("run-", "slow-", "dupblock-")) and key in sent:
            continue  # one-off events: tell once
        if t - sent.get(key, 0) > REPEAT_AFTER_H * 3600:
            fresh.append(msg)
            sent[key] = t
    for k in [k for k, v in sent.items() if t - v > 7 * 86400]:
        del sent[k]
    token, owner = (e("TELEGRAM_BOT_TOKEN") or "").strip(), (e("OWNER_CHAT_ID") or "").strip()
    if back:
        tg(token, owner, "\n".join(back))
    if fresh:
        tg(token, owner, "🚨 BOSS BOT - needs your attention\n\n" + "\n\n".join(f"{i + 1}. {m}" for i, m in enumerate(fresh)))

    today = now().strftime("%Y-%m-%d")
    if (now().hour >= DIGEST_HOUR_IST and state.get("digest_day") != today) or e("BOSS_TEST") == "true":
        head = "📋 BOSS BOT - test report" if e("BOSS_TEST") == "true" else f"📋 BOSS BOT - daily report {now():%d %b}"
        lines = [head, f"Worker runs (last {stats.get('runs', 0)}): {stats.get('runs_ok', 0)} OK", ""] + roll
        lines += ["", f"Open problems: {len(problems)}" if problems else "No problems. All good ✅"]
        tg(token, owner, "\n".join(lines))
        if e("BOSS_TEST") != "true":
            state["digest_day"] = today
    new = json.dumps(state, indent=1, sort_keys=True)
    old = open(STATE, encoding="utf-8").read() if os.path.exists(STATE) else ""
    if new != old:
        open(STATE, "w", encoding="utf-8").write(new)


if __name__ == "__main__":
    main()
