# =====================================================================
# OMR Result Checker + Google Sheet saving
#
# SETUP (do secrets chahiye, dono Streamlit Cloud ya Hugging Face mein same naam se):
#   1. GCP_SERVICE_ACCOUNT_JSON = Google Cloud se download ki hui JSON key ka POORA text
#   2. SHEET_URL                = Google Sheet ka poora link
#   3. ADMIN_PASSWORD           = Admin panel ka password
#
# Streamlit Cloud: App > Settings > Secrets mein ye format:
#   ADMIN_PASSWORD = "aapka-password"
#   SHEET_URL = "https://docs.google.com/spreadsheets/d/XXXX/edit"
#   GCP_SERVICE_ACCOUNT_JSON = '''
#   { ...JSON file ka poora text yahan paste karein... }
#   '''
#
# Hugging Face: Settings > Variables and secrets mein teeno ko alag-alag
# Secret banayein (value mein JSON ka poora text paste karein).
#
# Google Sheet ko service account ke email (JSON mein "client_email")
# ke saath Editor access ke sath Share karna zaroori hai.
# =====================================================================
import os
import json
import hmac
import hashlib
from datetime import datetime, timezone, timedelta

import pandas as pd
import streamlit as st

try:
    import gspread
except ImportError:
    gspread = None

from omr import scan, evaluate

st.set_page_config(page_title="OMR Result Checker", page_icon="📝", layout="centered")

BASE = os.path.dirname(os.path.abspath(__file__))
KEYFILE = os.path.join(BASE, "keys.json")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
IST = timezone(timedelta(hours=5, minutes=30))
HEADER = ["Date/Time", "Name", "Shift", "Score", "Correct", "Wrong", "Unattempted", "Image ID"]


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


def save_result(name, shift, r, img_id):
    """Returns: ok / dup / nocfg / error"""
    try:
        ws = get_ws()
        if ws is None:
            return "nocfg"
        rows = ws.get_all_values()
        if not rows:
            ws.append_row(HEADER, value_input_option="RAW")
        for row in rows[1:]:
            if len(row) > 7 and row[2] == shift and row[7] == img_id:
                return "dup"
        now = datetime.now(IST).strftime("%d-%m-%Y %H:%M:%S")
        ws.append_row(
            [now, name, shift, r["score"], r["correct"], r["wrong"], r["unattempted"], img_id],
            value_input_option="RAW",
        )
        return "ok"
    except Exception:
        return "error"


@st.cache_data(ttl=60, show_spinner=False)
def get_rank(shift, score):
    """Sheet ke scores se is shift mein rank. Returns (rank, total) ya None."""
    try:
        ws = get_ws()
        if ws is None:
            return None
        scores = []
        for row in ws.get_all_values()[1:]:
            if len(row) > 3 and row[2] == shift:
                try:
                    scores.append(float(row[3].replace(",", ".")))
                except ValueError:
                    pass
        if not scores:
            return None
        return 1 + sum(1 for s in scores if s > score + 1e-9), len(scores)
    except Exception:
        return None


def load_keys():
    try:
        with open(KEYFILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_keys(d):
    with open(KEYFILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


def clean_key(txt):
    return "".join(txt.upper().replace(",", " ").split())


st.title("📝 OMR Result Checker")
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
        name = st.text_input("Aapka Naam", max_chars=80).strip()
        shift = st.selectbox("Shift / Exam chunein", list(keys.keys()))
        up = st.file_uploader("Apni OMR sheet (screenshot) upload karein", type=["png", "jpg", "jpeg"])
        if up is not None and not name:
            st.info("Result dekhne ke liye pehle apna Naam bharein.")
        elif up is not None:
            data = up.getvalue()
            res, cnt = scan(data)
            if res is None:
                st.error("Image padh nahi payi. Dusri image try karein.")
            else:
                with st.expander("Uploaded image dekhein"):
                    st.image(data)
                if cnt < 40 or cnt > 150:
                    st.warning(
                        "Sirf " + str(cnt) + " bhare bubbles mile. Poori OMR sheet ka screenshot "
                        "(Q1 se Q110 tak) upload karein, warna result galat aa sakta hai."
                    )
                cfg = keys[shift]
                r = evaluate(res, cfg["key"], float(cfg.get("pos", 1)), float(cfg.get("neg", 1 / 3)))
                st.subheader("Result")
                st.write("**" + name + "**")
                c1, c2 = st.columns(2)
                c1.metric("Correct", r["correct"])
                c2.metric("Wrong", r["wrong"])
                c3, c4 = st.columns(2)
                c3.metric("Unattempted", r["unattempted"])
                c4.metric("Score", str(r["score"]) + " / " + str(r["total"]))
                st.caption(
                    "Marking: +" + str(cfg.get("pos", 1)) + " sahi, -" + str(round(float(cfg.get("neg", 1 / 3)), 4)) + " galat"
                )

                # Google Sheet mein save (har result ek hi baar)
                if "saved" not in st.session_state:
                    st.session_state["saved"] = {}
                img_id = hashlib.md5(data).hexdigest()[:12]
                sig = shift + "|" + img_id
                if sig not in st.session_state["saved"]:
                    st.session_state["saved"][sig] = save_result(name, shift, r, img_id)
                status = st.session_state["saved"][sig]
                if status in ("ok", "dup"):
                    rr = get_rank(shift, r["score"])
                    if rr:
                        st.metric("Rank (is shift mein)", str(rr[0]) + " / " + str(rr[1]))
                        st.caption("Ye rank sirf un candidates ke beech hai jinhone is website par result nikala hai.")

                df = pd.DataFrame(r["rows"])
                wrong = df[df["Status"] == "Wrong"]
                if len(wrong) > 0:
                    st.write("**Galat answers:**")
                    st.dataframe(wrong, hide_index=True)
                with st.expander("Saare 110 questions ka breakdown"):
                    st.dataframe(df, hide_index=True)

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
            if st.button("Save Answer Key"):
                k = clean_key(kt)
                if not name_k:
                    st.error("Shift ka naam likhein.")
                elif len(k) != 110:
                    st.error("Key mein " + str(len(k)) + " letters hain, 110 chahiye.")
                elif any(ch not in "ABCD*" for ch in k):
                    st.error("Key mein sirf A, B, C, D ya * hona chahiye.")
                else:
                    keys[name_k] = {"key": k, "pos": pos, "neg": neg}
                    save_keys(keys)
                    st.success("Save ho gaya: " + name_k)
            if keys and mode == "Purani shift update karein" and name_k:
                st.divider()
                sure = st.checkbox("Haan, is shift ko delete karna hai")
                if st.button("Delete shift") and sure:
                    keys.pop(name_k, None)
                    save_keys(keys)
                    st.success("Delete ho gaya. Page refresh karein.")
        elif pw:
            st.error("Galat password.")
