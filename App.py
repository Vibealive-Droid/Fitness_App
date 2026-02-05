import altair as alt
import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# Config: chart scales (edit here)
# ============================================================
WEIGHT_DOMAIN = [145, 225]
BODYFAT_DOMAIN = [5, 30]

CALORIES_DOMAIN = [1000, 5000]       # Calories vs Expenditure scale
LEAN_DOMAIN = [150, 220]             # Lean mass scale
VOLUME_DOMAIN = [70000, 90000]       # Training volume scale (lbs·reps)
ADH_PCT_DOMAIN = [50, 130]           # Adherence % scale (set to [0, 120] if you prefer)

# ============================================================
# Streamlit page setup
# ============================================================
st.set_page_config(page_title="Body Composition Tracker", layout="wide")
st.title("📊 Body Composition Tracker")

# ============================================================
# Google Sheets setup
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

# ✅ Tab names
WS_BODY_WEEKLY = "Weekly_BodyComp"
WS_ENERGY_WEEKLY = "Weekly_Energy"
WS_TRAIN_WEEKLY = "Weekly_Training"

# Optional sources (used for "This week so far" + debug)
WS_DAILY_ENERGY = "Daily_Energy"
WS_WORKOUT_LOG = "Staging_Workout_Log"

# Food log staging
WS_FOOD_LOG = "Staging_MacroFactor_FoodLog"

# ============================================================
# Helpers
# ============================================================
@st.cache_data(ttl=120, show_spinner=False)
def _cached_get_all_records(_sheet_id: str, worksheet_name: str) -> list[dict]:
    """Cache records for 2 minutes to reduce Sheets calls."""
    try:
        ws = sh.worksheet(worksheet_name)
        rows = ws.get_all_records()
        return rows if rows else []
    except Exception:
        return []

def load_sheet(worksheet_name: str) -> pd.DataFrame:
    """Safe loader: returns empty df if sheet missing/empty."""
    rows = _cached_get_all_records(SHEET_ID, worksheet_name)
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
# Schema normalisers
# ----------------------------
def normalise_daily_energy_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Rename Daily_Energy columns (if variants exist) to the names the app expects."""
    if df.empty:
        return df
    df = df.copy()

    rename_map = {
        "logged": "days_logged_flag",
        "Logged": "days_logged_flag",

        "calTarget": "calorie_target",
        "Target Calories (kcal)": "calorie_target",

        "protein": "protein_g",
        "carbs": "carbs_g",
        "fat": "fat_g",

        "proteinAdh": "protein_adherence",
        "energyAdh": "energy_adherence",

        "trendW": "trend_weight_lb",
        "Trend Weight (lbs)": "trend_weight_lb",

        "scaleWeight": "scale_weight_lb",
        "Scale Weight (lbs)": "scale_weight_lb",
    }

    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    return df

def normalise_workout_log_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Rename Staging_Workout_Log columns (if variants exist) to names used by the app."""
    if df.empty:
        return df
    df = df.copy()

    rename_map = {
        "weight": "weight_lb",
        "Weight (lbs)": "weight_lb",
        "Reps": "reps",
        "RIR": "rir",
        "Workout Duration": "workout_duration",
        "Set Type": "set_type",
    }

    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    return df

def pick_logged_days(df: pd.DataFrame) -> pd.DataFrame:
    """
    Decide which daily rows count as 'logged'.
    Priority: days_logged_flag==1
    Fallback: any meaningful nutrition data (calories/macros > 0)
    """
    if df.empty:
        return df

    out = df.copy()
    flag = safe_num(out.get("days_logged_flag", pd.Series([pd.NA] * len(out)))).fillna(0)

    calories = safe_num(out.get("calories", pd.Series([pd.NA] * len(out)))).fillna(0)
    p = safe_num(out.get("protein_g", pd.Series([pd.NA] * len(out)))).fillna(0)
    c = safe_num(out.get("carbs_g", pd.Series([pd.NA] * len(out)))).fillna(0)
    f = safe_num(out.get("fat_g", pd.Series([pd.NA] * len(out)))).fillna(0)

    logged_mask = (flag >= 1) | (calories > 0) | (p > 0) | (c > 0) | (f > 0)
    return out.loc[logged_mask].copy()

def metric_or_dash(x, fmt="{:.0f}"):
    if x is None or pd.isna(x):
        return "—"
    try:
        return fmt.format(float(x))
    except Exception:
        return "—"

def safe_domain(values: pd.Series, preferred_domain: list[float], pad_pct: float = 0.05):
    """
    If data fits inside preferred_domain, use it.
    Otherwise compute a padded min/max domain from the data.
    """
    v = pd.to_numeric(values, errors="coerce").dropna()
    if v.empty:
        return preferred_domain
    vmin, vmax = float(v.min()), float(v.max())
    pmin, pmax = float(preferred_domain[0]), float(preferred_domain[1])
    if vmin >= pmin and vmax <= pmax:
        return preferred_domain
    pad = max(1e-6, (vmax - vmin) * pad_pct)
    return [vmin - pad, vmax + pad]

# ============================================================
# Load BODY weekly
# ============================================================
body = load_sheet(WS_BODY_WEEKLY)
body = body.rename(columns={"date_time": "date"}) if "date_time" in body.columns and "date" not in body.columns else body
body = normalise_date_col(body, "date")
body = align_to_monday(body, "date")
body = to_num(body, ["weight", "body_fat", "fat_free_mass"])

if not body.empty and {"weight", "body_fat"}.issubset(body.columns):
    body["fat_mass"] = (body["weight"] * (body["body_fat"] / 100)).round(2)
    body["lean_mass"] = (body["weight"] - body["fat_mass"]).round(2)

# ============================================================
# Date range selector (Monday weeks + presets, bulletproof)
# ============================================================
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
        value=st.session_state.get("include_report_monday", True),
        help="Weekly Energy/Training rows are stamped on the next Monday. Enable to include that row in filters/charts.",
        key="include_report_monday",
    )

max_end_allowed = max_body_date
if include_report_monday:
    max_end_allowed = (pd.Timestamp(max_body_date) + pd.Timedelta(days=7)).date()

with colA:
    preset_options = [
        "Last week (Mon–Sun)",
        "Last 4 weeks",
        "Last 12 weeks",
        "Last month (calendar)",
        "Last 3 months (calendar)",
        "Last year (rolling 365d)",
        "Year-to-date",
        "Custom",
    ]
    preset = st.selectbox("Quick range", preset_options, index=0, key="preset_range")

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
        end = first_this_month - pd.Timedelta(days=1)
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

# Clamp preset to widget bounds
preset_start = max(min_date, min(preset_start, max_end_allowed))
preset_end = max(min_date, min(preset_end, max_end_allowed))
if preset_start > preset_end:
    preset_start = preset_end

# Clamp existing session_state values if present
if "start_date" in st.session_state and st.session_state["start_date"] is not None:
    st.session_state["start_date"] = min(max(st.session_state["start_date"], min_date), max_end_allowed)
if "end_date" in st.session_state and st.session_state["end_date"] is not None:
    st.session_state["end_date"] = min(max(st.session_state["end_date"], min_date), max_end_allowed)

# For non-custom presets, force widget values
if preset != "Custom":
    st.session_state["start_date"] = preset_start
    st.session_state["end_date"] = preset_end

with colB:
    start_date = st.date_input(
        "Start date",
        value=st.session_state.get("start_date", preset_start),
        min_value=min_date,
        max_value=max_end_allowed,
        key="start_date",
        disabled=(preset != "Custom"),
    )

with colC:
    end_date = st.date_input(
        "End date",
        value=st.session_state.get("end_date", preset_end),
        min_value=min_date,
        max_value=max_end_allowed,
        key="end_date",
        disabled=(preset != "Custom"),
    )

if start_date is None or end_date is None:
    st.warning("Please select both a start and end date.")
    st.stop()

if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()

# Snap to Monday-week boundaries
start_dt_monday = monday_of(pd.Timestamp(start_date))
end_dt_sunday = sunday_of_week(pd.Timestamp(end_date))
start_date = start_dt_monday.date()
end_date = end_dt_sunday.date()

# Extend weekly filter window so report-Monday rows show up
end_date_for_weekly = end_date
if include_report_monday:
    end_date_for_weekly = (pd.Timestamp(end_date) + pd.Timedelta(days=7)).date()

st.caption(f"Using Monday-week range: {start_date} → {end_date} (weekly includes up to {end_date_for_weekly})")

# ============================================================
# Load weekly ENERGY
# ============================================================
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

# ============================================================
# Load weekly TRAINING
# ============================================================
train = load_sheet(WS_TRAIN_WEEKLY)
train = normalise_date_col(train, "date")
train = align_to_monday(train, "date")

train = to_num(train, [
    "sets_total", "volume_total", "workouts_completed",
    "avg_RIR", "muscle_groups_hit_count",
    "training_minutes_total", "avg_workout_minutes"
])

# ============================================================
# Filter weekly views
# ============================================================
body_view = filter_range(body, start_date, end_date_for_weekly, "date")
energy_view = filter_range(energy, start_date, end_date_for_weekly, "date")
train_view = filter_range(train, start_date, end_date_for_weekly, "date")

x_min = pd.Timestamp(start_date)
x_max = pd.Timestamp(end_date_for_weekly)

# ============================================================
# Display: Body table
# ============================================================
st.subheader("Weekly Body Comp (filtered)")
body_table = date_for_table(body_view)

if body_table.empty:
    st.dataframe(body_table, hide_index=True)
else:
    st.dataframe(
        body_table.style.format({
            "weight": "{:.2f}",
            "body_fat": "{:.2f}",
            "fat_free_mass": "{:.2f}",
            "fat_mass": "{:.2f}",
            "lean_mass": "{:.2f}",
        }),
        hide_index=True
    )

# ============================================================
# Charts: Weight Trend
# ============================================================
st.subheader("Weight Trend")
if not body_view.empty and {"date", "weight", "lean_mass"}.issubset(set(body_view.columns)):
    plot_df = body_view[["date", "weight", "lean_mass"]].copy()
    melted = plot_df.melt("date", var_name="metric", value_name="value").dropna()

    weight_chart = (
        alt.Chart(melted)
        .mark_line(interpolate="monotone", point=True)
        .encode(
            x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max])),
            y=alt.Y("value:Q", title="Lbs", scale=alt.Scale(domain=WEIGHT_DOMAIN)),
            color=alt.Color("metric:N", title=""),
            tooltip=[
                alt.Tooltip("date:T", title="Date"),
                alt.Tooltip("metric:N", title="Metric"),
                alt.Tooltip("value:Q", title="Lbs", format=".2f"),
            ],
        )
        .properties(height=360)
    )
    st.altair_chart(weight_chart, use_container_width=True)
else:
    st.info("No body data in the selected date range.")

# ============================================================
# Charts: Body Fat %
# ============================================================
st.subheader("Body Fat %")
if not body_view.empty and {"date", "body_fat"}.issubset(set(body_view.columns)):
    bf_df = body_view[["date", "body_fat"]].copy().dropna()

    bf_chart = (
        alt.Chart(bf_df)
        .mark_line(interpolate="monotone", point=True)
        .encode(
            x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max])),
            y=alt.Y("body_fat:Q", title="Body Fat %", scale=alt.Scale(domain=BODYFAT_DOMAIN)),
            tooltip=[
                alt.Tooltip("date:T", title="Date"),
                alt.Tooltip("body_fat:Q", title="Body Fat %", format=".2f"),
            ],
        )
        .properties(height=300)
    )
    st.altair_chart(bf_chart, use_container_width=True)
else:
    st.info("No data in the selected date range.")

# ============================================================
# Energy + Training (weekly combined)
# ============================================================
st.header("⚡ Energy Balance + Training")

# Join using UNION of dates (so report-Monday rows show)
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
    keep = [c for c in body_cols if c in body_view.columns]
    combined = combined.merge(body_view[keep], on="date", how="left")

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

# Energy balance (weekly avg kcal/day)
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

# ============================================================
# Debug toggles
# ============================================================
with st.expander("🛠 Debug", expanded=False):
    debug_on = st.checkbox("Show debug tables + computed metrics", value=False)

# ============================================================
# This week so far (ALWAYS current week: Monday -> today)
# ============================================================
st.subheader("This week so far")

today = pd.Timestamp.today().normalize()
week_start_dt = monday_of(today)   # Monday
week_start = week_start_dt.date()
week_end = today.date()

daily_energy = load_sheet(WS_DAILY_ENERGY)
daily_energy = normalise_daily_energy_schema(daily_energy)
daily_energy = normalise_date_col(daily_energy, "date")

daily_energy = to_num(daily_energy, [
    "days_logged_flag", "calories", "expenditure", "calorie_target", "calorie_delta",
    "protein_g", "carbs_g", "fat_g",
    "protein_target_g", "carbs_target_g", "fat_target_g",
    "protein_adherence", "energy_adherence",
    "scale_weight_lb", "trend_weight_lb",
    "steps"
])

daily_energy_week = filter_range(daily_energy, week_start, week_end, "date")
daily_energy_week_logged = pick_logged_days(daily_energy_week)

workout_log = load_sheet(WS_WORKOUT_LOG)
workout_log = normalise_workout_log_schema(workout_log)
workout_log = normalise_date_col(workout_log, "date")
workout_log = to_num(workout_log, ["workout_duration", "weight_lb", "reps", "rir"])

workout_week = filter_range(workout_log, week_start, week_end, "date")

c1, c2, c3, c4 = st.columns(4)

# Energy so far this week
if not daily_energy_week_logged.empty:
    logged = daily_energy_week_logged.copy()

    days_logged = int(len(logged))
    avg_cals = logged["calories"].mean() if "calories" in logged.columns else pd.NA
    avg_exp = logged["expenditure"].mean() if "expenditure" in logged.columns else pd.NA
    avg_bal = (avg_cals - avg_exp) if pd.notna(avg_cals) and pd.notna(avg_exp) else pd.NA
    avg_steps = logged["steps"].mean() if "steps" in logged.columns else pd.NA

    avg_p_adh = logged["protein_adherence"].mean() if "protein_adherence" in logged.columns else pd.NA
    avg_e_adh = logged["energy_adherence"].mean() if "energy_adherence" in logged.columns else pd.NA

    with c1:
        st.metric("Days logged", days_logged)
    with c2:
        st.metric("Avg calories", metric_or_dash(avg_cals, "{:.0f}"))
        st.metric("Avg expenditure", metric_or_dash(avg_exp, "{:.0f}"))
    with c3:
        st.metric("Avg balance", metric_or_dash(avg_bal, "{:.0f}"))
        st.metric("Avg steps", metric_or_dash(avg_steps, "{:.0f}"))
    with c4:
        # adherence from Daily_Energy is typically ratio (e.g., 0.92). Show as percent if present.
        st.metric("Protein adherence", metric_or_dash(avg_p_adh * 100 if pd.notna(avg_p_adh) else pd.NA, "{:.0f}%"))
        st.metric("Energy adherence", metric_or_dash(avg_e_adh * 100 if pd.notna(avg_e_adh) else pd.NA, "{:.0f}%"))
else:
    with c1:
        st.metric("Days logged", "—")
    with c2:
        st.metric("Avg calories", "—")
        st.metric("Avg expenditure", "—")
    with c3:
        st.metric("Avg balance", "—")
        st.metric("Avg steps", "—")
    with c4:
        st.metric("Protein adherence", "—")
        st.metric("Energy adherence", "—")

# Training so far this week (unique sessions)
if not workout_week.empty and ("workout_duration" in workout_week.columns) and ("workout" in workout_week.columns):
    wk = workout_week.copy()

    # unique session key by day + workout name
    wk["session_key"] = wk["date"].dt.date.astype(str) + "|" + wk["workout"].astype(str).str.strip().str.lower()

    # workout_duration looks like seconds in your screenshot; convert to minutes
    session_minutes = wk.groupby("session_key")["workout_duration"].max() / 60.0
    minutes_total = float(session_minutes.sum())
    workouts_completed = int(session_minutes.shape[0])

    stypes = wk["set_type"].astype(str).str.strip().str.lower() if "set_type" in wk.columns else pd.Series([], dtype=str)
    wk_working = wk[stypes.isin(["standard set", "failure set"])].copy() if len(stypes) else wk.copy()

    sets_total = int(len(wk_working))
    volume_total = (
        (safe_num(wk_working["weight_lb"]) * safe_num(wk_working["reps"])).sum()
        if ("weight_lb" in wk_working.columns and "reps" in wk_working.columns) else pd.NA
    )
    avg_rir = safe_num(wk_working["rir"]).mean() if "rir" in wk_working.columns else pd.NA
    sets_per_hour = sets_total / (minutes_total / 60.0) if minutes_total > 0 else pd.NA

    st.caption("Training this week so far")
    t1, t2, t3, t4 = st.columns(4)
    with t1:
        st.metric("Workouts", workouts_completed)
        st.metric("Minutes", metric_or_dash(minutes_total, "{:.1f}"))
    with t2:
        st.metric("Sets", sets_total)
        st.metric("Avg RIR", metric_or_dash(avg_rir, "{:.2f}"))
    with t3:
        st.metric("Volume (lbs·reps)", metric_or_dash(volume_total, "{:.0f}"))
    with t4:
        st.metric("Sets/hour", metric_or_dash(sets_per_hour, "{:.1f}"))
else:
    st.caption("No workout log rows for this week yet.")

# ============================================================
# Debug: last week (Mon–Sun)
# ============================================================
if debug_on:
    last_week_start = (this_monday - pd.Timedelta(days=7)).date()
    last_week_end = (this_monday - pd.Timedelta(days=1)).date()
    st.caption(f"Debug (last week Mon–Sun): {last_week_start} → {last_week_end}")

    de_last = filter_range(daily_energy, last_week_start, last_week_end, "date")
    wl_last = filter_range(workout_log, last_week_start, last_week_end, "date")

    st.subheader("Daily_Energy (last week)")
    st.dataframe(date_for_table(de_last), hide_index=True)

    st.subheader("Staging_Workout_Log (last week)")
    st.dataframe(date_for_table(wl_last), hide_index=True)

    # show computed metrics for last week as a sanity check
    logged_last = pick_logged_days(de_last)
    st.subheader("Computed metrics (last week)")

    d1, d2, d3, d4 = st.columns(4)
    if not logged_last.empty:
        avg_cals = logged_last["calories"].mean() if "calories" in logged_last.columns else pd.NA
        avg_exp = logged_last["expenditure"].mean() if "expenditure" in logged_last.columns else pd.NA
        avg_steps = logged_last["steps"].mean() if "steps" in logged_last.columns else pd.NA
        avg_p = logged_last["protein_adherence"].mean() if "protein_adherence" in logged_last.columns else pd.NA
        avg_e = logged_last["energy_adherence"].mean() if "energy_adherence" in logged_last.columns else pd.NA

        with d1:
            st.metric("Days logged", int(len(logged_last)))
        with d2:
            st.metric("Avg calories", metric_or_dash(avg_cals, "{:.0f}"))
            st.metric("Avg expenditure", metric_or_dash(avg_exp, "{:.0f}"))
        with d3:
            st.metric("Avg steps", metric_or_dash(avg_steps, "{:.0f}"))
        with d4:
            st.metric("Protein adherence", metric_or_dash(avg_p * 100 if pd.notna(avg_p) else pd.NA, "{:.0f}%"))
            st.metric("Energy adherence", metric_or_dash(avg_e * 100 if pd.notna(avg_e) else pd.NA, "{:.0f}%"))
    else:
        st.info("No logged days detected last week (days_logged_flag=0 AND calories/macros are 0).")

# ============================================================
# Combined table (weekly)
# ============================================================
with st.expander("Combined weekly table (filtered)", expanded=False):
    combined_table = date_for_table(combined)

    round0 = ["avg_calories", "avg_expenditure", "avg_calorie_target", "avg_calorie_delta",
              "energy_balance", "avg_steps", "training_minutes_total", "avg_workout_minutes", "volume_total"]
    round2 = ["weight", "fat_free_mass", "fat_mass", "lean_mass", "weight_change", "lean_change", "fat_change",
              "avg_scale_weight_lb", "avg_trend_weight_lb", "sets_per_hour", "volume_per_minute"]
    round3 = ["protein_adherence_avg", "energy_adherence_avg"]

    for c in round0:
        if c in combined_table.columns:
            combined_table[c] = pd.to_numeric(combined_table[c], errors="coerce").round(0)
    for c in round2:
        if c in combined_table.columns:
            combined_table[c] = pd.to_numeric(combined_table[c], errors="coerce").round(2)
    for c in round3:
        if c in combined_table.columns:
            combined_table[c] = pd.to_numeric(combined_table[c], errors="coerce").round(3)

    st.dataframe(combined_table, hide_index=True)

# ============================================================
# Charts
# ============================================================

# A) Calories vs Expenditure (weekly) — fixed y-scale 1000–5000
st.subheader("Calories vs Expenditure (weekly)")

if ("avg_calories" in combined.columns) and ("avg_expenditure" in combined.columns):
    ce = combined[["date", "avg_calories", "avg_expenditure"]].copy()
    ce["date"] = pd.to_datetime(ce["date"], errors="coerce")
    ce["avg_calories"] = pd.to_numeric(ce["avg_calories"], errors="coerce")
    ce["avg_expenditure"] = pd.to_numeric(ce["avg_expenditure"], errors="coerce")

    ce_m = ce.melt("date", var_name="metric", value_name="value").dropna(subset=["date", "value"])

    if ce_m.empty:
        st.caption("No weekly energy data in the selected date range yet.")
    else:
        base = alt.Chart(ce_m).encode(
            x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max])),
            y=alt.Y(
                "value:Q",
                title="kcal",
                scale=alt.Scale(domain=CALORIES_DOMAIN, clamp=True)
            ),
            color=alt.Color("metric:N", title=""),
            tooltip=[
                alt.Tooltip("date:T", title="Week"),
                alt.Tooltip("metric:N"),
                alt.Tooltip("value:Q", format=".0f"),
            ],
        )
        ce_chart = (base.mark_line(interpolate="monotone") + base.mark_circle(size=90)).properties(height=300)
        st.altair_chart(ce_chart, use_container_width=True)
else:
    st.caption("No weekly energy columns found.")

# B) Energy balance
if "energy_balance" in combined.columns and safe_num(combined["energy_balance"]).notna().any():
    st.subheader("Estimated energy balance (Calories − Expenditure)")
    eb = combined[["date", "energy_balance"]].copy()
    eb["date"] = pd.to_datetime(eb["date"], errors="coerce")
    eb["energy_balance"] = pd.to_numeric(eb["energy_balance"], errors="coerce")
    eb = eb.dropna(subset=["date", "energy_balance"])

    if not eb.empty:
        max_abs = float(eb["energy_balance"].abs().max())
        dom = max(600.0, max_abs)
        dom = (int(dom / 50) + 1) * 50

        eb_chart = alt.Chart(eb).mark_bar().encode(
            x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max])),
            y=alt.Y("energy_balance:Q", title="kcal / day (avg)", scale=alt.Scale(domain=[-dom, dom])),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("energy_balance:Q", format=".0f")]
        ).properties(height=250)

        st.altair_chart(eb_chart, use_container_width=True)

# C) Weight change vs energy balance
sc = combined[["date"]].copy()
if "energy_balance" in combined.columns:
    sc["energy_balance"] = pd.to_numeric(combined["energy_balance"], errors="coerce")
if "weight_change" in combined.columns:
    sc["weight_change"] = pd.to_numeric(combined["weight_change"], errors="coerce")
sc["date"] = pd.to_datetime(combined["date"], errors="coerce")
sc = sc.dropna(subset=["date", "energy_balance", "weight_change"]) if {"energy_balance", "weight_change"}.issubset(sc.columns) else pd.DataFrame()

if not sc.empty:
    st.subheader("Weight change vs energy balance (weekly)")
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

# D) Adherence dashboard — show as percent
st.subheader("Adherence (weekly)")

has_adh = (
    ("protein_adherence_avg" in combined.columns and safe_num(combined["protein_adherence_avg"]).notna().any())
    or
    ("energy_adherence_avg" in combined.columns and safe_num(combined["energy_adherence_avg"]).notna().any())
)

if has_adh:
    adh = combined[["date"]].copy()
    adh["date"] = pd.to_datetime(adh["date"], errors="coerce")

    if "protein_adherence_avg" in combined.columns:
        adh["protein_adherence_avg"] = pd.to_numeric(combined["protein_adherence_avg"], errors="coerce")
    if "energy_adherence_avg" in combined.columns:
        adh["energy_adherence_avg"] = pd.to_numeric(combined["energy_adherence_avg"], errors="coerce")

    adh_m = adh.melt("date", var_name="metric", value_name="value").dropna(subset=["date", "value"])

    if not adh_m.empty:
        adh_m = adh_m.copy()
        adh_m["value_pct"] = adh_m["value"] * 100

        adh_chart = alt.Chart(adh_m).mark_line(interpolate="monotone", point=True).encode(
            x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max])),
            y=alt.Y(
                "value_pct:Q",
                title="Adherence (%)",
                scale=alt.Scale(domain=ADH_PCT_DOMAIN, clamp=True),
            ),
            color=alt.Color("metric:N", title=""),
            tooltip=[
                alt.Tooltip("date:T", title="Week"),
                alt.Tooltip("metric:N", title="Metric"),
                alt.Tooltip("value_pct:Q", title="Adherence (%)", format=".0f"),
            ],
        ).properties(height=260)

        st.altair_chart(adh_chart, use_container_width=True)

    # Keep threshold logic in ratio space (0.9 = 90%)
    p_ok = (combined["protein_adherence_avg"] >= 0.9).mean() if "protein_adherence_avg" in combined.columns else pd.NA
    e_ok = (combined["energy_adherence_avg"] >= 0.9).mean() if "energy_adherence_avg" in combined.columns else pd.NA

    a1, a2 = st.columns(2)
    with a1:
        st.metric("% weeks protein ≥ 90%", metric_or_dash(p_ok * 100 if pd.notna(p_ok) else pd.NA, "{:.0f}%"))
    with a2:
        st.metric("% weeks energy ≥ 90%", metric_or_dash(e_ok * 100 if pd.notna(e_ok) else pd.NA, "{:.0f}%"))
else:
    st.caption("No adherence data yet.")

# E) Lean vs Fat change
st.subheader("Lean vs Fat change (weekly)")
lf = combined[["date"]].copy()
lf["date"] = pd.to_datetime(lf["date"], errors="coerce")
if "lean_change" in combined.columns:
    lf["lean_change"] = pd.to_numeric(combined["lean_change"], errors="coerce")
if "fat_change" in combined.columns:
    lf["fat_change"] = pd.to_numeric(combined["fat_change"], errors="coerce")

if {"lean_change", "fat_change"}.issubset(lf.columns):
    lf_m = lf.melt("date", var_name="metric", value_name="value").dropna(subset=["date", "value"])
    if not lf_m.empty:
        lf_chart = alt.Chart(lf_m).mark_bar().encode(
            x=alt.X("date:T", title="Week", scale=alt.Scale(domain=[x_min, x_max])),
            y=alt.Y("value:Q", title="lbs"),
            color=alt.Color("metric:N", title=""),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("metric:N"), alt.Tooltip("value:Q", format=".2f")]
        ).properties(height=260)
        st.altair_chart(lf_chart, use_container_width=True)
    else:
        st.caption("No change data yet.")
else:
    st.caption("No change data yet.")

# F) Training efficiency
st.subheader("Training efficiency")
has_eff = ("training_minutes_total" in combined.columns and safe_num(combined["training_minutes_total"]).notna().any())
if has_eff:
    te_cols = ["date", "training_minutes_total"]
    if "sets_per_hour" in combined.columns:
        te_cols.append("sets_per_hour")
    if "volume_per_minute" in combined.columns:
        te_cols.append("volume_per_minute")

    te = combined[te_cols].copy()
    te["date"] = pd.to_datetime(te["date"], errors="coerce")
    for c in te_cols:
        if c != "date":
            te[c] = pd.to_numeric(te[c], errors="coerce")

    te_m = te.melt("date", var_name="metric", value_name="value").dropna(subset=["date", "value"])

    if not te_m.empty:
        te_chart = alt.Chart(te_m).mark_line(interpolate="monotone", point=True).encode(
            x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max])),
            y=alt.Y("value:Q", title="Value"),
            color=alt.Color("metric:N", title=""),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("metric:N"), alt.Tooltip("value:Q", format=".2f")]
        ).properties(height=260)
        st.altair_chart(te_chart, use_container_width=True)
else:
    st.caption("No training minutes yet.")

# G) Training load + Lean mass (STACKED CHARTS — Option 02)
st.subheader("Training load and Lean Mass (weekly)")

# Choose training metric to show (prefer volume_total, else sets_total, else minutes)
train_metric = None
for candidate in ["volume_total", "sets_total", "training_minutes_total"]:
    if candidate in combined.columns and safe_num(combined[candidate]).notna().any():
        train_metric = candidate
        break

lm = combined[["date"]].copy()
lm["date"] = pd.to_datetime(lm["date"], errors="coerce")
if "lean_mass" in combined.columns:
    lm["lean_mass"] = pd.to_numeric(combined["lean_mass"], errors="coerce")

tr = combined[["date"]].copy()
tr["date"] = pd.to_datetime(tr["date"], errors="coerce")
if train_metric is not None:
    tr[train_metric] = pd.to_numeric(combined[train_metric], errors="coerce")

# Lean mass chart (fixed 150–220)
lm_plot = lm.dropna(subset=["date", "lean_mass"]) if "lean_mass" in lm.columns else pd.DataFrame()
if not lm_plot.empty:
    lean_chart = alt.Chart(lm_plot).mark_line(interpolate="monotone", point=True).encode(
        x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max])),
        y=alt.Y("lean_mass:Q", title="Lean mass (lbs)", scale=alt.Scale(domain=LEAN_DOMAIN, clamp=True)),
        tooltip=[alt.Tooltip("date:T", title="Week"), alt.Tooltip("lean_mass:Q", format=".2f")]
    ).properties(height=240)
    st.altair_chart(lean_chart, use_container_width=True)
else:
    st.caption("No lean mass data yet (or none in the selected range).")

# Training chart (fixed 70000–90000 for volume_total; auto fallback if values don't fit)
if train_metric is not None:
    tr_plot = tr.dropna(subset=["date", train_metric])
    if not tr_plot.empty:
        title_map = {
            "volume_total": "Training volume (lbs·reps)",
            "sets_total": "Total sets",
            "training_minutes_total": "Training minutes",
        }
        y_title = title_map.get(train_metric, train_metric)

        # Use your preferred domain only when the metric is volume_total; otherwise let it autoscale
        if train_metric == "volume_total":
            dom = safe_domain(tr_plot[train_metric], VOLUME_DOMAIN)
            y_scale = alt.Scale(domain=dom, clamp=True)
        else:
            y_scale = alt.Scale()

        train_chart = alt.Chart(tr_plot).mark_line(interpolate="monotone", point=True).encode(
            x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max])),
            y=alt.Y(f"{train_metric}:Q", title=y_title, scale=y_scale),
            tooltip=[alt.Tooltip("date:T", title="Week"), alt.Tooltip(f"{train_metric}:Q", format=".2f")]
        ).properties(height=240)

        st.altair_chart(train_chart, use_container_width=True)
        st.caption(f"Training metric shown: {train_metric}")
    else:
        st.caption("No training data in the selected range yet.")
else:
    st.caption("No training metric available yet (volume_total / sets_total / training_minutes_total).")

# ============================================================
# Food patterns
# ============================================================
st.header("🍽️ Food patterns")

food = load_sheet(WS_FOOD_LOG)

if food.empty:
    st.caption(f"{WS_FOOD_LOG} is empty (or missing).")
else:
    food = food.copy()

    staging_cols = {"date", "time", "food_name", "calories_kcal", "protein_g", "carbs_g", "fat_g"}
    raw_cols = {"Date", "Time", "Food Name", "Calories (kcal)", "Protein (g)", "Carbs (g)", "Fat (g)"}

    if staging_cols.issubset(set(food.columns)):
        food = food.rename(columns={
            "calories_kcal": "calories",
            "protein_g": "protein",
            "carbs_g": "carbs",
            "fat_g": "fat",
        })
    elif raw_cols.issubset(set(food.columns)):
        food = food.rename(columns={
            "Date": "date",
            "Time": "time",
            "Food Name": "food_name",
            "Calories (kcal)": "calories",
            "Protein (g)": "protein",
            "Carbs (g)": "carbs",
            "Fat (g)": "fat",
        })
    else:
        st.error(f"Food log columns not recognized. Columns found: {list(food.columns)}")
        st.stop()

    food = normalise_date_col(food, "date")
    for c in ["calories", "protein", "carbs", "fat"]:
        if c in food.columns:
            food[c] = pd.to_numeric(food[c], errors="coerce")

    if "food_name" in food.columns:
        food["food_name"] = food["food_name"].astype(str).str.strip()

    food_view = filter_range(food, start_date, end_date_for_weekly, "date")
    if food_view.empty:
        st.caption("No food rows in the selected date range.")
    else:
        st.subheader("Weekday vs weekend (avg daily totals)")
        daily_food = (
            food_view.groupby("date", as_index=False)[["calories", "protein", "carbs", "fat"]]
            .sum(numeric_only=True)
        )
        daily_food["weekday"] = daily_food["date"].dt.weekday
        daily_food["is_weekend"] = daily_food["weekday"].isin([5, 6])

        weekend_cmp = (
            daily_food.groupby("is_weekend")[["calories", "protein", "carbs", "fat"]]
            .mean(numeric_only=True)
            .reset_index()
        )
        weekend_cmp["day_type"] = weekend_cmp["is_weekend"].map({False: "Weekday", True: "Weekend"})
        st.dataframe(weekend_cmp[["day_type", "calories", "protein", "carbs", "fat"]].round(1), hide_index=True)

        st.subheader("Top foods (by frequency)")
        top_freq = food_view["food_name"].value_counts().head(15).reset_index()
        top_freq.columns = ["food_name", "count"]
        st.dataframe(top_freq, hide_index=True)

        st.subheader("Top foods (by totals)")
        colX, colY = st.columns(2)

        with colX:
            st.caption("By total calories")
            top_cal = (
                food_view.groupby("food_name", as_index=False)["calories"]
                .sum()
                .sort_values("calories", ascending=False)
                .head(15)
            )
            st.dataframe(top_cal, hide_index=True)

        with colY:
            st.caption("By total protein")
            top_pro = (
                food_view.groupby("food_name", as_index=False)["protein"]
                .sum()
                .sort_values("protein", ascending=False)
                .head(15)
            )
            st.dataframe(top_pro, hide_index=True)

        st.subheader("Most common foods by time of day")
        if "time" in food_view.columns and food_view["time"].notna().any():
            fv = food_view.copy()
            t1 = pd.to_datetime(fv["time"], format="%I:%M %p", errors="coerce")
            t2 = pd.to_datetime(fv["time"], format="%H:%M", errors="coerce")
            t = t1.fillna(t2)
            fv["hour"] = t.dt.hour

            def bucket(h):
                if pd.isna(h):
                    return "Unknown"
                h = int(h)
                if 5 <= h < 11:
                    return "Breakfast"
                if 11 <= h < 15:
                    return "Lunch"
                if 15 <= h < 19:
                    return "Dinner"
                return "Evening"

            fv["meal_bucket"] = fv["hour"].apply(bucket)

            for bucket_name in ["Breakfast", "Lunch", "Dinner", "Evening"]:
                sub = fv[fv["meal_bucket"] == bucket_name]
                if sub.empty:
                    continue
                st.caption(bucket_name)
                b = sub["food_name"].value_counts().head(10).reset_index()
                b.columns = ["food_name", "count"]
                st.dataframe(b, hide_index=True)
        else:
            st.caption("No time data available in the selected range.")

        st.subheader("Daily macros over time (grams)")
        daily_macros = (
            food_view.groupby("date", as_index=False)[["protein", "carbs", "fat"]]
            .sum(numeric_only=True)
            .dropna()
        )
        if not daily_macros.empty:
            macro_m = daily_macros.melt("date", var_name="macro", value_name="grams").dropna()
            macro_chart = (
                alt.Chart(macro_m)
                .mark_line(interpolate="monotone", point=True)
                .encode(
                    x=alt.X("date:T", title="Date"),
                    y=alt.Y("grams:Q", title="grams"),
                    color=alt.Color("macro:N", title=""),
                    tooltip=[
                        alt.Tooltip("date:T"),
                        alt.Tooltip("macro:N"),
                        alt.Tooltip("grams:Q", format=".0f"),
                    ],
                )
                .properties(height=260)
            )
            st.altair_chart(macro_chart, use_container_width=True)
