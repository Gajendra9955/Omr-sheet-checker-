"""Answer key PDF (keyTemplate) se shift ka naam aur 110 jawab nikalta hai. Streamlit ki zaroorat nahi."""
import io
import re

MAX_PDF = 5 * 1024 * 1024
ROW = re.compile(r"^\s*(\d{1,3})\s+(\d{6,})\s+(.+?)\s*$")


def parse_key_pdf(data):
    """-> {'shift': str, 'key': str (110 akshar ya ''), 'problems': [str]}"""
    out = {"shift": "", "key": "", "problems": []}
    if len(data) > MAX_PDF:
        out["problems"].append("PDF bahut badi hai (5 MB se chhoti chahiye).")
        return out
    try:
        import pypdf
        rd = pypdf.PdfReader(io.BytesIO(data))
        if rd.is_encrypted or len(rd.pages) > 20:
            out["problems"].append("PDF locked hai ya bahut lambi hai.")
            return out
        text = "\n".join((p.extract_text() or "") for p in rd.pages)
    except Exception:
        out["problems"].append("PDF padhi nahi ja saki.")
        return out
    m = re.search(r"Subject Name\s+(.+)", text)
    if m:
        out["shift"] = " ".join(m.group(1).split())
    ans = {}
    for line in text.split("\n"):
        r = ROW.match(line)
        if not r:
            continue
        q, rest = int(r.group(1)), r.group(3).upper()
        tok = rest.split()
        letter = tok[0] if tok else ""
        if "CORRECT" not in rest or letter not in ("A", "B", "C", "D"):
            out["problems"].append("Sawaal %d ki row alag tarah ki hai: %s" % (q, rest[:40]))
            continue
        if q in ans and ans[q] != letter:
            out["problems"].append("Sawaal %d do baar alag jawab ke saath hai." % q)
        ans[q] = letter
    if not ans:
        out["problems"].append("PDF mein koi answer row nahi mili. Ye key template PDF nahi lagti.")
        return out
    missing = [q for q in range(1, 111) if q not in ans]
    extra = [q for q in ans if q < 1 or q > 110]
    if missing:
        out["problems"].append("Ye sawaal nahi mile: " + ", ".join(map(str, missing[:15])))
    if extra:
        out["problems"].append("Faltu sawaal numbers: " + ", ".join(map(str, extra[:10])))
    if not missing and not extra and not out["problems"]:
        out["key"] = "".join(ans[q] for q in range(1, 111))
    return out
