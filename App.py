import altair as alt
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
ws = sh.worksheet("Raw_Data")  # weekly averages feed tab

# ----------------------------
# Helpers
# ----------------------------
def load_data() -> pd.DataFrame:
    rows = ws.get_all_records()  # row 1 = headers
    if not rows:
        return pd.DataFrame(columns=["date", "weight", "body_fat", "fat_free_mass"])

    df = pd.DataFrame(rows)

    # Support either header naming style
    # (your sheet currently uses: date, weight, body_fat, fat_free_mass)
    # If you ever switch to date_time / weight_lb naming, you can map here.
    if "date_time" in df.columns and "date" not in df.columns:
        df = df.rename(columns={"date_time": "date"})
    if "weight_lb" in df.columns and "weight" not in df.columns:
        df = df.rename(columns={"weight_lb": "weight"})
    if "fat_free_mass_lb" in df.columns and "fat_free_mass" not in df.columns:
        df = df.rename(columns={"fat_free_mass_lb": "fat_free_mass"})

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["weight"] = pd.to_numeric(df.get("weight"), errors="coerce")
    df["body_fat"] = pd.to_numeric(df.get("body_fat"), errors="coerce")

    # fat_free_mass might exist (your sheet shows it does); keep it if present
    if "fat_free_mass" in df.columns:
        df["fat_free_mass"] = pd.to_numeric(df["fat_free_mass"], errors="coerce")

    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


def upsert_row(entry_date, entry_weight, entry_bf):
    """
    Upsert by date (YYYY-MM-DD) into columns A:C of Raw_Data:
      A: date
      B: weight
      C: body_fat
    """
    date_str = pd.to_datetime(entry_date).strftime("%Y-%m-%d")

    all_values = ws.get_all_values()  # includes header row
    target_row = None
    for i, row in enumerate(all_values[1:], start=2):
        if row and len(row) > 0 and row[0].strip() == date_str:
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
# Date range selector (Option A)
# ----------------------------
st.subheader("Date range")

if data.empty:
    st.info("No data yet. Add your first entry above.")
    st.stop()

min_date = data["date"].min().date()
max_date = data["date"].max().date()

# Default: last 12 months (or all data if less than 12 months)
default_start = max(min_date, (pd.Timestamp(max_date) - pd.DateOffset(months=12)).date())

start_date, end_date = st.date_input(
    "Select range",
    value=(default_start, max_date),
    min_value=min_date,
    max_value=max_date,
)

# Ensure start <= end (Streamlit usually does, but keep it safe)
if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()

mask = (data["date"].dt.date >= start_date) & (data["date"].dt.date <= end_date)
data_view = data.loc[mask].copy()

# Shared X-axis domain for all charts
x_min = data_view["date"].min()
x_max = data_view["date"].max()


# ----------------------------
# Display
# ----------------------------
st.subheader("Raw Data (filtered)")
st.dataframe(
    data_view.style.format({
        "weight": "{:.1f}",
        "body_fat": "{:.2f}",
        "fat_mass": "{:.2f}",
        "lean_mass": "{:.2f}",
        "fat_free_mass": "{:.2f}" if "fat_free_mass" in data_view.columns else "{:.2f}",
    }) if not data_view.empty else data_view
)

# ----------------------------
# Weight Trend (Altair, fixed Y, scalable X)
# ----------------------------
st.subheader("Weight Trend")

if not data_view.empty:
    plot_df = data_view[["date", "weight", "lean_mass"]].copy()
    melted = plot_df.melt("date", var_name="metric", value_name="value")

    weight_chart = alt.Chart(melted).mark_line(interpolate="monotone").encode(
        x=alt.X(
            "date:T",
            title="Date",
            axis=alt.Axis(
                labelAngle=0,
                labelOverlap=True,
                tickCount=10,          # fewer ticks = less clutter
                titlePadding=20        # pushes the word "Date" down
            )
        ),
        y=alt.Y(
            "value:Q",
            title="Lbs",
            scale=alt.Scale(domain=[145, 225]),
            axis=alt.Axis(titlePadding=10)
        ),
        color=alt.Color("metric:N", title=""),
        tooltip=[
            alt.Tooltip("date:T", title="Date"),
            alt.Tooltip("metric:N", title="Metric"),
            alt.Tooltip("value:Q", title="Lbs", format=".2f"),
        ],
    ).properties(
        height=360,
        padding={"left": 10, "right": 10, "top": 10, "bottom": 40}  # gives room for x labels + "Date"
    )

    st.altair_chart(weight_chart, use_container_width=True)
else:
    st.info("No data in the selected date range.")

# ----------------------------
# Body Fat % (Altair, fixed Y, scalable X)
# ----------------------------
st.subheader("Body Fat %")

if not data_view.empty:
    bf_df = data_view[["date", "body_fat"]].copy()

    bf_chart = alt.Chart(bf_df).mark_line(
        color="#E45756",
        interpolate="monotone"
    ).encode(
        x=alt.X(
            "date:T",
            title="Date",
            scale=alt.Scale(domain=[x_min, x_max]),  # 🔑 shared X domain
            axis=alt.Axis(
                tickCount=10,
                titlePadding=20
            )
        ),
        y=alt.Y(
            "body_fat:Q",
            title="Body Fat %",
            scale=alt.Scale(domain=[5, 30])
        ),
        tooltip=[
            alt.Tooltip("date:T", title="Date"),
            alt.Tooltip("body_fat:Q", title="Body Fat %", format=".2f"),
        ],
    ).properties(
        height=300,
        padding={"left": 10, "right": 10, "top": 10, "bottom": 40}
    )

    st.altair_chart(bf_chart, use_container_width=True)
else:
    st.info("No data in the selected date range.")






