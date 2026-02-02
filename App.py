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

# Optional sources (used for "This week so far" + patterns)
WS_DAILY_ENERGY = "Daily_Energy"
WS_WORKOUT_LOG = "Staging_Workout_Log"

# Food log staging
WS_FOOD_LOG = "Staging_MacroFactor_FoodLog"

# ----------------------------
# Helpers
# ----------------------------
def load_sheet(worksheet_name: str) -> pd.DataFrame:
    """Safe loader: returns empty df if sheet missing/empty."""
    try:
        ws = sh.worksheet(worksheet_name)
    except Exception:
        return pd.DataFrame()
    rows = ws.get_all_records()
    return pd.DataFrame(rows) if rows else pd.DataFrame()

def normalise_date_col(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    """Keep df[col] as datetime for filtering + Altair. Normalise to midnight."""
    if df.empty or col not in df.columns:
        return df
    df = df.copy()
    df[col] = pd.to_datetime(df[col], errors="coerce").dt.normalize()
    df = df.dropna(subset=[col]).sort_values(col).reset_index(drop=True)
    return df

def align_to_monday(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    """Force weekly dates to Monday."""
    if df.empty or col not in df.columns:
        return df
    df = df.copy()
    df[col] = pd.to_datetime(df[col], errors="coerce").dt.normalize()
    df[col] = df[col] - pd.to_timedelta(df[col].dt.weekday, unit="D")
    df = df.dropna(subset=[col]).sort_values(col).reset_index(drop=True)
    return df

def to_num(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
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
    """For table display only (clean YYYY-MM-DD)."""
    if df.empty or col not in df.columns:
        return df
    out = df.copy()
    out[col] = pd.to_datetime(out[col], errors="coerce").dt.date
    return out

def safe_num(series):
    return pd.to_numeric(series, errors="coerce")

# ----------------------------
# Week helpers (Monday week)
# ----------------------------
def monday_of(d: pd.Timestamp) -> pd.Timestamp:
    d = pd.to_datetime(d).normalize()
    return d - pd.to_timedelta(d.weekday(), unit="D")

def sunday_of_week(d: pd.Timestamp) -> pd.Timestamp:
    return monday_of(d) + pd.Timedelta(days=6)

# ----------------------------
# Load BODY weekly
# ----------------------------
body = load_sheet(WS_BODY_WEEKLY)
if "date_time" in body.columns and "date" not in body.columns:
    body = body.rename(columns={"date_time": "date"})

body = normalise_date_col(body, "date")
body = align_to_monday(body, "date")
body = to_num(body, ["weight", "body_fat", "fat_free_mass"])

if not body.empty:
    body["fat_mass"] = (body["weight"] * (body["body_fat"] / 100)).round(2)
    body["lean_mass"] = (body["weight"] - body["fat_mass"]).round(2)

# ----------------------------
# Date range selector (ONCE)
# ----------------------------
st.subheader("Date range")

if body.empty:
    st.info("No body data yet.")
    st.stop()

min_date = body["date"].min().date()
max_body_date = body["date"].max().date()

today = pd.Timestamp.today().normalize()
this_monday = monday_of(today)

colA, colB, colC, colD = st.columns([1.4, 1, 1, 2])

with colD:
    include_report_monday = st.checkbox(
        "Include current week (report Monday)",
        value=True,
        help="Weekly Energy/Training rows are stamped on the next Monday. Enable to include that row in filters/charts.",
        key="include_report_monday",
    )

max_end_allowed = max_body_date
if include_report_monday:
    max_end_allowed = (pd.Timestamp(max_body_date) + pd.Timedelta(days=7)).date()

with colA:
    preset = st.selectbox(
        "Quick range",
        [
            "Last week (Mon–Sun)",
            "Last 4 weeks",
            "Last 12 weeks",
            "Last month (calendar)",
            "Last 3 months (calendar)",
            "Last year (rolling 365d)",
            "Year-to-date",
            "Custom",
        ],
        index=0,
        key="preset_range",
    )

def compute_preset(preset_name: str):
    if preset_name == "Last week (Mon–Sun)":
        end = this_monday - pd.Timedelta(days=1)  # last Sunday
        start = end - pd.Timedelta(days=6)        # last Monday
        return start.date(), end.date()

    if preset_name == "Last 4 weeks":
        end = this_monday - pd.Timedelta(days=1)
        start = end - pd.Timedelta(days=27)
        return monday_of(start).date(), end.date()

    if preset_name == "Last 12 weeks":
        end = this_monday - pd.Timedelta(days=1)
        start = end - pd.Timedelta(days=83)
        return monday_of(start).date(), end.date()

    if preset_name == "Last month (calendar)":
        first_this_month = pd.Timestamp(today.year, today.month, 1)
        last_month_end = first_this_month - pd.Timedelta(days=1)
        last_month_start = pd.Timestamp(last_month_end.year, last_month_end.month, 1)
        return last_month_start.date(), last_month_end.date()

    if preset_name == "Last 3 months (calendar)":
        first_this_month = pd.Timestamp(today.year, today.month, 1)
        end = first_this_month - pd.Timedelta(days=1)  # end of last month
        start_month = (first_this_month - pd.DateOffset(months=3)).normalize()
        start = pd.Timestamp(start_month.year, start_month.month, 1)
        return start.date(), end.date()

    if preset_name == "Last year (rolling 365d)":
        end = today
        start = today - pd.Timedelta(days=365)
        return start.date(), end.date()

    if preset_name == "Year-to-date":
        start = pd.Timestamp(today.year, 1, 1)
        end = today
        return start.date(), end.date()

    # Custom fallback (12 months based on data)
    end = max_body_date
    start = max(min_date, (pd.Timestamp(end) - pd.DateOffset(months=12)).date())
    return start, end

preset_start, preset_end = compute_preset(preset)

# Clamp to widget bounds
preset_start = max(min_date, min(preset_start, max_end_allowed))
preset_end = max(min_date, min(preset_end, max_end_allowed))
if preset_start > preset_end:
    preset_start = preset_end

# If preset isn’t Custom, force the values
if preset != "Custom":
    start_date = preset_start
    end_date = preset_end
else:
    with colB:
        start_date = st.date_input(
            "Start date",
            value=preset_start,
            min_value=min_date,
            max_value=max_end_allowed,
            key="start_date",
        )

    with colC:
        end_date = st.date_input(
            "End date",
            value=preset_end,
            min_value=min_date,
            max_value=max_end_allowed,
            key="end_date",
        )

# Validate and snap to Monday/Sunday
if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()

start_dt_monday = monday_of(pd.Timestamp(start_date))
end_dt_sunday = sunday_of_week(pd.Timestamp(end_date))

start_date = start_dt_monday.date()
end_date = end_dt_sunday.date()

end_date_for_weekly = end_date
if include_report_monday:
    end_date_for_weekly = (pd.Timestamp(end_date) + pd.Timedelta(days=7)).date()

st.caption(f"Using Monday-week range: {start_date} → {end_date} (weekly includes up to {end_date_for_weekly})")

# ----------------------------
# Load weekly ENERGY
# ----------------------------
energy = load_sheet(WS_ENERGY_WEEKLY)
energy = normalise_date_col(energy, "date")
energy = align_to_monday(energy, "date")
energy = to_num(energy, [
    "days_logged",
    "avg_calories", "avg_expenditure", "avg_calorie_target", "avg_calorie_delta",
    "avg_protein_g", "avg_carbs_g", "avg_fat_g",
    "protein_adherence_avg", "energy_adherence_avg",
    "avg_scale_weight_lb", "avg_trend_weight_lb", "avg_steps"
])

# ----------------------------
# Load weekly TRAINING
# ----------------------------
train = load_sheet(WS_TRAIN_WEEKLY)
train = normalise_date_col(train, "date")
train = align_to_monday(train, "date")
train = to_num(train, [
    "sets_total", "volume_total", "workouts_completed",
    "avg_RIR", "muscle_groups_hit_count",
    "training_minutes_total", "avg_workout_minutes"
])

# ----------------------------
# Filter views
# ----------------------------
body_view = filter_range(body, start_date, end_date_for_weekly, "date")
energy_view = filter_range(energy, start_date, end_date_for_weekly, "date")
train_view = filter_range(train, start_date, end_date_for_weekly, "date")

x_min = pd.Timestamp(start_date)
x_max = pd.Timestamp(end_date_for_weekly)

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

    weight_chart = (
        alt.Chart(melted)
        .mark_line(interpolate="monotone")
        .encode(
            x=alt.X(
                "date:T",
                title="Date",
                scale=alt.Scale(domain=[x_min, x_max]),
                axis=alt.Axis(tickCount=10, titlePadding=20, labelOverlap=True),
            ),
            y=alt.Y(
                "value:Q",
                title="Lbs",
                scale=alt.Scale(domain=[145, 225]),
                axis=alt.Axis(titlePadding=10),
            ),
            color=alt.Color("metric:N", title=""),
            tooltip=[
                alt.Tooltip("date:T", title="Date"),
                alt.Tooltip("metric:N", title="Metric"),
                alt.Tooltip("value:Q", title="Lbs", format=".2f"),
            ],
        )
        .properties(height=360, padding={"left": 10, "right": 10, "top": 10, "bottom": 40})
    )
    st.altair_chart(weight_chart, use_container_width=True)
else:
    st.info("No body data in the selected date range.")

# ----------------------------
# Body Fat % (Altair)
# ----------------------------
st.subheader("Body Fat %")
if not body_view.empty:
    bf_df = body_view[["date", "body_fat"]].copy()

    bf_chart = (
        alt.Chart(bf_df)
        .mark_line(interpolate="monotone")
        .encode(
            x=alt.X(
                "date:T",
                title="Date",
                scale=alt.Scale(domain=[x_min, x_max]),
                axis=alt.Axis(tickCount=10, titlePadding=20, labelOverlap=True),
            ),
            y=alt.Y("body_fat:Q", title="Body Fat %", scale=alt.Scale(domain=[5, 30])),
            tooltip=[
                alt.Tooltip("date:T", title="Date"),
                alt.Tooltip("body_fat:Q", title="Body Fat %", format=".2f"),
            ],
        )
        .properties(height=300, padding={"left": 10, "right": 10, "top": 10, "bottom": 40})
    )
    st.altair_chart(bf_chart, use_container_width=True)
else:
    st.info("No data in the selected date range.")

# ============================================================
# Energy + Training
# ============================================================
st.header("⚡ Energy Balance + Training")

# UNION of dates (so report-Monday rows show)
date_series = []
if not body_view.empty and "date" in body_view.columns:
    date_series.append(body_view["date"])
if not energy_view.empty and "date" in energy_view.columns:
    date_series.append(energy_view["date"])
if not train_view.empty and "date" in train_view.columns:
    date_series.append(train_view["date"])

if date_series:
    all_dates = pd.Series(pd.concat(date_series).unique())
    all_dates = pd.to_datetime(all_dates, errors="coerce").dropna().sort_values()
    combined = pd.DataFrame({"date": all_dates})
else:
    combined = pd.DataFrame(columns=["date"])

if not body_view.empty:
    body_cols = ["date", "weight", "body_fat", "fat_free_mass", "fat_mass", "lean_mass"]
    combined = combined.merge(body_view[body_cols], on="date", how="left")

if not energy_view.empty:
    combined = combined.merge(energy_view, on="date", how="left")

if not train_view.empty:
    combined = combined.merge(train_view, on="date", how="left")

combined = combined.sort_values("date").reset_index(drop=True)

# Derived deltas
if "weight" in combined.columns:
    combined["weight_change"] = safe_num(combined["weight"]).diff()
if "lean_mass" in combined.columns:
    combined["lean_change"] = safe_num(combined["lean_mass"]).diff()
if "fat_mass" in combined.columns:
    combined["fat_change"] = safe_num(combined["fat_mass"]).diff()

# Energy balance
if "avg_calories" in combined.columns and "avg_expenditure" in combined.columns:
    combined["energy_balance"] = safe_num(combined["avg_calories"]) - safe_num(combined["avg_expenditure"])

# Training efficiency metrics
if "training_minutes_total" in combined.columns and "sets_total" in combined.columns:
    minutes = safe_num(combined["training_minutes_total"])
    sets = safe_num(combined["sets_total"])
    combined["sets_per_hour"] = sets / (minutes / 60.0)
    combined.loc[(minutes <= 0) | (minutes.isna()), "sets_per_hour"] = pd.NA

if "training_minutes_total" in combined.columns and "volume_total" in combined.columns:
    minutes = safe_num(combined["training_minutes_total"])
    vol = safe_num(combined["volume_total"])
    combined["volume_per_minute"] = vol / minutes
    combined.loc[(minutes <= 0) | (minutes.isna()), "volume_per_minute"] = pd.NA

# ----------------------------
# This week so far (placeholder)
# ----------------------------
st.subheader("This week so far")
st.caption("Hook Daily_Energy + Staging_Workout_Log here.")