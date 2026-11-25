"""One-off: post the newest notice of each live university to the Telegram channel, as a text message with the
official link. Reads the token and channel id from the environment (GitHub secrets); prints neither."""
import json, os, glob, html, time, urllib.request, urllib.parse
from firebase_sync import UNI_LABELS, STREAM_LABELS, TYPE_EMOJI

token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
order = ["knruhs", "ntruhs", "rguhs", "tnmgrmu", "abvmu", "ruhs", "wbuhs", "bfuhs", "uhsr", "cgau", "buhs", "amru"]
for uni in order:
    path = f"site/feed-{uni}.json"
    if not os.path.exists(path):
        print(uni, "no feed"); continue
    ns = json.load(open(path))["notices"]
    if not ns:
        print(uni, "empty"); continue
    n = max(ns, key=lambda x: x.get("published_at") or x.get("detected_at") or "")
    e = html.escape
    head = f"{TYPE_EMOJI.get(n['type'], '📌')} <b>{e(UNI_LABELS.get(uni, uni.upper()))}</b> · {e(STREAM_LABELS.get(n['stream'], 'All courses'))}"
    text = f"{head}\n\n<b>{e(n['title'])}</b>\n\n{e(n.get('summary') or '')[:600]}\n\n🗓 {e(n.get('published_day') or '')}\n📄 <a href=\"{e(n['source_url'], quote=True)}\">Official notice</a>"
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data), timeout=30))
        print(uni, "sent" if r.get("ok") else "failed", n["title"][:70])
    except Exception as ex:
        print(uni, "failed", type(ex).__name__, getattr(ex, "code", ""))
    time.sleep(4)
