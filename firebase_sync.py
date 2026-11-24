"""firebase_sync.py - connects the notice bot to the KNRUHS Updates apps.

Each run (called from cloud.py):
  1. read_controls(): reads the admin's switches (auto-publish, pause) and the admin's decisions on notices
     (publish / hold / drop / edits) from Firestore.
  2. push(): writes every notice to Firestore (for the admin app + console), sends Firebase push alerts to the
     user app for newly published notices, and writes the public feed (site/feed.json + slide images) that the
     user app reads.

Secret: FIREBASE_SA (the service-account JSON from Firebase > Project settings > Service accounts). Never printed.
"""
import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
PROJECT = "knruhs-updates"
FS = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents"
FCM = f"https://fcm.googleapis.com/v1/projects/{PROJECT}/messages:send"
STREAM_LABELS = {"mbbs": "MBBS", "bds": "BDS", "mdms": "MD / MS", "mds": "MDS", "ss": "DM / MCh", "ayush": "BHMS / Ayush",
                 "nursing": "Nursing", "allied": "Allied Health", "jobs": "Jobs"}
TYPE_EMOJI = {"counselling": "🎓", "result": "📢", "exam": "📝", "recruitment": "💼", "admission": "🏫", "circular": "📌"}
UNI_LABELS = {"knruhs": "KNRUHS", "ntruhs": "Dr. NTR UHS"}


def merge_feeds(site, now):
    """The app reads one feed: site/feed.json = every university's feed-<uni>.json, newest first."""
    allx = []
    for name in sorted(os.listdir(site)):
        if name.startswith("feed-") and name.endswith(".json"):
            try:
                with open(os.path.join(site, name), encoding="utf-8") as f:
                    allx += json.load(f).get("notices", [])
            except Exception:
                pass
    allx.sort(key=lambda n: n.get("published_at") or n.get("detected_at") or "", reverse=True)
    with open(os.path.join(site, "feed.json"), "w", encoding="utf-8") as f:
        json.dump({"updated_at": now, "notices": allx}, f, ensure_ascii=False, separators=(",", ":"))


# ----------------------------------------------------------------------------- Firestore value encoding
def enc(v):
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [enc(x) for x in v]}}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {k: enc(x) for k, x in v.items()}}}
    return {"stringValue": str(v)}


def dec(v):
    if not isinstance(v, dict):
        return v
    if "nullValue" in v:
        return None
    for k in ("booleanValue", "stringValue", "doubleValue", "timestampValue"):
        if k in v:
            return v[k]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "arrayValue" in v:
        return [dec(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v:
        return {k: dec(x) for k, x in v["mapValue"].get("fields", {}).items()}
    return None


def fields_of(doc):
    return {k: dec(v) for k, v in (doc.get("fields") or {}).items()}



KNOWN_STREAMS = {"mbbs", "bds", "mdms", "mds", "ss", "nursing", "ayush", "allied", "jobs"}


def push_condition(stream, ntype, university="knruhs"):
    """Phones get a notice only if they follow its university AND its course AND its alert type.
    The app subscribes to u_<university>, s_<course> and t_<type> for each one it follows
    (every course / every type when the list is empty). A notice with no known course goes
    by its university and type alone."""
    u = f"'u_{university or 'knruhs'}' in topics"
    t = f"'t_{ntype or 'circular'}' in topics"
    if stream in KNOWN_STREAMS:
        return f"{u} && 's_{stream}' in topics && {t}"
    return f"{u} && {t}"

class FirebaseSync:
    def __init__(self, sa_json, log, repo, here, university="knruhs", slides=True):
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.s = AuthorizedSession(creds)
        self.log = log
        self.repo = repo or "spellcaster2013/knruhs-x"
        self.here = here
        self.site = os.path.join(here, "site")
        self.settings = {"auto_publish": True, "paused": False}
        self.actions = {}   # notice id -> "publish" | "hold" | "dismiss"
        self.edits = {}     # notice id -> {"title":..., "summary":..., "points": [...]}
        self.manual = {}    # notice id -> fields of a notice written by hand in the admin console
        self.ok = True
        self.uni = university or "knruhs"   # which university this run is for; every notice carries it
        self.slides = slides                # app-only universities have no slide pictures

    # ------------------------------------------------------------------ low level
    def get(self, path, params=None):
        r = self.s.get(f"{FS}/{path}", params=params or {}, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def patch(self, path, data, mask=None):
        params = [("updateMask.fieldPaths", f) for f in (mask or data.keys())]
        r = self.s.patch(f"{FS}/{path}", params=params, json={"fields": {k: enc(v) for k, v in data.items()}}, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Firestore write {path} failed: HTTP {r.status_code} {r.text[:200]}")

    def list_all(self, coll):
        out, token = [], None
        while True:
            params = {"pageSize": 300}
            if token:
                params["pageToken"] = token
            js = self.get(coll, params) or {}
            out += js.get("documents", [])
            token = js.get("nextPageToken")
            if not token:
                return out

    # ------------------------------------------------------------------ 1. read what the admin decided
    def read_controls(self):
        try:
            doc = self.get("config/settings")
            if doc:
                self.settings.update(fields_of(doc))
            else:
                self.patch("config/settings", self.settings)
            for doc in self.list_all("notices"):
                f = fields_of(doc)
                nid = doc["name"].rsplit("/", 1)[-1]
                if f.get("action") in ("publish", "hold", "dismiss"):
                    self.actions[nid] = f["action"]
                if isinstance(f.get("edits"), dict) and any(f["edits"].values()):
                    self.edits[nid] = f["edits"]
                if f.get("manual") is True and f.get("action") == "publish":
                    self.manual[nid] = f
            self.log("firebase: auto-publish", "ON" if self.settings.get("auto_publish") else "OFF",
                     "| paused" if self.settings.get("paused") else "", "| admin actions:", len(self.actions), "| edits:", len(self.edits))
        except Exception as e:
            self.ok = False
            self.log("firebase: could not read controls:", e)

    def import_manual(self, data_dir, pack_id_fn):
        """Notices typed by hand in the admin console (Publish screen) become normal drafts, so they get
        slides and captions and go to every channel exactly like a notice the bot found itself."""
        if not self.manual:
            return
        from datetime import datetime, timezone, timedelta
        path = os.path.join(data_dir, "drafts.json")
        try:
            drafts = json.load(open(path, encoding="utf-8"))
        except Exception:
            drafts = []
        have = {pack_id_fn(d) for d in drafts}
        now = datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(timespec="seconds")
        added = 0
        for nid, f in self.manual.items():
            if nid in have or not str(f.get("title") or "").strip():
                continue
            if (f.get("university") or "knruhs") != self.uni:
                continue
            d = {"title": str(f["title"]).strip(), "summary": str(f.get("summary") or "").strip(),
                 "points": [str(x) for x in (f.get("points") or []) if x], "stream": f.get("stream") or "mbbs", "type": f.get("type") or "circular",
                 "urgent": bool(f.get("urgent")), "early": 0, "deadline": int(f["deadline"]) if f.get("deadline") else None,
                 "ref": str(f.get("ref") or ""), "pdf": "", "pages": 0,
                 "meta": {"source_url": str(f.get("source_url") or ""), "uid": str(f.get("uid") or ""), "published": now, "confidence": 1.0, "flags": [],
                          "drafted_by": "admin", "detected_at": now, "status": "needs-review", "pdf_path": ""}}
            if pack_id_fn(d) != nid:
                self.log("firebase: manual notice", nid, "skipped (id does not match its content)")
                continue
            drafts.insert(0, d); have.add(nid); added += 1
        if added:
            json.dump(drafts, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            self.log("firebase: imported", added, "notice(s) written in the admin console")

    def apply_edits(self, data_dir, pack_id_fn):
        """Admin edits change the draft itself, so slides and captions are rebuilt with the corrected text."""
        if not self.edits:
            return
        path = os.path.join(data_dir, "drafts.json")
        try:
            drafts = json.load(open(path, encoding="utf-8"))
        except Exception:
            return
        changed = False
        for d in drafts:
            e = self.edits.get(pack_id_fn(d))
            if not e:
                continue
            for k in ("title", "summary"):
                if e.get(k) and d.get(k) != e[k]:
                    d[k], changed = e[k], True
            if isinstance(e.get("points"), list) and e["points"] and d.get("points") != e["points"]:
                d["points"], changed = [p for p in e["points"] if p], True
        if changed:
            json.dump(drafts, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            self.log("firebase: applied admin edits")

    # ------------------------------------------------------------------ 2. write notices, alerts, public feed
    def _status(self, st):
        if st.get("posted"):
            return "published"
        return {"held": "held", "dismissed": "dismissed"}.get(st.get("status"), "pending")

    def _export_slides(self, pk, folder):
        dst = os.path.join(self.site, "slides", pk["id"])
        if not os.path.isdir(dst) or len(os.listdir(dst)) < len(pk["slides"]):
            os.makedirs(dst, exist_ok=True)
            for name in pk["slides"]:
                src = os.path.join(folder, name)
                if os.path.exists(src):
                    shutil.copyfile(src, os.path.join(dst, name))
        base = f"https://raw.githubusercontent.com/{self.repo}/main/site/slides/{pk['id']}"
        return [f"{base}/{n}" for n in sorted(os.listdir(dst))] if os.path.isdir(dst) else []

    def _send_push(self, pk, topic_condition, title, body, kind="notice"):
        msg = {"message": {
            "condition": topic_condition,
            "notification": {"title": title[:120], "body": body[:240]},
            "data": {"id": pk["id"], "kind": kind},
            "android": {"priority": "high", "notification": {"channel_id": "notices" if kind == "notice" else "admin", "tag": pk["id"]}},
        }}
        r = self.s.post(FCM, json=msg, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"push failed: HTTP {r.status_code} {r.text[:200]}")

    def push(self, desk, outbox, bot_info):
        if not self.ok:
            return
        packs = sorted(desk.packs(), key=lambda p: p.get("detected_at", ""), reverse=True)
        feed, now = [], datetime.now(IST).isoformat(timespec="seconds")
        first_sync = not desk.state.get("fb_init")
        desk.state["fb_init"] = desk.state.get("fb_init") or now
        written = 0
        for pk in packs[:300]:
            st = desk.item_state(pk["id"])
            status = self._status(st)
            slides = self._export_slides(pk, os.path.join(outbox, pk["id"])) if status == "published" and self.slides else []
            first_post = min((v.get("at", "") for v in st.get("posted", {}).values()), default="")
            doc = {
                "id": pk["id"], "title": pk["title"], "summary": pk.get("summary") or "", "points": pk.get("points") or [],
                "stream": pk.get("stream") or "", "type": pk.get("type") or "", "urgent": bool(pk.get("urgent")),
                "deadline": pk.get("deadline") or 0, "source_url": pk.get("source_url") or "", "published_day": pk.get("published_day") or "",
                "detected_at": pk.get("detected_at") or "", "published_at": first_post, "status": status,
                "posted": {k: v.get("at", "") for k, v in st.get("posted", {}).items()},
                "errors": st.get("errors") or {}, "flags": pk.get("flags") or [], "confidence": float(pk.get("confidence") or 0),
                "slides": slides, "updated_at": now, "action": None, "university": self.uni,
            }
            sig = json.dumps(doc, sort_keys=True, default=str).replace(now, "")
            if st.get("fs_sig") != sig or pk["id"] in self.actions:
                try:
                    self.patch(f"notices/{pk['id']}", doc)
                    st["fs_sig"] = sig
                    written += 1
                except Exception as e:
                    self.log("firebase:", e)
            if status == "published":
                feed.append({k: doc[k] for k in ("id", "title", "summary", "points", "stream", "type", "urgent", "deadline", "source_url",
                                                 "published_day", "detected_at", "published_at", "slides", "university")})
                if not st.get("pushed"):
                    if first_sync:
                        st["pushed"] = "before-app"
                    elif first_post and first_post < (datetime.now(IST) - timedelta(hours=6)).isoformat():
                        st["pushed"] = "skipped-old"  # do not alert phones about old notices
                    else:
                        try:
                            label = STREAM_LABELS.get(pk.get("stream"), UNI_LABELS.get(self.uni, "KNRUHS"))
                            if self.uni != "knruhs":
                                label = f"{UNI_LABELS.get(self.uni, self.uni.upper())} · {label}"
                            emoji = TYPE_EMOJI.get(pk.get("type"), "📌")
                            cond = push_condition(pk.get("stream"), pk.get("type"), self.uni)
                            self._send_push(pk, cond, f"{emoji} {label} update", pk["title"])
                            st["pushed"] = now
                            self.log("firebase: alert sent:", pk["title"][:60])
                        except Exception as e:
                            self.log("firebase:", e)
            elif status == "pending" and not st.get("admin_alerted") and not first_sync:
                try:
                    self._send_push(pk, "'admin' in topics", "New notice waiting for you", pk["title"], kind="admin")
                except Exception as e:
                    self.log("firebase:", e)
                st["admin_alerted"] = True
        self.log("firebase: notices written:", written, "| public feed:", len(feed))
        os.makedirs(self.site, exist_ok=True)
        with open(os.path.join(self.site, f"feed-{self.uni}.json"), "w", encoding="utf-8") as f:
            json.dump({"updated_at": now, "notices": feed}, f, ensure_ascii=False, separators=(",", ":"))
        merge_feeds(self.site, now)
        try:
            self.patch("status/bot" if self.uni == "knruhs" else f"status/bot_{self.uni}", dict(bot_info, last_run=now))
        except Exception as e:
            self.log("firebase:", e)
        desk.save()
