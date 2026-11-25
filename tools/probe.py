import sys, re, urllib.request, html
for u in sys.argv[1:]:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (KNRUHS-X notice bot)"})
        b = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
    except Exception as e:
        print("PAGE", u, "ERR", e); continue
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', b, re.S | re.I)
    pdf = [(h, re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", "", t))).strip()) for h, t in links if ".pdf" in h.lower()]
    print("PAGE", u, "bytes", len(b), "links", len(links), "pdfs", len(pdf))
    for h, t in pdf[:40]:
        print("  ~", t[:110], "~", h)
