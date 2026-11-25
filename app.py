import os
import json
import hmac

import pandas as pd
import streamlit as st

from omr import scan, evaluate

st.set_page_config(page_title="OMR Result Checker", page_icon="📝", layout="centered")

BASE = os.path.dirname(os.path.abspath(__file__))
KEYFILE = os.path.join(BASE, "keys.json")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


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
tab_c, tab_a = st.tabs(["Candidate", "Admin"])

# ---------------------------------------------------------------- Candidate
with tab_c:
    keys = load_keys()
    if not keys:
        st.warning("Abhi koi Answer Key available nahi hai. Admin se key add karwayein.")
    else:
        shift = st.selectbox("Shift / Exam chunein", list(keys.keys()))
        up = st.file_uploader("Apni OMR sheet (screenshot) upload karein", type=["png", "jpg", "jpeg"])
        if up is not None:
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
                c1, c2 = st.columns(2)
                c1.metric("Correct", r["correct"])
                c2.metric("Wrong", r["wrong"])
                c3, c4 = st.columns(2)
                c3.metric("Unattempted", r["unattempted"])
                c4.metric("Score", str(r["score"]) + " / " + str(r["total"]))
                st.caption(
                    "Marking: +" + str(cfg.get("pos", 1)) + " sahi, -" + str(round(float(cfg.get("neg", 1 / 3)), 4)) + " galat"
                )
                df = pd.DataFrame(r["rows"])
                wrong = df[df["Status"] == "Wrong"]
                if len(wrong) > 0:
                    st.write("**Galat answers:**")
                    st.dataframe(wrong, hide_index=True)
                with st.expander("Saare 110 questions ka breakdown"):
                    st.dataframe(df, hide_index=True)

# --------------------------------------------------------------------- Admin
with tab_a:
    if not ADMIN_PASSWORD:
        st.warning("ADMIN_PASSWORD set nahi hai. Space Settings > Secrets mein ADMIN_PASSWORD banayein. Tab tak koi login nahi chalega.")
    else:
        pw = st.text_input("Admin password", type="password")
        if pw and hmac.compare_digest(pw.encode(), ADMIN_PASSWORD.encode()):
            keys = load_keys()
            mode = st.radio("Kya karna hai?", ["Purani shift update karein", "Nayi shift add karein"], horizontal=True)
            cur = {}
            if mode == "Purani shift update karein":
                if keys:
                    name = st.selectbox("Shift", list(keys.keys()), key="sel_shift")
                    cur = keys[name]
                else:
                    name = ""
                    st.info("Abhi koi shift nahi hai. 'Nayi shift add karein' chunein.")
            else:
                name = st.text_input("Nayi shift ka naam (jaise: STATION MASTER 13 Oct 2026 S2)").strip()
            kt = st.text_area(
                "Answer Key: 110 letters (A/B/C/D). Dropped question ke liye *",
                value=cur.get("key", ""),
                height=130,
                key="keytxt_" + name,
            )
            k1, k2 = st.columns(2)
            pos = k1.number_input("Sahi answer ke marks", value=float(cur.get("pos", 1.0)), step=0.25)
            neg = k2.number_input("Galat answer ka negative", value=float(cur.get("neg", 1 / 3)), step=0.05, format="%.4f")
            if st.button("Save Answer Key"):
                k = clean_key(kt)
                if not name:
                    st.error("Shift ka naam likhein.")
                elif len(k) != 110:
                    st.error("Key mein " + str(len(k)) + " letters hain, 110 chahiye.")
                elif any(ch not in "ABCD*" for ch in k):
                    st.error("Key mein sirf A, B, C, D ya * hona chahiye.")
                else:
                    keys[name] = {"key": k, "pos": pos, "neg": neg}
                    save_keys(keys)
                    st.success("Save ho gaya: " + name)
            if keys and mode == "Purani shift update karein" and name:
                st.divider()
                sure = st.checkbox("Haan, is shift ko delete karna hai")
                if st.button("Delete shift") and sure:
                    keys.pop(name, None)
                    save_keys(keys)
                    st.success("Delete ho gaya. Page refresh karein.")
        elif pw:
            st.error("Galat password.")
