import cv2
import numpy as np

# Station Master tablet-test sheet ka fixed layout (image width 482 px par)
XL = [69, 111, 153, 195, 330, 372, 414, 456]


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
