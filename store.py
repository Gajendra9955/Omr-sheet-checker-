"""Shak wali screenshots ko Supabase Storage (private bucket) mein rakhna. Sirf standard library + OpenCV."""
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

BUCKET = "omr-proofs"
TIMEOUT = 10
MAX_W = 700


def compress(img_bytes):
    """OMR image ko chhota JPEG (lagbhag 40-90 KB) banata hai. Fail ho to None."""
    try:
        import cv2
        import numpy as np
        a = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        if a is None:
            return None
        h, w = a.shape[:2]
        if w > MAX_W:
            a = cv2.resize(a, (MAX_W, max(1, int(h * MAX_W / w))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", a, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None
    except Exception:
        return None


def _req(url, key, path, method="GET", body=None, ctype=None, extra=None):
    h = {"Authorization": "Bearer " + key, "apikey": key}
    if ctype:
        h["Content-Type"] = ctype
    if extra:
        h.update(extra)
    rq = urllib.request.Request(url.rstrip("/") + "/storage/v1" + path, data=body, headers=h, method=method)
    with urllib.request.urlopen(rq, timeout=TIMEOUT) as r:
        return r.read()


def upload(url, key, img_id, jpg):
    """True agar upload ho gayi."""
    try:
        _req(url, key, "/object/%s/%s.jpg" % (BUCKET, img_id), "POST", jpg, "image/jpeg", {"x-upsert": "true"})
        return True
    except Exception:
        return False


def fetch(url, key, img_id):
    """Image ke bytes, ya None."""
    try:
        return _req(url, key, "/object/%s/%s.jpg" % (BUCKET, img_id))
    except Exception:
        return None


def delete_old(url, key, days=30):
    """days se purani images hatata hai. Returns (hata_di, mili) ya None (error)."""
    try:
        body = json.dumps({"prefix": "", "limit": 1000, "offset": 0}).encode()
        items = json.loads(_req(url, key, "/object/list/" + BUCKET, "POST", body, "application/json"))
        cut = datetime.now(timezone.utc) - timedelta(days=days)
        old = []
        for it in items:
            ts = it.get("created_at") or ""
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if t < cut and it.get("name"):
                old.append(it["name"])
        if old:
            _req(url, key, "/object/" + BUCKET, "DELETE", json.dumps({"prefixes": old}).encode(), "application/json")
        return len(old), len(items)
    except Exception:
        return None
