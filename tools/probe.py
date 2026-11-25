import sys, re, urllib.request, html
for u in sys.argv[1:]:
    b = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=40).read().decode("utf-8", "replace")
    rows = [r for r in re.findall(r"(?is)<tr[^>]*>.*?</tr>", b) if ".pdf" in r.lower()]
    print("PAGE", u, "rows", len(rows), "pager", re.findall(r'href="([^"]*page=\d+[^"]*)"', b)[:4])
    for r in rows[:3]: print("  ROW", re.sub(r"\s+", " ", r)[:700])
    for r in rows[:12]: print("  TXT", re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", " ", r))).strip()[:200])
