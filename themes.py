"""themes.py - one visual identity + brand per university, used by studio.py.

Adding a university = one entry in THEMES (and, if it needs a new motif, one motif_* function below).
Every motif is drawn with plain PIL shapes (no logos, emblems or seals) on a transparent layer at low opacity.
"""
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

W, H = 1080, 1350


def hx(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


# ----------------------------------------------------------------------------- the table
# short     : display name on slides and in captions
# disc_from : who the disclaimer says we are NOT ("Unofficial alert, not from X.")
# tags      : the two university hashtags
# brand     : default page name; handle "" = no handle / Telegram line anywhere
# light     : light background (dark text)
THEMES = {
    "knruhs": dict(short="KNRUHS", tags=["#KNRUHS", "#Telangana"], brand="KNRUHS Updates", handle="t.me/knruhsx",
                   bg="#0E4F5C", accent="#E9B44C", text="#FFFFFF", motif="thoranam", motif_alpha=45),
    "ntruhs": dict(short="Dr. NTR UHS", tags=["#NTRUHS", "#AndhraPradesh"], brand="NTRUHS Updates",
                   bg="#8F3518", accent="#F3DDB3", text="#FFFFFF", motif="stupa", motif_alpha=50),
    "rguhs": dict(short="RGUHS", tags=["#RGUHS", "#Karnataka"], brand="RGUHS Updates",
                  bg="#4A2C7D", accent="#F2C14E", text="#FFFFFF", motif="hoysala_star", motif_alpha=40),
    "tnmgrmu": dict(short="TN Dr. MGR Medical Univ.", tags=["#TNMGRMU", "#TamilNadu"], brand="TNMGRMU Updates",
                    bg="#7D1A52", accent="#F5B700", text="#FFFFFF", motif="kolam", motif_alpha=55),
    "abvmu": dict(short="ABVMU", tags=["#ABVMU", "#UttarPradesh"], brand="ABVMU Updates",
                  bg="#2B5C8A", accent="#F8CBD4", text="#FFFFFF", motif="awadhi_arch", motif_alpha=45),
    "ruhs": dict(short="RUHS", tags=["#RUHS", "#Rajasthan"], brand="RUHS Updates", light=True,
                 bg="#F4CDBE", accent="#8E2F0C", text="#2A1410", motif="jaali", motif_alpha=70),
    "wbuhs": dict(short="WBUHS", tags=["#WBUHS", "#WestBengal"], brand="WBUHS Updates", light=True,
                  bg="#FBF7F0", accent="#B3121B", text="#1A1A1A", motif="laal_paar", motif_alpha=45),
    "bfuhs": dict(short="BFUHS", tags=["#BFUHS", "#Punjab"], brand="BFUHS Updates", light=True,
                  bg="#E9A81E", accent="#6E0D3E", text="#1A1206", motif="phulkari", motif_alpha=80),
    "uhsr": dict(short="UHS Rohtak", tags=["#UHSR", "#Haryana"], brand="UHSR Updates",
                 bg="#4B5E1C", accent="#F0D78C", text="#FFFFFF", motif="wheat", motif_alpha=50),
    "cgau": dict(short="CG Health & Ayush Univ.", tags=["#CGAyushUniversity", "#Chhattisgarh"], brand="CG Ayush Univ Updates",
                 bg="#4A2F1B", accent="#E3A56B", text="#FFFFFF", motif="gond_leaf", motif_alpha=50),
    "buhs": dict(short="BUHS", tags=["#BUHS", "#Bihar"], brand="BUHS Updates",
                 bg="#1C1917", accent="#E4572E", accent2="#F2C230", text="#F8F1E4", motif="madhubani", motif_alpha=90),
    "amru": dict(short="AMRU", tags=["#AMRU", "#HimachalPradesh"], brand="AMRU Updates", light=True,
                 bg="#E6F0F4", accent="#2D5A3D", text="#102027", motif="pahadi", motif_alpha=45),
    "national": dict(short="All-India", disc_from="MCC/NMC", tags=["#MCC", "#NMC"], brand="All-India Med Updates",
                     bg="#0B1F3A", accent="#FF9933", accent2="#138808", text="#FFFFFF", motif="tricolour", motif_alpha=70),
}
OLD_DEFAULT_BRAND = "KNRUHS Updates"  # copied into every config; means "not set" for any university but KNRUHS


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _legible(col, toward, against, target=4.6):
    """Move `col` toward `toward` until it reaches `target` contrast against every colour in `against` (WCAG AA)."""
    for k in range(0, 21):
        c = mix(col, toward, k / 20)
        if all(contrast(c, b) >= target for b in against):
            return c
    return toward


def theme(uni):
    """Resolved colours for a university (unknown ids fall back to KNRUHS colours with their own name)."""
    uni = (uni or "knruhs").lower()
    raw = dict(THEMES.get(uni) or dict(THEMES["knruhs"], short=uni.upper(), tags=["#" + uni.upper()], brand=uni.upper() + " Updates", handle=""))
    t = dict(raw, id=uni)
    t.setdefault("light", False)
    t.setdefault("handle", "")
    t.setdefault("disc_from", t["short"])
    for k in ("bg", "accent", "text"):
        t[k] = hx(raw[k])
    t["accent2"] = hx(raw.get("accent2") or raw["accent"])
    bg, tx = t["bg"], t["text"]
    t["bg2"] = mix(bg, (0, 0, 0), 0.22) if not t["light"] else mix(bg, (0, 0, 0), 0.05)
    # every background the small grey text can sit on: top of the gradient, the footer row, the bottom
    grounds = [bg, mix(bg, t["bg2"], 0.85), t["bg2"]]
    t["muted"] = _legible(mix(tx, bg, 0.30), tx, grounds)
    t["card"] = mix(bg, tx, 0.10) if not t["light"] else mix(bg, (255, 255, 255), 0.55)
    t["line"] = mix(tx, bg, 0.62)
    # text on an accent-coloured chip: dark ink on dark themes (accents there are light), else the light background
    t["on_accent"] = (20, 20, 20) if not t["light"] else (255, 255, 255)
    # the accent used for words on the background itself (bfuhs: plum on mustard reads fine; all others too)
    t["ink_accent"] = _legible(t["accent"], tx, grounds[:2], 4.5)
    return t


def brand(uni, cfg):
    """(name, tagline, handle). A config's brand wins only when it is not the old copied 'KNRUHS Updates' default."""
    t = theme(uni)
    name = (cfg.get("brand_name") or "").strip()
    handle = (cfg.get("handle") or "").strip()
    tag = (cfg.get("brand_tagline") or "").strip()
    if t["id"] == "knruhs":
        return name or t["brand"], tag, handle if name else t["handle"]
    if name and name != OLD_DEFAULT_BRAND:
        return name, tag, handle
    return t["brand"], "", t["handle"]


# ----------------------------------------------------------------------------- background + motif
SS = 2  # motifs are drawn at 2x and scaled down (smooth edges)


def background(t, strength=1.0, avoid=None, avoid_body=None, fade=None):
    """Theme background with its motif.

    strength < 1 fades the motif (used behind dense text).
    avoid      : L mask (255 = text, padded) of everything on the slide. Movable motif parts (zone br/bl/b/tr) are
                 scaled towards their corner until they no longer touch it.
    avoid_body : the same for the content block only (header/footer left out); used by top-right motifs, which can
                 never clear the header pill.
    fade       : L mask (tight) where the motif is capped at FADE_CAP alpha whatever happens (no motif behind text).
    """
    top, bot = t["bg"], t["bg2"]
    im = Image.new("RGB", (W, H), top)
    d = ImageDraw.Draw(im)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=mix(top, bot, y / (H - 1)))
    parts = MOTIFS.get(t["motif"]) or []
    if not parts:
        return im
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for fn, zone in parts:
        part = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
        fn(part, t)
        part = part.resize((W, H), Image.LANCZOS)
        if zone != "fixed" and avoid is not None:
            part = _fit(part, zone, avoid_body if (zone == "tr" and avoid_body is not None) else avoid)
        if strength != 1.0 and zone != "fixed":  # frames and edge bands stay identical on every slide of a carousel
            part.putalpha(part.getchannel("A").point(lambda v: int(v * strength)))
        layer.alpha_composite(part)
    alpha = layer.getchannel("A")
    if fade is not None:
        soft = ImageChops.lighter(fade, fade.filter(ImageFilter.GaussianBlur(4)))  # hard inside, feathered outside
        alpha = Image.composite(alpha.point(lambda v: min(v, FADE_CAP)), alpha, soft)
    layer.putalpha(alpha)
    LAST["alpha"] = alpha  # for the layout checks (tools/tests): the motif alpha actually painted
    return Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")


LAST = {}
FADE_CAP = 20          # max motif alpha (of 255, ~8 %) left behind any text
FIT_MIN = 0.4          # a movable motif never shrinks below 40 % of its size
ZONE_OF = {}           # motif name -> zone of its movable part ("fixed" if none); filled after MOTIFS


def _fit(part, zone, mask):
    """Scale the part towards its corner (br, bl, tr) or squash it towards the bottom edge (b) until it clears `mask`."""
    a = part.getchannel("A").point(lambda v: 255 if v > 6 else 0)
    box = a.getbbox()
    if not box:
        return part
    x0, y0, x1, y1 = box
    crop = part.crop(box)
    cw, ch = crop.size
    k = 1.0
    best = None
    kmin = 0.25 if zone == "b" else FIT_MIN   # a full-width band may flatten further (it lives below the footer)
    while k >= kmin - 1e-6:
        w, h = max(1, int(cw * (k if zone != "b" else 1))), max(1, int(ch * k))
        if zone == "br":
            pos = (x1 - w, y1 - h)
        elif zone == "bl":
            pos = (x0, y1 - h)
        elif zone == "tr":
            pos = (x1 - w, y0)
        else:  # b: full-width band, keep the width, lower the height
            pos = (x0, y1 - h)
        sc = crop if k == 1.0 else crop.resize((w, h), Image.LANCZOS)
        sa = sc.getchannel("A").point(lambda v: 255 if v > 6 else 0)
        under = mask.crop((pos[0], pos[1], pos[0] + w, pos[1] + h))
        best = (sc, pos)
        if ImageChops.multiply(sa, under).getbbox() is None:
            break
        k = round(k - 0.04, 2)
    out = Image.new("RGBA", part.size, (0, 0, 0, 0))
    out.alpha_composite(best[0], best[1])
    return out


def _c(col, a):
    return tuple(col) + (int(a),)


def _s(v):
    return v * SS


def _poly(cx, cy, r_out, r_in, n, rot=0.0):
    pts = []
    for i in range(n * 2):
        r = r_out if i % 2 == 0 else r_in
        a = rot + math.pi * i / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def motif_thoranam(L, t):
    """Kakatiya thoranam: two pillars, two lintels, a pointed arch between them (bottom-right)."""
    d = ImageDraw.Draw(L)
    a = t["motif_alpha"]
    col = t["accent"]
    s = _s
    x0, x1, base, top = 650, 1010, 1300, 860  # pillars stand on base, lintels at top
    pw = 46
    lw = 8
    for px in (x0, x1 - pw):
        d.rectangle([s(px), s(top + 40), s(px + pw), s(base)], fill=_c(col, a * 0.5), outline=_c(col, a * 1.8), width=s(lw // 2))
        for k in range(4):  # carved bands
            yy = top + 120 + k * 90
            d.line([s(px), s(yy), s(px + pw), s(yy)], fill=_c(col, a * 1.8), width=s(3))
    # two lintels, the upper one wider with flared ends
    d.rectangle([s(x0 - 24), s(top + 10), s(x1 + 24), s(top + 40)], fill=_c(col, a * 0.9), outline=_c(col, a * 1.8), width=s(3))
    d.polygon([(s(x0 - 50), s(top - 30)), (s(x1 + 50), s(top - 30)), (s(x1 + 30), s(top - 4)), (s(x0 - 30), s(top - 4))], fill=_c(col, a * 1.0))
    # pointed (ogee-ish) arch under the lintel, between the pillars
    cx = (x0 + x1) / 2
    ix0, ix1 = x0 + pw + 10, x1 - pw - 10
    spring = top + 210
    apex = top + 60
    pts_l = [(ix0 + (cx - ix0) * math.sin(u * math.pi / 2) , spring - (spring - apex) * (u ** 1.6)) for u in [i / 20 for i in range(21)]]
    pts_r = [(2 * cx - x, y) for x, y in reversed(pts_l)]
    d.line([(s(x), s(y)) for x, y in pts_l + pts_r], fill=_c(col, a * 1.9), width=s(lw), joint="curve")
    d.line([(s(ix0), s(spring)), (s(ix0), s(base))], fill=_c(col, a * 1.8), width=s(4))
    d.line([(s(ix1), s(spring)), (s(ix1), s(base))], fill=_c(col, a * 1.8), width=s(4))
    # swan-like finials on top
    for fx in (x0 + 10, cx, x1 - 10):
        d.polygon([(s(fx - 16), s(top - 30)), (s(fx + 16), s(top - 30)), (s(fx), s(top - 80))], fill=_c(col, a * 1.0))


def motif_stupa(L, t):
    """Amaravati stupa: dome on a drum, harmika and chhatra on top, railing in front (bottom-right)."""
    d = ImageDraw.Draw(L)
    a = t["motif_alpha"]
    col = t["accent"]
    s = _s
    cx, base = 840, 1290
    r = 220
    drum_top = base - 110
    d.pieslice([s(cx - r), s(drum_top - r), s(cx + r), s(drum_top + r)], 180, 360, fill=_c(col, a * 0.6), outline=_c(col, a * 1.8), width=s(5))
    for k in (0.35, 0.65):  # garland bands across the dome
        rr = r * (1 - k * 0.15)
        yy = drum_top - r * k
        half = math.sqrt(max(r * r - (r * k) ** 2, 0))
        d.line([(s(cx - half), s(yy)), (s(cx + half), s(yy))], fill=_c(col, a * 1.4), width=s(3))
    d.rectangle([s(cx - r - 20), s(drum_top), s(cx + r + 20), s(base)], fill=_c(col, a * 0.45), outline=_c(col, a * 1.6), width=s(4))
    # harmika + chhatra
    ht = drum_top - r
    d.rectangle([s(cx - 34), s(ht - 46), s(cx + 34), s(ht + 4)], fill=_c(col, a * 1.0), outline=_c(col, a * 1.8), width=s(3))
    d.line([(s(cx), s(ht - 46)), (s(cx), s(ht - 130))], fill=_c(col, a * 1.8), width=s(5))
    for k, w in enumerate((70, 54, 38)):
        yy = ht - 66 - k * 24
        d.line([(s(cx - w), s(yy)), (s(cx + w), s(yy))], fill=_c(col, a * 1.8), width=s(5))
    # railing (vedika): posts + three rails
    x0, x1, rt = cx - r - 70, cx + r + 70, base - 80
    for k in range(3):
        yy = rt + 12 + k * 22
        d.line([(s(x0), s(yy)), (s(x1), s(yy))], fill=_c(col, a * 1.6), width=s(4))
    px = x0
    while px <= x1:
        d.rectangle([s(px), s(rt), s(px + 14), s(base)], fill=_c(col, a * 1.4))
        px += 46


def motif_hoysala_star(L, t):
    """Hoysala 32-point stellate star, partly off the top-right corner."""
    d = ImageDraw.Draw(L)
    a = t["motif_alpha"]
    col = t["accent"]
    s = _s
    cx, cy = 1000, 120
    big = _poly(cx, cy, 400, 330, 32, -math.pi / 2)
    d.polygon([(s(x), s(y)) for x, y in big], fill=_c(col, a * 0.5), outline=_c(col, a * 1.8))
    d.line([(s(x), s(y)) for x, y in big + big[:1]], fill=_c(col, a * 1.8), width=s(4))
    mid = _poly(cx, cy, 300, 250, 16, -math.pi / 2)
    d.line([(s(x), s(y)) for x, y in mid + mid[:1]], fill=_c(col, a * 1.8), width=s(4))
    d.ellipse([s(cx - 200), s(cy - 200), s(cx + 200), s(cy + 200)], outline=_c(col, a * 1.6), width=s(4))
    inner = _poly(cx, cy, 160, 110, 8, -math.pi / 2)
    d.polygon([(s(x), s(y)) for x, y in inner], fill=_c(col, a * 0.7))
    d.ellipse([s(cx - 60), s(cy - 60), s(cx + 60), s(cy + 60)], outline=_c(col, a * 1.8), width=s(4))


def motif_kolam(L, t):
    """Kolam: a 7x7 dot grid with loops around the dots (top-right)."""
    d = ImageDraw.Draw(L)
    a = t["motif_alpha"]
    col = t["accent"]
    s = _s
    n, gap = 7, 50
    ox, oy = W - 40 - (n - 1) * gap, 40
    for i in range(n):
        for j in range(n):
            x, y = ox + i * gap, oy + j * gap
            d.ellipse([s(x - 6), s(y - 6), s(x + 6), s(y + 6)], fill=_c(col, a * 1.8))
    # loops: diamonds threading between dots, plus circles around alternate dots
    for i in range(n - 1):
        for j in range(n - 1):
            cx, cy = ox + (i + 0.5) * gap, oy + (j + 0.5) * gap
            if (i + j) % 2 == 0:
                r = gap * 0.5
                d.polygon([(s(cx), s(cy - r)), (s(cx + r), s(cy)), (s(cx), s(cy + r)), (s(cx - r), s(cy))], outline=_c(col, a * 1.5))
                d.line([(s(cx), s(cy - r)), (s(cx + r), s(cy)), (s(cx), s(cy + r)), (s(cx - r), s(cy)), (s(cx), s(cy - r))], fill=_c(col, a * 1.5), width=s(3))
    for i in range(n):
        for j in range(n):
            if (i + j) % 2 == 1:
                x, y = ox + i * gap, oy + j * gap
                d.ellipse([s(x - 20), s(y - 20), s(x + 20), s(y + 20)], outline=_c(col, a * 1.4), width=s(3))
    cx, cy, R = ox + (n - 1) * gap / 2, oy + (n - 1) * gap / 2, (n - 1) * gap / 2 + 40
    d.line([(s(cx), s(cy - R)), (s(cx + R), s(cy)), (s(cx), s(cy + R)), (s(cx - R), s(cy)), (s(cx), s(cy - R))], fill=_c(col, a * 1.5), width=s(4), joint="curve")


def _buti(d, x, y, col, a, r=10):
    s = _s
    for k in range(5):
        ang = -math.pi / 2 + k * 2 * math.pi / 5
        px, py = x + r * math.cos(ang), y + r * math.sin(ang)
        d.ellipse([s(px - r * 0.55), s(py - r * 0.55), s(px + r * 0.55), s(py + r * 0.55)], fill=_c(col, a))
    d.ellipse([s(x - r * 0.35), s(y - r * 0.35), s(x + r * 0.35), s(y + r * 0.35)], fill=_c(col, a * 1.5))


def motif_awadhi_arch(L, t):
    """Cusped (multi-foil) Awadhi arch on the right (the buti flowers are motif_awadhi_buti)."""
    d = ImageDraw.Draw(L)
    a = t["motif_alpha"]
    col = t["accent"]
    s = _s
    x0, x1, base = 700, 1040, 1310
    cx = (x0 + x1) / 2
    spring, apex = 920, 700
    for inset, wdt in ((0, 7), (34, 4)):
        lx, rx = x0 + inset, x1 - inset
        left = [(lx + (cx - lx) * math.sin(u * math.pi / 2), spring - (spring - apex - inset) * (u ** 1.5)) for u in [i / 24 for i in range(25)]]
        right = [(2 * cx - x, y) for x, y in reversed(left)]
        d.line([(s(x), s(y)) for x, y in left + right], fill=_c(col, a * 1.7), width=s(wdt), joint="curve")
        d.line([(s(lx), s(spring)), (s(lx), s(base))], fill=_c(col, a * 1.7), width=s(wdt))
        d.line([(s(rx), s(spring)), (s(rx), s(base))], fill=_c(col, a * 1.7), width=s(wdt))
    # cusps along the inner arch
    lx = x0 + 34
    inner = [(lx + (cx - lx) * math.sin(u * math.pi / 2), spring - (spring - apex - 34) * (u ** 1.5)) for u in [i / 6 for i in range(7)]]
    inner = inner + [(2 * cx - x, y) for x, y in reversed(inner[:-1])]
    for (xa, ya), (xb, yb) in zip(inner, inner[1:]):
        mx, my = (xa + xb) / 2, (ya + yb) / 2
        r = math.hypot(xb - xa, yb - ya) / 2
        d.ellipse([s(mx - r), s(my - r), s(mx + r), s(my + r)], outline=_c(col, a * 1.5), width=s(3))
    d.rectangle([s(x0 - 10), s(spring - 6), s(x0 + 44), s(spring + 6)], fill=_c(col, a * 1.5))
    d.rectangle([s(x1 - 44), s(spring - 6), s(x1 + 10), s(spring + 6)], fill=_c(col, a * 1.5))


def motif_awadhi_buti(L, t):
    """Small chikan buti flowers in a row above the header and along the bottom-left edge."""
    d = ImageDraw.Draw(L)
    a = t["motif_alpha"]
    col = t["accent"]
    for (x, y) in [(70, 70), (180, 110), (300, 60), (420, 105), (540, 62), (660, 108), (780, 66), (900, 104), (1010, 64),
                   (60, 1280), (190, 1240), (320, 1288), (450, 1244), (580, 1290)]:
        _buti(d, x, y, col, a * 1.3, 11)


def _fade_rows(L, y_full, y_zero):
    """Fade a motif layer from full opacity at y_full to nothing at y_zero (in 1x coordinates)."""
    mask = Image.new("L", L.size, 0)
    md = ImageDraw.Draw(mask)
    for y in range(L.size[1]):
        yy = y / SS
        v = 255 if yy <= y_full else (0 if yy >= y_zero else int(255 * (y_zero - yy) / (y_zero - y_full)))
        md.line([(0, y), (L.size[0], y)], fill=v)
    alpha = L.getchannel("A")
    from PIL import ImageChops
    L.putalpha(ImageChops.multiply(alpha, mask))


def motif_jaali(L, t):
    """Rajasthani jaali: a diamond lattice across the top band, fading out downwards."""
    d = ImageDraw.Draw(L)
    a = t["motif_alpha"]
    col = t["accent"]
    s = _s
    step = 60
    for k in range(-30, 40):
        x = k * step
        d.line([(s(x), s(0)), (s(x + 420), s(420))], fill=_c(col, a * 1.6), width=s(3))
        d.line([(s(x), s(0)), (s(x - 420), s(420))], fill=_c(col, a * 1.6), width=s(3))
    for i in range(0, W // step + 2):
        for j in range(0, 8):
            x, y = i * step, j * step
            d.ellipse([s(x - 7), s(y - 7), s(x + 7), s(y + 7)], fill=_c(col, a * 2))
            d.ellipse([s(x + step / 2 - 4), s(y + step / 2 - 4), s(x + step / 2 + 4), s(y + step / 2 + 4)], fill=_c(col, a * 1.6))
    _fade_rows(L, 40, 175)  # gone before the brand line (y 150-200) and the headline


def motif_laal_paar(L, t):
    """Bengal laal-paar: a red sari-border frame round the slide (the alpana is motif_alpana)."""
    d = ImageDraw.Draw(L)
    a = t["motif_alpha"]
    col = t["accent"]
    s = _s
    # the border itself is the identity: strong, but it only sits in the outer 40 px
    d.rectangle([s(0), s(0), s(W), s(H)], outline=_c(col, 235), width=s(26))
    d.rectangle([s(34), s(34), s(W - 34), s(H - 34)], outline=_c(col, 200), width=s(4))
    for x in range(52, W - 40, 34):  # tiny woven dots inside the border
        for y in (13, H - 13):
            d.ellipse([s(x - 3), s(y - 3), s(x + 3), s(y + 3)], fill=(255, 255, 255, 170))
    for y in range(52, H - 40, 34):
        for x in (13, W - 13):
            d.ellipse([s(x - 3), s(y - 3), s(x + 3), s(y + 3)], fill=(255, 255, 255, 170))


def motif_alpana(L, t):
    """Alpana: concentric arcs and petals around the bottom-left corner."""
    d = ImageDraw.Draw(L)
    a = t["motif_alpha"]
    col = t["accent"]
    s = _s
    cx, cy = 40, H - 40
    for r in (150, 210, 330):
        d.arc([s(cx - r), s(cy - r), s(cx + r), s(cy + r)], 270, 360, fill=_c(col, a * 1.6), width=s(4))
    for k in range(9):
        ang = -math.pi / 2 + k * (math.pi / 2) / 8
        px, py = cx + 270 * math.cos(ang), cy + 270 * math.sin(ang)
        rot = Image.new("RGBA", (s(60), s(120)), (0, 0, 0, 0))
        ImageDraw.Draw(rot).ellipse([0, 0, s(60), s(120)], fill=_c(col, a * 1.4))
        rot = rot.rotate(-math.degrees(ang) - 90, expand=True)
        L.alpha_composite(rot, (int(s(px) - rot.size[0] / 2), int(s(py) - rot.size[1] / 2)))
    d.pieslice([s(cx - 90), s(cy - 90), s(cx + 90), s(cy + 90)], 270, 360, fill=_c(col, a * 1.4))


def _nested_diamond(d, cx, cy, r, cols):
    s = _s
    for k, (col, alpha) in enumerate(cols):
        rr = r * (1 - k * 0.3)
        d.polygon([(s(cx), s(cy - rr)), (s(cx + rr), s(cy)), (s(cx), s(cy + rr)), (s(cx - rr), s(cy))], fill=_c(col, alpha))


def motif_phulkari(L, t):
    """Phulkari: rows of nested diamonds along the top and bottom edges."""
    d = ImageDraw.Draw(L)
    a = t["motif_alpha"]
    col, light = t["accent"], (255, 246, 220)
    for cy in (64, H - 64):
        x = 0
        while x <= W + 60:
            _nested_diamond(d, x, cy, 52, [(col, a * 1.6), (light, a * 1.4), (col, a * 2.2)])
            x += 108
        d.line([(0, _s(cy - 70)), (_s(W), _s(cy - 70))], fill=_c(col, a * 1.4), width=_s(3))
        d.line([(0, _s(cy + 70)), (_s(W), _s(cy + 70))], fill=_c(col, a * 1.4), width=_s(3))


def motif_wheat(L, t):
    """Haryana fields: furrow lines and three wheat ears (bottom-right)."""
    d = ImageDraw.Draw(L)
    a = t["motif_alpha"]
    col = t["accent"]
    s = _s
    vx, vy = 1300, 900  # vanishing point off to the right
    for k in range(9):
        x0 = 380 + k * 95
        d.line([(s(x0), s(H + 10)), (s(vx), s(vy))], fill=_c(col, a * 1.0), width=s(4))
    for (bx, by, h, lean) in ((800, 1290, 420, -0.18), (900, 1300, 480, 0.02), (1000, 1290, 400, 0.2)):
        tx, ty = bx + lean * h, by - h
        d.line([(s(bx), s(by)), (s(tx), s(ty))], fill=_c(col, a * 1.7), width=s(5))
        for g in range(9):
            u = 0.45 + g * 0.065
            gx, gy = bx + (tx - bx) * u, by + (ty - by) * u
            for side in (-1, 1):
                grain = Image.new("RGBA", (s(22), s(46)), (0, 0, 0, 0))
                ImageDraw.Draw(grain).ellipse([0, 0, s(22), s(46)], fill=_c(col, a * 1.8))
                grain = grain.rotate(side * 32 - math.degrees(math.atan2(tx - bx, by - ty)), expand=True)
                L.alpha_composite(grain, (int(s(gx + side * 14) - grain.size[0] / 2), int(s(gy) - grain.size[1] / 2)))
        for side in (-1, 1):  # awns
            d.line([(s(tx), s(ty + 20)), (s(tx + side * 24), s(ty - 50))], fill=_c(col, a * 1.4), width=s(2))


def motif_gond_leaf(L, t):
    """Gond art leaf: a big leaf with line-fill and a dotted outline (bottom-right)."""
    a = t["motif_alpha"]
    col = t["accent"]
    s = _s
    lw, lh = 330, 640
    leaf = Image.new("RGBA", (s(lw), s(lh)), (0, 0, 0, 0))
    mask = Image.new("L", leaf.size, 0)
    md = ImageDraw.Draw(mask)
    pts = []
    for i in range(41):
        u = i / 40
        pts.append((lw / 2 + (lw / 2 - 10) * math.sin(math.pi * u) * (1 - 0.25 * u), 10 + (lh - 20) * u))
    outline = pts + [(lw - x, y) for x, y in reversed(pts)]
    md.polygon([(s(x), s(y)) for x, y in outline], fill=255)
    fill = Image.new("RGBA", leaf.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(fill)
    for y in range(20, lh, 22):  # Gond line-fill: chevrons off the midrib
        fd.line([(s(lw / 2), s(y)), (s(0), s(y - 60))], fill=_c(col, a * 1.6), width=s(3))
        fd.line([(s(lw / 2), s(y)), (s(lw), s(y - 60))], fill=_c(col, a * 1.6), width=s(3))
    fd.line([(s(lw / 2), s(0)), (s(lw / 2), s(lh))], fill=_c(col, a * 2), width=s(6))
    leaf.paste(fill, (0, 0), mask)
    ld = ImageDraw.Draw(leaf)
    for i in range(0, len(outline), 1):
        x, y = outline[i]
        ld.ellipse([s(x - 5), s(y - 5), s(x + 5), s(y + 5)], fill=_c(col, a * 2.2))
    leaf = leaf.rotate(-28, expand=True, resample=Image.BICUBIC)
    L.alpha_composite(leaf, (s(640), s(700)))
    # a small second leaf for rhythm
    small = leaf.resize((leaf.size[0] // 2, leaf.size[1] // 2), Image.LANCZOS).rotate(60, expand=True)
    L.alpha_composite(small, (s(560), s(1010)))


def motif_madhubani(L, t):
    """Madhubani: double-line frame with sawtooth triangles between the lines (two colours)."""
    d = ImageDraw.Draw(L)
    a = t["motif_alpha"]
    c1, c2 = t["accent"], t["accent2"]
    s = _s
    o, i = 14, 50  # outer and inner line insets (inner line ~26 px clear of the 76 px text margin)
    for inset in (o, i):
        d.rectangle([s(inset), s(inset), s(W - inset), s(H - inset)], outline=_c(c1, a * 2.2), width=s(5))
    step = 40
    k = 0
    x = o
    while x < W - o:  # top and bottom rows
        col = c1 if k % 2 == 0 else c2
        d.polygon([(s(x), s(o)), (s(x + step), s(o)), (s(x + step / 2), s(i))], fill=_c(col, a * 1.8))
        d.polygon([(s(x), s(H - o)), (s(x + step), s(H - o)), (s(x + step / 2), s(H - i))], fill=_c(col, a * 1.8))
        x += step
        k += 1
    y, k = o, 0
    while y < H - o:
        col = c2 if k % 2 == 0 else c1
        d.polygon([(s(o), s(y)), (s(o), s(y + step)), (s(i), s(y + step / 2))], fill=_c(col, a * 1.8))
        d.polygon([(s(W - o), s(y)), (s(W - o), s(y + step)), (s(W - i), s(y + step / 2))], fill=_c(col, a * 1.8))
        y += step
        k += 1
    for (cx, cy) in ((i, i), (W - i, i), (i, H - i), (W - i, H - i)):  # corner suns
        d.ellipse([s(cx - 24), s(cy - 24), s(cx + 24), s(cy + 24)], fill=_c(c2, a * 2.4), outline=_c(c1, a * 2.6), width=s(4))


def motif_pahadi(L, t):
    """Himachal: peaks with snow caps and two deodars along the bottom."""
    d = ImageDraw.Draw(L)
    a = t["motif_alpha"]
    col = t["accent"]
    s = _s
    peaks = [(-60, 1350, 180, 1020, 440, 1350), (230, 1350, 520, 940, 820, 1350), (600, 1350, 860, 1060, 1140, 1350)]
    for x0, y0, px, py, x1, y1 in peaks:
        d.polygon([(s(x0), s(y0)), (s(px), s(py)), (s(x1), s(y1))], fill=_c(col, a * (1.0 if py > 1000 else 1.4)))
        # snow cap: the top 30 % of the peak, with a jagged lower edge
        f = 0.3
        lx, ly = px + (x0 - px) * f, py + (y0 - py) * f
        rx, ry = px + (x1 - px) * f, py + (y1 - py) * f
        jag = [(lx, ly), (lx + (rx - lx) * 0.25, ly - 26), (lx + (rx - lx) * 0.5, ly + 6), (lx + (rx - lx) * 0.75, ly - 22), (rx, ry)]
        d.polygon([(s(px), s(py))] + [(s(x), s(y)) for x, y in reversed(jag)], fill=(255, 255, 255, 235))
    for bx, h in ((560, 250), (650, 200)):  # deodars: stacked drooping tiers
        top = 1320 - h
        d.rectangle([s(bx - 7), s(1270), s(bx + 7), s(1330)], fill=_c(col, a * 2.2))
        for k in range(6):
            ty = top + k * (h - 60) / 6
            wdt = 24 + k * 15
            d.polygon([(s(bx), s(ty)), (s(bx + wdt), s(ty + 70)), (s(bx - wdt), s(ty + 70))], fill=_c(col, a * 2.4))


def motif_tricolour(L, t):
    """A tricolour sash across the top-right corner (saffron, white, green). No chakra.

    It sits in the corner beyond the UNOFFICIAL pill (pill corner is ~165 px from the slide corner along the sash's
    normal); centre line 100 px in, 3 x 22 px bands, so its inner edge stays ~30 px clear of the pill.
    """
    s = _s
    band = 22
    ang = math.radians(40)
    nx, ny = -math.sin(ang), math.cos(ang)  # unit normal pointing from the corner into the slide
    cols = [(t["accent"], 225), ((255, 255, 255), 210), (t["accent2"], 225)]
    rib = Image.new("RGBA", (s(700), s(band * 3)), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rib)
    for k, (c, al) in enumerate(cols):
        rd.rectangle([0, s(k * band), s(700), s((k + 1) * band)], fill=_c(c, al))
    rib = rib.rotate(-40, expand=True, resample=Image.BICUBIC)
    dist = 100
    cx, cy = W + nx * dist, ny * dist
    L.alpha_composite(rib, (int(s(cx) - rib.size[0] / 2), int(s(cy) - rib.size[1] / 2)))
    # a thin saffron echo stripe, between the sash and the corner
    echo = Image.new("RGBA", (s(500), s(6)), _c(t["accent"], 110))
    echo = echo.rotate(-40, expand=True, resample=Image.BICUBIC)
    dist = 46
    cx, cy = W + nx * dist, ny * dist
    L.alpha_composite(echo, (int(s(cx) - echo.size[0] / 2), int(s(cy) - echo.size[1] / 2)))


# motif name -> parts as (draw function, zone). zone: "fixed" (edges, frames, bands: never moves),
# "br"/"bl"/"tr" (scaled towards that corner to clear the text), "b" (full-width band, lowered to clear the text)
MOTIFS = {
    "thoranam": [(motif_thoranam, "br")],
    "stupa": [(motif_stupa, "br")],
    "hoysala_star": [(motif_hoysala_star, "tr")],
    "kolam": [(motif_kolam, "tr")],
    "awadhi_arch": [(motif_awadhi_buti, "fixed"), (motif_awadhi_arch, "br")],
    "jaali": [(motif_jaali, "fixed")],
    "laal_paar": [(motif_laal_paar, "fixed"), (motif_alpana, "bl")],
    "phulkari": [(motif_phulkari, "fixed")],
    "wheat": [(motif_wheat, "br")],
    "gond_leaf": [(motif_gond_leaf, "br")],
    "madhubani": [(motif_madhubani, "fixed")],
    "pahadi": [(motif_pahadi, "b")],
    "tricolour": [(motif_tricolour, "fixed")],
}
ZONE_OF.update({k: next((z for _, z in v if z != "fixed"), "fixed") for k, v in MOTIFS.items()})
