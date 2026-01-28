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

# ✅ Tab names
WS_BODY_WEEKLY = "Weekly_BodyComp"
WS_ENERGY_WEEKLY = "Weekly_Energy"
WS_TRAIN_WEEKLY = "Weekly_Training"

# ----------------------------
# Helpers
# ----------------------------
def load_sheet(worksheet_name: str) -> pd.DataFrame:
    ws = sh.worksheet(worksheet_name)
    rows = ws.get_all_records()
    return pd.DataFrame(rows) if rows else pd.DataFrame()

def normalise_date_col(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    """
    Keep df[col] as pandas datetime for filtering + Altair.
    Normalizes time to midnight for consistency.
    """
    if df.empty or col not in df.columns:
        return df
    df[col] = pd.to_datetime(df[col], errors="coerce").dt.normalize()
    df = df.dropna(subset=[col]).sort_values(col).reset_index(drop=True)
    return df

def to_num(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def filter_range(df: pd.DataFrame, start_date, end_date, col="date") -> pd.DataFrame:
    if df.empty or col not in df.columns:
        return df
    mask = (df[col].dt.date >= start_date) & (df[col].dt.date <= end_date)
    return df.loc[mask].copy()

def date_for_table(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    """
    For table display only: convert to python date (YYYY-MM-DD look).
    Keeps charts safe because we only apply this on a copy.
    """
    if df.empty or col not in df.columns:
        return df
    out = df.copy()
    out[col] = pd.to_datetime(out[col], errors="coerce").dt.date
    return out

# ----------------------------
# Load BODY weekly
# ----------------------------
body = load_sheet(WS_BODY_WEEKLY)
body = body.rename(columns={"date_time": "date"}) if "date_time" in body.columns and "date" not in body.columns else body
body = normalise_date_col(body, "date")
body = to_num(body, ["weight", "body_fat", "fat_free_mass"])

if not body.empty:
    body["fat_mass"] = (body["weight"] * (body["body_fat"] / 100)).round(2)
    body["lean_mass"] = (body["weight"] - body["fat_mass"]).round(2)

# ----------------------------
# Date range selector
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

body_view = filter_range(body, start_date, end_date, "date")
x_min = body_view["date"].min()
x_max = body_view["date"].max()

# ----------------------------
# Display: Body table
# ----------------------------
st.subheader("Weekly Body Comp (filtered)")
body_table = date_for_table(body_view)

st.dataframe(
    body_table.style.format({
        "weight": "{:.2f}",
        "body_fat": "{:.2f}",
        "fat_free_mass": "{:.2f}",
        "fat_mass": "{:.2f}",
        "lean_mass": "{:.2f}",
    }) if not body_table.empty else body_table
)

# ----------------------------
# Weight Trend (Altair)
# ----------------------------
st.subheader("Weight Trend")
if not body_view.empty:
    plot_df = body_view[["date", "weight", "lean_mass"]].copy()
    melted = plot_df.melt("date", var_name="metric", value_name="value")

    weight_chart = alt.Chart(melted).mark_line(interpolate="monotone").encode(
        x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max]),
                axis=alt.Axis(tickCount=10, titlePadding=20, labelOverlap=True)),
        y=alt.Y("value:Q", title="Lbs", scale=alt.Scale(domain=[145, 225]),
                axis=alt.Axis(titlePadding=10)),
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

    bf_chart = alt.Chart(bf_df).mark_line(interpolate="monotone").encode(
        x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max]),
                axis=alt.Axis(tickCount=10, titlePadding=20, labelOverlap=True)),
        y=alt.Y("body_fat:Q", title="Body Fat %", scale=alt.Scale(domain=[5, 30])),
        tooltip=[
            alt.Tooltip("date:T", title="Date"),
            alt.Tooltip("body_fat:Q", title="Body Fat %", format=".2f"),
        ],
    ).properties(height=300, padding={"left": 10, "right": 10, "top": 10, "bottom": 40})

    st.altair_chart(bf_chart, use_container_width=True)
else:
    st.info("No data in the selected date range.")

# ============================================================
# Energy + Training (REAL)
# ============================================================
st.header("⚡ Energy Balance + Training")

# ----------------------------
# Load weekly ENERGY
# ----------------------------
energy = load_sheet(WS_ENERGY_WEEKLY)
energy = normalise_date_col(energy, "date")

# Your actual Weekly_Energy columns
energy = to_num(energy, [
    "days_logged",
    "avg_calories", "avg_expenditure", "avg_calorie_target", "avg_calorie_delta",
    "avg_protein_g", "avg_carbs_g", "avg_fat_g",
    "protein_adherence_avg", "energy_adherence_avg",
    "avg_scale_weight_lb", "avg_trend_weight_lb", "avg_steps"
])

energy_view = filter_range(energy, start_date, end_date, "date")

# ----------------------------
# Load weekly TRAINING
# ----------------------------
train = load_sheet(WS_TRAIN_WEEKLY)
train = normalise_date_col(train, "date")

# Your actual Weekly_Training columns (note avg_RIR casing)
train = to_num(train, [
    "sets_total", "volume_total", "workouts_completed",
    "avg_RIR", "muscle_groups_hit_count",
    "training_minutes_total", "avg_workout_minutes"
])

train_view = filter_range(train, start_date, end_date, "date")

# ----------------------------
# Join (weekly on "date")
# ----------------------------
combined = body_view[["date", "weight", "body_fat", "fat_free_mass", "fat_mass", "lean_mass"]].copy()

if not energy_view.empty:
    combined = combined.merge(energy_view, on="date", how="left")

if not train_view.empty:
    combined = combined.merge(train_view, on="date", how="left")

combined = combined.sort_values("date").reset_index(drop=True)

# Derived deltas
combined["weight_change"] = combined["weight"].diff()
combined["lean_change"] = combined["lean_mass"].diff()
combined["fat_change"] = combined["fat_mass"].diff()

# Energy balance (weekly avg kcal/day)
if "avg_calories" in combined.columns and "avg_expenditure" in combined.columns:
    combined["energy_balance"] = combined["avg_calories"] - combined["avg_expenditure"]

# ----------------------------
# Show combined table (optional)
# ----------------------------
with st.expander("Combined weekly table (filtered)", expanded=False):
    combined_table = date_for_table(combined)

    # Optional rounding to keep it clean
    for c in [
        "avg_calories", "avg_expenditure", "avg_calorie_target", "avg_calorie_delta",
        "avg_protein_g", "avg_carbs_g", "avg_fat_g",
        "protein_adherence_avg", "energy_adherence_avg",
        "avg_scale_weight_lb", "avg_trend_weight_lb", "avg_steps",
        "sets_total", "volume_total", "workouts_completed", "avg_RIR",
        "muscle_groups_hit_count", "training_minutes_total", "avg_workout_minutes",
        "energy_balance", "weight_change", "lean_change", "fat_change"
    ]:
        if c in combined_table.columns:
            combined_table[c] = pd.to_numeric(combined_table[c], errors="coerce")

    # round some common fields
    if "protein_adherence_avg" in combined_table.columns:
        combined_table["protein_adherence_avg"] = combined_table["protein_adherence_avg"].round(3)
    if "energy_adherence_avg" in combined_table.columns:
        combined_table["energy_adherence_avg"] = combined_table["energy_adherence_avg"].round(3)

    for c in combined_table.columns:
        if c in ["avg_calories", "avg_expenditure", "avg_calorie_target", "avg_calorie_delta", "energy_balance", "avg_steps"]:
            combined_table[c] = pd.to_numeric(combined_table[c], errors="coerce").round(0)
        if c in ["weight", "fat_free_mass", "fat_mass", "lean_mass", "weight_change", "lean_change", "fat_change",
                 "avg_scale_weight_lb", "avg_trend_weight_lb"]:
            combined_table[c] = pd.to_numeric(combined_table[c], errors="coerce").round(2)

    st.dataframe(combined_table)

# ----------------------------
# Charts: Calories vs Expenditure
# ----------------------------
if not energy_view.empty and ("avg_calories" in combined.columns) and ("avg_expenditure" in combined.columns):
    st.subheader("Calories vs Expenditure (weekly)")
    ce = combined[["date", "avg_calories", "avg_expenditure"]].copy()
    ce_m = ce.melt("date", var_name="metric", value_name="value").dropna()

    ce_chart = alt.Chart(ce_m).mark_line(interpolate="monotone").encode(
        x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max]),
                axis=alt.Axis(tickCount=10, titlePadding=20, labelOverlap=True)),
        y=alt.Y("value:Q", title="kcal"),
        color=alt.Color("metric:N", title=""),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("metric:N"), alt.Tooltip("value:Q", format=".0f")]
    ).properties(height=300, padding={"left": 10, "right": 10, "top": 10, "bottom": 40})

    st.altair_chart(ce_chart, use_container_width=True)

# ----------------------------
# Chart: Energy balance (bar)
# ----------------------------
if "energy_balance" in combined.columns and combined["energy_balance"].notna().any():
    st.subheader("Estimated energy balance (Calories − Expenditure)")
    eb = combined[["date", "energy_balance"]].dropna()

    eb_chart = alt.Chart(eb).mark_bar().encode(
        x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max]),
                axis=alt.Axis(tickCount=10, titlePadding=20, labelOverlap=True)),
        y=alt.Y("energy_balance:Q", title="kcal / day (avg)"),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("energy_balance:Q", format=".0f")]
    ).properties(height=250, padding={"left": 10, "right": 10, "top": 10, "bottom": 40})

    st.altair_chart(eb_chart, use_container_width=True)

# ----------------------------
# Chart: Weight change vs energy balance (scatter)
# ----------------------------
if "energy_balance" in combined.columns and combined["energy_balance"].notna().any():
    st.subheader("Weight change vs energy balance (weekly)")
    sc = combined[["date", "energy_balance", "weight_change"]].dropna()

    scatter = alt.Chart(sc).mark_circle(size=90).encode(
        x=alt.X("energy_balance:Q", title="Energy balance (kcal/day avg)"),
        y=alt.Y("weight_change:Q", title="Weekly weight change (lbs)"),
        tooltip=[
            alt.Tooltip("date:T", title="Week"),
            alt.Tooltip("energy_balance:Q", format=".0f"),
            alt.Tooltip("weight_change:Q", format=".2f"),
        ]
    ).properties(height=300)

    st.altair_chart(scatter, use_container_width=True)

# ----------------------------
# Chart: Training vs Lean Mass
# ----------------------------
if not train_view.empty and ("volume_total" in combined.columns or "sets_total" in combined.columns):
    st.subheader("Training load vs Lean Mass (weekly)")

    metric = "volume_total" if "volume_total" in combined.columns and combined["volume_total"].notna().any() else "sets_total"

    tl = combined[["date", "lean_mass", metric]].copy().dropna(subset=["lean_mass"])
    tl_m = tl.melt("date", var_name="metric", value_name="value")

    tl_chart = alt.Chart(tl_m).mark_line(interpolate="monotone").encode(
        x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max]),
                axis=alt.Axis(tickCount=10, titlePadding=20, labelOverlap=True)),
        y=alt.Y("value:Q", title="Value"),
        color=alt.Color("metric:N", title=""),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("metric:N"), alt.Tooltip("value:Q", format=".2f")]
    ).properties(height=300, padding={"left": 10, "right": 10, "top": 10, "bottom": 40})

    st.altair_chart(tl_chart, use_container_width=True)
else:
    st.info("Training or energy tabs are empty or missing expected columns. Once populated, charts will appear automatically.")
