import sys, re, urllib.request, html
for u in sys.argv[1:]:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (KNRUHS-X notice bot)"})
        b = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
    except Exception as e:
        print("PAGE", u, "ERR", e); continue
    print("PAGE", u, len(b))
    for m in re.findall(r'<(?:iframe|embed|object)[^>]+(?:src|data)="([^"]+)"', b, re.I): print("  ~ FRAME ~", m)
    main = b.split('id="main"', 1)[-1]
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', main, re.S | re.I)
    seen = 0
    for h, t in links:
        t = re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", "", t))).strip()
        if "ggsmch.org/" in h and not re.search(r"\.(pdf|jpe?g|png)|uploads/", h) : continue
        print("  ~", t[:110], "~", h); seen += 1
        if seen > 45: break
    txt = re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", " ", re.sub(r"(?s)<(script|style).*?</\1>", "", main))))
    print("  TEXT", txt[:600])
