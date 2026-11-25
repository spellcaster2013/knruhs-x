"""studio.py - turn one bot draft into a ready-to-post pack.

A pack is a folder with:
  slide-1.jpg ... slide-N.jpg   Instagram carousel (1080x1350, 4:5)
  pack.json                     title, captions for Instagram / Facebook / WhatsApp / Telegram, source link
Only needs Pillow (pip3 install pillow). Fonts are bundled in ./fonts (Inter and Noto Sans Devanagari, SIL Open Font License).

Look: one theme per university (themes.py: colours, motif, brand, hashtags). The notice type only changes the
kicker chip and which lines show (deadline box, "search your register number" line).
cfg (from desk.py): brand_name, brand_tagline, handle, no_slides, and optionally university (default "knruhs").
"""
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import themes

IST = timezone(timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1350
MX = 76                      # side margin
SAFE_TOP, SAFE_BOT = 135, 1215  # the central 1080x1080 square (what a square crop keeps)

STREAMS = {
    "mbbs": ("MBBS", (47, 111, 237), ["#MBBS", "#NEETUG"]),
    "bds": ("BDS", (139, 92, 246), ["#BDS", "#NEETUG", "#Dental"]),
    "mdms": ("MD / MS", (14, 159, 142), ["#NEETPG", "#MDMS", "#PGMedical"]),
    "mds": ("MDS", (217, 70, 168), ["#MDS", "#NEETMDS", "#Dental"]),
    "ss": ("DM / MCh", (229, 72, 77), ["#SuperSpeciality", "#NEETSS"]),
    "ayush": ("BHMS / Ayush", (34, 160, 107), ["#Ayush", "#BAMS", "#BHMS"]),
    "nursing": ("Nursing", (245, 158, 11), ["#Nursing", "#BScNursing"]),
    "allied": ("Allied Health", (14, 165, 233), ["#AlliedHealth", "#Paramedical"]),
    "jobs": ("Jobs", (249, 115, 22), ["#MedicalJobs", "#Recruitment"]),
}
# type -> (kicker, emoji, optional type hashtag)
TYPES = {
    "counselling": ("COUNSELLING", "🎓", "#Counselling"),
    "result": ("RESULT", "📢", "#Results"),
    "exam": ("EXAM", "📝", ""),
    "recruitment": ("JOBS", "💼", "#Recruitment"),
    "admission": ("ADMISSION", "🏫", ""),
    "circular": ("NOTICE", "📌", ""),
}
LIST_RX = re.compile(r"MERIT LIST|MERIT POSITION|ALLOTMENT|ALLOCATION|SELECTED CANDIDATES|SELECTION LIST|LIST OF (?:ELIGIBLE |SELECTED )?CANDIDATES", re.I)
REGNO_LINE = "Open the PDF and search your register number."      # results
LIST_LINE = "Open the PDF and search your name or roll number."    # merit / allotment / selection lists
BOILER_SUMMARY = re.compile(r"^(result )?list released\b|^open the pdf", re.I)


# ----------------------------------------------------------------------------- fonts / text helpers
_FONT_CACHE = {}
DEVA = re.compile(r"[\u0900-\u097F]")


def font(weight, size, deva=False):
    key = (weight, size, deva)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    names = {"regular": "Inter-Regular.otf", "semi": "Inter-SemiBold.otf", "bold": "Inter-Bold.otf", "xbold": "Inter-ExtraBold.otf"}
    heavy = weight in ("bold", "xbold", "semi")
    if deva:
        candidates = [os.path.join(HERE, "fonts", "NotoSansDevanagari-Bold.ttf" if heavy else "NotoSansDevanagari-Regular.ttf"),
                      "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if heavy else "/usr/share/fonts/truetype/freefont/FreeSans.ttf"]
    else:
        candidates = [os.path.join(HERE, "fonts", names[weight])]
    candidates += [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if heavy else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if heavy else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    f = None
    for c in candidates:
        try:
            if deva:  # complex script: needs the raqm layout engine for conjuncts and matras
                try:
                    f = ImageFont.truetype(c, size, layout_engine=ImageFont.Layout.RAQM)
                except Exception:
                    f = ImageFont.truetype(c, size)
            else:
                f = ImageFont.truetype(c, size)
            break
        except Exception:
            continue
    f = f or ImageFont.load_default(size)
    _FONT_CACHE[key] = f
    return f


def tfont(weight, size, text):
    return font(weight, size, bool(DEVA.search(str(text or ""))))


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


def _whole(lines, text):
    return " ".join(lines) == " ".join(str(text).split())


def wrap_balanced(draw, text, fnt, max_w):
    """Same number of lines as wrap(), but with the narrowest width that keeps it: even lines, no one-word orphan."""
    lines = wrap(draw, text, fnt, max_w)
    n = len(lines)
    if n < 2 or not _whole(lines, text):
        return lines
    lo, hi, best = max_w * 0.45, max_w, lines
    while hi - lo > 3:
        mid = (lo + hi) / 2
        trial = wrap(draw, text, fnt, mid)
        if len(trial) == n and _whole(trial, text):
            best, hi = trial, mid
        else:
            lo = mid
    return best


def no_orphan(draw, text, fnt, max_w):
    """wrap(), but if the last line is a single word, narrow the measure (up to 22 %) to pull a second word down."""
    lines = wrap(draw, text, fnt, max_w)
    if len(lines) < 2 or len(lines[-1].split()) > 1 or not _whole(lines, text):
        return lines
    w = max_w
    while w > max_w * 0.78:
        w -= 8
        trial = wrap(draw, text, fnt, w)
        if len(trial) == len(lines) and len(trial[-1].split()) > 1 and _whole(trial, text):
            return trial
    return lines


def fit(draw, text, weight, sizes, max_w, max_lines):
    """Largest size from `sizes` (descending) whose wrapped text fits max_lines; the last size ends on a whole word + ellipsis."""
    for s in sizes:
        f = tfont(weight, s, text)
        lines = no_orphan(draw, text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines
    f = tfont(weight, sizes[-1], text)
    lines = wrap(draw, text, f, max_w)[:max_lines]
    if lines:
        words = lines[-1].split()
        while words and draw.textlength(" ".join(words) + "…", font=f) > max_w:
            words.pop()
        lines[-1] = (" ".join(words) or lines[-1][:-2]).rstrip(" ,;:-·") + "…"
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


def chip(d, x, y, text, fnt, bg=None, fg=(255, 255, 255), outline=None, pad_x=22, h=54, dot=None):
    """Rounded label. Returns its right edge (+gap)."""
    tw = int(d.textlength(text, font=fnt))
    extra = 26 if dot else 0
    w = tw + pad_x * 2 + extra
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=bg, outline=outline, width=2 if outline else 0)
    if dot:
        d.ellipse([x + pad_x - 2, y + h / 2 - 8, x + pad_x + 14, y + h / 2 + 8], fill=dot)
    b = fnt.getbbox(text)
    d.text((x + pad_x + extra, y + (h - (b[3] - b[1])) / 2 - b[1]), text, font=fnt, fill=fg)
    return x + w + 14


def chip_w(d, text, fnt, pad_x=22, dot=False):
    return int(d.textlength(text, font=fnt)) + pad_x * 2 + (26 if dot else 0) + 14


def bell(d, x, y, size, ring, body, dotc):
    """Bell icon - the same mark as the profile picture."""
    d.ellipse([x, y, x + size, y + size], fill=ring)
    s = size / 64.0
    cx = x + 32 * s
    d.pieslice([cx - 14 * s, y + 14 * s, cx + 14 * s, y + 42 * s], 180, 360, fill=body)
    d.polygon([(cx - 14 * s, y + 28 * s), (cx + 14 * s, y + 28 * s), (cx + 19 * s, y + 43 * s), (cx - 19 * s, y + 43 * s)], fill=body)
    d.rounded_rectangle([cx - 21 * s, y + 42 * s, cx + 21 * s, y + 46 * s], radius=2 * s, fill=body)
    d.ellipse([cx - 4 * s, y + 47 * s, cx + 4 * s, y + 55 * s], fill=body)
    d.ellipse([x + 42 * s, y + 10 * s, x + 54 * s, y + 22 * s], fill=dotc)


def magnifier(d, x, y, size, col, width=5):
    r = size * 0.36
    d.ellipse([x, y, x + 2 * r, y + 2 * r], outline=col, width=width)
    d.line([(x + 2 * r * 0.85, y + 2 * r * 0.85), (x + size, y + size)], fill=col, width=width + 2)


def fmt_dt(ms):
    d = datetime.fromtimestamp(ms / 1000, IST)
    h = d.hour % 12 or 12
    ap = "AM" if d.hour < 12 else "PM"
    t = "" if (d.hour == 0 and d.minute == 0) else f", {h}:{d.minute:02d} {ap}"
    return f"{d.day} {d.strftime('%b %Y')}{t}"


def fmt_day(dt):
    return f"{dt.day} {dt.strftime('%b %Y')}"


# ----------------------------------------------------------------------------- title cleanup (display only)
ACRO = {a.lower(): a for a in [
    "MBBS", "BDS", "MDS", "MD", "MS", "DM", "MCh", "BHMS", "BAMS", "BUMS", "BSMS", "BNYS", "BPT", "MPT", "CQ", "MQ", "PG", "UG", "AY",
    "CBME", "NEET", "NRI", "BSc", "MSc", "GNM", "ANM", "OBC", "EWS", "SC", "ST", "BC", "CA", "NMC", "MCC", "DNB", "FMGE", "NEXT",
    "PhD", "MPH", "BPharm", "DPharm", "MPharm", "JENPAS", "KNRUHS", "NTRUHS", "RGUHS", "TNMGRMU", "ABVMU", "RUHS", "WBUHS",
    "BFUHS", "UHSR", "BUHS", "AMRU", "UHS", "NTR", "MGR", "AIIMS", "ND", "MJ", "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII"]}
SMALL = {"of", "the", "and", "for", "to", "in", "on", "at", "by", "a", "an", "or", "as", "is", "with", "from", "under", "into", "after"}
MONTHS = {m.lower(): m for m in ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                                 "November", "December", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec"]}
SAFE_ACRO = {k: ACRO[k] for k in ("mbbs", "bds", "mds", "bhms", "bams", "bpt", "mpt", "bsc", "msc", "cbme", "neet", "pg", "ug", "mq", "cq", "mch")}
CLAUSE_RX = re.compile(r"\s(?:-|–|·|:)\s|,\s|\s\(|\s(?:for|of|regarding|under|in|to|with|after)\s", re.I)


def _fix_word(w, sentence_start):
    core = re.match(r"^([\W_]*)(.*?)([\W_]*)$", w)
    pre, mid, post = core.groups()
    lo = mid.lower()
    if lo in ACRO:
        return pre + ACRO[lo] + post
    if lo in MONTHS:
        return pre + MONTHS[lo] + post
    return None


def sentence_case(t):
    out = []
    for i, w in enumerate(t.split()):
        fixed = _fix_word(w, i == 0)
        if fixed:
            out.append(fixed)
            continue
        letters = re.sub(r"[^A-Za-z]", "", w)
        if any(c.isdigit() for c in w) or (letters.isupper() and 2 <= len(letters) <= 3 and w.lower().strip("().,") not in SMALL) or "." in w.strip(".") :
            out.append(w)  # codes, short acronyms (CA, MQ), dotted abbreviations (B.Sc., M.Ch.)
            continue
        out.append(w.lower())
    s = " ".join(out)
    return s[:1].upper() + s[1:]


# file-name residue at the end of a title: "...Counselling 2026 merged", "...final", "_0001", "(1)", ".pdf", "new"
RESIDUE_RX = re.compile(r"(?:[\s_\-]*(?:\(new\)|\bnew|\bmerged|\bsigned|\bfinal|\bcopy|\bcompressed|\bscanned|click here(?: to view)?|"
                        r"\bdownload|\.pdf|_\d{1,4}|\(\d{1,2}\)))\s*$", re.I)
# "final" stays when it is part of the exam/course name ("MBBS Final", "Part-II Final", "Final year")
KEEP_FINAL = re.compile(r"\b(?:mbbs|bds|bams|bhms|bpt|prof|professional|part|year|semester|sem|phase|ii|iii|i)[\s\-]+final\s*$", re.I)


def strip_residue(t):
    for _ in range(4):
        if KEEP_FINAL.search(t):
            break
        new = RESIDUE_RX.sub("", t).rstrip(" ,-_")
        if new == t or not new:
            break
        t = new
    return t


def clean_title(raw, deadline=None, published_day=""):
    t = " ".join(str(raw or "").split())
    if not t:
        return ""
    if DEVA.search(t):  # Hindi titles: keep the wording (only tidy spaces and punctuation)
        t = re.sub(r"\s+([,:;)।])", r"\1", t)
        t = re.sub(r"([,:;])(?=[^\s\d])", r"\1 ", t)
        return t
    # 3. noise
    t = re.sub(r"^[\s•\-»*]+", "", t)
    t = strip_residue(t)
    # 8. revisions
    t = re.sub(r"\s*\(\s*notification\s*\d+\s*\)", "", t, flags=re.I)
    rev = re.search(r"\b(revised|corrigendum|addendum)\b", t, re.I)
    # 5. duplicated dates
    t = re.sub(r"\[\s*date of result[^\]]*\]?", "", t, flags=re.I)
    t = re.sub(r"\b(?:dated|dt)\.?\s*[:.]?\s*\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", "", t, flags=re.I)
    # 4. scraper truncation: a dangling bracket or date at the end
    t = re.sub(r"[\[(][^\])]*$", "", t) if t.count("[") > t.count("]") or t.count("(") > t.count(")") else t
    t = re.sub(r"\b(?:dated|dt)\.?\s*[\d.\-/]*$", "", t, flags=re.I)
    truncated = len(raw or "") >= 145
    # 2. file-name-like
    if len(re.findall(r"(?<=[A-Za-z0-9])-(?=[A-Za-z])|(?<=[A-Za-z])-(?=[0-9])", t)) >= 3:
        t = re.sub(r"(?<!\d)-|-(?!\d)", " ", t)
        t = sentence_case(t)
    # 1. ALL CAPS
    letters = re.sub(r"[^A-Za-z]", "", t)
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        t = sentence_case(t)
    # 6. boilerplate
    t = re.sub(r"^(?:public notice for|notification for|notice regarding|notice for|notice of|regarding|examinations? results? of)\s+", "", t, flags=re.I)
    t = re.sub(r"\bfor the (?:academic year|AY)\s+(\d{4}\s*-\s*\d{2,4})", r"\1", t, flags=re.I)
    t = re.sub(r"\bof the candidates who have applied for admission to (\w[\w.]*) course", r"\1", t, flags=re.I)
    if rev:
        word = rev.group(1).capitalize()
        rest = re.sub(r"\s*[-–:]?\s*\b" + rev.group(1) + r"\b(?:\s+to\b)?[\s:\-–]*", " ", t, count=1, flags=re.I).strip()
        t = f"{word}: " + rest[:1].upper() + rest[1:]
    t = re.sub(r"\bcompetent authority quota\b", "CQ", t, flags=re.I)
    t = re.sub(r"\bmanagement quota\b", "MQ", t, flags=re.I)
    t = re.sub(r"\bdegree diploma\b", "degree/diploma", t, flags=re.I)
    t = re.sub(r"^(?:KNRUHS|NTRUHS|RGUHS|TNMGRMU|ABVMU|RUHS|WBUHS|BFUHS|UHSR|BUHS|AMRU)\s*[-–:]\s*", "", t, flags=re.I)
    t = " ".join(SAFE_ACRO.get(w.lower(), w) if w.islower() or w.istitle() else w for w in t.split())
    t = re.sub(r"\b(january|february|march|april|june|july|august|september|october|november|december)\b", lambda m: m.group(1).capitalize(), t)
    # 10. punctuation
    t = re.sub(r"\s+([,)])", r"\1", t)
    t = re.sub(r",(?=[A-Za-z])", ", ", t)
    t = re.sub(r"\(\s+", "(", t)
    t = re.sub(r"(?<![A-Za-z0-9]) – (?![A-Za-z0-9])", " · ", t)
    t = re.sub(r"(\w+) [–—] (\w+)", lambda m: m.group(0) if (m.group(1).lower() in MONTHS or m.group(1).isdigit()) else f"{m.group(1)} · {m.group(2)}", t)
    t = re.sub(r"\.{2,}", ".", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,-–")
    t = strip_residue(t)
    if truncated:
        t = cut_clause(t, len(t) - 1)
    return t[:1].upper() + t[1:]


STRONG_RX = re.compile(r"\s(?:-|–|·)\s|[,;:]\s|\s\(")


def cut_clause(t, limit):
    """Shorten to <= limit chars at the last whole clause (else whole word) and add an ellipsis. Never mid-word."""
    if len(t) <= limit:
        return t
    head = t[:limit]
    cuts = [m.start() for m in STRONG_RX.finditer(head) if m.start() >= limit * 0.55]
    if cuts:
        head = head[:cuts[-1]]
    else:
        head = head[:head.rfind(" ")] if " " in head else head
        words = head.split()
        while len(words) > 3 and words[-1].lower().strip(",") in SMALL:  # do not end on "of", "for", "the"...
            words.pop()
        head = " ".join(words)
    return head.rstrip(" ,;:-–·(") + "…"


def headline(p):
    t = clean_title(p.get("title"), p.get("deadline"), p.get("published_day"))
    return cut_clause(t, 90)


# ----------------------------------------------------------------------------- what a post shows
def ctx(p, cfg):
    """Everything the slides and captions need, resolved once."""
    uni = (cfg.get("university") or "knruhs").lower()
    th = themes.theme(uni)
    name, tagline, handle = themes.brand(uni, cfg)
    ty = p.get("type") or "circular"
    kicker, emoji, ty_tag = TYPES.get(ty, ("NOTICE", "📌", ""))
    is_list = ty != "result" and bool(LIST_RX.search(p.get("title") or ""))
    if is_list:
        kicker = "ALLOTMENT" if re.search(r"ALLOTMENT|ALLOCATION", p.get("title") or "", re.I) else "MERIT LIST"
    hl = headline(p)
    now_ms = datetime.now(IST).timestamp() * 1000
    dl = p.get("deadline") or None
    urgent = bool(dl) and 0 <= dl - now_ms <= 72 * 3600 * 1000
    names_free = ty == "result" or is_list   # no names / marks / ranks on these
    search_line = (REGNO_LINE if ty == "result" else LIST_LINE) if names_free else ""
    summary = " ".join(str(p.get("summary") or "").split())
    if (names_free or not summary or BOILER_SUMMARY.search(summary) or summary.lower() == hl.lower().rstrip("…")
            or (p.get("title") or "").lower().startswith(summary.lower()[:60])):
        summary = ""
    if summary:
        sents, acc = re.split(r"(?<=[.!?])\s+", summary), ""
        for snt in sents:
            if len(acc) + len(snt) > 220:
                break
            acc = (acc + " " + snt).strip()
        summary = re.sub(r"\s+\(?\d{1,2}[.)]$", "", acc) or cut_clause(summary, 220)
    pts = []
    if not names_free:
        for t in (p.get("points") or []):
            t = re.sub(r"[\uf000-\uf8ff]", "", " ".join(str(t).split())).strip(" •-")
            if t and t.lower() not in hl.lower() and t.lower() != summary.lower():
                pts.append(cut_clause(t, 110))
        pts = pts[:4]
    src = p.get("source_url") or ""
    dom = urlparse(src).netloc.lower()
    dom = dom[4:] if dom.startswith("www.") else dom
    return dict(uni=uni, th=th, brand=name, tagline=tagline, handle=handle, kicker=kicker, emoji=emoji, ty_tag=ty_tag,
                headline=hl, deadline=fmt_dt(dl) if dl else "", urgent=urgent, regno=search_line, summary=summary, points=pts,
                published_day=p.get("published_day") or "",
                domain=dom, stream=STREAMS.get(p.get("stream")), disclaimer=f"Unofficial alert, not from {th['disc_from']}. Verify on the official PDF.")


# ----------------------------------------------------------------------------- slides
# Every slide is drawn as three transparent layers (header, body, footer) first. Their pixels give the text mask the
# motif has to stay clear of (themes.background moves / scales it, and caps whatever is left behind text). Then the
# layers go on top of the background.
CONTENT_TOP, CONTENT_BOT = 232, SAFE_BOT - 84   # free area between the header row and the footer row
FOOT_Y = SAFE_BOT - 40


def _layer():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def text_mask(layers, pad):
    """L mask of every drawn pixel in `layers`, grown by about `pad` px."""
    a = Image.new("L", (W, H), 0)
    for L in layers:
        a = ImageChops.lighter(a, L.getchannel("A"))
    a = a.point(lambda v: 255 if v > 8 else 0)
    if pad <= 0:
        return a
    q = 4
    small = a.resize((W // q, H // q), Image.BOX).point(lambda v: 255 if v > 0 else 0)
    small = small.filter(ImageFilter.MaxFilter(2 * max(1, pad // q) + 1))
    return small.resize((W, H), Image.NEAREST)


def block_mask(L, pad):
    """The content block as one rectangle across the whole text column (MX .. W-MX), grown by pad: the motif may not
    sit beside a short line either, only above or below the block."""
    m = Image.new("L", (W, H), 0)
    box = L.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if box:
        ImageDraw.Draw(m).rectangle([MX - pad, box[1] - pad, W - MX + pad, box[3] + pad], fill=255)
    return m


def compose(c, strength, hdr, body, foot):
    th = c["th"]
    blk = block_mask(body, 30)
    around = ImageChops.lighter(text_mask([hdr, foot], 30), blk)
    bg = themes.background(th, strength,
                           avoid=around,                               # movable motif parts keep 30 px from all text
                           avoid_body=blk,
                           fade=ImageChops.lighter(text_mask([hdr, foot], 8), block_mask(body, 8)))  # only a ghost behind text
    out = bg.convert("RGBA")
    for L in (hdr, body, foot):
        out.alpha_composite(L)
    return out.convert("RGB")


def zone(c):
    return themes.ZONE_OF.get(c["th"]["motif"], "fixed")


def place(c, block_h):
    """Top y for a content block of block_h: centred in the free area, nudged away from the motif's corner."""
    bias = {"br": 0.3, "bl": 0.3, "b": 0.3, "tr": 0.62}.get(zone(c), 0.5)
    return CONTENT_TOP + int(max(0, CONTENT_BOT - CONTENT_TOP - block_h) * bias)


def header(d, c):
    th = c["th"]
    y = 150
    bell(d, MX, y - 6, 58, th["accent"], th["bg"], th["text"])
    fb = font("bold", 30)
    d.text((MX + 74, y + 4 if not c["tagline"] else y - 6), c["brand"], font=fb, fill=th["text"])
    if c["tagline"]:
        d.text((MX + 74, y + 30), c["tagline"], font=font("regular", 22), fill=th["muted"])
    f = font("bold", 22)
    label = "UNOFFICIAL"
    w = int(d.textlength(label, font=f)) + 36
    x1 = W - MX
    d.rounded_rectangle([x1 - w, y + 2, x1, y + 44], radius=21, fill=th["bg"], outline=th["muted"], width=2)
    d.text((x1 - w + 18, y + 10), label, font=f, fill=th["muted"])


def footer(d, c, n, total, left):
    """Source line + slide dots. They stay on the side away from the motif: bottom-right motif -> both on the left,
    bottom-left motif -> both on the right, otherwise source left and dots right."""
    th = c["th"]
    y = FOOT_Y
    z = zone(c)
    dots_w = total * 30 - 14 if total > 1 else 0
    f = tfont("semi", 27, left)
    text = ""
    if left:
        _, lines = fit(d, left, "semi", [27, 26], W - 2 * MX - dots_w - 40, 1)
        text = lines[0] if lines else ""
    tw = int(d.textlength(text, font=f)) if text else 0
    gap = 28 if text and dots_w else 0
    if z == "br":
        tx, dx = MX, MX + tw + gap
    elif z == "bl":
        dx = W - MX - dots_w
        tx = dx - gap - tw
    else:
        tx, dx = MX, W - MX - dots_w
    if text:
        d.text((tx, y), text, font=f, fill=th["muted"])
    if total > 1:
        for i in range(total):
            on = i == n
            d.ellipse([dx + i * 30, y + 8, dx + i * 30 + 16, y + 24], fill=th["accent"] if on else th["line"])


def kicker_row(d, c, y):
    th = c["th"]
    x = MX
    x = chip(d, x, y, c["kicker"], font("xbold", 26), bg=th["accent"], fg=th["on_accent"])
    if c["urgent"]:
        x = chip(d, x, y, "URGENT", font("xbold", 26), bg=None, fg=th["ink_accent"], outline=th["accent"])
    if c["stream"]:
        label, col, _ = c["stream"]
        fc = font("semi", 26)
        if x + chip_w(d, label, fc, dot=True) <= W - MX:
            chip(d, x, y, label, fc, bg=None, fg=th["text"], outline=th["line"], dot=col)
    return y + 54


def _cover_block(d, c, y, f, lines, lf):
    """Draw the cover's content block from y; returns the bottom y."""
    th = c["th"]
    y = kicker_row(d, c, y) + 40
    y = draw_lines(d, MX, y, lines, f, th["text"], lf)
    if c["published_day"]:
        y += 18
        fm = font("semi", 30)
        d.text((MX, y), f"Official notice dated {c['published_day']}", font=fm, fill=th["muted"])
        y += line_h(fm, 1.2)
    if c["deadline"]:
        y += 34
        bh = 136
        d.rounded_rectangle([MX, y, W - MX, y + bh], radius=24, fill=th["card"], outline=th["accent"], width=4)
        d.text((MX + 34, y + 20), "LAST DATE", font=font("bold", 26), fill=th["ink_accent"])
        fd, dl = fit(d, c["deadline"], "xbold", [54, 48, 42], W - 2 * MX - 68, 1)
        d.text((MX + 34, y + 58), dl[0], font=fd, fill=th["text"])
        y += bh
    if c["regno"]:
        y += 36
        fr = font("semi", 36)
        rl = wrap_balanced(d, c["regno"], fr, W - 2 * MX - 60)
        magnifier(d, MX, y + 4, 36, th["ink_accent"])
        y = draw_lines(d, MX + 60, y - 2, rl, fr, th["text"], 1.2)
    return y


def _cover_extra(d, c):
    """Height of everything in the cover block except the headline lines (measured, not guessed)."""
    probe = ImageDraw.Draw(_layer())
    return _cover_block(probe, c, 0, font("xbold", 72), [], 1.12)


def slide_cover(p, cfg, n, total, c=None):
    c = c or ctx(p, cfg)
    th = c["th"]
    hdr, body, foot = _layer(), _layer(), _layer()
    header(ImageDraw.Draw(hdr), c)
    d = ImageDraw.Draw(body)
    hl = c["headline"]
    lf = 1.45 if DEVA.search(hl) else 1.12   # Devanagari needs room for the matras above and below
    extra = _cover_extra(d, c)
    room = CONTENT_BOT - CONTENT_TOP - extra
    best = None
    for s in (96, 90, 84, 78, 72):
        f = tfont("xbold", s, hl)
        lines = wrap(d, hl, f, W - 2 * MX)
        if len(lines) * line_h(f, lf) <= room and len(lines) <= 5:
            cand = (f, wrap_balanced(d, hl, f, W - 2 * MX))
            best = best or cand
            last = cand[1][-1]
            # a short single word alone on the last line is an orphan; a long one ("verification…") reads fine
            if len(cand[1]) < 2 or len(last.split()) > 1 or d.textlength(last, font=f) >= 0.4 * (W - 2 * MX):
                best = cand
                break
    if not best:
        max_lines = max(1, room // line_h(tfont("xbold", 72, hl), lf))
        best = fit(d, hl, "xbold", [72], W - 2 * MX, min(5, max_lines))
    f, lines = best
    block = extra + len(lines) * line_h(f, lf)
    _cover_block(d, c, place(c, block), f, lines, lf)
    left = f"Source: {c['domain']}" if c["domain"] else th["short"]
    footer(ImageDraw.Draw(foot), c, n, total, left)
    return compose(c, 1.0, hdr, body, foot)


def _points_block(d, c, y, sz, wrapped, sum_f, sum_lines):
    th = c["th"]
    if sum_lines:
        y = draw_lines(d, MX, y, sum_lines, sum_f, th["text"], 1.3) + 40
    if wrapped:
        if sum_lines:
            d.line([(MX, y - 18), (MX + 120, y - 18)], fill=th["accent"], width=4)
            y += 14
        for t, ls in zip(c["points"], wrapped):
            f = tfont("regular", sz, t)
            d.rounded_rectangle([MX, y + sz * 0.38, MX + 18, y + sz * 0.38 + 18], radius=4, fill=th["accent"])
            y = draw_lines(d, MX + 50, y, ls, f, th["text"], 1.28) + 28
        y -= 28
    return y


def slide_points(p, cfg, n, total, c=None):
    c = c or ctx(p, cfg)
    th = c["th"]
    hdr, body, foot = _layer(), _layer(), _layer()
    header(ImageDraw.Draw(hdr), c)
    d = ImageDraw.Draw(body)
    avail = CONTENT_BOT - CONTENT_TOP
    sum_f, sum_lines, used = None, [], 0
    if c["summary"]:
        sum_f, sum_lines = fit(d, c["summary"], "semi", [42, 40, 38, 36], W - 2 * MX, 5 if c["points"] else 9)
        used = len(sum_lines) * line_h(sum_f, 1.3) + 40 + (14 if c["points"] else 0)
    pts = c["points"]
    sz, wrapped = 34, []
    if pts:
        tw = W - 2 * MX - 50
        chosen = None
        for sz in (40, 38, 36, 34):  # one size for every point
            f = font("regular", sz)
            wr = [no_orphan(d, t, tfont("regular", sz, t), tw) for t in pts]
            need = sum(len(w) * line_h(f, 1.28) + 28 for w in wr) - 28
            if need <= avail - used:
                chosen = (sz, wr)
                break
        if not chosen:
            sz = 34
            per = max(1, (avail - used) // len(pts))
            maxl = max(1, (per - 28) // line_h(font("regular", sz), 1.28))
            chosen = (sz, [fit(d, t, "regular", [sz], tw, maxl)[1] for t in pts])
        sz, wrapped = chosen
    probe = ImageDraw.Draw(_layer())
    block = _points_block(probe, c, 0, sz, wrapped, sum_f, sum_lines)
    _points_block(d, c, place(c, block), sz, wrapped, sum_f, sum_lines)
    footer(ImageDraw.Draw(foot), c, n, total, f"Source: {c['domain']}" if c["domain"] else th["short"])
    return compose(c, 0.5, hdr, body, foot)


def _final_block(d, c, p, y):
    th = c["th"]
    d.text((MX, y), "OFFICIAL NOTICE", font=font("bold", 28), fill=th["ink_accent"])
    y += 58
    rows = []
    if c["deadline"]:
        rows.append(("Last date", c["deadline"], th["ink_accent"]))
    if p.get("ref"):
        rows.append(("Ref. no.", p["ref"], th["text"]))
    if p.get("published_day"):
        rows.append(("Notice dated", p["published_day"], th["text"]))
    if c["domain"]:
        rows.append(("Official PDF", c["domain"], th["text"]))
    for label, value, col in rows:
        d.rounded_rectangle([MX, y, W - MX, y + 124], radius=22, fill=th["card"])
        d.text((MX + 34, y + 20), label.upper(), font=font("bold", 24), fill=th["muted"])
        f, lines = fit(d, value, "xbold", [46, 42, 38, 34], W - 2 * MX - 68, 1)
        d.text((MX + 34, y + 56), lines[0], font=f, fill=col)
        y += 142
    y += 14
    f, lines = fit(d, c["disclaimer"], "semi", [36, 34, 32], W - 2 * MX - 64, 3)
    bh = len(lines) * line_h(f, 1.3) + 52
    d.rounded_rectangle([MX, y, W - MX, y + bh], radius=22, fill=th["card"], outline=th["line"], width=2)
    draw_lines(d, MX + 32, y + 26, lines, f, th["text"], 1.3)
    y += bh
    if c["handle"]:
        y += 36
        bell(d, MX, y - 4, 50, th["accent"], th["bg"], th["text"])
        d.text((MX + 66, y + 2), f"Alerts: {c['handle']}", font=font("bold", 36), fill=th["text"])
        y += 50
    return y


def slide_final(p, cfg, n, total, c=None):
    c = c or ctx(p, cfg)
    hdr, body, foot = _layer(), _layer(), _layer()
    header(ImageDraw.Draw(hdr), c)
    block = _final_block(ImageDraw.Draw(_layer()), c, p, 0)
    _final_block(ImageDraw.Draw(body), c, p, place(c, block))
    footer(ImageDraw.Draw(foot), c, n, total, "")
    return compose(c, 0.5, hdr, body, foot)


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
        req = urllib.request.Request(url, headers={"User-Agent": cfg.get("user_agent", "Student-Notice-Alerts-Bot")})
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
    """Picture(s) of the actual university notice, in a minimal themed frame."""
    try:
        import pymupdf as fitz
    except Exception:
        try:
            import fitz
        except Exception:
            return []
    path = _pdf_file(p, draft, cfg)
    if not path:
        return []
    th = themes.theme((cfg.get("university") or "knruhs"))
    out = []
    try:
        doc = fitz.open(path)
        n = min(len(doc), max_pages)
        for i in range(n):
            page = doc[i]
            band = 116
            box_w, box_h = W - 80, H - band - 40
            zoom = min(box_w / page.rect.width, box_h / page.rect.height)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            pg = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            im = Image.new("RGB", (W, H), th["bg"])
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, W, band - 22], fill=th["bg2"])
            label = f"{th['short']}  ·  Official notice" + (f"  ·  page {i + 1}/{len(doc)}" if len(doc) > 1 else "")
            f = font("bold", 30)
            d.rectangle([MX, 38, MX + 8, 72], fill=th["accent"])
            d.text((MX + 24, 36), label, font=f, fill=th["text"])
            x = (W - pg.width) // 2
            y = band + (H - band - 20 - pg.height) // 2
            d.rectangle([x - 4, y - 4, x + pg.width + 4, y + pg.height + 4], fill=th["accent"])
            im.paste(pg, (x, y))
            out.append(im)
    except Exception:
        return []
    return out


# ----------------------------------------------------------------------------- captions
def clip(t, n):
    t = " ".join(str(t).split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def hashtags(c, p):
    tags = list(c["th"]["tags"])
    course = list(c["stream"][2]) if c["stream"] else []
    if "#BDS" in course or "#MDS" in course:
        course = [t for t in course if t != "#Dental"]
    tags += course[:3]
    if c["ty_tag"]:
        tags.append(c["ty_tag"])
    return list(dict.fromkeys(tags))[:8]


def _join(lines):
    s = "\n".join(lines)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def captions(p, cfg):
    c = ctx(p, cfg)
    hl, dl, emoji = c["headline"], c["deadline"], c["emoji"]
    src = p.get("source_url") or ""
    link = c["handle"] if c["handle"].startswith("http") else ("https://" + c["handle"] if c["handle"] else "")
    body = []
    if dl:
        body.append(f"⏰ Last date: {dl}")
    if c["summary"]:
        body.append(clip(c["summary"], 400))
    body += [f"• {t}" for t in c["points"]]
    if c["regno"]:
        body.append(f"🔍 {c['regno']}")
    tags = hashtags(c, p)

    ig = [f"{emoji} {hl}"] + body + ["📄 Official PDF on the last slides."]
    if c["handle"]:
        ig.append(f"🔔 Alerts: {c['handle']}")
    ig += ["", c["disclaimer"], " ".join(tags)]
    instagram = _join(ig)
    if len(instagram) > 2150:
        instagram = instagram[:2147].rstrip() + "…"

    fb = [f"{emoji} {hl}"] + body
    if src:
        fb.append(f"📄 Official PDF: {src}")
    if link:
        fb.append(f"🔔 Alerts: {link}")
    fb += ["", c["disclaimer"], " ".join(c["th"]["tags"])]
    facebook = _join(fb)

    wa = [f"{emoji} *{hl}*"]
    if dl:
        wa.append(f"⏰ *Last date: {dl}*")
    wa += [f"• {t}" for t in c["points"]]
    if c["regno"]:
        wa.append(f"🔍 {c['regno']}")
    if src:
        wa.append(f"📄 {src}")
    if link:
        wa.append(f"🔔 {link}")
    wa.append(f"_{c['disclaimer']}_")
    whatsapp = _join(wa)

    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    tg = [f"{emoji} <b>{esc(hl)}</b>"]
    if dl:
        tg.append(f"⏰ <b>Last date: {esc(dl)}</b>")
    if c["summary"]:
        tg.append(esc(clip(c["summary"], 400)))
    tg += [f"• {esc(t)}" for t in c["points"]]
    if c["regno"]:
        tg.append(f"🔍 {c['regno']}")
    if src:
        tg.append(f'📄 <a href="{esc(src)}">Official PDF</a>')
    tg.append(f"<i>{esc(c['disclaimer'])}</i>")
    telegram = _join(tg)
    if len(telegram) > 1000:  # photo captions are limited to 1024 chars; the full text goes as a separate message
        telegram_caption = "\n".join(telegram.split("\n")[:2])
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
    """Render slides + captions for one draft. Cached: only re-renders when the content, brand or theme changes."""
    m = draft.get("meta", {})
    pid = pack_id(draft)
    folder = os.path.join(out_root, pid)
    p = {
        "id": pid, "title": cap1(draft.get("title")), "summary": draft.get("summary") or "", "points": draft.get("points") or [],
        "stream": draft.get("stream") or "mbbs", "type": draft.get("type") or "circular", "urgent": bool(draft.get("urgent")),
        "deadline": draft.get("deadline"), "ref": draft.get("ref") or "", "source_url": m.get("source_url", ""),
        "published_day": fmt_day(datetime.fromisoformat(m["published"])) if m.get("published") else "",
    }
    uni = (cfg.get("university") or "knruhs").lower()
    sig = hashlib.sha1(json.dumps(["v5", p, cfg.get("brand_name"), cfg.get("handle"), cfg.get("brand_tagline"), uni],
                                  sort_keys=True, default=str).encode()).hexdigest()[:12]
    if cfg.get("no_slides"):  # a university that only goes to the app: no pictures, no captions, nothing to render
        return dict(p, sig=sig, slides=[], captions={}, flags=m.get("flags", []), confidence=m.get("confidence"))
    meta_path = os.path.join(folder, "pack.json")
    if not force and os.path.exists(meta_path):
        try:
            old = json.load(open(meta_path, encoding="utf-8"))
            if old.get("sig") == sig and all(os.path.exists(os.path.join(folder, s)) for s in old.get("slides", [])):
                return old
        except Exception:
            pass
    os.makedirs(folder, exist_ok=True)
    c = ctx(p, cfg)
    has_body = bool(c["points"]) or bool(c["summary"])
    makers = [slide_cover] + ([slide_points] if has_body else []) + [slide_final]
    originals = []
    try:  # only fresh notices get pictures of the original PDF (old ones are already published; avoids re-downloading every run)
        det = datetime.fromisoformat((draft.get("meta") or {}).get("detected_at", ""))
        # (a notice being posted by hand with POST_CODE also gets them, e.g. a one-time catch-up of older notices)
        if (datetime.now(IST) - det).total_seconds() < 3 * 86400 or pid in os.environ.get("POST_CODE", ""):
            originals = slides_original(p, draft, cfg)
    except Exception:
        pass
    total = len(makers)
    order = makers[:-1] + ["orig"] * len(originals) + makers[-1:]  # cover, points, ORIGINAL pages, official-notice slide
    names, oi = [], 0
    for i, mk in enumerate(order):
        if mk == "orig":
            im = originals[oi]
            oi += 1
        else:
            im = mk(p, cfg, len([x for x in order[:i] if x != "orig"]), total, c)
        name = f"slide-{i + 1}.jpg"
        im.save(os.path.join(folder, name), "JPEG", quality=90, optimize=True)  # Instagram's API accepts JPEG only
        names.append(name)
    pack = dict(p, sig=sig, slides=names, captions=captions(p, cfg), flags=m.get("flags", []), confidence=m.get("confidence"),
                built_at=datetime.now(IST).isoformat(timespec="seconds"))
    json.dump(pack, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return pack
