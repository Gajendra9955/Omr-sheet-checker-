# =====================================================================
# OMR Result Checker created by Gajendra verma
#   - OMR screenshot ya ZIP upload -> score, rank (same exam ke beech), scorecard download
#   - Google Sheet mein result save, dashboard mein sirf masked ID aur marks
#   - Admin panel (?admin=1): answer key, alias, dashboard, data download
#
# FILES (sab ek hi folder mein): app.py, omr.py, board.py, keys.json,
#                                requirements.txt, packages.txt, README.md
#
# SECRETS (Streamlit Cloud: App > Settings > Secrets / Hugging Face: Variables and secrets):
#   ADMIN_PASSWORD           = "aapka-password"
#   SHEET_URL                = "https://docs.google.com/spreadsheets/d/XXXX/edit"
#   GCP_SERVICE_ACCOUNT_JSON = '''{ ...JSON key ka poora text... }'''
# Google Sheet ko service account ke email (JSON mein "client_email") ke saath
# Editor access se Share karna zaroori hai.
# =====================================================================
import os
import io
import json
import hmac
import hashlib
import textwrap
from datetime import datetime, timezone, timedelta

import pandas as pd
import streamlit as st

try:
    import gspread
except ImportError:
    gspread = None

import board
import keyparse
import store
from omr import scan, evaluate, read_header, pick_shift, validate_omr, images_from_zip, is_zip, MAX_FILE

st.set_page_config(page_title="OMR Result Checker created by Gajendra Verma", page_icon="📝", layout="centered")

BASE = os.path.dirname(os.path.abspath(__file__))
KEYFILE = os.path.join(BASE, "keys.json")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
IST = timezone(timedelta(hours=5, minutes=30))
PLACEHOLDER_CAT = "-- Category chunein --"
PLACEHOLDER_SHIFT = "-- Shift chunein --"
NOTICE = ("दिखाई गई रैंक और रिजल्ट अनुमानित है यह कोई आधिकारिक नहीं है, दिखाई गई रैंक इस ऐप में चेक किए गए कैंडिडेट के बीच में है")


# ------------------------------------------------------------ secrets, Sheet
def get_secret(name):
    v = os.environ.get(name, "")
    if v:
        return v
    try:
        return str(st.secrets[name])
    except Exception:
        return ""


@st.cache_resource
def get_ws():
    """Google Sheet ka pehla tab. Secrets set nahi hain to None."""
    raw = get_secret("GCP_SERVICE_ACCOUNT_JSON")
    url = get_secret("SHEET_URL")
    if gspread is None or not raw or not url:
        return None
    gc = gspread.service_account_from_dict(json.loads(raw))
    return gc.open_by_url(url).sheet1


@st.cache_resource
def counters():
    """Admin ke liye ginti (app restart hone par zero ho jati hai)."""
    return {"uploads": 0, "invalid": 0, "saved": 0, "failed_saves": 0, "manual_skipped": 0, "unverified": 0, "stored": 0}


def once(tag):
    """Har screenshot ke liye ginti sirf ek baar (page dobara chalne par nahi)."""
    seen = st.session_state.setdefault("seen", set())
    if tag in seen:
        return False
    seen.add(tag)
    return True


@st.cache_resource(ttl=60, show_spinner=False)
def sheet_data():
    """Saare candidates ki Sheet, 1 minute ke liye yaad (sab candidates ke liye ek hi baar padhi jati hai)."""
    ws = get_ws()
    if ws is None:
        raise RuntimeError("Sheet connect nahi hai")
    rows = board.with_retry(lambda: ws.get_all_values())
    recs = board.parse_rows(board.body_rows(rows))
    return {"rows": rows, "recs": recs, "groups": board.build_groups(recs)}


def get_data():
    try:
        return sheet_data()
    except Exception:
        return None


def save_result(name, cand_id, shift, r, img_id, cat, src):
    """Returns: ok / dup / nocfg / error"""
    try:
        ws = get_ws()
        if ws is None:
            return "nocfg"
        data = get_data()
        rows = data["rows"] if data is not None else board.with_retry(lambda: ws.get_all_values())
        if not rows:
            board.with_retry(lambda: ws.append_row(board.HEADER, value_input_option="RAW"))
        elif str(rows[0][0]).strip() == board.HEADER[0] and len(rows[0]) < len(board.HEADER):
            first = chr(65 + len(rows[0]))
            last = chr(64 + len(board.HEADER))
            extra = board.HEADER[len(rows[0]):]
            board.with_retry(lambda: ws.update(values=[extra], range_name=first + "1:" + last + "1"))
        for row in board.body_rows(rows):
            if len(row) > 3 and row[3].strip() == shift:
                rid = "".join(ch for ch in row[2] if ch.isdigit())
                if cand_id and rid == cand_id:
                    return "dup"
                if len(row) > 8 and row[8].strip() == img_id:
                    return "dup"
        now = datetime.now(IST).strftime("%d-%m-%Y %H:%M:%S")
        line = [now, name, cand_id, shift, r["score"], r["correct"], r["wrong"], r["unattempted"], img_id, cat, src]
        board.with_retry(lambda: ws.append_row(line, value_input_option="RAW"))
        return "ok"
    except Exception:
        return "error"


# ------------------------------------------------------------ reports (score galat lage to)
REPORT_HEADER = ["Date/Time", "Image ID", "Candidate ID", "Name", "Shift", "Score", "Reason", "Note", "Status"]
REPORT_REASONS = [
    "Answer key galat lag rahi hai",
    "Mere jawab galat padhe gaye",
    "Shift galat pehchani gayi",
    "Kuch aur",
]


def reports_sheet(create=False):
    ws = get_ws()
    if ws is None:
        return None
    book = ws.spreadsheet
    try:
        return book.worksheet("Reports")
    except gspread.WorksheetNotFound:
        if not create:
            return None
        rws = book.add_worksheet(title="Reports", rows=200, cols=len(REPORT_HEADER))
        rws.append_row(REPORT_HEADER, value_input_option="RAW")
        return rws


def save_report(img_id, cand_id, name, shift, score, reason, note):
    """Returns: ok / dup / nocfg / error"""
    try:
        if get_ws() is None:
            return "nocfg"
        rws = reports_sheet(create=True)
        if rws is None:
            return "nocfg"
        rows = board.with_retry(lambda: rws.get_all_values())
        for row in rows[1:]:
            if len(row) > 1 and row[1].strip() == img_id:
                return "dup"
        now = datetime.now(IST).strftime("%d-%m-%Y %H:%M:%S")
        line = [now, img_id, cand_id, name, shift, score, reason, note, "new"]
        board.with_retry(lambda: rws.append_row(line, value_input_option="RAW"))
        return "ok"
    except Exception:
        return "error"


def report_box(name, cand_id, shift, score, img_id):
    with st.expander("Score galat lag raha hai? Report karein"):
        mine = st.session_state.setdefault("reported", {})
        if img_id in mine:
            st.info("Aapki report pehle hi bheji ja chuki hai. Admin ise dekhega.")
            return
        reason = st.selectbox("Kya dikkat hai?", REPORT_REASONS, key="rep_reason_" + img_id)
        note = st.text_area(
            "Kaunsa sawaal aur kya galat hai? (jaise: Q27 ka jawab C hona chahiye)",
            max_chars=300, key="rep_note_" + img_id,
        ).strip()
        if st.button("Report bhejein", key="rep_btn_" + img_id):
            res = save_report(img_id, cand_id, name, shift, score, reason, note)
            mine[img_id] = res
            if res in ("ok", "dup"):
                st.success("Report bhej di gayi. Dhanyavaad.")
            elif res == "nocfg":
                st.warning("Abhi report nahi bheji ja sakti. Baad mein try karein.")
            else:
                st.error("Report save nahi ho paayi. Thodi der baad dobara try karein.")


# ------------------------------------------------------------ answer keys
def load_file_keys():
    """keys.json (GitHub wali default keys)."""
    try:
        with open(KEYFILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_file_keys(d):
    with open(KEYFILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


def keys_sheet(create=False):
    """Google Sheet ka 'Keys' tab. Nahi hai aur create=False ho to None."""
    ws = get_ws()
    if ws is None:
        return None
    book = ws.spreadsheet
    try:
        return book.worksheet("Keys")
    except gspread.WorksheetNotFound:
        if not create:
            return None
        kws = book.add_worksheet(title="Keys", rows=100, cols=5)
        kws.append_row(
            ["Shift", "Key", "Marks per correct", "Negative per wrong", "Aliases"],
            value_input_option="RAW",
        )
        return kws


def split_aliases(txt):
    """'a, b; c' -> ['a', 'b', 'c']"""
    parts = txt.replace(";", ",").replace("|", ",").replace("\n", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


@st.cache_data(ttl=60, show_spinner=False)
def sheet_keys():
    """Sheet ke 'Keys' tab ki saari keys (1 minute ke liye yaad rakhi jati hain)."""
    out = {}
    try:
        kws = keys_sheet()
        if kws is None:
            return out
        rows_k = board.with_retry(lambda: kws.get_all_values())
        if rows_k and rows_k[0] and rows_k[0][0].strip().lower() == "shift":
            rows_k = rows_k[1:]
        for row in rows_k:
            if len(row) < 2 or not row[0].strip() or not row[1].strip():
                continue
            kk = "".join(row[1].upper().split())
            if len(kk) != 110 or any(ch not in "ABCD*" for ch in kk):
                continue
            try:
                pos = float(row[2].replace(",", ".")) if len(row) > 2 and row[2].strip() else 1.0
                neg = float(row[3].replace(",", ".")) if len(row) > 3 and row[3].strip() else 1 / 3
            except ValueError:
                continue
            al = split_aliases(row[4]) if len(row) > 4 else []
            out[row[0].strip()] = {"key": kk, "pos": pos, "neg": neg, "aliases": al}
    except Exception:
        return {}
    return out


def load_keys():
    """keys.json ki default keys + Sheet ki keys (same naam par Sheet wali chalti hai)."""
    d = dict(load_file_keys())
    d.update(sheet_keys())
    return d


def alias_map(keys):
    """{alias naam: shift ka naam}"""
    m = {}
    for nm, cfg in keys.items():
        for a in cfg.get("aliases", []) or []:
            m[a] = nm
    return m


def save_key_sheet(name, k, pos, neg, aliases=""):
    """Key ko Sheet ke 'Keys' tab mein save karta hai. True = ho gaya."""
    try:
        kws = keys_sheet(create=True)
        if kws is None:
            return False
        if kws.col_count < 5:
            kws.resize(cols=5)
            kws.update(values=[["Aliases"]], range_name="E1")
        names = board.with_retry(lambda: kws.col_values(1))
        row = [name, k, pos, neg, aliases]
        if name in names:
            i = names.index(name) + 1
            board.with_retry(lambda: kws.update(values=[row], range_name="A" + str(i) + ":E" + str(i)))
        else:
            board.with_retry(lambda: kws.append_row(row, value_input_option="RAW"))
        sheet_keys.clear()
        return True
    except Exception:
        return False


def delete_key_sheet(name):
    """Shift ko Sheet ke 'Keys' tab se hatata hai. True = hata di."""
    try:
        kws = keys_sheet()
        if kws is None:
            return False
        names = board.with_retry(lambda: kws.col_values(1))
        if name not in names:
            return False
        board.with_retry(lambda: kws.delete_rows(names.index(name) + 1))
        sheet_keys.clear()
        return True
    except Exception:
        return False


def clean_key(txt):
    return "".join(txt.upper().replace(",", " ").split())


# ------------------------------------------------------------ image, scan (yaad rakhe jate hain)
@st.cache_data(show_spinner=False, max_entries=20)
def prepare_image(data):
    """(OMR image ke bytes, kaaran). ZIP ho to usme se pehli valid OMR image."""
    if len(data) > MAX_FILE:
        return None, "big"
    cands = images_from_zip(data) if is_zip(data) else [data]
    for b in cands:
        if validate_omr(b)[0]:
            return b, ""
    return None, "invalid"


@st.cache_data(show_spinner=False, max_entries=30)
def cached_scan(img):
    return scan(img)


@st.cache_data(show_spinner="Screenshot se naam aur shift padh rahe hain...", max_entries=30)
def cached_header(img):
    return read_header(img)


# ------------------------------------------------------------ scorecard
def make_scorecard(name, cid, shift, cat, r, rk):
    """Scorecard ki PNG image (bytes)."""
    from PIL import Image, ImageDraw, ImageFont

    def font(size, bold=False):
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
        except Exception:
            try:
                return ImageFont.load_default(size=size)
            except TypeError:
                return ImageFont.load_default()

    W, H = 720, 980
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 110], fill=(233, 30, 99))
    d.text((36, 30), "OMR SCORECARD", fill="white", font=font(46, True))
    y = 140
    d.text((36, y), str(name), fill="black", font=font(36, True))
    y += 56
    if cid:
        d.text((36, y), "Candidate ID: " + str(cid), fill=(60, 60, 60), font=font(26))
        y += 40
    for line in textwrap.wrap(str(shift), 38):
        d.text((36, y), line, fill=(60, 60, 60), font=font(26))
        y += 36
    if cat:
        d.text((36, y), "Category: " + cat, fill=(60, 60, 60), font=font(26))
        y += 40
    y += 20
    d.rounded_rectangle([36, y, W - 36, y + 150], radius=18, fill=(252, 228, 236))
    d.text((60, y + 18), "SCORE", fill=(120, 20, 60), font=font(24, True))
    d.text((60, y + 54), str(r["score"]) + " / " + str(r["total"]), fill=(120, 20, 60), font=font(64, True))
    y += 190
    bw = (W - 72 - 40) // 3
    for n, (lab, val, col) in enumerate((("Correct", r["correct"], (46, 125, 50)), ("Wrong", r["wrong"], (198, 40, 40)), ("Unattempted", r["unattempted"], (90, 90, 90)))):
        x = 36 + n * (bw + 20)
        d.rounded_rectangle([x, y, x + bw, y + 110], radius=14, outline=col, width=3)
        d.text((x + 16, y + 12), lab, fill=col, font=font(22, True))
        d.text((x + 16, y + 48), str(val), fill=col, font=font(44, True))
    y += 150
    if rk:
        d.text((36, y), "RANK", fill="black", font=font(26, True))
        y += 42
        lines = [("In my shift", rk.get("shift")), ("In my shift (" + cat + ")", rk.get("shift_cat")),
                 ("In whole exam", rk.get("exam")), ("In whole exam (" + cat + ")", rk.get("exam_cat"))]
        for lab, v in lines:
            if v:
                d.text((36, y), lab + ": " + str(v[0]) + " / " + str(v[1]), fill=(40, 40, 40), font=font(28))
                y += 42
    d.text((36, H - 70), "Unofficial estimate from the answer key. Rank is among users of this site only.", fill=(120, 120, 120), font=font(16))
    d.text((36, H - 42), datetime.now(IST).strftime("%d-%m-%Y %H:%M"), fill=(120, 120, 120), font=font(16))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------------ rank aur dashboard
def fmt_rank(v):
    return (str(v[0]) + " / " + str(v[1])) if v else "-"


def board_df(rows):
    return pd.DataFrame({
        "Rank": [r["rank"] for r in rows],
        "ID": [r["id"] for r in rows],
        "Marks": [round(r["marks"], 2) for r in rows],
        "Aap": ["◀ Aap" if r["own"] else "" for r in rows],
    })


def show_df(df):
    try:
        sty = df.style.apply(lambda row: ["background-color: #ffe08a; color: #000000" if row["Aap"] else ""] * len(row), axis=1)
        st.dataframe(sty, hide_index=True, use_container_width=True)
    except Exception:
        st.dataframe(df, hide_index=True, use_container_width=True)


def show_ranks(groups, own, uid):
    """Rank ke chaar dabbe aur dashboard (Meri shift / Sabhi shifts)."""
    rk = board.ranks_for(groups, own)
    cat = own["cat"]
    a, b = st.columns(2)
    a.metric("Meri shift: overall", fmt_rank(rk["shift"]))
    b.metric("Meri shift: " + (cat or "category"), fmt_rank(rk["shift_cat"]))
    c, d = st.columns(2)
    c.metric("Poora exam: overall", fmt_rank(rk["exam"]))
    d.metric("Poora exam: " + (cat or "category"), fmt_rank(rk["exam_cat"]))
    st.caption("Rank sirf un candidates ke beech hai jinhone is website par result nikala hai, aur sirf same exam ke beech. Ye asli rank nahi hai.")
    st.subheader("Dashboard")
    tab_s, tab_e = st.tabs(["Meri shift", "Sabhi shifts (" + own["exam"].title() + ")"])
    for tab, level in ((tab_s, "shift"), (tab_e, "exam")):
        with tab:
            opts = ["Overall"] + (["Meri category (" + cat + ")"] if cat else [])
            view = st.radio("Dikhayein", opts, horizontal=True, key=uid + level + "_view")
            ents = board.entries_for(groups, level, own)
            if view != "Overall":
                ents = board.only_cat(ents, cat)
            rows = board.table(ents, own["key"])
            own_idx = next((n for n, r in enumerate(rows) if r["own"]), 0)
            st.caption("Aap rank " + str(rows[own_idx]["rank"]) + " par hain. Kul " + str(len(rows)) + " log. Aapke upar " + str(own_idx) + " log hain.")
            win, _, _ = board.window(rows)
            show_df(board_df(win))
            if st.checkbox("Poori list dekhein", key=uid + level + "_full"):
                per = 100
                pages = max(1, -(-len(rows) // per))
                pg = st.number_input("Page (" + str(pages) + " mein se)", min_value=1, max_value=pages,
                                     value=own_idx // per + 1, step=1, key=uid + level + "_page")
                lo = (int(pg) - 1) * per
                show_df(board_df(rows[lo:lo + per]))
    return rk


# ------------------------------------------------------------ candidate screen
def keep_proof(img, img_id):
    """Shak wali image ko Supabase mein rakhna. Fail ho to chupchap chhod dena (candidate ka result na rukey)."""
    url, key = get_secret("SUPABASE_URL"), get_secret("SUPABASE_KEY")
    if not url or not key or not once("proof|" + img_id):
        return
    jpg = store.compress(img)
    if jpg and store.upload(url, key, img_id, jpg):
        counters()["stored"] += 1


def handle_upload(up, name_in, cat_in, keys):
    cn = counters()
    data = up.getvalue()
    img_id = hashlib.md5(data).hexdigest()[:12]
    first_time = once("up|" + img_id)
    if first_time:
        cn["uploads"] += 1

    img, why = prepare_image(data)
    if img is None:
        if first_time:
            cn["invalid"] += 1
        if why == "big":
            st.error("File bahut badi hai. 10 MB se chhoti file upload karein.")
        else:
            st.error("Please upload valid OMR sheet.")
            st.caption("Q1 se Q110 tak poori OMR sheet ki original image (ya uski ZIP file) hi upload karein. Screenshot, Edited image ya crop image accept nahi hoti.")
        return

    res, cnt = cached_scan(img)
    hd = cached_header(img)
    cand_id = hd["cand_id"]
    ocr_name = hd["name"]
    name = ocr_name or name_in
    img_id = hashlib.md5(img).hexdigest()[:12]

    shift = pick_shift(hd, list(keys.keys()), alias_map(keys))
    manual = False
    if shift is None:
        manual = True
        seen = hd.get("shift_text", "")
        if seen:
            st.caption("Screenshot par ye padha gaya: " + seen)
        pick = st.selectbox(
            "Shift chunein (screenshot se apne aap nahi padhi gayi)",
            [PLACEHOLDER_SHIFT] + list(keys.keys()),
            key="manual_shift",
        )
        shift = None if pick == PLACEHOLDER_SHIFT else pick

    missing = []
    if cat_in == PLACEHOLDER_CAT:
        missing.append("category chunein")
    if not name:
        missing.append("naam bharein")
    if shift is None:
        missing.append("shift chunein")
    if missing:
        st.info("Result dekhne ke liye apni " + " aur ".join(missing) + ".")
        return
    cat = cat_in

    cfg = keys[shift]
    r = evaluate(res, cfg["key"], float(cfg.get("pos", 1)), float(cfg.get("neg", 1 / 3)))
    with st.expander("Uploaded image dekhein"):
        st.image(img)
    st.subheader("Result")
    st.write("**" + name + "**" + ((" | ID: " + cand_id) if cand_id else "") + " | " + cat)
    st.caption(shift + (" (aapne khud chuni)" if manual else ""))
    c1, c2 = st.columns(2)
    c1.metric("Correct", r["correct"])
    c2.metric("Wrong", r["wrong"])
    c3, c4 = st.columns(2)
    c3.metric("Unattempted", r["unattempted"])
    c4.metric("Score", str(r["score"]) + " / " + str(r["total"]))
    st.caption("Marking: +" + str(cfg.get("pos", 1)) + " sahi, -" + str(round(float(cfg.get("neg", 1 / 3)), 4)) + " galat")

    # Sheet mein save: har result ek hi baar
    own = None
    if cnt < 40 or cnt > 150:
        st.warning(
            "Sirf " + str(cnt) + " bhare bubbles mile. Poori OMR sheet (Q1 se Q110 tak) ka screenshot upload karein. "
            "Ye result Sheet mein save nahi kiya gaya."
        )
    elif manual and r["score"] <= board.MIN_MANUAL_SCORE:
        if once("skip|" + img_id):
            cn["manual_skipped"] += 1
        st.info("Shift screenshot se pakki nahi hui aur score " + str(board.MIN_MANUAL_SCORE) + " se kam hai, isliye ye result rank mein nahi judega.")
    else:
        src = "manual" if manual else "auto"
        id_ok = board.valid_id(cand_id)
        if not id_ok:
            src = "unverified"
        sig = shift + "|" + img_id
        saved = st.session_state.setdefault("saved", {})
        if sig not in saved:
            saved[sig] = save_result(ocr_name or name, cand_id, shift, r, img_id, cat, src)
            if saved[sig] == "ok":
                cn["saved"] += 1
                if not id_ok:
                    cn["unverified"] += 1
                if (not id_ok) or manual or (r["wrong"] == 0 and r["correct"] >= 90):
                    keep_proof(img, img_id)
            elif saved[sig] == "error":
                cn["failed_saves"] += 1
        if id_ok:
            own = board.make_record(ocr_name or name, cand_id, shift, r["score"], cat, img_id, src)
        else:
            st.warning(
                "Screenshot mein Candidate ID saaf nahi padhi gayi, isliye ye result rank mein nahi joda gaya. "
                "Poora aur saaf screenshot dobara upload karein. Verification ke liye ye image kuch din rakhi ja sakti hai."
            )

    rk = None
    if own is not None:
        data_all = get_data()
        if data_all is not None:
            rk = show_ranks(data_all["groups"], own, "res_")
        else:
            st.caption("Rank abhi load nahi ho paya. Thodi der baad page dobara kholein.")

    df = pd.DataFrame(r["rows"])
    try:
        png = make_scorecard(name, cand_id, shift, cat, r, rk)
        d1, d2 = st.columns(2)
        d1.download_button("Scorecard (PNG)", data=png, file_name="scorecard.png", mime="image/png", key="dl_png")
        d2.download_button("Sawaal-wise (CSV)", data=df.to_csv(index=False).encode("utf-8-sig"),
                           file_name="answers.csv", mime="text/csv", key="dl_csv")
    except Exception:
        pass
    wrong = df[df["Status"] == "Wrong"]
    if len(wrong) > 0:
        st.write("**Galat answers:**")
        st.dataframe(wrong, hide_index=True)
    with st.expander("Saare 110 questions ka breakdown"):
        st.dataframe(df, hide_index=True)
    report_box(name, cand_id, shift, r["score"], img_id)


def lookup_box():
    with st.expander("Sirf rank dekhna hai? Candidate ID daalein"):
        cid_in = st.text_input("Candidate ID", max_chars=12, key="lookup_id").strip()
        if not cid_in:
            return
        data_all = get_data()
        if data_all is None:
            st.warning("Abhi rank load nahi ho paya. Thodi der baad try karein.")
            return
        rec = board.pick_by_id(data_all["recs"], cid_in)
        if rec is None:
            st.info("Is ID ka result abhi nahi mila. Pehle apni OMR sheet upload karein.")
            return
        st.write("**ID: " + board.mask_id(rec["cid"]) + "** | " + rec["shift"] + " | Marks: " + str(rec["score"]))
        show_ranks(data_all["groups"], rec, "lk_")


st.title("📝 OMR Result Checker")
st.caption("Created by Gajendra Verma")
# Admin tab sirf tab dikhta hai jab app ke link ke aakhir mein ?admin=1 lagaya ho
is_admin = st.query_params.get("admin", "") == "1"
if is_admin:
    tab_c, tab_a = st.tabs(["Candidate", "Admin"])
else:
    tab_c = st.container()

# ---------------------------------------------------------------- Candidate
with tab_c:
    keys = load_keys()
    if not keys:
        st.warning("Abhi koi Answer Key available nahi hai. Admin se key add karwayein.")
    else:
        name_in = st.text_input("Aapka naam", max_chars=80).strip()
        cat_in = st.selectbox("Category", [PLACEHOLDER_CAT] + board.CATEGORIES)
        up = st.file_uploader("Apni OMR ki image (JPG/PNG) ya ZIP upload karein", type=["png", "jpg", "jpeg", "zip"])
        st.caption(NOTICE)
        if up is not None:
            handle_upload(up, name_in, cat_in, keys)
        lookup_box()

# --------------------------------------------------------------------- Admin
if not is_admin:
    st.stop()

with tab_a:
    if not ADMIN_PASSWORD:
        st.warning("ADMIN_PASSWORD set nahi hai. Secrets mein ADMIN_PASSWORD banayein. Tab tak koi login nahi chalega.")
    else:
        pw = st.text_input("Admin password", type="password")
        if pw and hmac.compare_digest(pw.encode(), ADMIN_PASSWORD.encode()):
            st.write("**Google Sheet connection**")
            if st.button("Connection test karein"):
                try:
                    ws = get_ws()
                    if ws is None:
                        st.error("GCP_SERVICE_ACCOUNT_JSON ya SHEET_URL secrets mein set nahi hai (ya gspread install nahi hua).")
                    else:
                        st.success("Connected: " + ws.spreadsheet.title)
                except Exception as e:
                    st.error("Connection fail: " + type(e).__name__ + " " + str(e)[:200])

            st.divider()
            st.write("**Dashboard**")
            if st.button("Data abhi refresh karein"):
                sheet_data.clear()
            adata = get_data()
            if adata is None:
                st.info("Sheet abhi padhi nahi ja saki.")
            else:
                recs = adata["recs"]
                summ = board.admin_summary(recs)
                st.caption("Sheet mein " + str(len(recs)) + " rows. Har ID ek hi baar gini gayi hai.")
                if summ:
                    st.dataframe(pd.DataFrame(summ), hide_index=True)
                    exams = sorted({r["exam"] for r in recs})
                    ex = st.selectbox("Score ka distribution (exam)", exams, key="adm_exam")
                    scs = [r["score"] for r in board.dedupe([r for r in recs if r["exam"] == ex], "exam")]
                    buckets = pd.Series([int(s // 10) * 10 for s in scs]).value_counts().sort_index()
                    st.bar_chart(pd.Series(buckets.values, index=[str(b) + "-" + str(b + 9) for b in buckets.index]))
                cn = counters()
                st.caption(
                    "App chalu hone ke baad: uploads " + str(cn["uploads"]) + ", valid OMR nahi " + str(cn["invalid"])
                    + ", save hue " + str(cn["saved"]) + ", save fail " + str(cn["failed_saves"])
                    + ", manual shift (40 se kam) " + str(cn["manual_skipped"]) + ", bina ID " + str(cn["unverified"]) + ", image rakhi " + str(cn["stored"]) + "."
                )
                body = board.body_rows(adata["rows"])
                width = len(board.HEADER)
                padded = [(list(r) + [""] * width)[:width] for r in body]
                csv = pd.DataFrame(padded, columns=board.HEADER).to_csv(index=False).encode("utf-8-sig")
                st.download_button("Poora data (CSV)", data=csv, file_name="omr_results.csv", mime="text/csv", key="adm_csv")

            st.divider()
            st.write("**Shak wale results (review)**")
            sus = board.suspects(adata["rows"]) if adata is not None else []
            if not sus:
                st.caption("Abhi koi shak wali entry nahi hai.")
            else:
                st.caption(str(len(sus)) + " entries. 'Rank mein judein' ya 'Hatayein' dabate hi ye list se hat jayengi.")
                st.dataframe(pd.DataFrame(sus)[["row_no", "name", "cid", "shift", "score", "src", "why", "img"]], hide_index=True)
                pick_sus = st.selectbox("Entry (Image ID)", [x["img"] or ("row " + str(x["row_no"])) for x in sus], key="sus_pick")
                chosen = next((x for x in sus if (x["img"] or ("row " + str(x["row_no"]))) == pick_sus), None)
                surl, skey = get_secret("SUPABASE_URL"), get_secret("SUPABASE_KEY")
                if chosen and chosen["img"] and surl and skey:
                    pic = store.fetch(surl, skey, chosen["img"])
                    if pic:
                        st.image(pic)
                    else:
                        st.caption("Is entry ki image store mein nahi mili (purani ho sakti hai).")
                a1, a2 = st.columns(2)
                for btn, label, newsrc in ((a1, "Rank mein judein", "approved"), (a2, "Hatayein (rank se)", "rejected")):
                    if btn.button(label, key="sus_" + newsrc) and chosen:
                        try:
                            wsx = get_ws()
                            board.with_retry(lambda: wsx.update(values=[[newsrc]], range_name="K" + str(chosen["row_no"])))
                            sheet_data.clear()
                            st.success("Ho gaya: row " + str(chosen["row_no"]) + " -> " + newsrc + ". Page refresh karein.")
                        except Exception as e:
                            st.error("Update nahi ho paya: " + type(e).__name__)
            surl2, skey2 = get_secret("SUPABASE_URL"), get_secret("SUPABASE_KEY")
            if surl2 and skey2:
                if st.button("30 din se purani images hatayein"):
                    dr = store.delete_old(surl2, skey2, 30)
                    st.info("Storage tak nahi pahunch paya." if dr is None else str(dr[0]) + " images hata di (kul " + str(dr[1]) + " mein se).")
            else:
                st.caption("Image storage chalu nahi hai (Secrets mein SUPABASE_URL aur SUPABASE_KEY nahi mili).")

            st.divider()
            st.write("**Candidate reports (score galat)**")
            try:
                rws = reports_sheet()
                rrows = board.with_retry(lambda: rws.get_all_values()) if rws is not None else []
            except Exception:
                rrows = []
            pend = [(n + 1, row) for n, row in enumerate(rrows) if n > 0 and (row + [""] * 9)[8].strip().lower() in ("new", "")]
            if not pend:
                st.caption("Abhi koi nayi report nahi hai.")
            else:
                st.caption(str(len(pend)) + " nayi reports.")
                rdf = pd.DataFrame([(r + [""] * 9)[:9] for _, r in pend], columns=REPORT_HEADER)
                rdf.insert(0, "row_no", [n for n, _ in pend])
                st.dataframe(rdf[["row_no", "Date/Time", "Name", "Candidate ID", "Shift", "Score", "Reason", "Note"]], hide_index=True)
                pick_rep = st.selectbox("Report (row_no)", [n for n, _ in pend], key="rep_pick")
                b1, b2 = st.columns(2)
                for bt, lab, newst in ((b1, "Theek kiya (resolved)", "resolved"), (b2, "Reject (galat report)", "rejected")):
                    if bt.button(lab, key="rep_" + newst):
                        try:
                            board.with_retry(lambda: rws.update(values=[[newst]], range_name="I" + str(pick_rep)))
                            st.success("Row " + str(pick_rep) + " -> " + newst + ". Page refresh karein.")
                        except Exception as e:
                            st.error("Update nahi ho paya: " + type(e).__name__)

            st.divider()
            st.write("**PDF se Answer Key**")
            kpdfs = st.file_uploader("Key PDF (keyTemplate) upload karein (ek saath kai PDF chun sakte hain, 10 tak)", type=["pdf"], key="keypdf", accept_multiple_files=True)
            kpdfs = list(kpdfs or [])[:10]
            ready = []
            seen_names = set()
            for fi, kf in enumerate(kpdfs):
                pr = keyparse.parse_key_pdf(kf.getvalue())
                title = "PDF " + str(fi + 1) + ": " + (pr["shift"] or str(getattr(kf, "name", "")))
                if pr["problems"]:
                    st.error(title + " - " + pr["problems"][0])
                    continue
                pdf_name = st.text_input("Shift ka naam (PDF " + str(fi + 1) + ")", value=pr["shift"], key="pdfname_" + str(fi) + "_" + pr["shift"]).strip()
                pk = pr["key"]
                with st.expander(title + " - 110 jawab mile, check karein"):
                    st.code("\n".join("%3d-%3d  %s" % (i + 1, i + 10, " ".join(pk[i:i + 10])) for i in range(0, 110, 10)))
                if not pdf_name:
                    st.error("PDF " + str(fi + 1) + ": shift ka naam likhein.")
                    continue
                if pdf_name in seen_names:
                    st.warning("PDF " + str(fi + 1) + ": '" + pdf_name + "' ka naam upar kisi aur PDF mein bhi hai, ye wali chhodi jayegi.")
                    continue
                seen_names.add(pdf_name)
                old = load_keys().get(pdf_name)
                go = True
                if old:
                    diff = [i + 1 for i in range(110) if old["key"][i:i + 1] != pk[i]]
                    if diff:
                        st.warning(pdf_name + ": pehle se hai aur " + str(len(diff)) + " sawaalon mein alag hai: " + ", ".join(map(str, diff[:20])))
                        go = st.checkbox("Haan, '" + pdf_name + "' ki purani key badalni hai", key="pdfover_" + str(fi) + "_" + pdf_name)
                    else:
                        st.info(pdf_name + ": pehle se bilkul yahi key hai, kuch badalna nahi.")
                        go = False
                if go:
                    ready.append((pdf_name, pk, old or {}))
            if kpdfs and st.button("PDF wali keys save karein"):
                if not ready:
                    st.info("Save karne ke liye koi nayi ya tick ki hui key nahi hai.")
                for nm, kk, prev in ready:
                    if save_key_sheet(nm, kk, float(prev.get("pos", 1.0)), float(prev.get("neg", 1 / 3)), ", ".join(prev.get("aliases", []) or [])):
                        st.success("Save ho gaya (Sheet ke 'Keys' tab mein, permanent): " + nm)
                    else:
                        st.warning("Sheet connect nahi hui, save nahi hui: " + nm)

            st.divider()
            st.write("**Answer Key (manual)**")
            keys = load_keys()
            mode = st.radio("Kya karna hai?", ["Purani shift update karein", "Nayi shift add karein"], horizontal=True)
            cur = {}
            if mode == "Purani shift update karein":
                if keys:
                    name_k = st.selectbox("Shift", list(keys.keys()), key="sel_shift")
                    cur = keys[name_k]
                else:
                    name_k = ""
                    st.info("Abhi koi shift nahi hai. 'Nayi shift add karein' chunein.")
            else:
                name_k = st.text_input("Nayi shift ka naam (jaise: STATION MASTER 13 Oct 2026 S2)").strip()
            kt = st.text_area(
                "Answer Key: 110 letters (A/B/C/D). Dropped question ke liye *",
                value=cur.get("key", ""),
                height=130,
                key="keytxt_" + name_k,
            )
            k1, k2 = st.columns(2)
            pos = k1.number_input("Sahi answer ke marks", value=float(cur.get("pos", 1.0)), step=0.25)
            neg = k2.number_input("Galat answer ka negative", value=float(cur.get("neg", 1 / 3)), step=0.05, format="%.4f")
            al_txt = st.text_input(
                "Alag naam (alias), comma se alag. Jaise: Goods Train Manager 05 Oct 2026 S2_2",
                value=", ".join(cur.get("aliases", []) or []),
                key="alias_" + name_k,
            )
            st.caption("Agar kisi screenshot par is shift ka title alag likha aata hai, to use yahan likhein, bilkul waisa jaisa screenshot par hai.")
            if st.button("Save Answer Key"):
                k = clean_key(kt)
                if not name_k:
                    st.error("Shift ka naam likhein.")
                elif len(k) != 110:
                    st.error("Key mein " + str(len(k)) + " letters hain, 110 chahiye.")
                elif any(ch not in "ABCD*" for ch in k):
                    st.error("Key mein sirf A, B, C, D ya * hona chahiye.")
                else:
                    al_list = split_aliases(al_txt)
                    if save_key_sheet(name_k, k, pos, neg, ", ".join(al_list)):
                        st.success("Save ho gaya (Sheet ke 'Keys' tab mein, permanent): " + name_k)
                    else:
                        d = load_file_keys()
                        d[name_k] = {"key": k, "pos": pos, "neg": neg, "aliases": al_list}
                        save_file_keys(d)
                        st.warning("Sheet connect nahi hui, isliye key sirf temporary save hui (app restart par ud jayegi): " + name_k)
            if keys and mode == "Purani shift update karein" and name_k:
                st.divider()
                sure = st.checkbox("Haan, is shift ko delete karna hai")
                if st.button("Delete shift") and sure:
                    gone = delete_key_sheet(name_k)
                    in_file = name_k in load_file_keys()
                    if gone:
                        st.success("Sheet ke 'Keys' tab se delete ho gaya. Page refresh karein.")
                    if in_file:
                        st.warning("Ye shift GitHub ki keys.json mein bhi hai. Wahan se hatayein, warna ye wapas dikhegi.")
                    if not gone and not in_file:
                        st.info("Ye shift kahin mili nahi.")
        elif pw:
            st.error("Galat password.")
