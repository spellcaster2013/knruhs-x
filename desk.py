"""desk.py - the Channel Desk: a small web page on your own computer to review what the bot found, then hold or post it.

Run:  python3 bot.py --desk      (then open http://localhost:8800 in your browser)
It also keeps the bot checking the KNRUHS site in the background.
Nothing here is reachable from the internet: the server only listens on 127.0.0.1.
"""
import io
import json
import mimetypes
import os
import secrets
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import publish
import studio

IST = timezone(timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))


def now_iso():
    return datetime.now(IST).isoformat(timespec="seconds")


class Desk:
    def __init__(self, cfg, args, data_dir, run_once, log):
        self.cfg, self.args, self.data_dir, self.run_once, self.log = cfg, args, data_dir, run_once, log
        self.lock = threading.RLock()
        self.outbox = os.path.join(data_dir, "outbox")
        os.makedirs(self.outbox, exist_ok=True)
        self.state_path = os.path.join(data_dir, "state.json")
        self.token = secrets.token_urlsafe(16)
        self.scfg = {"brand_name": cfg["brand"]["name"], "brand_tagline": cfg["brand"]["tagline"], "handle": cfg["brand"]["handle"],
                     "no_slides": bool(cfg.get("app_only")), "university": cfg.get("university", "knruhs")}
        self.state = {"items": {}, "auto": {"on": False, "min_conf": 0.85, "delay_minutes": 5, "max_per_hour": 6, "since": 0, "targets": ["telegram", "instagram"], "log": []}}
        try:
            with open(self.state_path, encoding="utf-8") as f:
                saved = json.load(f)
            self.state["items"].update(saved.get("items", {}))
            self.state["auto"].update(saved.get("auto", {}))
            for k, v in saved.items():
                if k not in ("items", "auto"):
                    self.state[k] = v
        except Exception:
            pass
        self.last_check = None
        self.next_check = None
        self.checking = False
        self.last_error = ""
        self._cache = {}

    # ---------------------------------------------------------------- state
    def save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.state_path)

    def item_state(self, pid):
        return self.state["items"].setdefault(pid, {"status": "new", "posted": {}, "errors": {}})

    def drafts(self):
        p = os.path.join(self.data_dir, "drafts.json")
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def packs(self):
        out = []
        for d in self.drafts():
            try:
                pk = studio.build_pack(d, self.scfg, self.outbox)
                pk = dict(pk, detected_at=(d.get("meta") or {}).get("detected_at", ""))
                out.append(pk)
            except Exception as e:
                self.log("could not build post pack:", d.get("title", "?")[:50], e)
        return out

    def eligible(self, pk):
        a = self.state["auto"]
        if a.get("all"):  # post everything new, even the doubtful ones
            return bool(pk.get("title")) and bool(pk.get("source_url"))
        return (not pk.get("flags")) and (pk.get("confidence") or 0) >= a["min_conf"] and bool(pk.get("summary")) and bool(pk.get("points")) and bool(pk.get("stream")) and bool(pk.get("type"))

    def view(self):
        with self.lock:
            conf = publish.configured(self.cfg)
            items = []
            for pk in self.packs():
                st = self.item_state(pk["id"])
                items.append(dict(pk, status=st["status"], posted=st["posted"], errors=st.get("errors", {}), eligible=self.eligible(pk)))
            items.sort(key=lambda x: x.get("detected_at", ""), reverse=True)
            return {
                "brand": self.cfg["brand"], "auto": {k: v for k, v in self.state["auto"].items() if k != "log"}, "channels": conf,
                "items": items, "last_check": self.last_check, "next_check": self.next_check, "checking": self.checking, "last_error": self.last_error,
                "interval": self.cfg["interval_seconds"],
            }

    # ---------------------------------------------------------------- actions
    def post(self, pid, targets, auto=False, force=False):
        pk = next((p for p in self.packs() if p["id"] == pid), None)
        if not pk:
            raise ValueError("unknown post")
        folder = os.path.join(self.outbox, pid)
        results, errors = {}, {}
        conf = publish.configured(self.cfg)
        st = self.item_state(pid)
        social = [t for t in targets if t in ("telegram", "facebook", "instagram") and conf.get(t)]
        if social and auto and getattr(self, "precheck", None):
            # last look at the admin app: a Drop / Hold pressed while the notice was waiting stops the post here
            act = self.precheck(pid)
            if act in ("dismiss", "hold"):
                st["status"] = "dismissed" if act == "dismiss" else "held"
                self.log(f"not posting - you pressed {'Drop' if act == 'dismiss' else 'Hold'} in the admin app: {pk['title'][:60]}")
                self.save()
                return {"posted": [], "errors": {}, "stopped_by_admin": act}
        if False and social and not force and not any(t in st["posted"] for t in social):
            # Duplicate guard switched OFF (28 Nov): KNRUHS re-issues notices (corrigendum 5, addendum 1...) whose
            # captions are identical to earlier ones, and the guard blocked real notices twice. The real cause of
            # double posts (memory lost when a run was cut off) is fixed by saving right after posting.
            # duplicate guard: never post a notice that is already on our Instagram / Facebook (memory lost mid-run)
            where = publish.find_existing(self.cfg, pk)
            if where:
                for t in social:
                    st["posted"][t] = {"at": now_iso(), "by": "duplicate-guard", "found_on": where}
                self.dup_blocked = getattr(self, "dup_blocked", []) + [pk["title"][:80]]
                self.log(f"duplicate guard: already on {', '.join(where)} - NOT posting again: {pk['title'][:60]}")
                self.save()
                return {"posted": [], "errors": {}, "duplicate": where}
        for t in targets:
            st = self.item_state(pid)
            if t in st["posted"]:
                continue
            if t not in publish.TARGETS or not conf.get(t):
                errors[t] = f"{t.title()} is not connected yet (see README)"
                continue
            try:
                res = publish.TARGETS[t](self.cfg, pk, folder)
                st["posted"][t] = dict(res, at=now_iso(), by="auto" if auto else "you")
                st["errors"].pop(t, None)
                results[t] = res
                self.log(f"{'auto-' if auto else ''}posted to {t}: {pk['title'][:60]}")
            except Exception as e:
                st["errors"][t] = str(e)
                errors[t] = str(e)
                self.log(f"post to {t} failed:", e)
        self.save()
        return {"posted": list(results), "errors": errors}

    def act(self, body):
        pid, action = body.get("id"), body.get("action")
        with self.lock:
            st = self.item_state(pid)
            if action == "hold":
                st["status"] = "held"
            elif action == "dismiss":
                st["status"] = "dismissed"
            elif action == "restore":
                st["status"] = "new"
            elif action == "mark_whatsapp":
                st["posted"]["whatsapp"] = {"at": now_iso(), "by": "you"}
            elif action == "unmark_whatsapp":
                st["posted"].pop("whatsapp", None)
            elif action == "post":
                st["status"] = "new" if st["status"] == "held" else st["status"]
                self.save()
                return self.post(pid, body.get("targets") or [])
            else:
                raise ValueError("unknown action")
            self.save()
            return {"ok": True}

    def set_auto(self, body):
        with self.lock:
            a = self.state["auto"]
            if "on" in body:
                if body["on"] and not a["on"]:
                    a["since"] = time.time()
                a["on"] = bool(body["on"])
            if "min_conf" in body:
                a["min_conf"] = max(0.5, min(0.99, float(body["min_conf"])))
            if "delay_minutes" in body:
                a["delay_minutes"] = max(0, min(120, int(body["delay_minutes"])))
            if "targets" in body:
                a["targets"] = [t for t in body["targets"] if t in publish.TARGETS]
            self.save()
            self.log("auto-post", "ON" if a["on"] else "OFF")
            return {"ok": True}

    def auto_tick(self):
        a = self.state["auto"]
        if not a["on"]:
            return
        targets = [t for t in a["targets"] if publish.configured(self.cfg).get(t)]
        if not targets:
            return
        with self.lock:
            recent = [t for t in a.get("log", []) if time.time() - t < 3600]
            a["log"] = recent
            for pk in sorted(self.packs(), key=lambda p: p.get("detected_at", "")):
                st = self.item_state(pk["id"])
                if st["status"] != "new" or not self.eligible(pk):
                    continue
                try:
                    det = datetime.fromisoformat(pk["detected_at"]).timestamp()
                except Exception:
                    continue
                todo = [t for t in targets if t not in st["posted"] and det >= a.get("target_since", {}).get(t, 0)]
                if not todo or det < a.get("since", 0) or time.time() - det < a["delay_minutes"] * 60:
                    continue
                if st.get("errors"):  # do not hammer a failing post: retry only by hand
                    continue
                if len(recent) >= a["max_per_hour"]:
                    return
                recent.append(time.time())
                self.post(pk["id"], todo, auto=True)

    def check_now(self):
        if self.checking:
            return {"ok": True, "busy": True}
        with self.lock:
            self.checking = True
        try:
            self.run_once(self.cfg, self.args)
            self.last_error = ""
        except Exception as e:
            self.last_error = str(e)
            self.log("check failed:", e)
        finally:
            self.checking = False
            self.last_check = now_iso()
            self.next_check = (datetime.now(IST) + timedelta(seconds=self.cfg["interval_seconds"])).isoformat(timespec="seconds")
        return {"ok": True}

    def zip_pack(self, pid):
        folder = os.path.join(self.outbox, pid)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for fn in sorted(os.listdir(folder)):
                if fn.endswith(".jpg"):
                    z.write(os.path.join(folder, fn), fn)
            pk = json.load(open(os.path.join(folder, "pack.json"), encoding="utf-8"))
            z.writestr("caption-instagram.txt", pk["captions"]["instagram"])
            z.writestr("caption-whatsapp.txt", pk["captions"]["whatsapp"])
        return buf.getvalue()

    # ---------------------------------------------------------------- background loops
    def watcher(self):
        time.sleep(1)
        while True:
            self.check_now()
            time.sleep(self.cfg["interval_seconds"])

    def autoposter(self):
        while True:
            try:
                self.auto_tick()
            except Exception as e:
                self.log("auto-post error:", e)
            time.sleep(20)


def make_handler(desk, port):
    with open(os.path.join(HERE, "desk_template.html"), encoding="utf-8") as f:
        page = f.read()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _host_ok(self):
            return (self.headers.get("Host") or "").split(":")[0] in ("localhost", "127.0.0.1")

        def _send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden"})
            path = self.path.split("?")[0]
            try:
                if path == "/":
                    return self._send(200, page.replace("__TOKEN__", desk.token), "text/html; charset=utf-8")
                if path == "/api/state":
                    return self._send(200, desk.view())
                if path.startswith("/packs/"):
                    parts = path.split("/")
                    if len(parts) == 4 and parts[2].isalnum() and parts[3].endswith(".jpg") and "/" not in parts[3] and ".." not in parts[3]:
                        fp = os.path.join(desk.outbox, parts[2], parts[3])
                        if os.path.isfile(fp):
                            with open(fp, "rb") as f:
                                return self._send(200, f.read(), "image/jpeg", {"Cache-Control": "max-age=300"})
                if path.startswith("/api/zip/"):
                    pid = path.rsplit("/", 1)[-1]
                    if pid.isalnum() and os.path.isdir(os.path.join(desk.outbox, pid)):
                        return self._send(200, desk.zip_pack(pid), "application/zip", {"Content-Disposition": f'attachment; filename="post-{pid}.zip"'})
            except Exception as e:
                return self._send(500, {"error": str(e)})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-Desk-Token") != desk.token:
                return self._send(403, {"error": "forbidden"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/api/act":
                    return self._send(200, desk.act(body))
                if self.path == "/api/auto":
                    return self._send(200, desk.set_auto(body))
                if self.path == "/api/check":
                    threading.Thread(target=desk.check_now, daemon=True).start()
                    return self._send(200, {"ok": True})
            except Exception as e:
                return self._send(400, {"error": str(e)})
            self._send(404, {"error": "not found"})

    return H


def serve(cfg, args, data_dir, run_once, log):
    desk = Desk(cfg, args, data_dir, run_once, log)
    port = int(cfg["desk"].get("port", 8800))
    threading.Thread(target=desk.watcher, daemon=True).start()
    threading.Thread(target=desk.autoposter, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(desk, port))
    log(f"Channel Desk is running: open http://localhost:{port} in your browser. Press Ctrl+C here to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("stopped")
    return desk
