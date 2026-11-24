"""One-off check: can the bot reach these sites from GitHub, and what notice links do they show? (prints only)"""
import re, sys, urllib.request, urllib.parse, html, ssl, certifi
UA = "Mozilla/5.0 (KNRUHS-Pulse-Bot/0.1; student notice alerts)"
ctx = ssl.create_default_context(cafile=certifi.where())
for url in sys.argv[1:]:
    print("=" * 100); print("URL", url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
        with urllib.request.urlopen(req, timeout=40, context=ctx) as r:
            body = r.read(6_000_000).decode("utf-8", "ignore"); print("STATUS", r.status, "FINAL", r.geturl(), "BYTES", len(body))
    except Exception as e:
        print("ERROR", type(e).__name__, str(e)[:200]); continue
    links = re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', body, re.S | re.I)
    print("LINKS", len(links))
    shown = 0
    for href, txt in links:
        t = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(txt)).split())[:110]
        u = urllib.parse.urljoin(url, html.unescape(href))
        if re.search(r"\.pdf|notice|notification|news|uploads|viewnbe|document|current", u, re.I):
            print("  ", t, "->", u[:170]); shown += 1
            if shown >= 45: break
