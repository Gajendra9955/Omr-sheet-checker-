import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

# Station Master tablet-test sheet ka fixed layout (image width 482 px par)
XL = [69, 111, 153, 195, 330, 372, 414, 456]


os.environ.setdefault("OMP_THREAD_LIMIT", "1")

# Screenshot chhoti/badi, pink/lal koi bhi ho: header ko kai alag (threshold, zoom) settings se padha jata hai.
# Title: kam threshold; ID aur naam: Otsu (apne aap).
TITLE_VARIANTS = [(t, k) for t in (110, 130, 150) for k in (3, 4, 6)]
AUTO_VARIANTS = [("otsu", 3), ("otsu", 4), ("otsu", 6)]
ID_CFG = "--psm 7 -c tessedit_char_whitelist=0123456789"


def _prep(img, s, box, thr, k):
    """Screenshot ke ek hisse (box, 482 px width ke hisaab se) ko OCR ke liye saaf karta hai."""
    x0, y0, x1, y1 = box
    c = img[int(y0 * s):int(y1 * s), int(x0 * s):int(x1 * s)]
    if c.size == 0:
        return None
    m = c.min(axis=2)
    f = k / s
    m = cv2.resize(m, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC if f > 1 else cv2.INTER_AREA)
    if thr == "otsu":
        _, b = cv2.threshold(m, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        _, b = cv2.threshold(m, thr, 255, cv2.THRESH_BINARY)
    return cv2.copyMakeBorder(cv2.bitwise_not(b), 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)


def _ocr_job(job):
    import pytesseract

    img, s, box, cfg, thr, k = job
    try:
        b = _prep(img, s, box, thr, k)
        if b is None:
            return ""
        return " ".join(pytesseract.image_to_string(b, config=cfg).split())
    except Exception:
        return ""


def _clean_name(t):
    t = re.sub(r"[^A-Za-z .'-]", " ", t)
    toks = t.split()
    while len(toks) > 2 and len(toks[-1]) <= 2:
        toks.pop()
    return " ".join(toks).title()


def _most_common(items):
    items = [x for x in items if x]
    if not items:
        return ""
    return Counter(items).most_common(1)[0][0]


def read_header(data):
    """Upar ki patti se naam, candidate ID aur shift ka text. Fail hone par sab khali.

    l1 / l2: title ki pehli aur doosri line, alag-alag settings se padhe hue kai text.
    """
    out = {"name": "", "cand_id": "", "shift_text": "", "shift_alt": "", "l1": [], "l2": []}
    try:
        arr = np.frombuffer(data, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return out
        s = img.shape[1] / 482.0
        jobs = []
        for thr, k in TITLE_VARIANTS:
            jobs.append((img, s, (30, 0, 335, 31), "--psm 7", thr, k))
        for thr, k in TITLE_VARIANTS:
            jobs.append((img, s, (30, 31, 150, 62), "--psm 7", thr, k))
        for thr, k in AUTO_VARIANTS:
            jobs.append((img, s, (335, 0, 425, 31), ID_CFG, thr, k))
        for thr, k in AUTO_VARIANTS:
            jobs.append((img, s, (75, 31, 425, 62), "--psm 7", thr, k))
        with ThreadPoolExecutor(max_workers=4) as ex:
            res = list(ex.map(_ocr_job, jobs))
    except Exception:
        return out
    n = len(TITLE_VARIANTS)
    m = len(AUTO_VARIANTS)
    l1 = res[:n]
    l2 = res[n:2 * n]
    ids = [re.sub(r"\D", "", t) for t in res[2 * n:2 * n + m]]
    names = [_clean_name(t) for t in res[2 * n + m:]]
    out["l1"] = l1
    out["l2"] = l2
    out["shift_text"] = (_most_common(l1) + " " + _most_common(l2)).strip()
    out["shift_alt"] = out["shift_text"]
    good_ids = [x for x in ids if 8 <= len(x) <= 10]
    out["cand_id"] = _most_common(good_ids)
    good_names = [x for x in names if len(x) >= 3]
    out["name"] = _most_common(good_names)
    return out


def _norm(t):
    t = re.sub(r"[^A-Z0-9]", "", t.upper().replace("$", "S"))
    return t.replace("0", "O").replace("I", "1").replace("L", "1")


def match_shift(texts, names, aliases=None):
    """Screenshot ke shift text (ek ya kai) ko keys ke naam ya unke alias se exact milata hai.

    texts: ek string ya strings ki list. aliases: {alias naam: shift ka naam}.
    Na mile to None.
    """
    if isinstance(texts, str):
        texts = [texts]
    norm_names = {_norm(nm): nm for nm in names}
    norm_alias = {}
    for a, nm in (aliases or {}).items():
        if _norm(a):
            norm_alias[_norm(a)] = nm
    for tx in texts:
        if not tx:
            continue
        t = _norm(tx)
        if t in norm_names:
            return norm_names[t]
        if t in norm_alias:
            return norm_alias[t]
    return None


def pick_shift(hd, names, aliases=None):
    """read_header ke kai padhe hue texts se vote karke shift chunta hai. Pakka na ho to None.

    Sirf tabhi chunta hai jab ek shift ko kam se kam 2 baar (ya sirf ek hi koshish ho to 1 baar)
    vote mile aur wo doosre se kam se kam double ho. Isse ek galat padha text galat shift nahi chunwata.
    """
    l1 = [t for t in hd.get("l1", []) if t]
    l2 = [t for t in hd.get("l2", []) if t] + [""]
    if not l1 and hd.get("shift_text"):
        l1 = [hd["shift_text"]]

    def tally(second_lines):
        v = Counter()
        for a in l1:
            for b in second_lines:
                m = match_shift([(a + " " + b).strip()], names, aliases)
                if m:
                    v[m] += 1
        return v

    votes = tally(l2)
    if not votes:
        # Aakhri akshar aksar galat padha jata hai: B->8, D->0, A->4. Sirf tab try karte hain jab kuch na mile.
        fix = {"8": "B", "0": "D", "4": "A"}
        fixed = []
        for b in l2:
            b2 = b.strip()
            if b2 and b2[-1] in fix:
                fixed.append(b2[:-1] + fix[b2[-1]])
        votes = tally(fixed)
    if not votes:
        return None
    ranked = votes.most_common()
    top, n1 = ranked[0]
    n2 = ranked[1][1] if len(ranked) > 1 else 0
    need = min(2, max(1, len(l1) * len(l2)))
    if n1 >= need and n1 >= 2 * n2:
        return top
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
