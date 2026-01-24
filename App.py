import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Body Composition Tracker", layout="wide")
st.title("📊 Body Composition Tracker")

# ----------------------------
# Google Sheets setup
# ----------------------------
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
ws = sh.sheet1  # first tab

# ----------------------------
# Helpers
# ----------------------------
def load_data() -> pd.DataFrame:
    rows = ws.get_all_records()  # uses row 1 as headers
    if not rows:
        return pd.DataFrame(columns=["date", "weight", "body_fat"])

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["weight"] = pd.to_numeric(df["weight"], errors="coerce")
    df["body_fat"] = pd.to_numeric(df["body_fat"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    return df

def upsert_row(entry_date, entry_weight, entry_bf):
    """
    If date exists, update that row. Otherwise append.
    Assumes headers: date, weight, body_fat in row 1.
    """
    date_str = pd.to_datetime(entry_date).strftime("%Y-%m-%d")

    all_values = ws.get_all_values()  # includes header row
    # Find existing row by date in column A (index 0)
    target_row = None
    for i, row in enumerate(all_values[1:], start=2):  # start=2 = row number in sheet
        if len(row) > 0 and row[0] == date_str:
            target_row = i
            break

    if target_row:
        ws.update(f"A{target_row}:C{target_row}", [[date_str, float(entry_weight), float(entry_bf)]])
    else:
        ws.append_row([date_str, float(entry_weight), float(entry_bf)], value_input_option="USER_ENTERED")

# ----------------------------
# Log form (phone-friendly)
# ----------------------------
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
    upsert_row(entry_date, entry_weight, entry_bf)
    st.success("Saved to Google Sheet ✅")
    st.rerun()

# ----------------------------
# Load + compute metrics
# ----------------------------
data = load_data()

if not data.empty:
    data["fat_mass"] = (data["weight"] * (data["body_fat"] / 100)).round(2)
    data["lean_mass"] = (data["weight"] - data["fat_mass"]).round(2)

# ----------------------------
# Display
# ----------------------------
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
