#!/usr/bin/env python3
"""
KNRUHS Pulse - notice-fetching bot (prototype)

What it does, every run:
  1. Opens the listing page(s) in config.json and collects links to notice PDFs.
  2. Compares them with its memory (data/seen.db) and finds the NEW ones.
  3. Downloads each new PDF and extracts its text.
  4. Drafts a post: title, summary, key points, stream, type, deadline, reference no., urgency.
  5. Writes drafts to data/drafts.json and a human review page data/review.html.

A human must approve every draft before it is published. The bot never publishes by itself.

Usage:
  python3 bot.py --once                 # one check, then exit
  python3 bot.py --watch                # check forever (every interval_seconds in config.json)
  python3 bot.py --once --backfill      # on the first run, process everything on the page (default: only newest 5)
  python3 bot.py --reset                # forget everything seen so far
Python 3.9+ , standard library only. Optional: `pip3 install pypdf` (PDF text) and ANTHROPIC_API_KEY (better drafts).
"""
import argparse, hashlib, ssl, html, json, os, re, shutil, sqlite3, subprocess, sys, time
import urllib.error, urllib.parse, urllib.request, urllib.robotparser
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
IST = timezone(timedelta(hours=5, minutes=30))
STREAMS = ["mbbs", "bds", "mdms", "mds", "ss", "ayush", "nursing", "allied", "jobs"]
TYPES = ["counselling", "result", "exam", "admission", "circular", "recruitment"]

DEFAULT_CONFIG = {
    "sources": [
        {"name": "Home (latest)", "url": "https://www.knruhs.telangana.gov.in/"},
        {"name": "Admission notifications", "url": "https://www.knruhs.telangana.gov.in/admission-notification/"},
        {"name": "Exam notifications", "url": "https://www.knruhs.telangana.gov.in/exam-notifications/"},
        {"name": "Results", "url": "https://www.knruhs.telangana.gov.in/results/"},
    ],
    "link_patterns": [r"/wp-content/uploads/notifications/.*\.pdf($|\?)"],
    "user_agent": "KNRUHS-Pulse-Bot/0.1 (student notice alerts; contact: set-your-email@example.com)",
    "delay_seconds": 2,
    "interval_seconds": 300,
    "first_run_limit": 5,
    "max_new_per_run": 25,
    "respect_robots": True,
    "data_dir": "data",
    "llm": {"enabled": False, "model": "claude-haiku-5-5"},
    "brand": {"name": "KNRUHS Pulse", "tagline": "Updates in plain words", "handle": "@knruhspulse"},
    "telegram": {},
    "instagram": {},
    "desk": {"port": 8800},
}


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


def log(*a):
    print(datetime.now(IST).strftime("%H:%M:%S"), *a, flush=True)


# ----------------------------------------------------------------------------- fetching
class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._href, self._txt = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._txt = []

    def handle_data(self, data):
        if self._href is not None:
            self._txt.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._txt).split())))
            self._href = None


def ssl_context(cfg):
    """Mac python.org builds often lack root certificates. Use certifi if present; verify_ssl:false is a last resort."""
    if cfg.get("verify_ssl", True) is False:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def http_get(url, cfg, max_bytes=30_000_000):
    # some sites link files with spaces or non-English letters in their names ("Letter No-4017.pdf"): escape them, keep what is already escaped
    url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~")
    req = urllib.request.Request(url, headers={"User-Agent": cfg["user_agent"], "Accept": "*/*"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl_context(cfg)) as r:
                return r.read(max_bytes), r.headers
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    hint = ""
    if "CERTIFICATE_VERIFY_FAILED" in str(last):
        hint = "  [HINT: run  pip3 install certifi  and try again; if it still fails set \"verify_ssl\": false in config.json]"
    raise RuntimeError(f"could not fetch {url}: {last}{hint}")


def robots_ok(url, cfg):
    if not cfg.get("respect_robots", True):
        return True
    p = urllib.parse.urlsplit(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        data, _ = http_get(f"{p.scheme}://{p.netloc}/robots.txt", cfg, 200_000)
        rp.parse(data.decode("utf-8", "ignore").splitlines())
    except Exception:
        return True  # no robots.txt reachable -> allowed
    return rp.can_fetch(cfg["user_agent"], url)


STAMP_IN_IST = False  # set per university from the config: KNRUHS file names carry UTC time, Dr. NTR UHS file names carry Indian time
FILE_STAMPS = True    # False where file names are not times (MCC / NMC): the date then comes from the link text or the server


def file_stamp(url):
    """Real KNRUHS uploads start with a 14-digit UTC timestamp, e.g. 20261116122909_KNRUHS-... (matches Last-Modified).
    Dr. NTR UHS uploads are named like 20261124182505900.pdf (Indian time, with milliseconds)."""
    if not FILE_STAMPS:
        return None
    name = urllib.parse.unquote(os.path.basename(urllib.parse.urlsplit(url).path))
    m = re.match(r"(\d{14})", name)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=IST if STAMP_IN_IST else timezone.utc).astimezone(IST)
        except ValueError:
            pass
    return None


def anchor_date(text):
    """Home-page links look like '16 Nov: KNRUHS - ...' (no year). NMC links look like '22/11/2026 - ...'."""
    m2 = re.match(r"\s*(\d{1,2})/(\d{1,2})/(20\d{2})\s*[-–:]", text or "")
    if m2:
        try:
            return datetime(int(m2.group(3)), int(m2.group(2)), int(m2.group(1)), 12, tzinfo=IST)
        except ValueError:
            return None
    m = re.match(r"\s*(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s*:", text or "")
    if not m:
        return None
    try:
        now = datetime.now(IST)
        d = datetime.strptime(f"{m.group(1)} {m.group(2)} {now.year}", "%d %b %Y").replace(hour=12, tzinfo=IST)
        return d - timedelta(days=365) if d > now + timedelta(days=31) else d
    except ValueError:
        return None


ACRONYMS = {"MDS", "MBBS", "BDS", "MD", "MS", "PG", "BHMS", "BAMS", "BUMS", "BNYS", "AY", "KNRUHS", "DM", "MCH", "NMC", "CA", "CQ", "MQ",
            "GNM", "ANM", "NEET", "CRRI", "NRI", "MLT", "BPT", "AHS", "MBA", "II", "III", "IV", "NIMS", "RTI", "SC", "ST", "BC", "EWS", "OBC",
            "UG", "DNB", "AIQ", "ABVMU", "RUHS", "WBUHS", "BFUHS", "UHSR", "BUHS", "AMRU", "BHMS", "BAMS", "CBDC", "PBBSC", "PGIMS", "PGIDS", "UMC", "ANM", "RLA", "MDS", "BDS", "MBBS", "MLS", "APB", "AOTT", "DAOTT", "BASLP", "MASLP", "DMLT", "DMRIT", "GNM", "ANM", "MHHA", "JEMAS", "PDCC", "PDF", "BPT", "MPH", "TNMGRMU", "EC", "AISHE", "RGUHS", "NTRUHS", "KNRUHS", "CBME", "RS3", "RS4", "MCC", "NMC", "NBEMS", "INI", "CET", "FMGE", "AIIMS", "NTA", "SS", "MSC", "BSC"}


def smart_case(t):
    """ALL-CAPS file/anchor titles -> readable sentence case, keeping known acronyms."""
    t = " ".join(t.split())
    letters = [c for c in t if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.7:
        return t
    out = []
    for w in t.split(" "):
        core = re.sub(r"[^A-Za-z]", "", w)
        parts = [re.sub(r"[^A-Za-z]", "", x).upper() for x in re.split(r"[/]", w) if re.sub(r"[^A-Za-z]", "", x)]
        if len(parts) > 1 and all(x in ACRONYMS for x in parts):  # MD/MS/DNB, BDS/B.SC
            out.append(w); continue
        out.append(w if core.upper() in ACRONYMS or re.match(r"^[A-Z]\.?[A-Z]", w) and "." in w else w.lower())
    r = " ".join(out)
    return r[:1].upper() + r[1:]


def clean_name(url):
    name = urllib.parse.unquote(os.path.basename(urllib.parse.urlsplit(url).path))
    name = re.sub(r"\.pdf$", "", name, flags=re.I)
    name = re.sub(r"^\d{14}[_ -]*", "", name)
    name = re.sub(r"^\s*KNRUHS\s*[-_ ]+", "", name, flags=re.I)
    name = name.replace("_.", ".").replace("._", ".").replace("_", " ")
    if " " not in name.strip():
        name = name.replace("-", " ")
    name = " ".join(name.split()).strip(" -")
    return smart_case(name)


GENERIC_ANCHOR = re.compile(r"^(pdf|view( document)?|download|click here|read more|more)$", re.I)


RGUHS_COURSES = {"medical": "mbbs", "dental": "bds", "nursing": "nursing", "ayurveda": "ayush", "unani": "ayush", "homeopathy": "ayush",
                 "naturopathy & yogic sciences": "ayush", "pharmacy": "allied", "allied health sciences": "allied", "physiotherapy": "allied"}


def json_items(src, body, found):
    """RGUHS lists its notices through a JSON feed (the web page fills itself from it): title, PDF link, date, courses.
    Only the title, the PDF link, the date and the course are used; nothing about the staff who posted it."""
    try:
        rows = json.loads(body.decode("utf-8", "ignore"))["data"]["notifications"]
    except Exception as e:
        log(f"could not read the notice list of {src['name']}: {e}")
        return
    for n in rows:
        url = (n.get("attachment_url") or "").strip()
        if not url.lower().endswith(".pdf"):
            continue  # a link to another portal, or no PDF at all
        hint = None
        try:
            hint = datetime.strptime((n.get("created_at") or "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).astimezone(IST)
        except ValueError:
            pass
        streams = {RGUHS_COURSES.get(str(c).strip().lower()) for c in (n.get("courses") or [])} - {None}
        found.setdefault(url, {"url": url, "anchor": " ".join(str(n.get("title") or "").split()), "source": src["name"], "hint": hint,
                               "stream_hint": streams.pop() if len(streams) == 1 else None, "prefer_anchor": False, "include": None,
                               "exclude": src.get("exclude")})


def grid_items(src, body, found):
    """BFUHS lists date sheets and results in an ASP.NET grid whose "View" buttons post the page back, so there is
    no PDF address to give students. Each row (number, date, course, session) becomes a link-only notice that opens
    the university's own date-sheet or result page, where the student presses View."""
    page = body.decode("utf-8", "ignore")
    open_url = src.get("open_url") or src["url"]
    for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", page):
        if "__doPostBack" not in row:
            continue
        cells = [" ".join(html.unescape(re.sub(r"(?s)<[^>]+>", " ", c)).split()) for c in re.findall(r"(?is)<td[^>]*>(.*?)</td>", row)]
        cells = [c for c in cells if c and c.lower() not in ("view", "view result")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        sr, rest = cells[0], cells[1:]
        hint = None
        for c in rest:
            try:
                hint = datetime.strptime(c, "%d-%m-%Y").replace(hour=12, tzinfo=IST); break
            except ValueError:
                pass
        words = [c for c in rest if not re.fullmatch(r"\d\d-\d\d-\d{4}", c)]
        if src["grid"] == "bfuhs_result":
            # columns: course, year/prof, session, notification no.
            course, year, session, nno = (words + ["", "", "", ""])[:4]
            title = f"{course} {year} result, {session} session" + (f" (notification {nno})" if nno and nno != "1" else "")
        else:
            # columns: session, title
            title = words[-1] if words else ""
            title = re.sub(r"(?i)\bdate ?sheet", "Date sheet", title)
        title = " ".join(re.sub(r"\(NS\d+\)", " (new scheme) ", title).split())  # "(NS21)" is the new-scheme code; students know it as the new scheme
        if not title:
            continue
        url = f"{open_url}#{src['grid']}-{sr}"
        found.setdefault(url, {"url": url, "anchor": title, "source": src["name"], "hint": hint, "stream_hint": src.get("stream"),
                               "prefer_anchor": True, "include": src.get("include"), "exclude": src.get("exclude"), "link_only": True})


def row_items(src, body, found, cfg):
    """Drupal-style tables (UHSR): the link only says "Click to View", the title and the date sit in other cells of
    the same row. Each row with a PDF link becomes a notice titled from its longest text cell."""
    page = body.decode("utf-8", "ignore")
    for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", page):
        hrefs = [urllib.parse.urljoin(src["url"], html.unescape(h)) for h in re.findall(r'(?i)href="([^"]+)"', row)]
        hrefs = [h for h in hrefs if any(re.search(p, h, re.I) for p in cfg["link_patterns"])]
        if not hrefs:
            continue
        hint = None
        m = re.search(r'datetime="(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)', row)
        if m:
            hint = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=IST)
        else:
            m = re.search(r"\b(\d\d)/(\d\d)/(\d\d(?:\d\d)?)\b", re.sub(r"(?s)<[^>]+>", " ", row))
            if m:
                y = int(m.group(3)); y = y + 2000 if y < 100 else y
                try: hint = datetime(y, int(m.group(2)), int(m.group(1)), 12, tzinfo=IST)
                except ValueError: pass
        cells = [" ".join(html.unescape(re.sub(r"(?s)<[^>]+>", " ", re.sub(r"(?s)<!--.*?-->", "", c))).split())
                 for c in re.findall(r"(?is)<td[^>]*>(.*?)</td>", row)]
        cells = [c for c in cells if not re.search(r"(?i)click to view|download pdf", c) and not re.fullmatch(r"[\d/ :.-]*", c)]
        title = max(cells, key=len) if cells else ""
        for h in hrefs[:1]:
            found.setdefault(h, {"url": h, "anchor": title, "source": src["name"], "hint": hint or name_date(h), "stream_hint": src.get("stream"),
                                 "prefer_anchor": True, "include": src.get("include"), "exclude": src.get("exclude")})


def name_date(url):
    """Dates some sites bake into file names: BUHS "12Nov202615213983.pdf", Chhattisgarh "1795433705_KlC7.pdf" (Unix time)."""
    name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    m = re.match(r"(\d\d)([A-Za-z]{3})(20\d\d)", name)
    if m:
        try:
            return datetime.strptime("".join(m.groups()), "%d%b%Y").replace(hour=12, tzinfo=IST)
        except ValueError:
            pass
    m = re.match(r"(1[6-9]\d{8})_", name)
    if m:
        return datetime.fromtimestamp(int(m.group(1)), IST)
    return None


def upload_month(url):
    """WordPress / S3WaaS sites file uploads under /uploads/2026/11/ - at least the month is known."""
    m = re.search(r"/uploads/(20\d\d)/(\d\d)/", url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), 1, 12, tzinfo=IST)
        except ValueError:
            return None
    return None


def discover(cfg):
    found = {}
    for src in cfg["sources"]:
        if not robots_ok(src["url"], cfg):
            log(f"robots.txt disallows {src['url']} - skipping (set respect_robots=false only if you have permission)")
            continue
        try:
            body, _ = http_get(src["url"], cfg)
        except Exception as e:
            log(f"could not open {src['url']}: {e}")
            continue
        if src.get("rows"):
            row_items(src, body, found, cfg)
            time.sleep(cfg["delay_seconds"])
            continue
        if src.get("grid"):
            grid_items(src, body, found)
            time.sleep(cfg["delay_seconds"])
            continue
        if src.get("json") == "rguhs":
            json_items(src, body, found)
            time.sleep(cfg["delay_seconds"])
            continue
        p = LinkParser()
        p.feed(body.decode("utf-8", "ignore"))
        for href, text in p.links:
            absu = urllib.parse.urljoin(src["url"], html.unescape(href.strip()))
            if not any(re.search(pat, absu, re.I) for pat in cfg["link_patterns"]):
                continue
            item = found.setdefault(absu, {"url": absu, "anchor": "", "source": src["name"], "hint": None, "stream_hint": src.get("stream"),
                                              "prefer_anchor": bool(src.get("prefer_anchor")),
                                              "include": src.get("include"), "exclude": src.get("exclude")})
            if text and not GENERIC_ANCHOR.match(text) and len(text) > len(item["anchor"]):
                item["anchor"] = text
            item["hint"] = item["hint"] or anchor_date(text) or upload_month(absu) or name_date(absu)
        time.sleep(cfg["delay_seconds"])
    # a source with "include" keeps only notices whose link text matches it (NMC posts much that is not for students)
    # and "exclude" drops notices meant for colleges, not students (affiliation applications and the like)
    items = [i for i in found.values() if (not i.get("include") or re.search(i["include"], i["anchor"] or "", re.I))
             and not (i.get("exclude") and re.search(i["exclude"], (i["anchor"] or "") + " " + clean_name(i["url"]), re.I))]
    floor = datetime.min.replace(tzinfo=IST)
    items.sort(key=lambda l: file_stamp(l["url"]) or l["hint"] or floor, reverse=True)
    return items


# ----------------------------------------------------------------------------- memory
def open_db(data_dir):
    db = sqlite3.connect(os.path.join(data_dir, "seen.db"))
    db.execute("""CREATE TABLE IF NOT EXISTS seen(url TEXT PRIMARY KEY, sha256 TEXT, first_seen TEXT, title TEXT, pdf TEXT, processed INTEGER)""")
    return db


# ----------------------------------------------------------------------------- PDF text
def pdf_text(path):
    text, pages, meta = "", 0, ""
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        pages = len(r.pages)
        text = "\n".join((pg.extract_text() or "") for pg in r.pages)
        meta = (r.metadata.title or "") if r.metadata else ""
    except Exception:
        pass
    if len(text.strip()) < 40 and shutil.which("pdftotext"):
        try:
            text = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, timeout=60).stdout
        except Exception:
            pass
    if not pages:
        try:
            with open(path, "rb") as f:
                pages = max(1, len(re.findall(rb"/Type\s*/Page[^s]", f.read())))
        except Exception:
            pages = 1
    meta = re.sub(r"^(Microsoft Word|Microsoft Office Word|Untitled)\s*[-–]\s*", "", meta.strip(), flags=re.I)
    return text, pages, meta


# ----------------------------------------------------------------------------- drafting (rules)
STREAM_RULES = {
    "mds": r"\bMDS\b|\bM\.D\.S\b|MASTER OF DENTAL|PG \(DENTAL\)",
    "ss": r"\bDM\b|\bM\.?CH\b|SUPER ?SPECI",
    "mdms": r"MD\s*/\s*MS|\bMD\b(?!\s*\((?i:A|H|HOMOEO|AYURVEDA|UNANI)\))(?!\s*-\s*(?i:HOMOEO|AYURVED|UNANI))|\bMS\b|(?i:PG MEDICAL|PG \(MEDICAL\)|POST ?GRADUATE|\(PG\)|PG COUNSEL|MEDICAL PG|PG DEGREE|PG DIPLOMA|NEET[- ]PG)",
    "bds": r"\bBDS\b|DENTAL",
    "mbbs": r"\bMBBS\b|BACHELOR OF MEDICINE AND BACHELOR OF SURGERY|\bCRRI\b|CONVENER QUOTA",
    "ayush": r"\bBHMS\b|\bBAMS\b|\bBUMS\b|\bBNYS\b|AYUSH|HOMOEO|AYURVED|UNANI|NATUROPATH|\bMD ?\((?:A|H)\)",
    "nursing": r"NURSING|\bGNM\b|ANMGNM|\bANM\b|\bB\.?SC\.? ?\(N\)|\bM\.?SC\.? ?\(N\)",
    "allied": r"ALLIED HEALTH|ALLIED AND HEALTH|PARA ?MEDICAL|\bBPT\b|\bMPT\b|PHYSIOTHERAPY|\bMLT\b|\bB\.?SC\.? ?\(?AHS|\bAHS\b|ALLIED HEALTHCARE|\bJEMAS\b|DIALYSIS|RESPIRATORY THERAPY|OPTOMETRY|PERFUSION|OPERATION THEATRE|ANAESTHESIA TECHNOLOGY|IMAGING TECHNOLOGY|CARDIAC (CARE )?TECHNOLOGY|MEDICAL LAB|\bB?MLS\b|\bD?MLT\b|\bAPB\b|\bB?\.?SC\.? ?AOTT\b|\bDAOTT\b|\bBASLP\b|\bMASLP\b|NUCLEAR MEDICINE|\bDMRIT\b",
    "jobs": r"RECRUIT|WALK[- ]?IN|DATA ENTRY OPERATOR|JUNIOR (?:OFFICE )?ASSISTANT|SYSTEM ADMINISTRATOR|COMPUTER OPERATOR|\bDEOS?\b|ASSISTANT PROFESSOR|\bTUTORS?\b|SENIOR RESIDENT|APPLICATIONS ARE INVITED|VACANC|POSTS? OF",
}
TYPE_RULES = {
    "recruitment": r"RECRUIT|WALK[- ]?IN|APPLICATIONS ARE INVITED|VACANC|POSTS? OF",
    "result": r"RESULTS?\b|DECLARED|PASSED|SUCCESSFUL",
    "exam": r"TIME ?TABLE|DATE ?SHEET|EXAMINATION CENTRE|HALL TICKET|EXAMINATIONS?|\bEXAMS?\b|SUPPLEMENTARY|EXAM FEE|\b(?:FINAL|PRELIMINARY) KEY\b|SEMESTER .*NOTIFICATION|RESCHEDULED|POSTPONED",
    "counselling": r"COUNSEL+ING|ALLO?T?MENTS?|WEB[- ]?OPTIONS?|MOP[- ]?UP|SEAT MATRIX|FREE EXIT|CHOICE FILLING|MERIT LIST|CLEAR VACANC|STRAY|\bROUND[- ]?\d|\d(ST|ND|RD|TH) ROUND|RESIGNATION",
    "admission": r"ADMISSIONS?|REGISTRATION|PROSPECTUS|ONLINE APPLICATION",
}
BOILER = re.compile(r"KALOJI|UNIVERSITY OF HEALTH|TELANGANA|WARANGAL|VIJAYAWADA|ANDHRA PRADESH|RAJIV GANDHI|KARNATAKA|BENGALURU|BANGALORE|TAMIL NADU|M\.?G\.?R\.? MEDICAL|CHENNAI|GUINDY|ATAL BIHARI|VAJPAYEE|UTTAR PRADESH|LUCKNOW|RAJASTHAN|JAIPUR|WEST BENGAL|KOLKATA|SALT LAKE|BABA FARID|FARIDKOT|PUNJAB|HARYANA|ROHTAK|BHAGWAT DAYAL|CHHATTISGARH|RAIPUR|DEENDAYAL|BIHAR|PATNA|HIMACHAL|MANDI|ATAL MEDICAL|^\s*(date|place)\s*:|REGISTRAR|CONTROLLER OF EXAM|^\s*sd/?-?\s*$", re.I)
DATE_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b")
TIME_RE = re.compile(r"(\d{1,2})[.:](\d{2})\s*(AM|PM|noon)|(\d{1,2})\s*(AM|PM)", re.I)
DEADLINE_WORDS = re.compile(r"last date|on or before|up ?to|not later than|till|until|before|by\b|from .* to", re.I)


def classify(rules, title, text):
    head = text[:1800]
    scores = {}
    for k, pat in rules.items():
        # patterns are case-insensitive except where they carry their own flags (MD / MS must be capitals)
        flags = 0 if k == "mdms" else re.I
        scores[k] = 3 * len(re.findall(pat, title, flags)) + len(re.findall(pat, head, flags))
    best = max(scores, key=lambda k: scores[k])
    if "result" in rules and re.search(rules["result"], title, re.I) and not re.search(r"TIME ?TABLE|DATE ?SHEET|HALL TICKET|FEE|ALLO?T?MENT|ROUND|COUNSEL", title, re.I):
        best, scores["result"] = "result", max(scores["result"], scores[best])
    total = sum(scores.values()) or 1
    return (best if scores[best] else None), round(scores[best] / total, 2)


def to_dt(d, m, y, hh=23, mm=59):
    try:
        return datetime(int(y), int(m), int(d), hh, mm, tzinfo=IST)
    except ValueError:
        return None


DATE_S = r"\d{1,2}[./-]\d{1,2}[./-]20\d{2}"
DL_KEYS = re.compile(r"last date|on or before|not later than|up ?to|\btill\b|\buntil\b|\bbefore\b|\bby\b|\bfrom\b", re.I)
RANGE_RE = re.compile(r"\bfrom\b.{0,70}?" + DATE_S + r".{0,40}?\bto\b.{0,40}?(" + DATE_S + ")", re.I)


def clock_near(sent, m):
    before = list(TIME_RE.finditer(sent[max(0, m.start() - 28): m.start()]))
    after = TIME_RE.search(sent[m.end(): m.end() + 28])
    t = before[-1] if before else after
    if not t:
        return 23, 59
    if t.group(1):
        hh, mm, ap = int(t.group(1)), int(t.group(2)), t.group(3).lower()
    else:
        hh, mm, ap = int(t.group(4)), 0, t.group(5).lower()
    if ap == "pm" and hh < 12: hh += 12
    if ap == "noon": hh = 12
    if ap == "am" and hh == 12: hh = 0
    return hh, mm


def find_deadline(text, published):
    """Earliest deadline-style date in the notice, with the clock time written next to it."""
    flat = re.sub(r"\s*\n\s*", " ", text)
    best = None
    for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z])", flat):
        cands = []
        rng = RANGE_RE.search(sent)
        if rng:
            cands = [m for m in DATE_RE.finditer(sent) if m.group(0) == rng.group(1)][-1:]
        else:
            for m in DATE_RE.finditer(sent):
                if DL_KEYS.search(sent[max(0, m.start() - 70): m.start()]):
                    cands.append(m)
        for m in cands:
            hh, mm = clock_near(sent, m)
            dt = to_dt(m.group(1), m.group(2), m.group(3), hh, mm)
            if dt and dt >= published - timedelta(days=1) and (best is None or dt < best):
                best = dt
    return best


def sentences(text):
    buf = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        if buf and re.search(r"[.:;)]$", buf[-1]) and line[:1].isupper():
            buf.append("\n" + line)
        elif buf:
            buf[-1] += " " + line
            continue
        else:
            buf.append(line)
            continue
        buf[-1] = buf[-1]
    joined = " ".join(buf)
    out = []
    for chunk in re.split(r"\n|(?<=[.!?])\s+(?=[A-Z])", joined):
        chunk = " ".join(chunk.split())
        if 35 <= len(chunk) <= 330 and not BOILER.search(chunk):
            out.append(chunk)
    return out


def tidy_title(s):
    s = re.sub(r"^(sub(ject)?\s*[:\-–]+\s*)", "", s.strip(), flags=re.I)
    s = re.sub(r"^KNRUHS\s*[-–]\s*", "", s, flags=re.I)
    s = re.sub(r"^(?:Exams?|Examinations?|Admissions?|Admn|Counsell?ing|Recruitment|Circulars?|Academic)\s*[-–]\s*", "", s, flags=re.I)
    s = re.sub(r"\s*[-–]+\s*Reg\.?\s*$", "", " ".join(s.split()), flags=re.I)
    s = s.strip(" .-")
    return s[:150]


def rule_draft(item, text, pages, published, meta_title=""):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    sub = next((l for l in lines if re.match(r"(sub(ject)?)\s*[:\-–]", l, re.I)), "")
    if sub:
        k = lines.index(sub)  # the subject wraps over several lines; stop at "Reg." or after 3 lines
        j = k
        while j < min(k + 3, len(lines) - 1) and not re.search(r"Reg\.?$", lines[j].strip(), re.I):
            j += 1
            sub += " " + lines[j]
    title = tidy_title(sub) if len(sub) > 20 else ""
    # the link text on the KNRUHS page is the human title; the PDF's hidden "title" is often junk (a file name like "Vacancies_P4 (1).xlsx")
    a = re.sub(r"^\s*\d{1,2}\s+[A-Za-z]{3}[a-z]*\.?\s*:\s*", "", item.get("anchor") or "")
    a = re.sub(r"^KNRUHS\s*[-:–]\s*", "", a, flags=re.I)
    a = re.sub(r"^\s*\d{1,2}/\d{1,2}/20\d{2}\s*[-–:]\s*", "", a)  # NMC: '22/11/2026 - title'
    a = re.sub(r"\s+new\s*$", "", a, flags=re.I)  # a blinking "new" badge inside the link (WBUHS)
    if item.get("prefer_anchor") and len(a) > 18 and not GENERIC_ANCHOR.match(a):
        title = ""  # MCC / NMC link texts are clean titles; use them instead of the PDF's subject line
    anchor_ok = len(a) > 18 and not GENERIC_ANCHOR.match(a)
    mt = meta_title.strip()
    meta_ok = len(mt) > 20 and not re.search(r"\.(xlsx?|docx?|pdf|pptx?|csv)\s*$", mt, re.I) and "_" not in mt and not re.search(r"\w-\w+-\w+-\w+", mt)
    if not title and anchor_ok:
        title = tidy_title(smart_case(a))
    if not title and meta_ok:
        title = tidy_title(smart_case(mt))
    if not title:
        title = clean_name(item["url"])
    title = title[:1].upper() + title[1:]
    flags = []
    # a title about admissions/counselling is never a job advert, even if it says "vacancies" (e.g. "vacancies for additional mop up phase")
    admissionish = re.search(r"ADMISSIONS?|COUNSEL|MOP[- ]?UP|WEB[- ]?OPTIONS?|ALLO?T?MENT|MERIT LIST|SEAT", title, re.I) and not re.search(r"RECRUIT|WALK[- ]?IN|ASSISTANT PROFESSOR|APPLICATIONS ARE INVITED", title, re.I)
    srules = {k: v for k, v in STREAM_RULES.items() if not (admissionish and k == "jobs")}
    trules = {k: v for k, v in TYPE_RULES.items() if not (admissionish and k == "recruitment")}
    stream, sconf = classify(srules, title, text)
    typ, tconf = classify(trules, title, text)
    if re.search(r"\bMDS\b|PG ?\(?DENTAL\)?|MASTER OF DENTAL", title, re.I):
        stream, sconf = "mds", max(sconf, 0.9)  # a title about MDS / PG dental beats the many "dental" words in the body (which point to BDS)
    if stream == "jobs" or (typ == "recruitment" and not stream):
        stream, typ = "jobs", "recruitment"
    if item.get("stream_hint") and (not stream or sconf < 0.5) and stream != "jobs":
        stream, sconf = item["stream_hint"], 0.6  # e.g. a notice on MCC's PG page with no course in its title is a PG notice
    if not stream:
        # no course named anywhere (holidays, calendar of events, sports meet): it is for every course.
        # The app shows it to everyone who follows its alert type, and phones are alerted the same way.
        stream = "general"; flags.append("stream-unsure")
    if not typ:
        typ = "circular"
    if sconf < 0.5 and "stream-unsure" not in flags: flags.append("stream-unsure")
    if len(text.strip()) < 80:
        flags.append("no-text-scanned-pdf")
    ss = sentences(text)
    summary = " ".join(ss[:2]) if ss else title
    if len(summary) > 300:
        summary = summary[:297].rsplit(" ", 1)[0] + "…"
    kw = re.compile(r"\b(shall|must|not|eligible|last date|before|after|only|required|allowed|permitted|withheld|informed|advised|directed|refund|return)\b", re.I)
    pts, seen_p = [], set()
    rest = sorted(ss[2:], key=lambda x: 0 if DATE_RE.search(x) else 1)
    for s in rest:
        if kw.search(s) or DATE_RE.search(s):
            short = s if len(s) <= 150 else s[:147].rsplit(" ", 1)[0] + "…"
            key = short.lower()[:40]
            if key not in seen_p:
                seen_p.add(key); pts.append(short)
        if len(pts) == 4:
            break
    dl = find_deadline(text, published)
    if re.search(r"last date|on or before", text, re.I) and not dl:
        flags.append("deadline-words-but-no-date")
    ref = ""
    m = re.search(r"(?:Lr\.?\s*No\.?|Ref\.?\s*No\.?|Notification\s*No\.?|F\.?\s*No\.?|Rc\.?\s*No\.?)\s*[:.]?\s*([A-Za-z0-9][A-Za-z0-9/\-\. ]{4,50}?)(?=\s{2,}|\s+Date|\s+Dt|$|\n)", text)
    if m:
        ref = m.group(1).strip(" .")
    urgent = bool(re.search(r"withheld|immediately|urgent|last date|not permitted", text, re.I)) or (dl is not None and dl - datetime.now(IST) < timedelta(hours=72))
    return {"title": title, "summary": summary, "points": pts, "stream": stream, "type": typ,
            "deadline": dl.isoformat() if dl else None, "ref": ref, "urgent": urgent, "flags": flags,
            "confidence": round(min(1.0, (sconf + (tconf or 0)) / 2 + (0.15 if dl else 0)), 2), "pages": pages}


# ----------------------------------------------------------------------------- optional LLM
def llm_draft(text, base, cfg):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not cfg.get("llm", {}).get("enabled") or not key:
        return None
    prompt = (
        "You turn an official Indian health-university notification into a short alert for students. "
        "Reply with ONLY a JSON object with keys: title (<=110 chars, plain), summary (<=240 chars), points (array of 2-4 short strings), "
        f"stream (one of {STREAMS}), type (one of {TYPES}), deadline_iso (ISO 8601 with +05:30 or null), urgent (boolean). "
        "Use ONLY facts in the text. If unsure about a date, use null.\n\nNOTIFICATION TEXT:\n" + text[:9000])
    body = json.dumps({"model": cfg["llm"]["model"], "max_tokens": 700, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.loads(r.read())
        return parse_llm_reply(out["content"][0]["text"])
    except Exception as e:
        log("LLM draft failed, using rules:", e)
        return None


def parse_llm_reply(txt):
    txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    d = json.loads(txt)
    if d.get("stream") not in STREAMS or d.get("type") not in TYPES:
        return None
    pts = [str(p)[:150] for p in d.get("points", []) if p][:4]
    return {"title": str(d["title"])[:150], "summary": str(d["summary"])[:300], "points": pts, "stream": d["stream"],
            "type": d["type"], "deadline": d.get("deadline_iso"), "urgent": bool(d.get("urgent"))}


# ----------------------------------------------------------------------------- output
def post_shape(d, item, published, pdf_name):
    dl = datetime.fromisoformat(d["deadline"]) if d.get("deadline") else None
    return {
        "title": d["title"], "summary": d["summary"], "points": d["points"], "stream": d["stream"], "type": d["type"],
        "urgent": d["urgent"], "early": 0, "deadline": int(dl.timestamp() * 1000) if dl else None,
        "ref": d.get("ref", ""), "pdf": pdf_name, "pages": d["pages"],
        "meta": {"source_url": item["url"], "published": published.isoformat(), "confidence": d["confidence"],
                 "flags": d["flags"], "drafted_by": d.get("drafted_by", "rules"),
                 "detected_at": datetime.now(IST).isoformat(timespec="seconds"), "status": "needs-review"},
    }


def write_outputs(data_dir, cfg=None):
    path = os.path.join(data_dir, "drafts.json")
    drafts = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    drafts.sort(key=lambda d: d["meta"]["detected_at"], reverse=True)
    json.dump(drafts, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open(os.path.join(HERE, "review_template.html"), encoding="utf-8") as f:
        tpl = f.read()
    blob = json.dumps(drafts, ensure_ascii=False).replace("</", "<\\/")
    with open(os.path.join(data_dir, "review.html"), "w", encoding="utf-8") as f:
        f.write(tpl.replace("/*__DATA__*/[]", blob))
    # Live inbox for the admin app: a plain .js file the admin page re-reads every few seconds (works from file://)
    inbox = (cfg or {}).get("admin_inbox")
    if inbox:
        try:
            items = []
            for d in drafts:
                m = d.get("meta", {})
                x = dict(d); x["id"] = hashlib.sha1((m.get("source_url") or m.get("uid") or d.get("title", "")).encode()).hexdigest()[:10]
                items.append(x)
            os.makedirs(os.path.dirname(os.path.abspath(inbox)), exist_ok=True)
            with open(inbox, "w", encoding="utf-8") as f:
                f.write("window.KP_INBOX = " + json.dumps({"updated": datetime.now(IST).isoformat(timespec="seconds"), "items": items}, ensure_ascii=False).replace("</", "<\\/") + ";\n")
        except Exception as e:
            log(f"could not write admin inbox {inbox}: {e}")
    return drafts


def last_modified(hdrs):
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(hdrs.get("Last-Modified")).astimezone(IST)
    except Exception:
        return None


def notify(title, body):
    if sys.platform == "darwin":
        t = title.replace('"', "'")[:90]; b = body.replace('"', "'")[:140]
        subprocess.run(["osascript", "-e", f'display notification "{b}" with title "{t}"'], check=False)


def run_once(cfg, args):
    global STAMP_IN_IST, FILE_STAMPS
    STAMP_IN_IST = bool(cfg.get("stamp_in_ist"))
    FILE_STAMPS = cfg.get("file_stamps", True)
    data_dir = os.path.join(HERE, cfg["data_dir"]) if not os.path.isabs(cfg["data_dir"]) else cfg["data_dir"]
    os.makedirs(os.path.join(data_dir, "pdfs"), exist_ok=True)
    db = open_db(data_dir)
    first_run = db.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0
    links = discover(cfg)
    log(f"found {len(links)} notice links on the page(s)")
    new = [l for l in links if not db.execute("SELECT 1 FROM seen WHERE url=?", (l["url"],)).fetchone()]
    if first_run and not args.backfill:
        keep = cfg["first_run_limit"]
        for l in new[keep:]:
            db.execute("INSERT INTO seen VALUES(?,?,?,?,?,0)", (l["url"], "", datetime.now(IST).isoformat(), l["anchor"], ""))
        db.commit()
        log(f"first run: remembering {max(0, len(new) - keep)} older notices without processing; drafting the newest {min(keep, len(new))}")
        new = new[:keep]
    new = new[: cfg["max_new_per_run"]]
    if not new:
        log("nothing new")
    path = os.path.join(data_dir, "drafts.json")
    drafts = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    for item in new:
        log("NEW ->", item["anchor"] or clean_name(item["url"]))
        if cfg.get("link_only") or item.get("link_only"):
            # sites that list notices openly but refuse to hand the PDFs to servers (RUHS): the app gets the title
            # from the link text and the student opens the original PDF on the university's website
            published = item.get("hint") or datetime.now(IST)
            d = rule_draft(item, "", 0, published, "")
            d["flags"] = [f for f in d["flags"] if f != "no-text-scanned-pdf"] + ["link-only"]
            d.setdefault("drafted_by", "rules")
            post = post_shape(d, item, published, "")
            post["meta"]["pdf_path"] = ""
            drafts.append(post)
            db.execute("INSERT INTO seen VALUES(?,?,?,?,?,1)", (item["url"], "", datetime.now(IST).isoformat(), post["title"], "")); db.commit()
            log(f"   drafted from the link text: [{post['stream']}/{post['type']}]")
            continue
        try:
            blob, hdrs = http_get(item["url"], cfg)
        except Exception as e:
            log("   download failed:", e); continue
        sha = hashlib.sha256(blob).hexdigest()
        dup = db.execute("SELECT url FROM seen WHERE sha256=?", (sha,)).fetchone()
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", clean_name(item["url"]))[:90] + ".pdf"
        pdf_path = os.path.join(data_dir, "pdfs", f"{sha[:8]}_{name}")
        if dup:
            log("   same file as an already-seen notice, skipping")
            db.execute("INSERT INTO seen VALUES(?,?,?,?,?,1)", (item["url"], sha, datetime.now(IST).isoformat(), item["anchor"], pdf_path)); db.commit()
            continue
        with open(pdf_path, "wb") as f:
            f.write(blob)
        text, pages, meta_title = pdf_text(pdf_path)
        published = file_stamp(item["url"]) or last_modified(hdrs) or item.get("hint") or datetime.now(IST)
        d = rule_draft(item, text, pages, published, meta_title)
        l = llm_draft(text, d, cfg)
        if l:
            d.update(l); d["drafted_by"] = "llm"
            if l.get("deadline") is None: d["deadline"] = d.get("deadline")
        d.setdefault("drafted_by", "rules")
        post = post_shape(d, item, published, os.path.basename(pdf_path))
        post["meta"]["pdf_path"] = os.path.relpath(pdf_path, data_dir)
        if post["type"] != "result" and re.search(r"MERIT LIST|ALLOTMENT|ALLOCATION LIST|SELECTED CANDIDATES|LIST OF (?:ELIGIBLE )?CANDIDATES", post["title"] or "", re.I):
            # merit and allotment lists are tables of candidates too: keep their names out of the summary
            post["summary"] = "List released. Open the PDF and search for your name or roll number."
            post["points"] = []
        if post["type"] == "result":
            # result PDFs are tables of students; a summary cut from them would print a student's name and marks
            post["summary"] = "Result list released. Open the PDF and search for your register number."
            post["points"] = []
        if item.get("exclude") and re.search(item["exclude"], post["title"] or "", re.I):
            # the link only said "Download", so the exclude list is checked again once the PDF gave us a real title
            log("   not for students (matched the exclude list by its title), skipping")
            db.execute("INSERT INTO seen VALUES(?,?,?,?,?,1)", (item["url"], sha, datetime.now(IST).isoformat(), post["title"], pdf_path)); db.commit()
            continue
        drafts.append(post)
        db.execute("INSERT INTO seen VALUES(?,?,?,?,?,1)", (item["url"], sha, datetime.now(IST).isoformat(), post["title"], pdf_path)); db.commit()
        log(f"   drafted: [{post['stream']}/{post['type']}] conf={d['confidence']} flags={d['flags']} deadline={d['deadline']}")
        if args.notify:
            notify("New KNRUHS notice drafted", post["title"])
    json.dump(drafts, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    out = write_outputs(data_dir, cfg)
    log(f"review page: {os.path.join(data_dir, 'review.html')}  ({len(out)} drafts total)")
    return len(new)


def main():
    ap = argparse.ArgumentParser(description="KNRUHS notice-fetching bot")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--desk", action="store_true", help="open the Channel Desk (review, post, auto-post) and keep watching")
    ap.add_argument("--notify", action="store_true", help="macOS notification for each new draft")
    args = ap.parse_args()
    cfg = load_config(args.config)
    data_dir = os.path.join(HERE, cfg["data_dir"]) if not os.path.isabs(cfg["data_dir"]) else cfg["data_dir"]
    if args.reset:
        shutil.rmtree(data_dir, ignore_errors=True); log("memory cleared"); return
    os.makedirs(data_dir, exist_ok=True)
    if args.desk:
        import desk
        desk.serve(cfg, args, data_dir, run_once, log)
        return
    if args.watch:
        log(f"watching every {cfg['interval_seconds']}s - Ctrl+C to stop")
        while True:
            try:
                run_once(cfg, args)
            except Exception as e:
                log("run failed:", e)
            time.sleep(cfg["interval_seconds"])
    else:
        run_once(cfg, args)


if __name__ == "__main__":
    main()
