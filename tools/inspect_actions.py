"""One-off check: which notices carry an admin action, and when they were last changed (read-only)."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from firebase_sync import FirebaseSync, fields_of
fs = FirebaseSync(os.environ["FIREBASE_SA"], print, os.environ.get("GITHUB_REPOSITORY"), ".")
want = (os.environ.get("NOTICE_ID") or "").strip()
if want:
    doc = fs.get(f"notices/{want}")
    if doc:
        f = fields_of(doc)
        print("DOC", want, "updateTime", doc.get("updateTime"), "| keys:", sorted(f.keys()))
        print("  action:", repr(f.get("action")), "| status:", repr(f.get("status")), "| hidden:", repr(f.get("hidden")),
              "| dismissed:", repr(f.get("dismissed")), "| state:", repr(f.get("state")))
    else:
        print("DOC", want, "not found")
for d in fs.query("notices", "action", "IN", ["publish", "hold", "dismiss"]):
    f = fields_of(d)
    print("ACTION", d["name"].rsplit("/", 1)[-1], repr(f.get("action")), d.get("updateTime"), (f.get("title") or "")[:60])
