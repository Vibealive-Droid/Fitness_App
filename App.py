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

# Your current "Food Log" staging (it is actually a daily summary, not meal-level)
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

DEBUG = st.sidebar.checkbox("Debug", value=False)

# ----------------------------
# Load BODY weekly
# ----------------------------
body = load_sheet(WS_BODY_WEEKLY)
body = body.rename(columns={"date_time": "date"}) if "date_time" in body.columns and "date" not in body.columns else body
body = normalise_date_col(body, "date")
body = align_to_monday(body, "date")
body = to_num(body, ["weight", "body_fat", "fat_free_mass"])

if not body.empty:
    body["fat_mass"] = (body["weight"] * (body["body_fat"] / 100)).round(2)
    body["lean_mass"] = (body["weight"] - body["fat_mass"]).round(2)

# ----------------------------
# Date range selector (bulletproof + allows "report Monday")
# ----------------------------
st.subheader("Date range")

if body.empty:
    st.info("No body data yet.")
    st.stop()

min_date = body["date"].min().date()
max_body_date = body["date"].max().date()
default_start = max(min_date, (pd.Timestamp(max_body_date) - pd.DateOffset(months=12)).date())

col1, col2, col3 = st.columns([1, 1, 2])

with col3:
    include_report_monday = st.checkbox(
        "Include current week (report Monday)",
        value=True,
        help="Weekly Energy/Training rows are stamped on the next Monday. Enable to include that row in filters/charts.",
        key="include_report_monday",
    )

# allow selecting past last body date if include_report_monday is enabled
max_end_allowed = max_body_date
if include_report_monday:
    max_end_allowed = (pd.Timestamp(max_body_date) + pd.Timedelta(days=7)).date()

with col1:
    start_date = st.date_input(
        "Start date",
        value=default_start,
        min_value=min_date,
        max_value=max_end_allowed,
        key="start_date",
    )

with col2:
    end_date = st.date_input(
        "End date",
        value=max_body_date,
        min_value=min_date,
        max_value=max_end_allowed,
        key="end_date",
    )

if start_date is None or end_date is None:
    st.warning("Please select both a start and end date.")
    st.stop()

if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()

# what we actually use to filter weekly tables
end_date_for_weekly = end_date
if include_report_monday:
    end_date_for_weekly = (pd.Timestamp(end_date) + pd.Timedelta(days=7)).date()

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

# X bounds: use requested window (not just body availability)
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
        .properties(
            height=360,
            padding={"left": 10, "right": 10, "top": 10, "bottom": 40},
        )
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
            y=alt.Y(
                "body_fat:Q",
                title="Body Fat %",
                scale=alt.Scale(domain=[5, 30]),
            ),
            tooltip=[
                alt.Tooltip("date:T", title="Date"),
                alt.Tooltip("body_fat:Q", title="Body Fat %", format=".2f"),
            ],
        )
    .properties(
        height=300,
        padding={"left": 10, "right": 10, "top": 10, "bottom": 40},
        )

                    

# ============================================================
# Energy + Training
# ============================================================
st.header("⚡ Energy Balance + Training")

# ----------------------------
# Join using UNION of dates (so report-Monday rows show)
# ----------------------------
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

# Derived deltas (only meaningful when body exists)
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

# ----------------------------
# Debug
# ----------------------------
if DEBUG:
    st.subheader("Debug: loaded frames")
    st.caption(f"Body rows in range: {len(body_view)} | Energy rows in range: {len(energy_view)} | Training rows in range: {len(train_view)}")
    st.write("Weekly_Training columns:", list(train.columns) if not train.empty else [])
    st.write("Weekly_Energy columns:", list(energy.columns) if not energy.empty else [])
    if "sets_total" in combined.columns:
        st.caption(f"combined non-null sets_total: {combined['sets_total'].notna().sum()}")

# ----------------------------
# This week so far (Daily_Energy + Staging_Workout_Log)
# ----------------------------
st.subheader("This week so far")

today = pd.Timestamp.today().normalize()
week_start_dt = today - pd.to_timedelta(today.weekday(), unit="D")  # Monday
week_start = week_start_dt.date()
week_end = today.date()

daily_energy = load_sheet(WS_DAILY_ENERGY)
daily_energy = normalise_date_col(daily_energy, "date")
daily_energy = to_num(daily_energy, [
    "days_logged_flag", "calories", "expenditure", "calorie_target",
    "protein_g", "carbs_g", "fat_g",
    "protein_adherence", "energy_adherence",
    "steps"
])
daily_energy_week = filter_range(daily_energy, week_start, week_end, "date")

workout_log = load_sheet(WS_WORKOUT_LOG)
workout_log = normalise_date_col(workout_log, "date")
workout_log = to_num(workout_log, ["workout_duration", "weight_lb", "reps", "rir"])
workout_week = filter_range(workout_log, week_start, week_end, "date")

c1, c2, c3, c4 = st.columns(4)

# Energy so far this week
if not daily_energy_week.empty and "calories" in daily_energy_week.columns:
    logged = daily_energy_week.copy()
    if "days_logged_flag" in logged.columns:
        logged = logged[safe_num(logged["days_logged_flag"]).fillna(0) == 1].copy()

    days_logged = int(safe_num(logged["days_logged_flag"]).sum()) if "days_logged_flag" in logged.columns else int(len(logged))
    avg_cals = logged["calories"].mean() if "calories" in logged.columns else pd.NA
    avg_exp = logged["expenditure"].mean() if "expenditure" in logged.columns else pd.NA
    avg_bal = (avg_cals - avg_exp) if pd.notna(avg_cals) and pd.notna(avg_exp) else pd.NA
    avg_steps = logged["steps"].mean() if "steps" in logged.columns else pd.NA
    avg_p_adh = logged["protein_adherence"].mean() if "protein_adherence" in logged.columns else pd.NA
    avg_e_adh = logged["energy_adherence"].mean() if "energy_adherence" in logged.columns else pd.NA

    with c1:
        st.metric("Days logged", days_logged)
    with c2:
        st.metric("Avg calories", f"{avg_cals:.0f}" if pd.notna(avg_cals) else "—")
        st.metric("Avg expenditure", f"{avg_exp:.0f}" if pd.notna(avg_exp) else "—")
    with c3:
        st.metric("Avg balance", f"{avg_bal:.0f}" if pd.notna(avg_bal) else "—")
        st.metric("Avg steps", f"{avg_steps:.0f}" if pd.notna(avg_steps) else "—")
    with c4:
        st.metric("Protein adherence", f"{avg_p_adh:.3f}" if pd.notna(avg_p_adh) else "—")
        st.metric("Energy adherence", f"{avg_e_adh:.3f}" if pd.notna(avg_e_adh) else "—")
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
    wk["session_key"] = wk["date"].dt.date.astype(str) + "|" + wk["workout"].astype(str).str.strip().str.lower()

    session_minutes = wk.groupby("session_key")["workout_duration"].max() / 60.0
    minutes_total = float(session_minutes.sum())
    workouts_completed = int(session_minutes.shape[0])

    stypes = wk["set_type"].astype(str).str.strip().str.lower() if "set_type" in wk.columns else pd.Series([], dtype=str)
    wk_working = wk[stypes.isin(["standard set", "failure set"])].copy() if len(stypes) else wk.copy()

    sets_total = int(len(wk_working))
    volume_total = (safe_num(wk_working["weight_lb"]) * safe_num(wk_working["reps"])).sum() if ("weight_lb" in wk_working.columns and "reps" in wk_working.columns) else pd.NA
    avg_rir = safe_num(wk_working["rir"]).mean() if "rir" in wk_working.columns else pd.NA
    sets_per_hour = sets_total / (minutes_total / 60.0) if minutes_total > 0 else pd.NA

    st.caption("Training this week so far")
    t1, t2, t3, t4 = st.columns(4)
    with t1:
        st.metric("Workouts", workouts_completed)
        st.metric("Minutes", f"{minutes_total:.1f}")
    with t2:
        st.metric("Sets", sets_total)
        st.metric("Avg RIR", f"{avg_rir:.2f}" if pd.notna(avg_rir) else "—")
    with t3:
        st.metric("Volume (lbs·reps)", f"{volume_total:.0f}" if pd.notna(volume_total) else "—")
    with t4:
        st.metric("Sets/hour", f"{sets_per_hour:.1f}" if pd.notna(sets_per_hour) else "—")
else:
    st.caption("No workout log rows for this week yet.")

# ----------------------------
# Combined table
# ----------------------------
with st.expander("Combined weekly table (filtered)", expanded=False):
    combined_table = date_for_table(combined)

    # clean rounding for display
    round0 = ["avg_calories", "avg_expenditure", "avg_calorie_target", "avg_calorie_delta", "energy_balance", "avg_steps",
              "training_minutes_total", "avg_workout_minutes", "volume_total"]
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

    st.dataframe(combined_table)

# ============================================================
# Charts
# ============================================================

# A) Calories vs Expenditure
st.subheader("Calories vs Expenditure (weekly)")
if ("avg_calories" in combined.columns) and ("avg_expenditure" in combined.columns) and combined["avg_calories"].notna().any():
    ce = combined[["date", "avg_calories", "avg_expenditure"]].copy()
    ce_m = ce.melt("date", var_name="metric", value_name="value").dropna()

    ce_chart = alt.Chart(ce_m).mark_line(interpolate="monotone").encode(
        x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max])),
        y=alt.Y("value:Q", title="kcal"),
        color=alt.Color("metric:N", title=""),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("metric:N"), alt.Tooltip("value:Q", format=".0f")]
    ).properties(height=300)

    st.altair_chart(ce_chart, use_container_width=True)
else:
    st.caption("No weekly energy data in the selected date range yet.")

# B) Energy balance (symmetric around 0, minimum ±600)
if "energy_balance" in combined.columns and combined["energy_balance"].notna().any():
    st.subheader("Estimated energy balance (Calories − Expenditure)")
    eb = combined[["date", "energy_balance"]].dropna()

    max_abs = float(eb["energy_balance"].abs().max())
    dom = max(600.0, max_abs)
    dom = (int(dom / 50) + 1) * 50  # round up to nearest 50

    eb_chart = alt.Chart(eb).mark_bar().encode(
        x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max])),
        y=alt.Y("energy_balance:Q", title="kcal / day (avg)", scale=alt.Scale(domain=[-dom, dom])),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("energy_balance:Q", format=".0f")]
    ).properties(height=250)

    st.altair_chart(eb_chart, use_container_width=True)

# C) Weight change vs energy balance
if "energy_balance" in combined.columns and combined["energy_balance"].notna().any() and "weight_change" in combined.columns:
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

# D) Adherence dashboard
st.subheader("Adherence (weekly)")
has_adh = (("protein_adherence_avg" in combined.columns and combined["protein_adherence_avg"].notna().any()) or
           ("energy_adherence_avg" in combined.columns and combined["energy_adherence_avg"].notna().any()))
if has_adh:
    adh = combined[["date"]].copy()
    if "protein_adherence_avg" in combined.columns:
        adh["protein_adherence_avg"] = combined["protein_adherence_avg"]
    if "energy_adherence_avg" in combined.columns:
        adh["energy_adherence_avg"] = combined["energy_adherence_avg"]

    adh_m = adh.melt("date", var_name="metric", value_name="value").dropna()
    adh_chart = alt.Chart(adh_m).mark_line(interpolate="monotone").encode(
        x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max])),
        y=alt.Y("value:Q", title="Adherence (ratio)", scale=alt.Scale(domain=[0, 2])),
        color=alt.Color("metric:N", title=""),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("metric:N"), alt.Tooltip("value:Q", format=".3f")]
    ).properties(height=260)
    st.altair_chart(adh_chart, use_container_width=True)

    p_ok = (combined["protein_adherence_avg"] >= 0.9).mean() if "protein_adherence_avg" in combined.columns else pd.NA
    e_ok = (combined["energy_adherence_avg"] >= 0.9).mean() if "energy_adherence_avg" in combined.columns else pd.NA
    a1, a2 = st.columns(2)
    with a1:
        st.metric("% weeks protein ≥ 0.9", f"{p_ok*100:.0f}%" if pd.notna(p_ok) else "—")
    with a2:
        st.metric("% weeks energy ≥ 0.9", f"{e_ok*100:.0f}%" if pd.notna(e_ok) else "—")
else:
    st.caption("No adherence data yet.")

# E) Lean vs Fat change decomposition
st.subheader("Lean vs Fat change (weekly)")
if ("lean_change" in combined.columns and combined["lean_change"].notna().any()) or ("fat_change" in combined.columns and combined["fat_change"].notna().any()):
    lf = combined[["date", "lean_change", "fat_change"]].copy()
    lf_m = lf.melt("date", var_name="metric", value_name="value").dropna()
    lf_chart = alt.Chart(lf_m).mark_bar().encode(
        x=alt.X("date:T", title="Week", scale=alt.Scale(domain=[x_min, x_max])),
        y=alt.Y("value:Q", title="lbs"),
        color=alt.Color("metric:N", title=""),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("metric:N"), alt.Tooltip("value:Q", format=".2f")]
    ).properties(height=260)
    st.altair_chart(lf_chart, use_container_width=True)
else:
    st.caption("No change data yet.")

# F) Training minutes + density
st.subheader("Training efficiency")
has_eff = ("training_minutes_total" in combined.columns and combined["training_minutes_total"].notna().any())
if has_eff:
    te_cols = ["date", "training_minutes_total"]
    if "sets_per_hour" in combined.columns:
        te_cols.append("sets_per_hour")
    if "volume_per_minute" in combined.columns:
        te_cols.append("volume_per_minute")
    te = combined[te_cols].copy().dropna(subset=["training_minutes_total"])
    te_m = te.melt("date", var_name="metric", value_name="value").dropna()

    te_chart = alt.Chart(te_m).mark_line(interpolate="monotone").encode(
        x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max])),
        y=alt.Y("value:Q", title="Value"),
        color=alt.Color("metric:N", title=""),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("metric:N"), alt.Tooltip("value:Q", format=".2f")]
    ).properties(height=260)

    st.altair_chart(te_chart, use_container_width=True)
else:
    st.caption("No training minutes yet.")

# G) Training vs Lean Mass
st.subheader("Training load vs Lean Mass (weekly)")
has_training_metric = (
    ("volume_total" in combined.columns and combined["volume_total"].notna().any()) or
    ("sets_total" in combined.columns and combined["sets_total"].notna().any())
)
if has_training_metric and ("lean_mass" in combined.columns):
    metric = "volume_total" if ("volume_total" in combined.columns and combined["volume_total"].notna().any()) else "sets_total"
    tl = combined[["date", "lean_mass", metric]].copy().dropna(subset=["lean_mass"])
    tl_m = tl.melt("date", var_name="metric", value_name="value").dropna()

    tl_chart = alt.Chart(tl_m).mark_line(interpolate="monotone").encode(
        x=alt.X("date:T", title="Date", scale=alt.Scale(domain=[x_min, x_max])),
        y=alt.Y("value:Q", title="Value"),
        color=alt.Color("metric:N", title=""),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("metric:N"), alt.Tooltip("value:Q", format=".2f")]
    ).properties(height=300)

    st.altair_chart(tl_chart, use_container_width=True)
else:
    st.caption("No training data in the selected date range yet.")

# ============================================================
# Food patterns (based on your current "FoodLog" staging columns)
# NOTE: Your current Staging_MacroFactor_FoodLog is a daily summary (no Food Name),
# so we can only do PATTERNS (weekday/weekend, highest calorie days, macro splits).
# If you want top food items, you need meal-level export columns (Food Name, Time, etc.).
# ============================================================
st.header("🍽️ Food patterns (from FoodLog tab)")

food = load_sheet(WS_FOOD_LOG)
if food.empty:
    st.caption("FoodLog tab is empty (or not present).")
else:
    # Your header row:
    # Date, Expenditure, Trend Weight, Weight, Calories, Protein, Carbs, Fat, Total Calories, Total Protein, Total Carbs, Total Fat, Steps
    # Normalize date + numeric
    if "Date" in food.columns:
        food = food.rename(columns={"Date": "date"})
    food = normalise_date_col(food, "date")
    food = to_num(food, [
        "Expenditure", "Trend Weight", "Weight",
        "Calories", "Protein", "Carbs", "Fat",
        "Total Calories", "Total Protein", "Total Carbs", "Total Fat",
        "Steps"
    ])

    food_view = filter_range(food, start_date, end_date_for_weekly, "date")

    if food_view.empty:
        st.caption("No FoodLog rows in the selected range.")
    else:
        st.subheader("Weekday vs weekend (avg)")
        fv = food_view.copy()
        fv["weekday"] = fv["date"].dt.weekday
        fv["is_weekend"] = fv["weekday"].isin([5, 6])

        grp = fv.groupby("is_weekend")[["Calories", "Protein", "Carbs", "Fat"]].mean(numeric_only=True).reset_index()
        grp["day_type"] = grp["is_weekend"].map({False: "Weekday", True: "Weekend"})
        st.dataframe(grp[["day_type", "Calories", "Protein", "Carbs", "Fat"]].round(1))

        st.subheader("Top days by calories")
        top_days = fv.sort_values("Calories", ascending=False)[["date", "Calories", "Protein", "Carbs", "Fat"]].head(10).copy()
        top_days = date_for_table(top_days, "date")
        st.dataframe(top_days)

        st.subheader("Macro split over time (daily)")
        # stack macros as calories-equivalent is overkill; just show grams lines
        macro_line = fv[["date", "Protein", "Carbs", "Fat"]].copy().dropna()
        macro_m = macro_line.melt("date", var_name="macro", value_name="grams").dropna()
        macro_chart = alt.Chart(macro_m).mark_line(interpolate="monotone").encode(
            x=alt.X("date:T", title="Date"),
            y=alt.Y("grams:Q", title="grams"),
            color=alt.Color("macro:N", title=""),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("macro:N"), alt.Tooltip("grams:Q", format=".0f")]
        ).properties(height=260)
        st.altair_chart(macro_chart, use_container_width=True)

        # If meal-level fields ever exist, we can do top foods
        if "Food Name" in food.columns:
            st.info("Meal-level 'Food Name' detected — we can add top foods charts here.")

# ============================================================
# Manual inputs (starter UI)
# ============================================================
st.header("📝 Manual inputs (starter)")

st.caption("These inputs are not saved yet. If you want, we can add a Daily_Notes tab + write-back.")
sleep_hours = st.number_input("Sleep hours (today)", min_value=0.0, max_value=24.0, value=0.0, step=0.25)
mood = st.selectbox("Mood (optional)", ["", "Great", "Good", "OK", "Low", "Rough"])
note = st.text_input("Quick note (optional)", value="")

if DEBUG:
    st.subheader("Debug: manual inputs")
    st.write({"sleep_hours": sleep_hours, "mood": mood, "note": note})






