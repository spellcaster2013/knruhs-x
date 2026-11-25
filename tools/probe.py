import sys, re, urllib.request, html
for u in sys.argv[1:]:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (KNRUHS-X notice bot)"})
        r = urllib.request.urlopen(req, timeout=40); b = r.read().decode("utf-8", "replace")
    except Exception as e:
        print("PAGE", u, "ERR", e); continue
    print("PAGE", u, r.geturl(), len(b))
    if u.endswith("robots.txt"): print("  ROBOTS", b[:400].replace("\n"," | ")); continue
    for m in re.findall(r'<(?:iframe|embed)[^>]+src="([^"]+)"', b, re.I): print("  ~ FRAME ~", m)
    links = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', b, re.S | re.I)
    n = 0
    for h, t in links:
        t = re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", "", t))).strip()
        if re.search(r"\.pdf|notice|news|result|circular|notification|exam|admission|download", h + " " + t, re.I):
            print("  ~", t[:110], "~", h); n += 1
            if n > 70: break
