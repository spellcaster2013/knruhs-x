"""studio.py - turn one bot draft into a ready-to-post pack.

A pack is a folder with:
  slide-1.jpg ... slide-N.jpg   Instagram carousel (1080x1350, 4:5)
  pack.json                     title, captions for Instagram / WhatsApp / Telegram, source link
Only needs Pillow (pip3 install pillow). Fonts are bundled in ./fonts (Inter, SIL Open Font License).
"""
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

IST = timezone(timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1350

NAVY = (11, 60, 93)
NAVY_DARK = (6, 36, 58)
TEAL = (24, 167, 176)
ORANGE = (245, 158, 11)
INK = (13, 32, 48)
MUTED = (96, 115, 130)
PAPER = (244, 248, 251)
WHITE = (255, 255, 255)

STREAMS = {
    "mbbs": ("MBBS", (47, 111, 237), ["#MBBS", "#NEETUG"]),
    "bds": ("BDS", (139, 92, 246), ["#BDS", "#NEETUG", "#Dental"]),
    "mdms": ("MD / MS", (14, 159, 142), ["#NEETPG", "#MDMS", "#PGMedical"]),
    "mds": ("MDS", (217, 70, 168), ["#MDS", "#NEETMDS", "#Dental"]),
    "ss": ("DM / MCh", (229, 72, 77), ["#SuperSpeciality", "#NEETSS"]),
    "ayush": ("BHMS / Ayush", (34, 160, 107), ["#Ayush", "#BAMS", "#BHMS"]),
    "nursing": ("Nursing", (245, 158, 11), ["#Nursing", "#BScNursing"]),
    "allied": ("Allied Health", (14, 165, 233), ["#AlliedHealth", "#Paramedical"]),
    "jobs": ("Jobs", (249, 115, 22), ["#MedicalJobs", "#Recruitment", "#AssistantProfessor"]),
}
TYPES = {
    "counselling": ("Counselling", "🎓"),
    "result": ("Result", "📢"),
    "exam": ("Exam", "📝"),
    "recruitment": ("Recruitment", "💼"),
    "admission": ("Admission", "🏫"),
    "circular": ("Circular", "📌"),
}
COMMON_TAGS = ["#KNRUHS", "#Telangana", "#MedicalStudents", "#MedicalUpdates"]


# ----------------------------------------------------------------------------- fonts / text helpers
_FONT_CACHE = {}


def font(weight, size):
    key = (weight, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    names = {"regular": "Inter-Regular.otf", "semi": "Inter-SemiBold.otf", "bold": "Inter-Bold.otf", "xbold": "Inter-ExtraBold.otf"}
    candidates = [
        os.path.join(HERE, "fonts", names[weight]),
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if weight in ("bold", "xbold") else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if weight in ("bold", "xbold") else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    f = None
    for c in candidates:
        try:
            f = ImageFont.truetype(c, size)
            break
        except Exception:
            continue
    f = f or ImageFont.load_default(size)
    _FONT_CACHE[key] = f
    return f


def wrap(draw, text, fnt, max_w):
    lines, cur = [], ""
    for word in str(text).split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=fnt) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        while draw.textlength(word, font=fnt) > max_w and len(word) > 1:  # very long token
            cut = len(word) - 1
            while cut > 1 and draw.textlength(word[:cut], font=fnt) > max_w:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        cur = word
    if cur:
        lines.append(cur)
    return lines


def fit(draw, text, weight, sizes, max_w, max_lines):
    """Largest size from `sizes` (descending) whose wrapped text fits max_lines; last size truncates with an ellipsis."""
    for s in sizes:
        f = font(weight, s)
        lines = wrap(draw, text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines
    f = font(weight, sizes[-1])
    lines = wrap(draw, text, f, max_w)[:max_lines]
    if lines:
        last = lines[-1]
        while last and draw.textlength(last + "…", font=f) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return f, lines


def line_h(fnt, factor=1.28):
    b = fnt.getbbox("Ag")
    return int((b[3] - b[1]) * factor) + 4


def draw_lines(d, x, y, lines, fnt, fill, factor=1.28):
    lh = line_h(fnt, factor)
    for ln in lines:
        d.text((x, y), ln, font=fnt, fill=fill)
        y += lh
    return y


def pill(d, x, y, text, fnt, bg, fg, pad_x=26, pad_y=12):
    w = int(d.textlength(text, font=fnt)) + pad_x * 2
    h = line_h(fnt, 1.0) + pad_y * 2 - 4
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=bg)
    b = fnt.getbbox(text)
    d.text((x + pad_x, y + (h - (b[3] - b[1])) / 2 - b[1]), text, font=fnt, fill=fg)
    return x + w + 14, h


def gradient_bg(top, bottom):
    im = Image.new("RGB", (W, H), top)
    px = ImageDraw.Draw(im)
    for y in range(H):
        t = y / (H - 1)
        px.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return im


def brand_mark(d, x, y, size=64):
    """Bell icon - the same mark as the profile picture on Instagram, Facebook and Telegram."""
    d.ellipse([x, y, x + size, y + size], fill=NAVY_DARK, outline=TEAL, width=max(2, size // 22))
    s = size / 64.0
    cx = x + 32 * s
    d.pieslice([cx - 14 * s, y + 14 * s, cx + 14 * s, y + 42 * s], 180, 360, fill=WHITE)
    d.polygon([(cx - 14 * s, y + 28 * s), (cx + 14 * s, y + 28 * s), (cx + 19 * s, y + 43 * s), (cx - 19 * s, y + 43 * s)], fill=WHITE)
    d.rounded_rectangle([cx - 21 * s, y + 42 * s, cx + 21 * s, y + 46 * s], radius=2 * s, fill=WHITE)
    d.ellipse([cx - 4 * s, y + 47 * s, cx + 4 * s, y + 55 * s], fill=WHITE)
    d.ellipse([x + 42 * s, y + 10 * s, x + 54 * s, y + 22 * s], fill=ORANGE)


def ecg_overlay(im, y, color, alpha=70):
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    pts, x = [], -40
    while x < W + 120:
        pts += [(x, y), (x + 110, y), (x + 140, y - 60), (x + 185, y + 80), (x + 225, y - 30), (x + 250, y), (x + 330, y)]
        x += 330
    d.line(pts, fill=color + (alpha,), width=5, joint="curve")
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def fmt_dt(ms):
    d = datetime.fromtimestamp(ms / 1000, IST)
    h = d.hour % 12 or 12
    ap = "AM" if d.hour < 12 else "PM"
    t = "" if (d.hour == 0 and d.minute == 0) else f", {h}:{d.minute:02d} {ap}"
    return f"{d.day} {d.strftime('%b %Y')}{t}"


def fmt_day(dt):
    return f"{dt.day} {dt.strftime('%b %Y')}"


# ----------------------------------------------------------------------------- slides
def header(im, d, cfg, light=False):
    ink = INK if light else WHITE
    brand_mark(d, 64, 56, 64)
    if cfg.get("brand_tagline"):
        d.text((142, 60), cfg["brand_name"], font=font("xbold", 34), fill=ink)
        d.text((142, 100), cfg["brand_tagline"], font=font("regular", 24), fill=MUTED if light else (170, 205, 220))
    else:
        d.text((142, 70), cfg["brand_name"], font=font("xbold", 38), fill=ink)
    f = font("bold", 22)
    label = "UNOFFICIAL UPDATE"
    w = int(d.textlength(label, font=f)) + 36
    d.rounded_rectangle([W - 64 - w, 66, W - 64, 112], radius=23, outline=MUTED if light else (170, 205, 220), width=2)
    d.text((W - 64 - w + 18, 76), label, font=f, fill=MUTED if light else (200, 225, 235))


def footer(d, cfg, light, n, total, cta=None):
    ink = MUTED if light else (170, 205, 220)
    d.text((64, H - 92), cta or "Source: knruhs.telangana.gov.in", font=font("semi", 28), fill=ink)
    # progress dots
    x = W - 64 - total * 30
    for i in range(total):
        on = i == n
        d.ellipse([x + i * 30, H - 84, x + i * 30 + 16, H - 68], fill=(TEAL if on else ((200, 212, 222) if light else (60, 100, 130))))


def slide_cover(p, cfg, n, total):
    im = gradient_bg(NAVY, NAVY_DARK)
    im = ecg_overlay(im, H - 190, TEAL, 60)
    d = ImageDraw.Draw(im)
    header(im, d, cfg)
    st_label, st_col, _ = STREAMS.get(p["stream"], ("Update", TEAL, []))
    ty_label = TYPES.get(p["type"], ("Update", ""))[0]
    x, y = 64, 190
    x, _ = pill(d, x, y, st_label, font("bold", 30), st_col, WHITE)
    x, _ = pill(d, x, y, ty_label, font("semi", 30), (30, 85, 118), WHITE)
    if p.get("urgent"):
        pill(d, x, y, "URGENT", font("bold", 28), ORANGE, (40, 24, 0))
    f, lines = fit(d, p["title"], "xbold", [82, 74, 66, 60, 54, 48], W - 128, 7)
    y = 300
    y = draw_lines(d, 64, y, lines, f, WHITE, 1.16)
    if p.get("deadline"):
        y += 36
        box_h = 150
        d.rounded_rectangle([64, y, W - 64, y + box_h], radius=26, fill=(255, 255, 255, 18) if False else (20, 78, 112), outline=ORANGE, width=4)
        d.text((100, y + 22), "LAST DATE", font=font("bold", 26), fill=ORANGE)
        fd = font("xbold", 52)
        d.text((100, y + 64), fmt_dt(p["deadline"]), font=fd, fill=WHITE)
    footer(d, cfg, False, n, total, "Swipe for details  →")
    return im


def slide_points(p, cfg, n, total):
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    header(im, d, cfg, light=True)
    y = 190
    d.text((64, y), "IN PLAIN WORDS", font=font("bold", 28), fill=TEAL)
    y += 52
    summary = p.get("summary") or ""
    if summary and summary.strip().lower() != p["title"].strip().lower():
        f, lines = fit(d, summary, "semi", [44, 40, 36, 33], W - 128, 7)
        y = draw_lines(d, 64, y, lines, f, INK, 1.3) + 26
    pts = [t for t in (p.get("points") or []) if t][:5]
    if pts:
        d.text((64, y), "KEY POINTS", font=font("bold", 28), fill=TEAL)
        y += 54
        room = H - 150 - y
        per = room // len(pts)
        for t in pts:
            f, lines = fit(d, t, "regular", [36, 33, 30, 28], W - 128 - 56, max(2, per // 44))
            top = y
            d.ellipse([64, y + 12, 64 + 22, y + 34], fill=TEAL)
            y = draw_lines(d, 64 + 56, y, lines, f, INK, 1.28) + 22
            if y > H - 150:
                break
    footer(d, cfg, True, n, total)
    return im


def slide_final(p, cfg, n, total):
    im = gradient_bg(NAVY, NAVY_DARK)
    d = ImageDraw.Draw(im)
    header(im, d, cfg)
    y = 200
    d.text((64, y), "CHECK THE ORIGINAL", font=font("bold", 28), fill=TEAL)
    y += 56
    rows = []
    if p.get("deadline"):
        rows.append(("Last date", fmt_dt(p["deadline"]), ORANGE))
    if p.get("ref"):
        rows.append(("Reference", p["ref"], WHITE))
    if p.get("published_day"):
        rows.append(("Notice dated", p["published_day"], WHITE))
    rows.append(("Source", "knruhs.telangana.gov.in", WHITE))
    for label, value, col in rows:
        d.rounded_rectangle([64, y, W - 64, y + 128], radius=24, fill=(20, 78, 112))
        d.text((100, y + 20), label.upper(), font=font("bold", 24), fill=(170, 205, 220))
        f, lines = fit(d, value, "xbold", [50, 44, 38, 32], W - 200, 1)
        d.text((100, y + 58), lines[0], font=f, fill=col)
        y += 148
    y += 8
    d.rounded_rectangle([64, y, W - 64, y + 150], radius=24, outline=(120, 170, 195), width=2)
    f, lines = fit(d, f"This is an unofficial summary by {cfg['brand_name']}. We are not part of KNRUHS. Always read the original notice before you act.", "regular", [30, 28, 26], W - 160, 4)
    draw_lines(d, 96, y + 28, lines, f, (210, 230, 240), 1.3)
    footer(d, cfg, False, n, total, "Read the original before you act")
    return im


def _pdf_file(p, draft, cfg):
    """Return a local path to the original notice PDF, downloading it again if this run does not have it."""
    path = (draft.get("meta") or {}).get("pdf_path") or ""
    if path and os.path.exists(path):
        return path
    url = p.get("source_url")
    if not url:
        return None
    import tempfile, urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": cfg.get("user_agent", "KNRUHS-Updates-Bot")})
        with urllib.request.urlopen(req, timeout=60) as r:
            blob = r.read()
        if not blob.startswith(b"%PDF"):
            return None
        fd, tmp = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, blob)
        os.close(fd)
        return tmp
    except Exception:
        return None


def slides_original(p, draft, cfg, max_pages=3):
    """Picture(s) of the actual university notice, so the post shows the real document, not only our summary."""
    try:
        import pymupdf as fitz
    except Exception:
        return []
    path = _pdf_file(p, draft, cfg)
    if not path:
        return []
    out = []
    try:
        doc = fitz.open(path)
        n = min(len(doc), max_pages)
        for i in range(n):
            page = doc[i]
            band = 120
            box_w, box_h = W - 64, H - band - 40
            zoom = min(box_w / page.rect.width, box_h / page.rect.height) * 1.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            pg = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            im = Image.new("RGB", (W, H), NAVY)
            d = ImageDraw.Draw(im)
            label = "ORIGINAL KNRUHS NOTICE" + (f"  ·  PAGE {i + 1} OF {len(doc)}" if len(doc) > 1 else "")
            d.text((40, 42), label, font=font("bold", 30), fill=WHITE)
            d.text((40, 84), "Exact copy of the official PDF", font=font("regular", 22), fill=(170, 205, 220))
            x = (W - pg.width) // 2
            y = band + (H - band - 20 - pg.height) // 2
            d.rectangle([x - 4, y - 4, x + pg.width + 4, y + pg.height + 4], fill=WHITE)
            im.paste(pg, (x, y))
            out.append(im)
    except Exception:
        return []
    return out


# ----------------------------------------------------------------------------- captions
def clip(t, n):
    t = " ".join(str(t).split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def captions(p, cfg):
    st_label, _, tags = STREAMS.get(p["stream"], ("Update", TEAL, []))
    ty_label, emoji = TYPES.get(p["type"], ("Update", "📌"))
    title = p["title"]
    pts = [clip(t, 170) for t in (p.get("points") or []) if t][:5]
    dl = fmt_dt(p["deadline"]) if p.get("deadline") else None
    src = p.get("source_url") or ""
    disc = f"Unofficial update by {cfg['brand_name']} (not affiliated with KNRUHS). Always verify with the original notice."

    ig = [f"{emoji} {title}"]
    if dl:
        ig.append(f"⏰ Last date: {dl}")
    ig.append("")
    if p.get("summary") and p["summary"].strip().lower() != title.strip().lower():
        ig += [clip(p["summary"], 400), ""]
    if pts:
        ig += ["Key points:"] + [f"• {t}" for t in pts] + [""]
    ig += ["📄 Swipe to see the original KNRUHS notice.",
           "📢 Follow our Telegram channel t.me/knruhsx to see the official notification (link in bio).",
           "", disc, "", " ".join(dict.fromkeys(COMMON_TAGS + tags))]
    instagram = "\n".join(ig).strip()
    if len(instagram) > 2150:
        instagram = instagram[:2147].rstrip() + "…"

    fbl = [f"{emoji} {title}"]
    if dl:
        fbl.append(f"⏰ Last date: {dl}")
    fbl.append("")
    if p.get("summary") and p["summary"].strip().lower() != title.strip().lower():
        fbl += [clip(p["summary"], 400), ""]
    if pts:
        fbl += ["Key points:"] + [f"• {t}" for t in pts] + [""]
    if src:
        fbl += [f"📄 Original notice (PDF): {src}", ""]
    fbl += ["⚡ Instant alerts on Telegram: https://t.me/knruhsx", "", disc]
    facebook = "\n".join(fbl).strip()

    wa = [f"{emoji} *{title}*"]
    if dl:
        wa.append(f"⏰ *Last date: {dl}*")
    wa.append("")
    if p.get("summary") and p["summary"].strip().lower() != title.strip().lower():
        wa += [clip(p["summary"], 400), ""]
    wa += [f"• {t}" for t in pts] + ([""] if pts else [])
    if src:
        wa += [f"📎 Original notice (PDF): {src}"]
    wa += ["", f"_{disc}_"]
    whatsapp = "\n".join(wa).strip()

    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    tg = [f"{emoji} <b>{esc(title)}</b>"]
    if dl:
        tg.append(f"⏰ <b>Last date: {esc(dl)}</b>")
    tg.append("")
    if p.get("summary") and p["summary"].strip().lower() != title.strip().lower():
        tg += [esc(clip(p["summary"], 400)), ""]
    tg += [f"• {esc(t)}" for t in pts] + ([""] if pts else [])
    if src:
        tg.append(f'📎 <a href="{esc(src)}">Original notice (PDF)</a>')
    tg += ["", f"<i>{esc(disc)}</i>"]
    telegram = "\n".join(tg).strip()
    if len(telegram) > 1000:  # photo captions are limited to 1024 chars; the full text goes as a separate message
        telegram_caption = f"{emoji} <b>{esc(clip(title, 200))}</b>" + (f"\n⏰ <b>Last date: {esc(dl)}</b>" if dl else "")
    else:
        telegram_caption = telegram
    return {"instagram": instagram, "facebook": facebook, "whatsapp": whatsapp, "telegram": telegram, "telegram_caption": telegram_caption}


# ----------------------------------------------------------------------------- public API
def pack_id(draft):
    m = draft.get("meta", {})
    return hashlib.sha1((m.get("source_url") or m.get("uid") or draft.get("title", "")).encode()).hexdigest()[:10]


def cap1(t):
    t = str(t or "").strip()
    return t[:1].upper() + t[1:]


def build_pack(draft, cfg, out_root, force=False):
    """Render slides + captions for one draft. Cached: only re-renders when the content or brand changes."""
    m = draft.get("meta", {})
    pid = pack_id(draft)
    folder = os.path.join(out_root, pid)
    p = {
        "id": pid, "title": cap1(draft.get("title")), "summary": draft.get("summary") or "", "points": draft.get("points") or [],
        "stream": draft.get("stream") or "mbbs", "type": draft.get("type") or "circular", "urgent": bool(draft.get("urgent")),
        "deadline": draft.get("deadline"), "ref": draft.get("ref") or "", "source_url": m.get("source_url", ""),
        "published_day": fmt_day(datetime.fromisoformat(m["published"])) if m.get("published") else "",
    }
    sig = hashlib.sha1(json.dumps(["v3", p, cfg["brand_name"], cfg["handle"], cfg["brand_tagline"]], sort_keys=True, default=str).encode()).hexdigest()[:12]
    meta_path = os.path.join(folder, "pack.json")
    if not force and os.path.exists(meta_path):
        try:
            old = json.load(open(meta_path, encoding="utf-8"))
            if old.get("sig") == sig and all(os.path.exists(os.path.join(folder, s)) for s in old.get("slides", [])):
                return old
        except Exception:
            pass
    os.makedirs(folder, exist_ok=True)
    has_body = bool(p["points"]) or (p["summary"].strip() and p["summary"].strip().lower() != p["title"].strip().lower())
    makers = [slide_cover] + ([slide_points] if has_body else []) + [slide_final]
    originals = []
    try:  # only fresh notices get pictures of the original PDF (old ones are already published; avoids re-downloading every run)
        det = datetime.fromisoformat((draft.get("meta") or {}).get("detected_at", ""))
        if (datetime.now(IST) - det).total_seconds() < 3 * 86400:
            originals = slides_original(p, draft, cfg)
    except Exception:
        pass
    ours = [None] * len(makers)
    total = len(makers)
    order = makers[:-1] + ["orig"] * len(originals) + makers[-1:]  # cover, plain words, ORIGINAL pages, check-the-original
    names, oi = [], 0
    for i, mk in enumerate(order):
        if mk == "orig":
            im = originals[oi]
            oi += 1
        else:
            im = mk(p, cfg, len([m for m in order[:i] if m != "orig"]), total)
        name = f"slide-{i + 1}.jpg"
        im.save(os.path.join(folder, name), "JPEG", quality=90, optimize=True)  # Instagram's API accepts JPEG only
        names.append(name)
    pack = dict(p, sig=sig, slides=names, captions=captions(p, cfg), flags=m.get("flags", []), confidence=m.get("confidence"), built_at=datetime.now(IST).isoformat(timespec="seconds"))
    json.dump(pack, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return pack
