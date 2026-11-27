"""Rank, dashboard aur admin ke hisaab-kitab. Streamlit ya Google ki zaroorat nahi, isliye alag se test ho jata hai."""
import re
import time

HEADER = [
    "Date/Time", "Name", "Candidate ID", "Shift", "Score", "Correct", "Wrong",
    "Unattempted", "Image ID", "Category", "Source",
]
CATEGORIES = ["UR", "SC", "ST"]
MIN_MANUAL_SCORE = 40      # dropdown se chuni shift ka result tabhi save hota hai jab score isse upar ho
ABOVE, BELOW = 50, 20      # dashboard mein aapke upar aur neeche kitne log dikhein


def exam_of(shift):
    """'STATION MASTER 13 Oct 2026 S1 A' -> 'STATION MASTER'. Tarikh se pehle ka hissa = exam (post)."""
    s = " ".join(str(shift).split())
    m = re.search(r"\s\d{1,2}\s[A-Za-z]{3,9}\s\d{4}", s)
    base = s[:m.start()] if m else s
    return base.strip().upper()


def mask_id(cid):
    """122450689 -> 122****89. ID na ho ya bahut chhoti ho to '----'."""
    d = "".join(ch for ch in str(cid) if ch.isdigit())
    if len(d) >= 6:
        return d[:3] + "****" + d[-2:]
    return "----"


def body_rows(rows):
    """Header wali pehli row hatata hai. Header na ho to saari rows data mani jati hain."""
    if rows and rows[0] and str(rows[0][0]).strip() == HEADER[0]:
        return rows[1:]
    return rows


def make_record(name, cid, shift, score, cat, img, src, i=10 ** 9):
    cid = "".join(ch for ch in str(cid) if ch.isdigit())
    key = cid if cid else ("img:" + img if img else "row:" + str(i))
    return {
        "i": i, "ts": "", "name": name, "cid": cid, "shift": str(shift).strip(),
        "exam": exam_of(shift), "score": float(score), "cat": (cat or "").strip().upper(),
        "img": img or "", "src": src or "auto", "key": key,
    }


def parse_rows(rows):
    """Sheet ki rows -> records ki list. Kharab rows chhod di jati hain."""
    recs = []
    for i, row in enumerate(rows):
        row = [str(c) for c in row]
        if len(row) < 5:
            continue
        try:
            score = float(row[4].replace(",", "."))
        except ValueError:
            continue
        if not row[3].strip():
            continue
        rec = make_record(
            row[1], row[2], row[3], score,
            row[9] if len(row) > 9 else "",
            row[8].strip() if len(row) > 8 else "",
            (row[10].strip().lower() if len(row) > 10 else "") or "auto",
            i,
        )
        rec["ts"] = row[0]
        recs.append(rec)
    return recs


def _pref(r):
    # screenshot se pakki hui shift ki entry ko manual wali par tarjeeh, phir sabse nayi
    return (0 if r["src"] == "manual" else 1, r["i"])


def dedupe(recs, level):
    """Ek ID ek hi baar (level = 'shift' ya 'exam')."""
    best = {}
    for r in recs:
        k = (r[level], r["key"])
        cur = best.get(k)
        if cur is None or _pref(r) > _pref(cur):
            best[k] = r
    return list(best.values())


def build_groups(recs):
    out = {"shift": {}, "exam": {}}
    for level in ("shift", "exam"):
        for r in dedupe(recs, level):
            out[level].setdefault(r[level], []).append(r)
    return out


def entries_for(groups, level, own):
    """Own ke group ki entries, own ko shaamil karke (Sheet mein abhi na bhi ho to bhi)."""
    base = groups.get(level, {}).get(own[level], [])
    return [r for r in base if r["key"] != own["key"]] + [own]


def only_cat(entries, cat):
    return [r for r in entries if cat and r["cat"] == cat]


def rank_of(entries, own):
    """(rank, kul) . Barabar score par barabar rank."""
    better = sum(1 for r in entries if r["score"] > own["score"] + 1e-9)
    return better + 1, len(entries)


def ranks_for(groups, own):
    """Chaar rank: shift/exam ke andar overall aur category ke hisaab se."""
    out = {}
    for level in ("shift", "exam"):
        ents = entries_for(groups, level, own)
        out[level] = rank_of(ents, own)
        out[level + "_cat"] = rank_of(only_cat(ents, own["cat"]), own) if own["cat"] else None
    return out


def table(entries, own_key):
    """Score ke hisaab se sorted rows: rank, masked ID, marks, kya ye own hai."""
    es = sorted(entries, key=lambda r: (-r["score"], r["i"]))
    out = []
    prev = None
    rank = 0
    for idx, r in enumerate(es):
        if r["score"] != prev:
            rank = idx + 1
            prev = r["score"]
        out.append({"rank": rank, "id": mask_id(r["cid"]), "marks": r["score"], "own": r["key"] == own_key})
    return out


def window(rows, above=ABOVE, below=BELOW):
    """Own ke upar 'above' aur neeche 'below' log. Own na mile to top 'above'."""
    idx = next((n for n, r in enumerate(rows) if r["own"]), None)
    if idx is None:
        return rows[:above], 0, 0
    lo = max(0, idx - above)
    hi = min(len(rows), idx + below + 1)
    return rows[lo:hi], idx - lo, len(rows) - hi


def pick_by_id(recs, cid):
    """Candidate ID se uski sabse bharosemand entry (screenshot se pakki shift, phir sabse nayi)."""
    cid = "".join(ch for ch in str(cid) if ch.isdigit())
    if len(cid) < 6:
        return None
    mine = [r for r in recs if r["cid"] == cid]
    if not mine:
        return None
    return max(mine, key=_pref)


def admin_summary(recs):
    """Har shift ke liye ginti, average, topper, sabse kam aur category ke hisaab se ginti."""
    rows = []
    for shift, lst in sorted(build_groups(recs)["shift"].items()):
        sc = [r["score"] for r in lst]
        rows.append({
            "Exam": exam_of(shift), "Shift": shift, "Candidates": len(lst),
            "Average": round(sum(sc) / len(sc), 2), "Topper": max(sc), "Lowest": min(sc),
            "UR": sum(1 for r in lst if r["cat"] == "UR"),
            "SC": sum(1 for r in lst if r["cat"] == "SC"),
            "ST": sum(1 for r in lst if r["cat"] == "ST"),
            "Category nahi": sum(1 for r in lst if r["cat"] not in CATEGORIES),
        })
    return rows


def with_retry(fn, tries=3, delay=1.0, sleep=time.sleep):
    """Google Sheet ki limit (429) ya server ki gadbad par 3 baar tak dobara koshish."""
    for n in range(tries):
        try:
            return fn()
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            retryable = code in (429, 500, 502, 503, 504) or isinstance(e, OSError)
            if n == tries - 1 or not retryable:
                raise
            sleep(delay)
            delay *= 2
