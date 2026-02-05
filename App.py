# ============================================================
# 📊 Body Composition Tracker — CLEAN STABLE BUILD
# Matt's version (GitHub paste-ready)
# ============================================================

import altair as alt
import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta

# ============================================================
# Chart Scales (GLOBAL)
# ============================================================

WEIGHT_DOMAIN = [150, 220]
BODYFAT_DOMAIN = [5, 30]
LEAN_DOMAIN = [150, 220]

CALORIES_DOMAIN = [1000, 5000]
BALANCE_DOMAIN = [-600, 600]

VOLUME_DOMAIN = [70000, 90000]
ADH_PCT_DOMAIN = [50, 130]

# ============================================================
# Page Setup
# ============================================================

st.set_page_config(page_title="Body Composition Tracker", layout="wide")
st.title("📊 Body Composition Tracker")

# ============================================================
# Goal Selector (NEW)
# ============================================================

goal = st.selectbox(
    "Goal Mode",
    ["Bulk", "Recomp", "Cut"],
    index=0
)

goal_colour = {
    "Bulk": "#4CAF50",
    "Recomp": "#FFC107",
    "Cut": "#F44336"
}[goal]

# ============================================================
# Google Sheets Connection
# ============================================================

SHEET_ID = st.secrets["SHEET_ID"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_info(
    st.secrets["gcp_service_account"],
    scopes=SCOPES
)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)

WS_BODY_WEEKLY = "Weekly_BodyComp"
WS_ENERGY_WEEKLY = "Weekly_Energy"
WS_TRAIN_WEEKLY = "Weekly_Training"

# ============================================================
# Helpers
# ============================================================

def load_ws(name):
    try:
        ws = sh.worksheet(name)
        data = ws.get_all_records()
        if not data:
            return pd.DataFrame()
        return pd.DataFrame(data)
    except:
        return pd.DataFrame()

def safe_num(series):
    return pd.to_numeric(series, errors="coerce")

# ============================================================
# Load Data
# ============================================================

body = load_ws(WS_BODY_WEEKLY)
energy = load_ws(WS_ENERGY_WEEKLY)
train = load_ws(WS_TRAIN_WEEKLY)

# ============================================================
# Date Range (Monday Weeks)
# ============================================================

today = pd.Timestamp.today().normalize()
monday = today - pd.Timedelta(days=today.weekday())
sunday = monday + pd.Timedelta(days=6)

st.caption(
    f"Using Monday-week range: {monday.date()} → {sunday.date()}"
)

# ============================================================
# Combine Weekly Tables
# ============================================================

combined = pd.DataFrame()

for df in [body, energy, train]:
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        combined = df if combined.empty else pd.merge(
            combined,
            df,
            on="date",
            how="outer"
        )

if combined.empty:
    st.warning("No weekly data found yet.")
    st.stop()

combined = combined.sort_values("date")

x_min = combined["date"].min()
x_max = combined["date"].max()

# ============================================================
# 🧠 Weight + Lean Mass Trend
# ============================================================

st.subheader("Weight Trend")

wt = combined[["date","weight","lean_mass"]].copy()
wt_m = wt.melt("date", var_name="metric", value_name="value").dropna()

if not wt_m.empty:

    chart = alt.Chart(wt_m).mark_line(
        interpolate="monotone",
        color=goal_colour
    ).encode(
        x=alt.X("date:T", scale=alt.Scale(domain=[x_min,x_max])),
        y=alt.Y("value:Q", scale=alt.Scale(domain=WEIGHT_DOMAIN)),
        color="metric:N",
        tooltip=["date","metric","value"]
    ).properties(height=260)

    st.altair_chart(chart, use_container_width=True)

# ============================================================
# ⚡ Calories vs Expenditure
# ============================================================

st.subheader("Calories vs Expenditure (weekly)")

if "avg_calories" in combined.columns:

    ce = combined[["date","avg_calories","avg_expenditure"]].copy()

    ce["avg_calories"] = safe_num(ce["avg_calories"])
    ce["avg_expenditure"] = safe_num(ce["avg_expenditure"])

    ce_m = ce.melt("date", var_name="metric", value_name="value").dropna()

    if not ce_m.empty:

        base = alt.Chart(ce_m).encode(
            x=alt.X("date:T", scale=alt.Scale(domain=[x_min,x_max])),
            y=alt.Y(
                "value:Q",
                title="kcal",
                scale=alt.Scale(domain=CALORIES_DOMAIN, clamp=True)
            ),
            color="metric:N",
            tooltip=["date","metric","value"]
        )

        ce_chart = (
            base.mark_line(interpolate="monotone") +
            base.mark_circle(size=80)
        ).properties(height=300)

        st.altair_chart(ce_chart, use_container_width=True)

# ============================================================
# 🔥 Energy Balance
# ============================================================

if "avg_balance" in combined.columns:

    eb = combined[["date","avg_balance"]].copy()
    eb["avg_balance"] = safe_num(eb["avg_balance"])

    chart = alt.Chart(eb.dropna()).mark_bar(
        color=goal_colour
    ).encode(
        x=alt.X("date:T", scale=alt.Scale(domain=[x_min,x_max])),
        y=alt.Y(
            "avg_balance:Q",
            scale=alt.Scale(domain=BALANCE_DOMAIN)
        ),
        tooltip=["date","avg_balance"]
    ).properties(height=260)

    st.altair_chart(chart, use_container_width=True)

# ============================================================
# 📊 Adherence %
# ============================================================

st.subheader("Adherence (weekly)")

adh = combined[["date"]].copy()

if "protein_adherence_avg" in combined.columns:
    adh["protein_adherence_avg"] = safe_num(
        combined["protein_adherence_avg"]
    ) * 100

if "energy_adherence_avg" in combined.columns:
    adh["energy_adherence_avg"] = safe_num(
        combined["energy_adherence_avg"]
    ) * 100

adh_m = adh.melt("date", var_name="metric", value_name="value").dropna()

if not adh_m.empty:

    chart = alt.Chart(adh_m).mark_line(
        interpolate="monotone",
        point=True
    ).encode(
        x=alt.X("date:T", scale=alt.Scale(domain=[x_min,x_max])),
        y=alt.Y(
            "value:Q",
            title="Adherence (%)",
            scale=alt.Scale(domain=ADH_PCT_DOMAIN)
        ),
        color="metric:N",
        tooltip=["date","metric","value"]
    ).properties(height=260)

    st.altair_chart(chart, use_container_width=True)

# ============================================================
# 🏋️ Training Efficiency
# ============================================================

st.subheader("Training efficiency")

metrics = [
    "sets_per_hour",
    "training_minutes_total",
    "volume_per_minute"
]

available = [m for m in metrics if m in combined.columns]

if available:

    te = combined[["date"]+available].copy()
    te_m = te.melt("date", var_name="metric", value_name="value").dropna()

    chart = alt.Chart(te_m).mark_line(
        interpolate="monotone"
    ).encode(
        x=alt.X("date:T", scale=alt.Scale(domain=[x_min,x_max])),
        y="value:Q",
        color="metric:N",
        tooltip=["date","metric","value"]
    ).properties(height=260)

    st.altair_chart(chart, use_container_width=True)

# ============================================================
# 🧬 Lean Mass vs Training Volume (Separated Scale FIX)
# ============================================================

st.subheader("Training load and Lean Mass (weekly)")

if "lean_mass" in combined.columns and "volume_total" in combined.columns:

    c1, c2 = st.columns(2)

    with c1:
        lm = combined[["date","lean_mass"]].dropna()
        chart = alt.Chart(lm).mark_line(color="#03A9F4").encode(
            x="date:T",
            y=alt.Y(
                "lean_mass:Q",
                scale=alt.Scale(domain=LEAN_DOMAIN)
            ),
            tooltip=["date","lean_mass"]
        ).properties(height=260)

        st.altair_chart(chart, use_container_width=True)

    with c2:
        vol = combined[["date","volume_total"]].dropna()
        chart = alt.Chart(vol).mark_bar(color="#9C27B0").encode(
            x="date:T",
            y=alt.Y(
                "volume_total:Q",
                scale=alt.Scale(domain=VOLUME_DOMAIN)
            ),
            tooltip=["date","volume_total"]
        ).properties(height=260)

        st.altair_chart(chart, use_container_width=True)

# ============================================================
# 🍽️ Food Patterns Placeholder
# ============================================================

st.subheader("🍽️ Food patterns")
st.caption("Food analytics block kept minimal here — your ingestion script controls this.")

# ============================================================
# 📝 Manual Inputs Placeholder
# ============================================================

st.subheader("📝 Manual inputs (starter)")

st.number_input("Sleep hours (today)", value=0.0)
st.text_input("Mood (optional)")
st.text_input("Quick note (optional)")
