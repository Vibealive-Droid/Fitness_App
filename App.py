from pathlib import Path
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Body Composition Tracker", layout="wide")
st.title("📊 Body Composition Tracker")

# ---------- File paths ----------
BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data.csv"

# ---------- Load data ----------
if DATA_PATH.exists():
    data = pd.read_csv(DATA_PATH, parse_dates=["date"])
else:
    # Start empty if file missing (still lets app run)
    data = pd.DataFrame(columns=["date", "weight", "body_fat"])
    data["date"] = pd.to_datetime(data["date"])

# Ensure correct dtypes
if not data.empty:
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data["weight"] = pd.to_numeric(data["weight"], errors="coerce")
    data["body_fat"] = pd.to_numeric(data["body_fat"], errors="coerce")

# ---------- Log form ----------
st.subheader("Log a new entry")

with st.form("log", clear_on_submit=True):
    c1, c2, c3 = st.columns(3)
    with c1:
        entry_date = st.date_input("Date")
    with c2:
        entry_weight = st.number_input("Weight (lbs)", min_value=0.0, step=0.1, format="%.1f")
    with c3:
        entry_bf = st.number_input("Body Fat %", min_value=0.0, max_value=100.0, step=0.01, format="%.2f")

    submitted = st.form_submit_button("Save")

if submitted:
    # Build row
    new_row = pd.DataFrame([{
        "date": pd.to_datetime(entry_date),
        "weight": float(entry_weight),
        "body_fat": float(entry_bf),
    }])

    # Append + de-dupe by date (keeps latest entry for the same date)
    data = pd.concat([data, new_row], ignore_index=True)
    data = data.sort_values("date")
    data = data.drop_duplicates(subset=["date"], keep="last")

    # Save back to CSV
    data.to_csv(DATA_PATH, index=False)

    st.success("Saved ✅")
    st.rerun()

# ---------- Derived metrics ----------
if not data.empty:
    data = data.sort_values("date")
    data["fat_mass"] = (data["weight"] * (data["body_fat"] / 100)).round(2)
    data["lean_mass"] = (data["weight"] - data["fat_mass"]).round(2)

# ---------- Display ----------
st.subheader("Raw Data")
st.dataframe(
    data.style.format({
        "weight": "{:.1f}",
        "body_fat": "{:.2f}",
        "fat_mass": "{:.2f}",
        "lean_mass": "{:.2f}",
    }) if not data.empty else data
)

st.subheader("Weight Trend")
if not data.empty:
    st.line_chart(data.set_index("date")[["weight", "lean_mass"]])
else:
    st.info("No data yet. Add your first entry above.")

st.subheader("Body Fat %")
if not data.empty:
    st.line_chart(data.set_index("date")[["body_fat"]])
