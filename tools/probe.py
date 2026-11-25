import sys, re, urllib.request, html, ssl
for u in sys.argv[1:]:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (KNRUHS-X notice bot)"})
        r = urllib.request.urlopen(req, timeout=40); b = r.read().decode("utf-8", "replace")
    except Exception as e:
        print("PAGE", u, "ERR", str(e)[:150]); continue
    print("PAGE", u, "->", r.geturl(), len(b))
    if u.endswith("robots.txt"):
        print("  ROBOTS", " | ".join(l for l in b.splitlines() if re.match(r"(?i)\s*(user-agent|disallow|allow)", l))[:300]); continue
    links = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', b, re.S | re.I)
    pdfs = 0; menu = 0
    for h, t in links:
        t = re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", "", t))).strip()
        if ".pdf" in h.lower():
            pdfs += 1
            if pdfs <= 14: print("  PDF ~", t[:100], "~", h[:160])
        elif re.search(r"notice|news|result|circular|notification|exam|admission|announce|counsel|latest|whats", h + " " + t, re.I):
            menu += 1
            if menu <= 18: print("  MENU ~", t[:60], "~", h[:160])
    print("  totals pdf", pdfs, "menu", menu)
