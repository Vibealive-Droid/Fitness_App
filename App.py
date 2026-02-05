import streamlit as st
import pandas as pd
import altair as alt
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# ============================================================
# Streamlit setup
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

# ============================================================
# CONFIG: sheet/tab names (MUST EXIST)
# ============================================================
# Raw staging
WS_STAGING_RENPHO = "Staging_Renpho"
WS_DAILY_ENERGY = "Daily_Energy"
WS_STAGING_MF_FOODLOG = "Staging_MacroFactor_FoodLog"
WS_STAGING_WORKOUT_LOG = "Staging_Workout_Log"
WS_STAGING_MUSCLE_SETS = "Staging_Muscle_Sets"
WS_STAGING_MUSCLE_VOLUME = "Staging_Muscle_Volume"

# Aggregates (optional, but recommended)
WS_WEEKLY_BODYCOMP = "Weekly_BodyComp"
WS_WEEKLY_ENERGY = "Weekly_Energy"
WS_WEEKLY_TRAINING = "Weekly_Training"

# ============================================================
# Helpers: Google Sheet <-> DataFrame
# ============================================================
def ws_to_df(ws_name: str) -> pd.DataFrame:
    ws = sh.worksheet(ws_name)
    vals = ws.get_all_values()
    if not vals or len(vals) < 2:
        return pd.DataFrame(columns=vals[0] if vals else [])
    df = pd.DataFrame(vals[1:], columns=vals[0])
    return df

def df_to_ws_overwrite(ws_name: str, df: pd.DataFrame):
    ws = sh.worksheet(ws_name)
    df = df.copy()
    df = df.fillna("")
    values = [df.columns.tolist()] + df.astype(str).values.tolist()
    ws.clear()
    ws.update(values)

def append_dedupe(ws_name: str, new_df: pd.DataFrame, key_cols: list[str]) -> pd.DataFrame:
    """
    Append new_df into ws_name and dedupe by key_cols (keeping last).
    Returns final dataframe written.
    """
    existing = ws_to_df(ws_name)

    # Normalise columns: union
    all_cols = list(dict.fromkeys(list(existing.columns) + list(new_df.columns)))
    existing = existing.reindex(columns=all_cols)
    new_df = new_df.reindex(columns=all_cols)

    merged = pd.concat([existing, new_df], ignore_index=True)

    # If key cols missing, just overwrite with merged
    for k in key_cols:
        if k not in merged.columns:
            df_to_ws_overwrite(ws_name, merged)
            return merged

    merged = merged.drop_duplicates(subset=key_cols, keep="last").reset_index(drop=True)
    df_to_ws_overwrite(ws_name, merged)
    return merged

def safe_to_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def safe_to_date(df: pd.DataFrame, col: str, fmt=None) -> pd.DataFrame:
    df = df.copy()
    if col in df.columns:
        if fmt:
            df[col] = pd.to_datetime(df[col], format=fmt, errors="coerce").dt.date
        else:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    return df

# ============================================================
# Parsers: MacroFactor XLSX + RENPHO CSV
# ============================================================
def parse_macrofactor_xlsx(xlsx_file) -> dict[str, pd.DataFrame]:
    """
    Expected sheet names from MacroFactor export:
      - Quick Export
      - Food Log
      - Workout Log
      - Muscle Groups - Sets
      - Muscle Groups - Volume
    """
    xl = pd.ExcelFile(xlsx_file)
    out = {}

    if "Quick Export" in xl.sheet_names:
        df = xl.parse("Quick Export")
        # Date in MF export is typically YYYY-MM-DD
        df = safe_to_date(df, "Date")
        out["daily_energy"] = df

    if "Food Log" in xl.sheet_names:
        df = xl.parse("Food Log")
        df = safe_to_date(df, "Date")
        out["food_log"] = df

    if "Workout Log" in xl.sheet_names:
        df = xl.parse("Workout Log")
        df = safe_to_date(df, "Date")
        out["workout_log"] = df

    if "Muscle Groups - Sets" in xl.sheet_names:
        df = xl.parse("Muscle Groups - Sets")
        df = safe_to_date(df, "Date")
        out["muscle_sets"] = df

    if "Muscle Groups - Volume" in xl.sheet_names:
        df = xl.parse("Muscle Groups - Volume")
        df = safe_to_date(df, "Date")
        out["muscle_volume"] = df

    return out

def parse_renpho_csv(csv_file) -> pd.DataFrame:
    df = pd.read_csv(csv_file)
    # RENPHO date example: 2026.02.04
    df = safe_to_date(df, "Date", fmt="%Y.%m.%d")
    return df

# ============================================================
# Basic Aggregations (weekly)
# ============================================================
def make_week_start(d: pd.Series) -> pd.Series:
    # Monday-week
    dd = pd.to_datetime(d, errors="coerce")
    return (dd - pd.to_timedelta(dd.dt.weekday, unit="D")).dt.date

def build_weekly_energy(daily: pd.DataFrame) -> pd.DataFrame:
    df = daily.copy()
    if "Date" not in df.columns:
        return pd.DataFrame()
    df["week"] = make_week_start(df["Date"])

    numeric_cols = [
        "Calories (kcal)", "Expenditure", "Steps",
        "Protein (g)", "Carbs (g)", "Fat (g)",
        "Target Calories (kcal)", "Target Protein (g)", "Target Carbs (g)", "Target Fat (g)",
    ]
    df = safe_to_numeric(df, numeric_cols)

    agg = {
        "Calories (kcal)": "mean",
        "Expenditure": "mean",
        "Steps": "mean",
        "Protein (g)": "mean",
        "Carbs (g)": "mean",
        "Fat (g)": "mean",
        "Target Calories (kcal)": "mean",
        "Target Protein (g)": "mean",
        "Target Carbs (g)": "mean",
        "Target Fat (g)": "mean",
    }
    wk = df.groupby("week", as_index=False).agg(agg)
    wk = wk.rename(columns={"week": "date"})
    return wk

def build_weekly_bodycomp(renpho: pd.DataFrame) -> pd.DataFrame:
    df = renpho.copy()
    if "Date" not in df.columns:
        return pd.DataFrame()
    df["week"] = make_week_start(df["Date"])

    # match your screenshots
    want = {
        "Weight(lb)": "weight",
        "Body Fat(%)": "body_fat",
        "Fat-Free Mass(lb)": "fat_free_mass",
        "Muscle Mass(lb)": "muscle_mass",
    }
    for k in list(want.keys()):
        if k not in df.columns:
            want.pop(k, None)

    cols = ["week"] + list(want.keys())
    df = df[cols].copy()
    df = safe_to_numeric(df, list(want.keys()))

    wk = df.groupby("week", as_index=False).mean(numeric_only=True)
    wk = wk.rename(columns={"week": "date", **want})
    return wk

def build_weekly_training(workout_log: pd.DataFrame) -> pd.DataFrame:
    df = workout_log.copy()
    if "Date" not in df.columns:
        return pd.DataFrame()

    df["week"] = make_week_start(df["Date"])
    # Make numbers
    df = safe_to_numeric(df, ["Weight (lbs)", "Reps", "RIR", "Workout Duration"])

    # total sets = count of rows where Exercise is not null (each row is a set in MF export)
    df["is_set"] = df["Exercise"].notna()

    # volume estimate: weight * reps (ignore bodyweight/unweighted rows)
    df["set_volume"] = df["Weight (lbs)"] * df["Reps"]

    wk = df.groupby("week", as_index=False).agg(
        workouts=("Workout", lambda s: s.dropna().nunique()),
        minutes=("Workout Duration", "sum"),
        sets=("is_set", "sum"),
        avg_rir=("RIR", "mean"),
        volume_total=("set_volume", "sum"),
    )
    wk = wk.rename(columns={"week": "date"})
    return wk

# ============================================================
# Upload & process section
# ============================================================
with st.sidebar:
    st.header("📥 Import data")
    mf_file = st.file_uploader("MacroFactor export (.xlsx)", type=["xlsx"])
    renpho_file = st.file_uploader("RENPHO export (.csv)", type=["csv"])

    do_process = st.button("Process & write to Google Sheet")

if do_process:
    if not mf_file and not renpho_file:
        st.warning("Upload at least one file (MacroFactor .xlsx and/or RENPHO .csv).")

    # MacroFactor
    if mf_file:
        mf = parse_macrofactor_xlsx(mf_file)

        if "daily_energy" in mf:
            daily = mf["daily_energy"].copy()
            # Keep Date + key cols first (nice for sheets)
            key = ["Date"]
            append_dedupe(WS_DAILY_ENERGY, daily, key_cols=key)
            st.success(f"✅ Updated {WS_DAILY_ENERGY}")

        if "food_log" in mf:
            food = mf["food_log"].copy()
            # dedupe by a practical unique key
            key = ["Date", "Time", "Food Name", "Serving Qty", "Serving Weight (g)"]
            append_dedupe(WS_STAGING_MF_FOODLOG, food, key_cols=key)
            st.success(f"✅ Updated {WS_STAGING_MF_FOODLOG}")

        if "workout_log" in mf:
            wl = mf["workout_log"].copy()
            key = ["Date", "Workout", "Exercise", "Set Type", "Weight (lbs)", "Reps", "RIR"]
            append_dedupe(WS_STAGING_WORKOUT_LOG, wl, key_cols=key)
            st.success(f"✅ Updated {WS_STAGING_WORKOUT_LOG}")

        if "muscle_sets" in mf:
            ms = mf["muscle_sets"].copy()
            key = ["Date"]
            append_dedupe(WS_STAGING_MUSCLE_SETS, ms, key_cols=key)
            st.success(f"✅ Updated {WS_STAGING_MUSCLE_SETS}")

        if "muscle_volume" in mf:
            mv = mf["muscle_volume"].copy()
            key = ["Date"]
            append_dedupe(WS_STAGING_MUSCLE_VOLUME, mv, key_cols=key)
            st.success(f"✅ Updated {WS_STAGING_MUSCLE_VOLUME}")

    # RENPHO
    if renpho_file:
        r = parse_renpho_csv(renpho_file)
        key = ["Date", "Time", "Weight(lb)"]
        append_dedupe(WS_STAGING_RENPHO, r, key_cols=key)
        st.success(f"✅ Updated {WS_STAGING_RENPHO}")

# ============================================================
# Load data from Sheets for dashboard
# ============================================================
daily_energy = ws_to_df(WS_DAILY_ENERGY)
food_log = ws_to_df(WS_STAGING_MF_FOODLOG)
workout_log = ws_to_df(WS_STAGING_WORKOUT_LOG)
muscle_sets = ws_to_df(WS_STAGING_MUSCLE_SETS)
muscle_volume = ws_to_df(WS_STAGING_MUSCLE_VOLUME)
renpho = ws_to_df(WS_STAGING_RENPHO)

# Parse dates back
daily_energy = safe_to_date(daily_energy, "Date")
food_log = safe_to_date(food_log, "Date")
workout_log = safe_to_date(workout_log, "Date")
muscle_sets = safe_to_date(muscle_sets, "Date")
muscle_volume = safe_to_date(muscle_volume, "Date")
renpho = safe_to_date(renpho, "Date")

# ============================================================
# Build weekly
# ============================================================
weekly_energy = build_weekly_energy(daily_energy) if len(daily_energy) else pd.DataFrame()
weekly_training = build_weekly_training(workout_log) if len(workout_log) else pd.DataFrame()
weekly_bodycomp = build_weekly_bodycomp(renpho) if len(renpho) else pd.DataFrame()

# ============================================================
# Date range controls
# ============================================================
st.subheader("Date range")

quick = st.selectbox(
    "Quick range",
    ["Last week (Mon–Sun)", "Last 4 weeks", "Last 12 weeks", "Custom"],
    index=0
)

def infer_range():
    today = datetime.now().date()
    if quick == "Last week (Mon–Sun)":
        # last complete Mon-Sun
        end = today - pd.to_timedelta(today.weekday() + 1, unit="D")
        start = end - pd.to_timedelta(6, unit="D")
        return start, end
    if quick == "Last 4 weeks":
        end = today
        start = today - pd.to_timedelta(27, unit="D")
        return start, end
    if quick == "Last 12 weeks":
        end = today
        start = today - pd.to_timedelta(83, unit="D")
        return start, end
    return None, None

default_start, default_end = infer_range()

if quick == "Custom":
    start_date = st.date_input("Start date")
    end_date = st.date_input("End date")
else:
    start_date = default_start
    end_date = default_end
    st.caption(f"Using: {start_date} → {end_date}")

# ============================================================
# Filter
# ============================================================
def filter_by_date(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    if df.empty or date_col not in df.columns or not start_date or not end_date:
        return df
    return df[(pd.to_datetime(df[date_col]) >= pd.to_datetime(start_date)) &
              (pd.to_datetime(df[date_col]) <= pd.to_datetime(end_date))].copy()

weekly_energy_f = filter_by_date(weekly_energy, "date")
weekly_training_f = filter_by_date(weekly_training, "date")
weekly_bodycomp_f = filter_by_date(weekly_bodycomp, "date")
daily_energy_f = filter_by_date(daily_energy, "Date")
food_log_f = filter_by_date(food_log, "Date")

# ============================================================
# This week so far (Mon-week)
# ============================================================
st.subheader("This week so far")

if not daily_energy_f.empty:
    # current Mon-week
    today = datetime.now().date()
    cur_week_start = (pd.to_datetime(today) - pd.to_timedelta(pd.to_datetime(today).weekday(), unit="D")).date()
    cur = daily_energy[daily_energy["Date"].apply(lambda d: d is not None and d >= cur_week_start)].copy()

    cur = safe_to_numeric(cur, ["Calories (kcal)", "Expenditure", "Steps", "Protein (g)", "Target Protein (g)", "Target Calories (kcal)"])
    days_logged = cur["Calories (kcal)"].dropna().shape[0]
    avg_cal = cur["Calories (kcal)"].mean()
    avg_exp = cur["Expenditure"].mean()
    avg_bal = (cur["Calories (kcal)"] - cur["Expenditure"]).mean()
    avg_steps = cur["Steps"].mean()

    protein_adherence = None
    energy_adherence = None
    if "Target Protein (g)" in cur.columns and cur["Target Protein (g)"].notna().any():
        protein_adherence = (cur["Protein (g)"] / cur["Target Protein (g)"]).mean() * 100
    if "Target Calories (kcal)" in cur.columns and cur["Target Calories (kcal)"].notna().any():
        energy_adherence = (cur["Calories (kcal)"] / cur["Target Calories (kcal)"]).mean() * 100

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Days logged (nutrition)", f"{days_logged}")
    c2.metric("Avg calories", f"{avg_cal:,.0f}" if pd.notna(avg_cal) else "—")
    c3.metric("Avg expenditure", f"{avg_exp:,.0f}" if pd.notna(avg_exp) else "—")
    c4.metric("Avg balance", f"{avg_bal:,.0f}" if pd.notna(avg_bal) else "—")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Avg steps", f"{avg_steps:,.0f}" if pd.notna(avg_steps) else "—")
    c6.metric("Protein adherence", f"{protein_adherence:,.0f}%" if protein_adherence is not None else "—")
    c7.metric("Energy adherence", f"{energy_adherence:,.0f}%" if energy_adherence is not None else "—")
else:
    st.info("No Daily_Energy data yet.")

st.divider()

# ============================================================
# Charts: Calories vs Expenditure (weekly)
# ============================================================
st.subheader("Calories vs Expenditure (weekly)")
if not weekly_energy_f.empty:
    chart_df = weekly_energy_f.copy()
    chart_df["date"] = pd.to_datetime(chart_df["date"])

    base = alt.Chart(chart_df).encode(x="date:T")
    line1 = base.mark_line(point=True).encode(
        y=alt.Y("Calories (kcal):Q", title="kcal"),
        tooltip=["date:T", "Calories (kcal):Q"]
    )
    line2 = base.mark_line(point=True).encode(
        y="Expenditure:Q",
        tooltip=["date:T", "Expenditure:Q"]
    )
    st.altair_chart(line1 + line2, use_container_width=True)

    # balance bars
    if "Calories (kcal)" in chart_df.columns and "Expenditure" in chart_df.columns:
        chart_df["balance"] = chart_df["Calories (kcal)"] - chart_df["Expenditure"]
        bal = alt.Chart(chart_df).mark_bar().encode(
            x="date:T",
            y=alt.Y("balance:Q", title="kcal/day (avg)"),
            tooltip=["date:T", "balance:Q"]
        )
        st.markdown("**Estimated energy balance (Calories – Expenditure)**")
        st.altair_chart(bal, use_container_width=True)
else:
    st.info("No Weekly_Energy data in range (or not enough data yet).")

st.divider()

# ============================================================
# Daily macros over time (grams)
# ============================================================
st.subheader("Daily macros over time (grams)")
if not daily_energy_f.empty:
    d = daily_energy_f.copy()
    d["Date"] = pd.to_datetime(d["Date"])
    d = safe_to_numeric(d, ["Protein (g)", "Carbs (g)", "Fat (g)"])
    m = d.melt(id_vars=["Date"], value_vars=["Carbs (g)", "Fat (g)", "Protein (g)"],
               var_name="macro", value_name="grams")
    m["macro"] = m["macro"].str.replace(" (g)", "", regex=False).str.lower()

    chart = alt.Chart(m).mark_line(point=True).encode(
        x="Date:T",
        y=alt.Y("grams:Q", title="grams"),
        color="macro:N",
        tooltip=["Date:T", "macro:N", "grams:Q"]
    )
    st.altair_chart(chart, use_container_width=True)
else:
    st.info("No daily macro data available for this range.")

st.divider()

# ============================================================
# Training load + Lean Mass (weekly)
# ============================================================
st.subheader("Training load and Lean Mass (weekly)")
if not weekly_bodycomp_f.empty:
    bb = weekly_bodycomp_f.copy()
    bb["date"] = pd.to_datetime(bb["date"])
else:
    bb = pd.DataFrame()

if not weekly_training_f.empty:
    tt = weekly_training_f.copy()
    tt["date"] = pd.to_datetime(tt["date"])
else:
    tt = pd.DataFrame()

if not bb.empty:
    # Lean mass proxy = fat-free mass if present, else muscle_mass, else nothing
    lean_col = "fat_free_mass" if "fat_free_mass" in bb.columns else ("muscle_mass" if "muscle_mass" in bb.columns else None)

    if lean_col:
        lean_chart = alt.Chart(bb).mark_line(point=True).encode(
            x="date:T",
            y=alt.Y(f"{lean_col}:Q", title="Lean mass (lbs)"),
            tooltip=["date:T", alt.Tooltip(f"{lean_col}:Q", title=lean_col)]
        )
        st.altair_chart(lean_chart, use_container_width=True)

    if not tt.empty and "volume_total" in tt.columns:
        vol_chart = alt.Chart(tt).mark_line(point=True).encode(
            x="date:T",
            y=alt.Y("volume_total:Q", title="Training volume (lbs·reps)"),
            tooltip=["date:T", "volume_total:Q", "sets:Q", "workouts:Q", "avg_rir:Q"]
        )
        st.altair_chart(vol_chart, use_container_width=True)
else:
    st.info("No weekly body comp available yet (RENPHO).")

st.divider()

# ============================================================
# Food patterns
# ============================================================
st.subheader("🍽️ Food patterns")

if not daily_energy_f.empty:
    d = daily_energy_f.copy()
    d["Date"] = pd.to_datetime(d["Date"])
    d["dow"] = d["Date"].dt.day_name()
    d["day_type"] = d["dow"].apply(lambda x: "Weekend" if x in ["Saturday", "Sunday"] else "Weekday")

    d = safe_to_numeric(d, ["Calories (kcal)", "Protein (g)", "Carbs (g)", "Fat (g)"])
    pat = d.groupby("day_type", as_index=False).agg(
        calories=("Calories (kcal)", "mean"),
        protein=("Protein (g)", "mean"),
        carbs=("Carbs (g)", "mean"),
        fat=("Fat (g)", "mean"),
    )
    st.markdown("**Weekday vs weekend (avg daily totals)**")
    st.dataframe(pat, use_container_width=True, hide_index=True)

if not food_log_f.empty and "Food Name" in food_log_f.columns:
    top = food_log_f["Food Name"].value_counts().head(15).reset_index()
    top.columns = ["food_name", "count"]
    st.markdown("**Top foods (by frequency)**")
    st.dataframe(top, use_container_width=True, hide_index=True)
else:
    st.caption("Food log not available in selected range (or not imported yet).")