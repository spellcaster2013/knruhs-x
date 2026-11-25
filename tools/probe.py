"""One-off check (removed again after use): can GitHub reach a site, and which notice links does it show? Prints only."""
import re, sys, urllib.request, urllib.parse, html, ssl
UA = "KNRUHS-Pulse-Bot/0.1 (student notice alerts; unofficial)"
for url in sys.argv[1:]:
    print("=" * 90); print("URL", url)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=40) as r:
            body = r.read(6_000_000).decode("utf-8", "ignore"); print("STATUS", r.status, "FINAL", r.geturl(), "BYTES", len(body))
    except Exception as e:
        print("ERROR", type(e).__name__, str(e)[:200]); continue
    if url.endswith(".js"):
        for m in sorted(set(re.findall(r'["\'`]((?:https?:)?/?/?[A-Za-z0-9_./:-]*(?:api|Api|API|notification|Notification|circular|Circular|news|News)[A-Za-z0-9_./?=&{}$-]{0,90})["\'`]', body)))[:150]: print("JSURL", m)
        for m in sorted(set(re.findall(r'https?://[A-Za-z0-9.-]+(?::\d+)?(?:/[A-Za-z0-9_./-]{0,40})?', body)))[:80]: print("HOST", m)
        for m in re.finditer(r'\.getWhatsNewsData\(|\.getListWebLinkData\(|\.getListDataForWebDept\(', body):
            print("CTX", body[max(0, m.start()-700):m.end()+500].replace("\n", " "))
        continue
    if "--raw" in sys.argv[0:1] or len(body) < 20000:
        print("RAW", body[:6000].replace("\n", " "))
        for m in sorted(set(re.findall(r'(?:src|href)=["\']([^"\']+\.js[^"\']*)', body))): print("SCRIPT", m)
    if body.lstrip()[:1] in "[{":
        print("JSON", body[:3000].replace("\n", " ")); continue
    links = re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', body, re.S | re.I)
    print("LINKS", len(links)); n = 0
    for href, txt in links:
        t = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(txt)).split())[:100]
        u = urllib.parse.urljoin(url, html.unescape(href))
        if re.search(r"\.pdf|notice|notification|circular|news|upload", u + t, re.I):
            print("  ", t, "->", u[:160]); n += 1
            if n >= 50: break
