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

# ✅ New tab names (your final structure)
WS_BODY_WEEKLY = "Weekly_BodyComp"
WS_ENERGY_WEEKLY = "Weekly_Energy"
WS_TRAIN_WEEKLY = "Weekly_Training_Load"

ws_body = sh.worksheet(WS_BODY_WEEKLY)

# ----------------------------
# Helpers
# ----------------------------
def load_sheet(worksheet_name: str) -> pd.DataFrame:
    ws = sh.worksheet(worksheet_name)
    rows = ws.get_all_records()
    return pd.DataFrame(rows) if rows else pd.DataFrame()

def normalise_date_col(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    if df.empty or col not in df.columns:
        return df
    df[col] = pd.to_datetime(df[col], errors="coerce")
    df = df.dropna(subset=[col]).sort_values(col).reset_index(drop=True)
    return df

def to_num(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

# ----------------------------
# Load BODY weekly
# ----------------------------
body = load_sheet(WS_BODY_WEEKLY)
body = body.rename(columns={"fat_free_mass": "fat_free_mass"})  # no-op; placeholder
body = body.rename(columns={"date_time": "date"}) if "date_time" in body.columns and "date" not in body.columns else body

body = normalise_date_col(body, "date")
body = to_num(body, ["weight", "body_fat", "fat_free_mass"])

if not body.empty:
    # Derived metrics
    body["fat_mass"] = (body["weight"] * (body["body_fat"] / 100)).round(2)
    body["lean_mass"] = (body["weight"] - body["fat_mass"]).round(2)

# ----------------------------
# Date range selector (Option A)
# ----------------------------
st.subheader("Date range")

if body.empty:
    st.info("No body data yet.")
    st.stop()

min_date = body["date"].min().date()
max_date = body["date"].max().date()

default_start = max(min_date, (pd.Timestamp(max_date) - pd.DateOffset(months=12)).date())

start_date, end_date = st.date_input(
    "Select range",
    value=(default_start, max_date),
    min_value=min_date,
    max_value=max_date,
)

if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()

mask = (body["date"].dt.date >= start_date) & (body["date"].dt.date <= end_date)
body_view = body.loc[mask].copy()

# Shared X-axis domain for aligned charts
x_min = body_view["date"].min()
x_max = body_view["date"].max()

# ----------------------------
# Display: Body table
# ----------------------------
st.subheader("Weekly Body Comp (filtered)")
st.dataframe(
    body_view.style.format({
        "weight": "{:.1f}",
        "body_fat": "{:.2f}",
        "fat_free_mass": "{:.2f}",
        "fat_mass": "{:.2f}",
        "lean_mass": "{:.2f}",
    }) if not body_view.empty else body_view
)

# ----------------------------
# Weight Trend (Altair)
# ----------------------------
st.subheader("Weight Trend")

if not body_view.empty:
    plot_df = body_view[["date", "weight", "lean_mass"]].copy()
    melted = plot_df.melt("date", var_name="metric", value_name="value")

    weight_chart = alt.Chart(melted).mark_line(interpolate="monotone").encode(
        x=alt.X(
            "date:T",
            title="Date",
            scale=alt.Scale(domain=[x_min, x_max]),
            axis=alt.Axis(tickCount=10, titlePadding=20, labelOverlap=True)
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
    ).properties(height=360, padding={"left": 10, "right": 10, "top": 10, "bottom": 40})

    st.altair_chart(weight_chart, use_container_width=True)
else:
    st.info("No data in the selected date range.")

# ----------------------------
# Body Fat % (Altair)
# ----------------------------
st.subheader("Body Fat %")

if not body_view.empty:
    bf_df = body_view[["date", "body_fat"]].copy()

    bf_chart = alt.Chart(bf_df).mark_line(
        color="#E45756",
        interpolate="monotone"
    ).encode(
        x=alt.X(
            "date:T",
            title="Date",
            scale=alt.Scale(domain=[x_min, x_max]),
            axis=alt.Axis(tickCount=10, titlePadding=20, labelOverlap=True)
        ),
        y=alt.Y("body_fat:Q", title="Body Fat %", scale=alt.Scale(domain=[5, 30])),
        tooltip=[
            alt.Tooltip("date:T", title="Date"),
            alt.Tooltip("body_fat:Q", title="Body Fat %", format=".2f"),
        ],
    ).properties(height=300, padding={"left": 10, "right": 10, "top": 10, "bottom": 40})

    st.altair_chart(bf_chart, use_container_width=True)
else:
    st.info("No data in the selected date range.")

# ----------------------------
# NEXT: Energy + Training (scaffold)
# ----------------------------
with st.expander("Energy Balance + Training (coming next)", expanded=False):
    st.write("Tabs wired for next step:")
    st.write(f"- {WS_ENERGY_WEEKLY}")
    st.write(f"- {WS_TRAIN_WEEKLY}")
    st.write("Next we’ll load these, normalise their dates, join on Monday date, and chart:")
    st.write("- Calories vs weight change")
    st.write("- Expenditure vs intake")
    st.write("- Training load vs lean mass")
