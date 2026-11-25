import re

import cv2
import numpy as np

# Station Master tablet-test sheet ka fixed layout (image width 482 px par)
XL = [69, 111, 153, 195, 330, 372, 414, 456]


def _ocr(img, s, box, cfg):
    """Screenshot ke ek hisse (box, 482 px width ke hisaab se) ka text padhta hai."""
    import pytesseract

    x0, y0, x1, y1 = box
    c = img[int(y0 * s):int(y1 * s), int(x0 * s):int(x1 * s)]
    if c.size == 0:
        return ""
    m = cv2.resize(c.min(axis=2), None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    _, b = cv2.threshold(m, 170, 255, cv2.THRESH_BINARY)
    b = cv2.copyMakeBorder(cv2.bitwise_not(b), 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return pytesseract.image_to_string(b, config=cfg).strip()


def read_header(data):
    """Upar ki pink patti se naam, candidate ID aur shift ka text. Fail hone par sab khali."""
    out = {"name": "", "cand_id": "", "shift_text": ""}
    try:
        arr = np.frombuffer(data, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return out
        s = img.shape[1] / 482.0
        title = _ocr(img, s, (36, 0, 335, 32), "--psm 7")
        cid = _ocr(img, s, (335, 0, 422, 32), "--psm 7 -c tessedit_char_whitelist=0123456789")
        letter = _ocr(img, s, (36, 30, 62, 62), "--psm 10 -c tessedit_char_whitelist=ABCDEFGH")
        name = _ocr(img, s, (70, 30, 422, 62), "--psm 7")
    except Exception:
        return out
    name = re.sub(r"[^A-Za-z .'-]", "", name)
    name = " ".join(name.split()).title()
    out["name"] = name if len(name) >= 3 else ""
    out["cand_id"] = cid if len(cid) >= 6 else ""
    out["shift_text"] = (title + " " + letter).strip()
    return out


def _norm(t):
    t = re.sub(r"[^A-Z0-9]", "", t.upper().replace("$", "S"))
    return t.replace("0", "O").replace("I", "1").replace("L", "1")


def match_shift(text, names):
    """Screenshot ke shift text ko keys ke naam se exact milata hai. Na mile to None."""
    if not text:
        return None
    t = _norm(text)
    for nm in names:
        if _norm(nm) == t:
            return nm
    return None


def scan(data):
    """Image bytes -> ({question: 'A'/'B'/'C'/'D'}, filled bubble count)."""
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None, 0
    m = img.max(axis=2)
    s = m.shape[1] / 482.0
    n, _, stats, cen = cv2.connectedComponentsWithStats((m < 90).astype(np.uint8))
    blobs = []
    for i in range(1, n):
        w = stats[i, 2] / s
        h = stats[i, 3] / s
        cx = cen[i][0] / s
        cy = cen[i][1] / s
        if 18 < w < 36 and 18 < h < 36 and 115 < cy < 2440:
            blobs.append((cx, cy))
    res = {}
    for cx, cy in blobs:
        side = int(cx > 240)
        q = side * 55 + int(round((cy - 142) / 42.17)) + 1
        opt = "ABCD"[int(np.argmin([abs(cx - x) for x in XL[4 * side:4 * side + 4]]))]
        if 1 <= q <= 110:
            res[q] = opt
    return res, len(blobs)


def evaluate(res, key, pos=1.0, neg=1.0 / 3):
    """res = scan() ka output, key = 110 letters ('*' = dropped question, sabko marks)."""
    rows = []
    ok = wr = na = 0
    for q in range(1, 111):
        k = key[q - 1]
        a = res.get(q, "-")
        if k == "*":
            ok += 1
            status = "Dropped (full marks)"
        elif a == "-":
            na += 1
            status = "Unattempted"
        elif a == k:
            ok += 1
            status = "Correct"
        else:
            wr += 1
            status = "Wrong"
        rows.append({"Q": q, "Your Answer": a, "Correct Answer": k, "Status": status})
    score = round(ok * pos - wr * neg, 2)
    return {
        "correct": ok,
        "wrong": wr,
        "unattempted": na,
        "score": score,
        "total": round(110 * pos, 2),
        "rows": rows,
    }
