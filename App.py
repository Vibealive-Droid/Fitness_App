import altair as alt
import streamlit as st
import pandas as pd
import gspread
import random
import time
import math
import base64
from io import BytesIO
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

# ============================================================
# Config: chart scales (edit here)
# ============================================================
WEIGHT_DOMAIN = [145, 225]
BODYFAT_DOMAIN = [5, 30]

CALORIES_DOMAIN = [1000, 5000]       # Calories vs Expenditure scale
LEAN_DOMAIN = [150, 220]             # Lean mass scale
VOLUME_DOMAIN = [70000, 90000]       # Training volume scale (lbs·reps)
ADH_PCT_DOMAIN = [50, 130]           # Adherence % scale

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
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

def _is_429(e: Exception) -> bool:
    if not isinstance(e, APIError):
        return False
    try:
        return e.response is not None and e.response.status_code == 429
    except Exception:
        return False

def with_backoff(fn, tries: int = 6, base: float = 0.8):
    """Exponential backoff for Sheets 429 throttling."""
    for i in range(tries):
        try:
            return fn()
        except APIError as e:
            if not _is_429(e) or i == tries - 1:
                raise
            sleep = base * (2 ** i) + random.random() * 0.25
            time.sleep(sleep)

@st.cache_resource(show_spinner=False)
def get_spreadsheet(sheet_id: str):
    """Cache gspread client + spreadsheet (prevents repeated metadata reads)."""
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=SCOPES,
    )
    gc = gspread.authorize(creds)
    sh = with_backoff(lambda: gc.open_by_key(sheet_id))
    return sh

sh = None
try:
    sh = get_spreadsheet(SHEET_ID)
except APIError as e:
    st.error("❌ Google Sheets API error while opening the spreadsheet.")
    if _is_429(e):
        st.caption("Status: 429 (Too Many Requests) — you hit the Sheets read quota. Try again in ~60 seconds.")
    else:
        st.caption(str(e))
    st.stop()
except Exception as e:
    st.error("❌ Unexpected error opening spreadsheet.")
    st.caption(str(e))
    st.stop()

WS_BODY_WEEKLY = "Weekly_BodyComp"
WS_ENERGY_WEEKLY = "Weekly_Energy"
WS_TRAIN_WEEKLY = "Weekly_Training"

WS_DAILY_ENERGY = "Daily_Energy"
WS_WORKOUT_LOG = "Staging_Workout_Log"

WS_FOOD_LOG = "Staging_MacroFactor_FoodLog"
WS_MUSCLE_SETS = "Staging_Muscle_Sets"
WS_MUSCLE_VOLUME = "Staging_Muscle_Volume"

# ============================================================
# Helpers
# ============================================================

# ============================================================
# ✅ Helpers — Compliance + Date/Time (Python 3.8/3.9 safe)
# ============================================================

TZ = "America/Montreal"

def now_local_date(tz=TZ):
    """Today's date in Montreal, normalized to midnight, returned timezone-naive."""
    return pd.Timestamp.now(tz=tz).normalize().tz_localize(None)

def week_window_last_full(today):
    """Last full week window: Monday -> Sunday immediately prior to 'today''s week."""
    this_monday = monday_of(today)
    start = this_monday - pd.Timedelta(days=7)
    end = start + pd.Timedelta(days=6)
    return start, end

def safe_num(series):
    return pd.to_numeric(series, errors="coerce")

def safe_mean(series):
    s = safe_num(series)
    return s.mean() if s.notna().any() else pd.NA

def within_tol(actual, target, tol):
    """True if actual is within ±tol of target. Returns False for NaNs or target==0."""
    try:
        a = float(actual)
        t = float(target)
    except Exception:
        return False
    if pd.isna(a) or pd.isna(t) or t == 0:
        return False
    return abs(a - t) <= abs(t) * float(tol)

def collapse_daily(df, date_col="date"):
    """Collapse many rows per date into ONE row per date using last non-null per column."""
    if df is None or df.empty or date_col not in df.columns:
        return pd.DataFrame()

    d = df.copy()
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce").dt.normalize()
    d = d[d[date_col].notna()].copy()
    if d.empty:
        return pd.DataFrame()

    d["_row"] = range(len(d))

    def last_nonnull(s):
        s2 = s.dropna()
        return s2.iloc[-1] if len(s2) else pd.NA

    cols = [c for c in d.columns if c != "_row"]
    agg = {c: last_nonnull for c in cols if c != date_col}

    out = (
        d.sort_values([date_col, "_row"])
         .groupby(date_col, as_index=False)
         .agg(agg)
    )
    return out

def logged_day_mask(df):
    """
    What counts as a 'logged day':
      1) days_logged_flag == 1 (if present)
      2) calories present (non-null)
      3) any macro grams present (non-null)
    """
    if df is None or df.empty:
        return pd.Series([], dtype=bool)

    d = df.copy()

    if "days_logged_flag" in d.columns:
        flag = safe_num(d["days_logged_flag"]).fillna(0).astype(int)
        return flag.eq(1)

    cal_ok = safe_num(d["calories"]).notna() if "calories" in d.columns else pd.Series(False, index=d.index)

    macro_cols = [c for c in ["protein_g", "carbs_g", "fat_g"] if c in d.columns]
    if macro_cols:
        macro_ok = d[macro_cols].apply(safe_num).notna().any(axis=1)
    else:
        macro_ok = pd.Series(False, index=d.index)

    return cal_ok | macro_ok

def pick_logged_days(df):
    """Returns ONE row per logged date (critical for correct days_logged)."""
    if df is None or df.empty:
        return pd.DataFrame()

    daily = collapse_daily(df, "date")
    if daily.empty:
        return pd.DataFrame()

    mask = logged_day_mask(daily)
    return daily.loc[mask].copy()

def count_logged_days(df_logged):
    """Count unique logged dates. Assumes df_logged is one-row-per-date."""
    if df_logged is None or df_logged.empty or "date" not in df_logged.columns:
        return 0
    return int(pd.to_datetime(df_logged["date"], errors="coerce").dt.normalize().nunique())

def compute_calorie_ok_pct(de_logged, cal_tol):
    """% of logged days where calories within ±cal_tol of calorie_target."""
    if de_logged is None or de_logged.empty:
        return pd.NA
    if not all(c in de_logged.columns for c in ["calories", "calorie_target"]):
        return pd.NA

    a = safe_num(de_logged["calories"])
    t = safe_num(de_logged["calorie_target"])

    ok = a.notna() & t.notna() & (t != 0) & ((a - t).abs() <= (t.abs() * cal_tol))
    return float(ok.mean() * 100.0) if ok.notna().any() else pd.NA

def compute_macros_ok_pct(de_logged, macro_tol):
    """
    % of logged days where ALL (P,C,F) within ±macro_tol of targets.
    Falls back to protein_adherence if targets missing/unpopulated.
    """
    if de_logged is None or de_logged.empty:
        return pd.NA

    needed_actuals = ["protein_g", "carbs_g", "fat_g"]
    needed_targets = ["protein_target_g", "carbs_target_g", "fat_target_g"]

    has_actuals = all(c in de_logged.columns for c in needed_actuals)
    has_targets = all(c in de_logged.columns for c in needed_targets)

    if not (has_actuals and has_targets):
        if "protein_adherence" in de_logged.columns:
            adh = safe_num(de_logged["protein_adherence"])
            ok = adh.notna() & adh.ge(1.0 - macro_tol)
            denom = ok.notna().sum()
            return float(ok.sum() / denom * 100.0) if denom else pd.NA
        return pd.NA

    pt = safe_num(de_logged["protein_target_g"]).fillna(0)
    ct = safe_num(de_logged["carbs_target_g"]).fillna(0)
    ft = safe_num(de_logged["fat_target_g"]).fillna(0)
    if (pt.sum() + ct.sum() + ft.sum()) <= 0:
        return pd.NA

    p = safe_num(de_logged["protein_g"])
    c = safe_num(de_logged["carbs_g"])
    f = safe_num(de_logged["fat_g"])

    p_ok = p.notna() & pt.notna() & (pt != 0) & ((p - pt).abs() <= (pt.abs() * macro_tol))
    c_ok = c.notna() & ct.notna() & (ct != 0) & ((c - ct).abs() <= (ct.abs() * macro_tol))
    f_ok = f.notna() & ft.notna() & (ft != 0) & ((f - ft).abs() <= (ft.abs() * macro_tol))

    all_ok = p_ok & c_ok & f_ok
    return float(all_ok.mean() * 100.0) if all_ok.notna().any() else pd.NA

def score_label(score):
    if score >= 85: return "Locked In 🔥"
    if score >= 70: return "Solid ✅"
    if score >= 55: return "Okay — Tighten Up"
    return "Tighten Up ⚠️"


def ensure_df(x) -> pd.DataFrame:
    """Guarantee a DataFrame so .empty and .columns never crash."""
    return x if isinstance(x, pd.DataFrame) else pd.DataFrame()

def _make_unique(cols):
    """Make header names unique: weight_g, weight_g_1, weight_g_2 ..."""
    seen = {}
    out = []
    for c in cols:
        c = (c or "").strip()
        if c == "":
            out.append("")  # placeholder; will be dropped
            continue
        if c not in seen:
            seen[c] = 0
            out.append(c)
        else:
            seen[c] += 1
            out.append(f"{c}_{seen[c]}")
    return out

@st.cache_data(ttl=300, show_spinner=False)
def load_sheet_cached(sheet_id: str, worksheet_name: str) -> pd.DataFrame:
    """
    Low-request, quota-friendly loader:
      - Uses get_all_values() (usually one read) instead of get_all_records()
      - Survives duplicate headers (e.g., weight_g twice)
      - Drops blank header columns
      - Cached for 5 minutes
    """
    try:
        ws = sh.worksheet(worksheet_name)
    except Exception:
        return pd.DataFrame()

    try:
        values = with_backoff(lambda: ws.get_all_values())
        if not values or len(values) < 2:
            return pd.DataFrame()

        header_raw = values[0]
        data = values[1:]

        header = _make_unique(header_raw)
        df = pd.DataFrame(data, columns=header)

        # Drop any columns that had blank header cells
        df = df.loc[:, [c for c in df.columns if str(c).strip() != ""]]

        # Normalise empties
        df = df.replace({"": pd.NA})

        # Strip col names
        df.columns = [str(c).strip() for c in df.columns]
        return df

    except Exception:
        return pd.DataFrame()

def load_sheet(worksheet_name: str) -> pd.DataFrame:
    """Wrapper for your existing code style."""
    return load_sheet_cached(SHEET_ID, worksheet_name)

def normalise_date_col(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    """Keep df[col] as datetime for filtering + Altair. Normalise to midnight."""
    if df is None or df.empty or col not in df.columns:
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    out = df.copy()
    out[col] = pd.to_datetime(out[col], errors="coerce")
    # strip tz if present
    try:
        if hasattr(out[col].dt, "tz") and out[col].dt.tz is not None:
            out[col] = out[col].dt.tz_localize(None)
    except Exception:
        pass
    out[col] = out[col].dt.normalize()
    out = out.dropna(subset=[col]).sort_values(col).reset_index(drop=True)
    return out

def monday_of(d: pd.Timestamp) -> pd.Timestamp:
    d = pd.to_datetime(d).normalize()
    return d - pd.to_timedelta(d.weekday(), unit="D")

def sunday_of_week(d: pd.Timestamp) -> pd.Timestamp:
    return monday_of(d) + pd.Timedelta(days=6)

def align_to_monday(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    """Force weekly dates to Monday."""
    if df is None or df.empty or col not in df.columns:
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    out = df.copy()
    out[col] = pd.to_datetime(out[col], errors="coerce").dt.normalize()
    out[col] = out[col] - pd.to_timedelta(out[col].dt.weekday, unit="D")
    out = out.dropna(subset=[col]).sort_values(col).reset_index(drop=True)
    return out

def to_num(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out

def filter_range(df: pd.DataFrame, start_date, end_date, date_col: str = "date") -> pd.DataFrame:
    """
    Robust filter: accepts start/end as datetime.date OR pd.Timestamp OR strings.
    Compares using normalized datetime (NOT .dt.date) to avoid type mismatches.
    Inclusive bounds.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty or date_col not in df.columns:
        return pd.DataFrame()

    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
    try:
        if hasattr(out[date_col].dt, "tz") and out[date_col].dt.tz is not None:
            out[date_col] = out[date_col].dt.tz_localize(None)
    except Exception:
        pass
    out[date_col] = out[date_col].dt.normalize()
    out = out.dropna(subset=[date_col])

    s0 = pd.to_datetime(start_date, errors="coerce")
    s1 = pd.to_datetime(end_date, errors="coerce")
    if pd.isna(s0) or pd.isna(s1):
        return pd.DataFrame()

    s0 = pd.Timestamp(s0).normalize()
    s1 = pd.Timestamp(s1).normalize()

    mask = (out[date_col] >= s0) & (out[date_col] <= s1)
    return out.loc[mask].copy()

def date_for_table(df: pd.DataFrame, col="date") -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if col in out.columns:
        out[col] = pd.to_datetime(out[col], errors="coerce")
    return out

def safe_num(series):
    return pd.to_numeric(series, errors="coerce")
    
def score_label(score: int) -> str:
    if score >= 85:
        return "Locked In 🔥"
    if score >= 70:
        return "Solid ✅"
    if score >= 55:
        return "Okay — Tighten Up"
    return "Tighten Up ⚠️"
    
def metric_or_dash(x, fmt="{:.0f}"):
    if x is None or pd.isna(x):
        return "—"
    try:
        return fmt.format(float(x))
    except Exception:
        return "—"

def coalesce_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if isinstance(df, pd.DataFrame) and c in df.columns:
            return c
    return None

def parse_time_to_hour(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    t1 = pd.to_datetime(s, format="%I:%M %p", errors="coerce")
    t2 = pd.to_datetime(s, format="%H:%M", errors="coerce")
    t = t1.fillna(t2)
    return t.dt.hour

def normalise_fibre_fiber_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    if "fibre_g" in out.columns and "fiber_g" not in out.columns:
        out["fiber_g"] = out["fibre_g"]
    if "fiber_g" in out.columns and "fibre_g" not in out.columns:
        out["fibre_g"] = out["fiber_g"]
    return out

def normalise_daily_energy_schema(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
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
    out = out.rename(columns={k: v for k, v in rename_map.items() if k in out.columns})
    return out

def normalise_workout_log_schema(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    rename_map = {
        "weight": "weight_lb",
        "Weight (lbs)": "weight_lb",
        "Reps": "reps",
        "RIR": "rir",
        "Workout Duration": "workout_duration",
        "Set Type": "set_type",
    }
    out = out.rename(columns={k: v for k, v in rename_map.items() if k in out.columns})
    return out

def pick_logged_days(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    flag = safe_num(out.get("days_logged_flag", pd.Series([pd.NA] * len(out)))).fillna(0)
    calories = safe_num(out.get("calories", pd.Series([pd.NA] * len(out)))).fillna(0)
    p = safe_num(out.get("protein_g", pd.Series([pd.NA] * len(out)))).fillna(0)
    c = safe_num(out.get("carbs_g", pd.Series([pd.NA] * len(out)))).fillna(0)
    f = safe_num(out.get("fat_g", pd.Series([pd.NA] * len(out)))).fillna(0)
    logged_mask = (flag >= 1) | (calories > 0) | (p > 0) | (c > 0) | (f > 0)
    return out.loc[logged_mask].copy()

# ============================================================
# Load BODY weekly
# ============================================================
body = load_sheet(WS_BODY_WEEKLY)
if "date_time" in body.columns and "date" not in body.columns:
    body = body.rename(columns={"date_time": "date"})
body = normalise_date_col(body, "date")
body = align_to_monday(body, "date")
body = to_num(body, ["weight", "body_fat", "fat_free_mass"])

if not body.empty and {"weight", "body_fat"}.issubset(body.columns):
    body["fat_mass"] = (body["weight"] * (body["body_fat"] / 100)).round(2)
    body["lean_mass"] = (body["weight"] - body["fat_mass"]).round(2)

# ============================================================
# Date range selector (Monday weeks + presets)
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
        end = this_monday - pd.Timedelta(days=1)
        start = end - pd.Timedelta(days=6)
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

    end = max_body_date
    start = max(min_date, (pd.Timestamp(end) - pd.DateOffset(months=12)).date())
    return start, end

preset_start, preset_end = compute_preset(preset)

preset_start = max(min_date, min(preset_start, max_end_allowed))
preset_end = max(min_date, min(preset_end, max_end_allowed))
if preset_start > preset_end:
    preset_start = preset_end

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

start_dt_monday = monday_of(pd.Timestamp(start_date))
end_dt_sunday = sunday_of_week(pd.Timestamp(end_date))
start_date = start_dt_monday.date()
end_date = end_dt_sunday.date()

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
    "avg_scale_weight_lb", "avg_trend_weight_lb", "avg_steps",
    "avg_fiber_g", "avg_sodium_mg", "avg_potassium_mg", "avg_caffeine_mg",
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
body_view   = filter_range(body, start_date, end_date_for_weekly, "date")
energy_view = filter_range(energy, start_date, end_date_for_weekly, "date")
train_view  = filter_range(train, start_date, end_date_for_weekly, "date")

x_min = pd.Timestamp(start_date)
x_max = pd.Timestamp(end_date_for_weekly)

# ============================================================
# Display: Body table
# ============================================================
st.subheader("Weekly Body Comp (filtered)")
body_table = date_for_table(body_view)

if body_table.empty:
    st.info("No body rows in selected range.")
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
# ============================================================
# Phase gating: compute logged days for the SELECTED RANGE
# ============================================================
daily_energy_for_phase = load_sheet(WS_DAILY_ENERGY)
daily_energy_for_phase = normalise_daily_energy_schema(daily_energy_for_phase)
daily_energy_for_phase = normalise_date_col(daily_energy_for_phase, "date")
daily_energy_for_phase = to_num(daily_energy_for_phase, [
    "days_logged_flag",
    "calories", "expenditure",
    "protein_g", "carbs_g", "fat_g",
])

# --- Logging confidence for phase suggestion (local to this section)
days_logged_phase = 0
if "days_logged" in combined.columns:
    # Weekly_Energy typically has days_logged per week row
    s = pd.to_numeric(combined["days_logged"], errors="coerce").dropna()
    days_logged_phase = int(round(s.mean())) if not s.empty else 0

energy_trustworthy = days_logged_phase >= 4
# ============================================================
# Suggested phase: Cut vs Recomp vs Lean bulk (selected range)
# ============================================================
st.subheader("🎯 Suggested phase (Cut / Recomp / Lean bulk)")

def _last_non_na(s: pd.Series):
    s2 = pd.to_numeric(s, errors="coerce").dropna()
    return s2.iloc[-1] if not s2.empty else pd.NA

def _safe_mean(s: pd.Series):
    s2 = pd.to_numeric(s, errors="coerce").dropna()
    return s2.mean() if not s2.empty else pd.NA

# --- Determine logging confidence for this selected range (from Weekly_Energy)
# This is NOT the same as "Daily_Energy logged days"; it's the weekly MacroFactor summary.
days_logged_phase = 0
if "days_logged" in combined.columns:
    dl = pd.to_numeric(combined["days_logged"], errors="coerce").dropna()
    days_logged_phase = int(round(dl.mean())) if not dl.empty else 0

energy_trustworthy = days_logged_phase >= 4  # tune this threshold if you want

# --- Compute energy balance (only if trustworthy)
avg_bal = pd.NA
if energy_trustworthy and {"avg_calories", "avg_expenditure"}.issubset(set(combined.columns)):
    avg_bal = _safe_mean(
        pd.to_numeric(combined["avg_calories"], errors="coerce")
        - pd.to_numeric(combined["avg_expenditure"], errors="coerce")
    )

# --- Latest body stats
w0    = _last_non_na(combined.get("weight", pd.Series(dtype=float)))
bf0   = _last_non_na(combined.get("body_fat", pd.Series(dtype=float)))
lean0 = _last_non_na(combined.get("lean_mass", pd.Series(dtype=float)))
fat0  = _last_non_na(combined.get("fat_mass", pd.Series(dtype=float)))

# --- Weight change rate over selected range (per week) as fallback signal
w_rate = pd.NA
if "weight" in combined.columns and combined["weight"].notna().sum() >= 2 and "date" in combined.columns:
    tmp = combined[["date", "weight"]].dropna().copy()
    if len(tmp) >= 2:
        days = (tmp["date"].iloc[-1] - tmp["date"].iloc[0]).days
        if days > 0:
            w_rate = (tmp["weight"].iloc[-1] - tmp["weight"].iloc[0]) / (days / 7.0)

# --- Rules
phase = "Recomp"
reason = []

# 1) Bodyfat anchor
if pd.notna(bf0):
    if bf0 >= 18:
        phase = "Cut"
        reason.append("Body fat is on the higher side (≥18%).")
    elif bf0 <= 13:
        phase = "Lean bulk"
        reason.append("Body fat is relatively low (≤13%).")
    else:
        phase = "Recomp"
        reason.append("Body fat is in a mid range (≈13–18%).")

# 2) Energy balance signal (only if trustworthy)
if pd.notna(avg_bal):
    if avg_bal <= -150:
        phase = "Cut"
        reason.append(f"Average energy balance looks like a deficit ({avg_bal:.0f} kcal/day).")
    elif avg_bal >= 150:
        if phase != "Cut":
            phase = "Lean bulk"
        reason.append(f"Average energy balance looks like a surplus ({avg_bal:.0f} kcal/day).")
    else:
        reason.append("Energy balance looks close to maintenance (±150 kcal/day).")
else:
    # 3) Weight-trend fallback signal
    if pd.notna(w_rate):
        if w_rate <= -0.25:
            phase = "Cut"
            reason.append(f"Weight trend is decreasing (~{w_rate:.2f} lb/week).")
        elif w_rate >= 0.25:
            if phase != "Cut":
                phase = "Lean bulk"
            reason.append(f"Weight trend is increasing (~{w_rate:.2f} lb/week).")
        else:
            reason.append("Weight trend is roughly stable.")
    else:
        # Explain why energy wasn't used
        if not energy_trustworthy:
            reason.append(f"Energy balance not reliable ({days_logged_phase}/7 days logged in Weekly_Energy).")
        else:
            reason.append("Energy balance unavailable.")

# --- Present result
badge = "✅ RECOMP" if phase == "Recomp" else ("🔥 LEAN BULK" if phase == "Lean bulk" else "✂️ CUT")
if phase == "Recomp":
    st.success(f"**Suggested focus: {badge}**")
elif phase == "Lean bulk":
    st.info(f"**Suggested focus: {badge}**")
else:
    st.warning(f"**Suggested focus: {badge}**")

cols = st.columns(4)
with cols[0]:
    st.metric("Latest weight", metric_or_dash(w0, "{:.1f} lb"))
with cols[1]:
    st.metric("Latest body fat", metric_or_dash(bf0, "{:.1f}%"))
with cols[2]:
    st.metric("Lean mass", metric_or_dash(lean0, "{:.1f} lb"))
with cols[3]:
    st.metric("Fat mass", metric_or_dash(fat0, "{:.1f} lb"))

# Show the signal we used
if pd.notna(avg_bal):
    st.caption(f"Avg energy balance in range: {avg_bal:.0f} kcal/day (trustworthy: {energy_trustworthy}, days_logged≈{days_logged_phase}/7)")
elif pd.notna(w_rate):
    st.caption(f"Estimated weight change rate: {w_rate:.2f} lb/week (energy trustworthy: {energy_trustworthy}, days_logged≈{days_logged_phase}/7)")
else:
    st.caption(f"Energy trustworthy: {energy_trustworthy} (days_logged≈{days_logged_phase}/7)")

if reason:
    st.caption("Why: " + " ".join(reason))

# Optional: simple targets
if phase == "Cut":
    st.info("Cut target: ~0.5–1.0 lb/week loss (small deficit, keep protein high, keep training performance stable).")
elif phase == "Lean bulk":
    st.info("Lean bulk target: ~0.25–0.5 lb/week gain (small surplus, watch waist/scale trend).")
else:
    st.info("Recomp target: hold weight steady (±0.25 lb/week) while pushing training quality and adherence.")

if "weight" in combined.columns:
    combined["weight_change"] = safe_num(combined["weight"]).diff()
if "lean_mass" in combined.columns:
    combined["lean_change"] = safe_num(combined["lean_mass"]).diff()
if "fat_mass" in combined.columns:
    combined["fat_change"] = safe_num(combined["fat_mass"]).diff()

if "avg_calories" in combined.columns and "avg_expenditure" in combined.columns:
    combined["energy_balance"] = safe_num(combined["avg_calories"]) - safe_num(combined["avg_expenditure"])

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
# ✅ Compliance (Mon -> Sun) — Matt Standard (uses helpers block)
# ============================================================
st.subheader("✅ Compliance (Mon → Sun) — Matt Standard")

# ---- Targets (edit here)
TARGET_WORKOUTS = 4
TARGET_MINUTES = 200
TARGET_STEPS = 8000
CAL_TOL = 0.10
MACRO_TOL = 0.10
MIN_LOG_DAYS = 6  # Matt standard: 6/7 logged days

# Use Montreal-local date to avoid server-time weirdness
today_ts = now_local_date()
start_monday, end_sunday = week_window_last_full(today_ts)

st.caption(f"Compliance window: **{start_monday.date()} → {end_sunday.date()}** (Mon→Sun)")

# ============================================================
# 1) DAILY ENERGY (logging, calories, macros, steps)
# ============================================================
daily_energy = load_sheet(WS_DAILY_ENERGY)
daily_energy = normalise_daily_energy_schema(daily_energy)
daily_energy = normalise_date_col(daily_energy, "date")
daily_energy = to_num(daily_energy, [
    "days_logged_flag",
    "calories", "expenditure", "calorie_target",
    "protein_g", "carbs_g", "fat_g",
    "protein_target_g", "carbs_target_g", "fat_target_g",
    "protein_adherence",
    "steps"
])

daily_energy_win = filter_range(daily_energy, start_monday, end_sunday, "date")
daily_energy_win = ensure_df(daily_energy_win)

# ✅ Key: logged days collapsed to ONE row per date
daily_energy_logged = pick_logged_days(daily_energy_win)

days_logged = count_logged_days(daily_energy_logged)
logging_ok = days_logged >= MIN_LOG_DAYS

# Calories/macros compliance %
cal_ok_pct = compute_calorie_ok_pct(daily_energy_logged, CAL_TOL)
macro_ok_pct = compute_macros_ok_pct(daily_energy_logged, MACRO_TOL)

# Steps
steps_avg = pd.NA
steps_ok = False
if not daily_energy_logged.empty and "steps" in daily_energy_logged.columns:
    steps_avg = safe_mean(daily_energy_logged["steps"])
    steps_ok = (pd.notna(steps_avg) and float(steps_avg) >= TARGET_STEPS)

# ============================================================
# 2) WORKOUT LOG (workouts + minutes)
# ============================================================
workout_log = load_sheet(WS_WORKOUT_LOG)
workout_log = normalise_workout_log_schema(workout_log)
workout_log = normalise_date_col(workout_log, "date")
workout_log = to_num(workout_log, ["workout_duration", "weight_lb", "reps", "rir"])

workout_win = filter_range(workout_log, start_monday, end_sunday, "date")
workout_win = ensure_df(workout_win)

workouts_done = 0
minutes_done = pd.NA

if not workout_win.empty and "date" in workout_win.columns:
    wk = workout_win.copy()

    # session_key prevents "sets counted as workouts"
    if "workout" in wk.columns:
        wk["session_key"] = (
            wk["date"].dt.date.astype(str)
            + "|"
            + wk["workout"].astype(str).str.strip().str.lower()
        )
    else:
        wk["session_key"] = wk["date"].dt.date.astype(str)

    workouts_done = int(wk["session_key"].nunique())

    # duration: max per session (MacroFactor repeats per set) then sum
    if "workout_duration" in wk.columns:
        mins = safe_num(wk.groupby("session_key")["workout_duration"].max()) / 60.0
        minutes_done = float(mins.sum()) if mins.notna().any() else pd.NA

training_ok = (
    workouts_done >= TARGET_WORKOUTS
    and (pd.notna(minutes_done) and float(minutes_done) >= TARGET_MINUTES)
)

# ============================================================
# 3) Score
# ============================================================
score = 0
score += 20 if logging_ok else 0

if pd.notna(cal_ok_pct):
    score += int(round((float(cal_ok_pct) / 100.0) * 30))  # up to 30

if pd.notna(macro_ok_pct):
    score += int(round((float(macro_ok_pct) / 100.0) * 25))  # up to 25

score += 15 if training_ok else 0

if pd.notna(steps_avg):
    steps_points = 10 if float(steps_avg) >= TARGET_STEPS else int(round((float(steps_avg) / TARGET_STEPS) * 10))
    score += max(0, min(10, steps_points))

score = int(max(0, min(100, score)))
label = score_label(score)

# ============================================================
# 1) DAILY ENERGY (logging, calories, macros, steps)
# ============================================================
daily_energy = load_sheet(WS_DAILY_ENERGY)
daily_energy = normalise_daily_energy_schema(daily_energy)
daily_energy = normalise_date_col(daily_energy, "date")
daily_energy = to_num(daily_energy, [
    "days_logged_flag",
    "calories", "expenditure", "calorie_target",
    "protein_g", "carbs_g", "fat_g",
    "protein_target_g", "carbs_target_g", "fat_target_g",
    "steps"
])

daily_energy_win = filter_range(daily_energy, start_monday, end_sunday, "date")
daily_energy_win = ensure_df(daily_energy_win)
daily_energy_logged = pick_logged_days(daily_energy_win)
# Back-compat alias (older code referenced this name)
daily_energy_win_logged = daily_energy_logged

# ============================================================
# Compute compliance metrics (single pass, no duplicates)
# ============================================================
days_logged = count_logged_days(daily_energy_logged) if not daily_energy_logged.empty else 0
logging_ok = days_logged >= MIN_LOG_DAYS

cal_ok_pct = pd.NA
macro_ok_pct = pd.NA
steps_avg = pd.NA
steps_ok = False

if not daily_energy_logged.empty:
    de = daily_energy_logged.copy()

    # Steps
    if "steps" in de.columns:
        steps_avg = _safe_mean(de["steps"])
        steps_ok = (pd.notna(steps_avg) and float(steps_avg) >= TARGET_STEPS)

    # Calories ± tolerance
    if "calories" in de.columns and "calorie_target" in de.columns:
        a = pd.to_numeric(de["calories"], errors="coerce")
        t = pd.to_numeric(de["calorie_target"], errors="coerce")
        cal_ok = a.notna() & t.notna() & (t != 0) & ((a - t).abs() <= (t.abs() * CAL_TOL))
        cal_ok_pct = (cal_ok.sum() / len(de)) * 100 if len(de) else pd.NA

    # Macros ± tolerance (prefer explicit macro targets when populated)
    needed_actuals = ["protein_g", "carbs_g", "fat_g"]
    needed_targets = ["protein_target_g", "carbs_target_g", "fat_target_g"]

    has_actuals = all(c in de.columns for c in needed_actuals)
    has_targets = all(c in de.columns for c in needed_targets)

    targets_populated = False
    if has_targets:
        pt = pd.to_numeric(de["protein_target_g"], errors="coerce").fillna(0)
        ct = pd.to_numeric(de["carbs_target_g"], errors="coerce").fillna(0)
        ft = pd.to_numeric(de["fat_target_g"], errors="coerce").fillna(0)
        targets_populated = (pt.sum() + ct.sum() + ft.sum()) > 0

    if has_actuals and has_targets and targets_populated:
        p = pd.to_numeric(de["protein_g"], errors="coerce")
        c = pd.to_numeric(de["carbs_g"], errors="coerce")
        f = pd.to_numeric(de["fat_g"], errors="coerce")

        pt = pd.to_numeric(de["protein_target_g"], errors="coerce")
        ct = pd.to_numeric(de["carbs_target_g"], errors="coerce")
        ft = pd.to_numeric(de["fat_target_g"], errors="coerce")

        p_ok = p.notna() & pt.notna() & (pt != 0) & ((p - pt).abs() <= (pt.abs() * MACRO_TOL))
        c_ok = c.notna() & ct.notna() & (ct != 0) & ((c - ct).abs() <= (ct.abs() * MACRO_TOL))
        f_ok = f.notna() & ft.notna() & (ft != 0) & ((f - ft).abs() <= (ft.abs() * MACRO_TOL))

        macros_ok = p_ok & c_ok & f_ok
        macro_ok_pct = (macros_ok.sum() / len(de)) * 100 if len(de) else pd.NA

    else:
        # Fallback: protein adherence proxy (>= 0.90)
        if "protein_adherence" in de.columns:
            adh = pd.to_numeric(de["protein_adherence"], errors="coerce")
            proxy_ok = adh.notna() & adh.ge(1.0 - MACRO_TOL)
            macro_ok_pct = (proxy_ok.sum() / adh.notna().sum()) * 100 if adh.notna().any() else pd.NA

# ============================================================
# 2) WORKOUT LOG (workouts + minutes)
# ============================================================
workout_log = load_sheet(WS_WORKOUT_LOG)
workout_log = normalise_workout_log_schema(workout_log)
workout_log = normalise_date_col(workout_log, "date")
workout_log = to_num(workout_log, ["workout_duration", "weight_lb", "reps", "rir"])

workout_win = filter_range(workout_log, start_monday, end_sunday, "date")
workout_win = ensure_df(workout_win)

workouts_done = 0
minutes_done = pd.NA

if not workout_win.empty and "date" in workout_win.columns:
    wk = workout_win.copy()

    # session_key: date + workout name if present (prevents sets being counted as workouts)
    if "workout" in wk.columns:
        wk["session_key"] = wk["date"].dt.date.astype(str) + "|" + wk["workout"].astype(str).str.strip().str.lower()
    else:
        wk["session_key"] = wk["date"].dt.date.astype(str)

    workouts_done = int(wk["session_key"].nunique())

    # duration: take max per session (MacroFactor can repeat duration per set), sum sessions
    if "workout_duration" in wk.columns:
        mins = pd.to_numeric(wk.groupby("session_key")["workout_duration"].max(), errors="coerce") / 60.0
        minutes_done = float(mins.sum()) if mins.notna().any() else pd.NA

training_ok = (workouts_done >= TARGET_WORKOUTS) and (pd.notna(minutes_done) and float(minutes_done) >= TARGET_MINUTES)

# ============================================================
# 3) Score
# ============================================================
score = 0
score += 20 if logging_ok else 0

if pd.notna(cal_ok_pct):
    score += int(round((float(cal_ok_pct) / 100.0) * 30))  # up to 30

if pd.notna(macro_ok_pct):
    score += int(round((float(macro_ok_pct) / 100.0) * 25))  # up to 25

score += 15 if training_ok else 0

if pd.notna(steps_avg):
    steps_points = 10 if float(steps_avg) >= TARGET_STEPS else int(round((float(steps_avg) / TARGET_STEPS) * 10))
    score += max(0, min(10, steps_points))

score = int(max(0, min(100, score)))
label = score_label(score)

# ============================================================
# 4) Display
# ============================================================
st.markdown(f"### ⚠️ Compliance Score: **{score}/100** — **{label}**")

c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    st.metric("Logging", "PASS ✅" if logging_ok else "FAIL ❌", f"{days_logged}/7 days")
with c2:
    st.metric(f"Calories ±{int(CAL_TOL*100)}%", metric_or_dash(cal_ok_pct, "{:.0f}%"))
with c3:
    st.metric(f"Macros ±{int(MACRO_TOL*100)}%", metric_or_dash(macro_ok_pct, "{:.0f}%"))
with c4:
    st.metric(
        f"Training ({TARGET_WORKOUTS}x / {TARGET_MINUTES}m)",
        "PASS ✅" if training_ok else "FAIL ❌",
        f"{workouts_done}x / {metric_or_dash(minutes_done, '{:.0f}')}m"
    )
with c5:
    st.metric(
        f"Steps ≥{TARGET_STEPS}",
        metric_or_dash(steps_avg, "{:.0f}"),
        "PASS ✅" if steps_ok else "LOW ⚠️"
    )

# ============================================================
# 📐 Shape ratios + exaggerated avatar (V-taper caricature)
# ============================================================
st.header("📐 Shape ratios + V-taper avatar")

# --- Defaults (from your RENPHO screenshot)
DEFAULT_HEIGHT_IN = 71.0  # 5'11"
DEFAULT_WEIGHT_LB = float(w0) if (w0 is not None and not pd.isna(w0)) else 195.0

DEFAULT_SHOULDERS = 47.25
DEFAULT_WAIST = 38.85
DEFAULT_HIPS = 36.92

left, right = st.columns([1.1, 1.4])

with left:
    st.subheader("Measurements (inches)")
    height_in = st.number_input("Height (in)", min_value=50.0, max_value=90.0, value=DEFAULT_HEIGHT_IN, step=0.5)
    weight_lb = st.number_input("Weight (lb)", min_value=120.0, max_value=350.0, value=float(DEFAULT_WEIGHT_LB), step=0.5)

    shoulders_in = st.number_input("Shoulders", min_value=30.0, max_value=70.0, value=float(DEFAULT_SHOULDERS), step=0.05)
    waist_in = st.number_input("Waist (navel)", min_value=20.0, max_value=70.0, value=float(DEFAULT_WAIST), step=0.05)
    hips_in = st.number_input("Hips", min_value=25.0, max_value=70.0, value=float(DEFAULT_HIPS), step=0.05)

    st.caption("Tip: keep tape tension + timing consistent. Update these monthly for best signal.")

def _safe_ratio(a, b):
    try:
        a = float(a); b = float(b)
        if b == 0: return pd.NA
        return a / b
    except Exception:
        return pd.NA

swr = _safe_ratio(shoulders_in, waist_in)   # Shoulder/Waist
shr = _safe_ratio(shoulders_in, hips_in)    # Shoulder/Hip
whtr = _safe_ratio(waist_in, height_in)     # Waist/Height

def v_state(swr_val):
    if pd.isna(swr_val):
        return "—"
    if swr_val < 1.25: return "Block"
    if swr_val < 1.35: return "Athletic"
    if swr_val < 1.45: return "Strong V"
    if swr_val < 1.55: return "Wide"
    return "Savage"

state = v_state(swr)

with left:
    st.subheader("Ratios")
    r1, r2, r3 = st.columns(3)
    with r1:
        st.metric("Shoulder / Waist", "—" if pd.isna(swr) else f"{swr:.2f}", state)
    with r2:
        st.metric("Shoulder / Hip", "—" if pd.isna(shr) else f"{shr:.2f}")
    with r3:
        st.metric("Waist / Height", "—" if pd.isna(whtr) else f"{whtr:.3f}")

    # quick guidance (kept simple)
    if pd.notna(whtr):
        if whtr <= 0.45:
            st.success("Waist/Height is in a very lean range.")
        elif whtr <= 0.50:
            st.info("Waist/Height is in a solid general range.")
        else:
            st.warning("Waist/Height suggests your midsection is currently the main lever for the taper.")

# --- Exaggerated avatar generator (SVG)
def make_v_avatar_svg(shoulders, waist, hips, swr_val):
    # Canvas
    W, H = 260, 360

    # Normalise widths to a cartoony but stable scale
    # Anchor SWR=1.22 as baseline (your current), then exaggerate improvements.
    base_swr = 1.22
    swr_val = float(swr_val) if (swr_val is not None and not pd.isna(swr_val)) else base_swr

    # Exaggeration (B mode): make improvements "look" bigger than they are
    exaggeration = 1.18

    # Width factors (sqrt keeps it stable)
    widen = math.sqrt(max(0.7, min(1.8, (swr_val / base_swr)))) * exaggeration
    tighten = math.sqrt(max(0.7, min(1.8, (base_swr / swr_val)))) * exaggeration

    # Convert circumferences into relative widths (not physically exact—visual proxy)
    # Shoulder and hips are driven more by their measurements; waist gets extra "tighten" effect.
    shoulder_w = max(90, min(190, (shoulders * 2.1) * widen))
    hip_w      = max(80, min(170, (hips * 2.0) * (0.90 + 0.10*widen)))
    waist_w    = max(55, min(150, (waist * 1.9) * (0.85 * tighten)))

    cx = W / 2

    # Y coordinates
    y_head = 55
    y_shoulder = 95
    y_waist = 190
    y_hip = 245
    y_leg = 330

    # Points for torso polygon (simple stylised outline)
    sx = shoulder_w / 2
    wx = waist_w / 2
    hx = hip_w / 2

    # Slight rounded shoulders and hips using a path
    path = f"""
    M {cx - sx:.1f},{y_shoulder:.1f}
    Q {cx:.1f},{y_shoulder - 18:.1f} {cx + sx:.1f},{y_shoulder:.1f}
    L {cx + wx:.1f},{y_waist:.1f}
    Q {cx:.1f},{y_waist + 10:.1f} {cx - wx:.1f},{y_waist:.1f}
    L {cx - hx:.1f},{y_hip:.1f}
    Q {cx:.1f},{y_hip + 14:.1f} {cx + hx:.1f},{y_hip:.1f}
    L {cx + hx*0.70:.1f},{y_leg:.1f}
    Q {cx:.1f},{y_leg + 10:.1f} {cx - hx*0.70:.1f},{y_leg:.1f}
    Z
    """

    # Colour is kept neutral; no style assumptions
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
      <rect x="0" y="0" width="{W}" height="{H}" fill="transparent"/>
      <!-- head -->
      <circle cx="{cx}" cy="{y_head}" r="26" fill="#2b2b2b" opacity="0.85"/>
      <!-- neck -->
      <rect x="{cx-12}" y="{y_head+26}" width="24" height="20" rx="10" fill="#2b2b2b" opacity="0.85"/>
      <!-- torso -->
      <path d="{path}" fill="#2b2b2b" opacity="0.90"/>
      <!-- tiny waist highlight line -->
      <path d="M {cx-wx:.1f},{y_waist:.1f} Q {cx:.1f},{y_waist+8:.1f} {cx+wx:.1f},{y_waist:.1f}"
            fill="none" stroke="#ffffff" stroke-width="2" opacity="0.25"/>
    </svg>
    """
    return svg

with right:
    st.subheader("Avatar (exaggerated V-taper)")
    if pd.isna(swr):
        st.info("Enter shoulders + waist to render the avatar.")
    else:
        svg = make_v_avatar_svg(shoulders_in, waist_in, hips_in, swr)
        b64 = base64.b64encode(svg.encode("utf-8")).decode("utf-8")
st.markdown(
    f"""
    <div style="display:flex; justify-content:center;">
      <img src="data:image/svg+xml;base64,{b64}" style="width:100%; max-width:520px;" />
    </div>
    """,
    unsafe_allow_html=True,
)

        # quick “next targets” nudge (simple + motivating)
        # Aim: SWR 1.40+ (Strong V zone)
        target_swr = 1.40
        if swr < target_swr:
            # If shoulders stayed the same, what waist would hit 1.40?
            waist_needed = shoulders_in / target_swr
            # If waist stayed the same, what shoulders would hit 1.40?
            shoulders_needed = waist_in * target_swr

            st.caption(
                f"To hit **Strong V (≥{target_swr:.2f})**:\n"
                f"- If shoulders stayed **{shoulders_in:.2f}\"**, waist would need ≈ **{waist_needed:.1f}\"**\n"
                f"- If waist stayed **{waist_in:.2f}\"**, shoulders would need ≈ **{shoulders_needed:.1f}\"**"
            )
        else:
            st.success("You’re in Strong V territory or better — keep stacking shoulder/back and guarding waist drift.")
# Main focus next week
misses = []
if not logging_ok:
    misses.append(f"log at least {MIN_LOG_DAYS}/7 days")
if pd.notna(cal_ok_pct) and float(cal_ok_pct) < 70:
    misses.append("tighten calories to within ±10% more often")
if pd.notna(macro_ok_pct) and float(macro_ok_pct) < 60:
    misses.append("hit protein/carbs/fat targets within ±10%")
if not training_ok:
    misses.append(f"hit {TARGET_WORKOUTS} workouts + {TARGET_MINUTES} min")
if pd.notna(steps_avg) and float(steps_avg) < TARGET_STEPS:
    misses.append("push steps toward 8k/day")

if misses:
    st.caption("Main focus next week: " + " • ".join(misses))
else:
    st.caption("Main focus next week: keep doing what you’re doing — this is clean.")
# -------------------------
# Small helpers
# -------------------------
def _within_tol(actual, target, tol):
    if pd.isna(actual) or pd.isna(target) or float(target) == 0:
        return False
    return abs(float(actual) - float(target)) <= (abs(float(target)) * float(tol))

def _safe_mean(series):
    s = pd.to_numeric(series, errors="coerce")
    return s.mean() if s.notna().any() else pd.NA

def _score_label(score: int) -> str:
    if score >= 85: return "Locked In 🔥"
    if score >= 70: return "Solid ✅"
    if score >= 55: return "Okay — Tighten Up"
    return "Tighten Up ⚠️"

# ============================================================
# This week so far (Mon -> today)
# ============================================================
st.subheader("This week so far")

today = pd.Timestamp.today().normalize()
week_start_ts = monday_of(today)          # Timestamp
week_end_ts = today                       # Timestamp

# ---- Daily energy
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

daily_energy_week = filter_range(daily_energy, week_start_ts, week_end_ts, "date")
daily_energy_week_logged = pick_logged_days(daily_energy_week)

# ---- Workout log
workout_log = load_sheet(WS_WORKOUT_LOG)
workout_log = normalise_workout_log_schema(workout_log)
workout_log = normalise_date_col(workout_log, "date")
workout_log = to_num(workout_log, ["workout_duration", "weight_lb", "reps", "rir"])

workout_week = filter_range(workout_log, week_start_ts, week_end_ts, "date")
workout_week = ensure_df(workout_week)

c1, c2, c3, c4 = st.columns(4)

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

# Training so far this week
if (not workout_week.empty) and ("workout_duration" in workout_week.columns) and ("workout" in workout_week.columns):
    wk = workout_week.copy()
    wk["session_key"] = wk["date"].dt.date.astype(str) + "|" + wk["workout"].astype(str).str.strip().str.lower()

    session_minutes = safe_num(wk.groupby("session_key")["workout_duration"].max()) / 60.0
    minutes_total = float(session_minutes.sum()) if session_minutes.notna().any() else pd.NA
    workouts_completed = int(session_minutes.shape[0])

    stypes = wk["set_type"].astype(str).str.strip().str.lower() if "set_type" in wk.columns else pd.Series([], dtype=str)
    wk_working = wk[stypes.isin(["standard set", "failure set"])].copy() if len(stypes) else wk.copy()

    sets_total = int(len(wk_working))
    volume_total = (
        (safe_num(wk_working["weight_lb"]) * safe_num(wk_working["reps"])).sum()
        if ("weight_lb" in wk_working.columns and "reps" in wk_working.columns) else pd.NA
    )
    avg_rir = safe_num(wk_working["rir"]).mean() if "rir" in wk_working.columns else pd.NA
    sets_per_hour = sets_total / (minutes_total / 60.0) if (pd.notna(minutes_total) and minutes_total > 0) else pd.NA

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
# Food patterns + Micronutrients
# ============================================================
st.header("🍽️ Food patterns")

food_view = pd.DataFrame()
food_week = pd.DataFrame()

food = load_sheet(WS_FOOD_LOG)

# normalise column names
food.columns = [str(c).strip() for c in food.columns]

if food.empty:
    st.caption(f"{WS_FOOD_LOG} is empty (or missing).")
else:
    food = food.copy()

    # Your staging columns (you may also have Servingsize, weight_g, weight_g_1, etc. — that's fine)
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
            "Fiber (g)": "fiber_g",
            "Sodium (mg)": "sodium_mg",
            "Potassium (mg)": "potassium_mg",
            "Caffeine (mg)": "caffeine_mg",
        })
    else:
        st.error(f"Food log columns not recognized. Columns found: {list(food.columns)}")
        st.stop()

    food = normalise_date_col(food, "date")
    food = normalise_fibre_fiber_cols(food)

    for c in ["calories", "protein", "carbs", "fat", "fiber_g", "fibre_g", "sodium_mg", "potassium_mg", "caffeine_mg"]:
        if c in food.columns:
            food[c] = pd.to_numeric(food[c], errors="coerce")

    if "food_name" in food.columns:
        food["food_name"] = food["food_name"].astype(str).str.strip()

    food_view = filter_range(food, start_date, end_date_for_weekly, "date")
    food_week = filter_range(food, week_start_ts, week_end_ts, "date")

# ============================================================
# 🍗 This week so far — Macro totals
# ============================================================
st.subheader("🍗 This week so far — Macros")

if food_week.empty:
    st.caption("No food rows this week so far.")
else:
    weekly_totals = (
        food_week.groupby("date", as_index=False)[["calories", "protein", "carbs", "fat"]]
        .sum(numeric_only=True)
    )
    avg_day = weekly_totals[["calories", "protein", "carbs", "fat"]].mean(numeric_only=True)
    total_week = weekly_totals[["calories", "protein", "carbs", "fat"]].sum(numeric_only=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Calories avg/day", f"{avg_day['calories']:.0f}")
    with c2:
        st.metric("Protein avg/day", f"{avg_day['protein']:.0f} g")
    with c3:
        st.metric("Carbs avg/day", f"{avg_day['carbs']:.0f} g")
    with c4:
        st.metric("Fat avg/day", f"{avg_day['fat']:.0f} g")

    st.caption(
        f"Week totals — Calories: {total_week['calories']:.0f} | "
        f"Protein: {total_week['protein']:.0f}g | "
        f"Carbs: {total_week['carbs']:.0f}g | "
        f"Fat: {total_week['fat']:.0f}g"
    )

# ============================================================
# 🧂 This week so far — Micronutrients + Quality
# ============================================================
st.subheader("🧂 This week so far — Micronutrients + Quality")

fiber_col     = coalesce_col(food_week, ["fiber_g", "fibre_g"])
sodium_col    = coalesce_col(food_week, ["sodium_mg"])
potassium_col = coalesce_col(food_week, ["potassium_mg"])
caffeine_col  = coalesce_col(food_week, ["caffeine_mg"])

def daily_avg_from_food(df: pd.DataFrame, colname: str | None):
    if df.empty or colname is None or colname not in df.columns:
        return pd.NA
    daily = df.groupby("date", as_index=False)[colname].sum(numeric_only=True)
    return pd.to_numeric(daily[colname], errors="coerce").mean()

fibre_avg     = daily_avg_from_food(food_week, fiber_col)
sodium_avg    = daily_avg_from_food(food_week, sodium_col)
potassium_avg = daily_avg_from_food(food_week, potassium_col)
caffeine_avg  = daily_avg_from_food(food_week, caffeine_col)

# Fallback from Weekly_Energy if food micros missing
micro_cols = ["avg_fiber_g", "avg_sodium_mg", "avg_potassium_mg", "avg_caffeine_mg"]
need_fallback = all(pd.isna(x) for x in [fibre_avg, sodium_avg, potassium_avg, caffeine_avg])

if need_fallback and not energy.empty:
    we = energy.copy()
    for c in micro_cols:
        if c in we.columns:
            we[c] = pd.to_numeric(we[c], errors="coerce")

    # Prefer the row matching this week's Monday (MacroFactor weekly stamp)
    this_week_row = we[we["date"].dt.normalize() == week_start_ts]
    if not this_week_row.empty:
        row = this_week_row.sort_values("date").iloc[-1]
    else:
        pick = we[we["date"] <= today].dropna(subset=[c for c in micro_cols if c in we.columns], how="all")
        row = pick.sort_values("date").iloc[-1] if not pick.empty else None

    if row is not None:
        fibre_avg     = row.get("avg_fiber_g", pd.NA)
        sodium_avg    = row.get("avg_sodium_mg", pd.NA)
        potassium_avg = row.get("avg_potassium_mg", pd.NA)
        caffeine_avg  = row.get("avg_caffeine_mg", pd.NA)

m1, m2, m3, m4 = st.columns(4)
with m1:
    st.metric("Fibre (avg/day)", metric_or_dash(fibre_avg, "{:.1f} g"))
with m2:
    st.metric("Sodium (avg/day)", metric_or_dash(sodium_avg, "{:.0f} mg"))
with m3:
    st.metric("Potassium (avg/day)", metric_or_dash(potassium_avg, "{:.0f} mg"))
with m4:
    st.metric("Caffeine (avg/day)", metric_or_dash(caffeine_avg, "{:.0f} mg"))

rules = [
    {"metric": "Fibre", "avg_per_day": fibre_avg, "rule": "Flag if < 25 g",
     "flag": (pd.notna(fibre_avg) and float(fibre_avg) < 25)},
    {"metric": "Sodium", "avg_per_day": sodium_avg, "rule": "Flag if > 2500 mg",
     "flag": (pd.notna(sodium_avg) and float(sodium_avg) > 2500)},
    {"metric": "Potassium", "avg_per_day": potassium_avg, "rule": "Flag if < 3000 mg",
     "flag": (pd.notna(potassium_avg) and float(potassium_avg) < 3000)},
    {"metric": "Caffeine", "avg_per_day": caffeine_avg, "rule": "Flag if > 400 mg",
     "flag": (pd.notna(caffeine_avg) and float(caffeine_avg) > 400)},
]
flags_df = pd.DataFrame(rules)
st.dataframe(flags_df, hide_index=True)

# ============================================================
# Patterns (selected range)
# ============================================================
st.subheader("📈 Patterns — Selected range")

if food_view.empty:
    st.caption("No food rows in the selected date range.")
else:
    fv = food_view.copy()
    fv["date"] = pd.to_datetime(fv["date"], errors="coerce").dt.normalize()

    st.subheader("Weekday vs weekend (avg daily totals)")
    daily_food = (
        fv.groupby("date", as_index=False)[["calories", "protein", "carbs", "fat"]]
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

    if "food_name" in fv.columns:
        st.subheader("Top foods (by frequency)")
        top_freq = (
            fv["food_name"].astype(str).str.strip().replace({"": pd.NA}).dropna()
            .value_counts().head(15).reset_index()
        )
        top_freq.columns = ["food_name", "count"]
        st.dataframe(top_freq, hide_index=True)

        st.subheader("Top foods (by totals)")
        colX, colY = st.columns(2)

        with colX:
            st.caption("By total calories")
            top_cal = (
                fv.groupby("food_name", as_index=False)["calories"]
                .sum().sort_values("calories", ascending=False).head(15)
            )
            st.dataframe(top_cal, hide_index=True)

        with colY:
            st.caption("By total protein")
            top_pro = (
                fv.groupby("food_name", as_index=False)["protein"]
                .sum().sort_values("protein", ascending=False).head(15)
            )
            st.dataframe(top_pro, hide_index=True)
    else:
        st.caption("Missing food_name column; can't compute top foods.")

    st.subheader("Most common foods by time of day")
    if "time" in fv.columns and fv["time"].notna().any() and "food_name" in fv.columns:
        t = pd.to_datetime(fv["time"], errors="coerce")
        fv["hour"] = t.dt.hour

        def bucket(h):
            if pd.isna(h): return "Unknown"
            h = int(h)
            if 5 <= h < 11:  return "Breakfast"
            if 11 <= h < 15: return "Lunch"
            if 15 <= h < 19: return "Dinner"
            return "Evening"

        fv["meal_bucket"] = fv["hour"].apply(bucket)

        for bucket_name in ["Breakfast", "Lunch", "Dinner", "Evening"]:
            sub = fv[fv["meal_bucket"] == bucket_name]
            if sub.empty:
                continue
            st.caption(bucket_name)
            b = (
                sub["food_name"].astype(str).str.strip().replace({"": pd.NA}).dropna()
                .value_counts().head(10).reset_index()
            )
            b.columns = ["food_name", "count"]
            st.dataframe(b, hide_index=True)
    else:
        st.caption("No usable time data available in the selected range.")

    st.subheader("Daily macros over time (grams)")
    daily_macros = (
        fv.groupby("date", as_index=False)[["protein", "carbs", "fat"]]
        .sum(numeric_only=True)
        .dropna()
    )

    macro_m = pd.DataFrame()
    if not daily_macros.empty:
        macro_m = daily_macros.melt("date", var_name="macro", value_name="grams").dropna()

    if macro_m.empty:
        st.caption("No daily macro totals to chart in the selected range.")
    else:
        st.altair_chart(
            alt.Chart(macro_m).mark_line(point=True).encode(
                x=alt.X("date:T", title="Date"),
                y=alt.Y("grams:Q", title="grams"),
                color=alt.Color("macro:N", title=""),
                tooltip=["date:T", "macro:N", alt.Tooltip("grams:Q", format=".0f")],
            ).properties(height=260),
            use_container_width=True
        )

# ============================================================
# Protein distribution by meal window (Food Log)
# ============================================================
st.header("🥩 Protein distribution (meal windows)")

if food_week.empty:
    st.caption("No food log rows this week so far.")
else:
    hour = parse_time_to_hour(food_week["time"]) if "time" in food_week.columns else pd.Series([pd.NA] * len(food_week))
    fw = food_week.copy()
    fw["hour"] = hour

    def meal_bucket(h):
        if pd.isna(h):
            return "Unknown"
        h = int(h)
        if 5 <= h < 11: return "Breakfast"
        if 11 <= h < 15: return "Lunch"
        if 15 <= h < 19: return "Dinner"
        return "Evening"

    fw["meal_bucket"] = fw["hour"].apply(meal_bucket)

    dist = (
        fw.groupby("meal_bucket", as_index=False)[["protein", "calories", "carbs", "fat"]]
        .sum(numeric_only=True)
    )

    order = ["Breakfast", "Lunch", "Dinner", "Evening", "Unknown"]
    dist["meal_bucket"] = pd.Categorical(dist["meal_bucket"], categories=order, ordered=True)
    dist = dist.sort_values("meal_bucket")

    cA, cB = st.columns([1, 1.4])
    with cA:
        st.subheader("This week so far (totals)")
        st.dataframe(dist.round(0), hide_index=True)

    with cB:
        pdist = dist[dist["protein"].notna()].copy()
        if not pdist.empty and pdist["protein"].sum() > 0:
            pdist["protein_pct"] = pdist["protein"] / pdist["protein"].sum() * 100
            chart = alt.Chart(pdist).mark_bar().encode(
                x=alt.X("meal_bucket:N", title="Meal window"),
                y=alt.Y("protein_pct:Q", title="Protein share (%)"),
                tooltip=[
                    alt.Tooltip("meal_bucket:N"),
                    alt.Tooltip("protein:Q", title="Protein (g)", format=".0f"),
                    alt.Tooltip("protein_pct:Q", title="Share (%)", format=".0f"),
                ],
            ).properties(height=280)
            st.subheader("Protein share by meal window")
            st.altair_chart(chart, use_container_width=True)

st.divider()

# ============================================================
# Muscle-group balance (Sets + Volume)
# ============================================================
st.header("💪 Muscle-group balance")

mus_sets = load_sheet(WS_MUSCLE_SETS)
mus_vol = load_sheet(WS_MUSCLE_VOLUME)

def normalise_muscle_table(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "Date" in out.columns and "date" not in out.columns:
        out = out.rename(columns={"Date": "date"})
    out = normalise_date_col(out, "date")
    for c in out.columns:
        if c != "date":
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out

mus_sets = normalise_muscle_table(mus_sets)
mus_vol = normalise_muscle_table(mus_vol)

ms_week = filter_range(mus_sets, week_start_ts, week_end_ts, "date") if not mus_sets.empty else pd.DataFrame()
mv_week = filter_range(mus_vol, week_start_ts, week_end_ts, "date") if not mus_vol.empty else pd.DataFrame()

def melt_muscles(df: pd.DataFrame, value_name: str) -> pd.DataFrame:
    if df.empty or "date" not in df.columns:
        return pd.DataFrame()
    muscle_cols = [c for c in df.columns if c != "date"]
    if not muscle_cols:
        return pd.DataFrame()
    m = df.melt("date", value_vars=muscle_cols, var_name="muscle", value_name=value_name)
    return m.dropna(subset=[value_name])

ms_sum = pd.DataFrame()
mv_sum = pd.DataFrame()

if not ms_week.empty:
    ms_m = melt_muscles(ms_week, "sets")
    ms_sum = ms_m.groupby("muscle", as_index=False)["sets"].sum().sort_values("sets", ascending=False)

if not mv_week.empty:
    mv_m = melt_muscles(mv_week, "volume")
    mv_sum = mv_m.groupby("muscle", as_index=False)["volume"].sum().sort_values("volume", ascending=False)

if ms_sum.empty and mv_sum.empty:
    st.caption("No muscle-group sets/volume data found (tabs empty or missing).")
else:
    left, right = st.columns(2)
    with left:
        if not ms_sum.empty:
            st.subheader("This week so far — Sets")
            st.dataframe(ms_sum.round(0), hide_index=True)
            chart = alt.Chart(ms_sum).mark_bar().encode(
                x=alt.X("muscle:N", sort="-y", title="Muscle"),
                y=alt.Y("sets:Q", title="Sets (sum)"),
                tooltip=[alt.Tooltip("muscle:N"), alt.Tooltip("sets:Q", format=".0f")],
            ).properties(height=300)
            st.altair_chart(chart, use_container_width=True)

    with right:
        if not mv_sum.empty:
            st.subheader("This week so far — Volume")
            st.dataframe(mv_sum.round(0), hide_index=True)
            chart = alt.Chart(mv_sum).mark_bar().encode(
                x=alt.X("muscle:N", sort="-y", title="Muscle"),
                y=alt.Y("volume:Q", title="Volume (sum)"),
                tooltip=[alt.Tooltip("muscle:N"), alt.Tooltip("volume:Q", format=".0f")],
            ).properties(height=300)
            st.altair_chart(chart, use_container_width=True)

st.divider()

# ============================================================
# Consistency score (week so far)
# ============================================================
st.header("✅ Consistency score (this week so far)")

days_elapsed = int((today - week_start_ts).days) + 1
days_logged = int(len(daily_energy_week_logged)) if not daily_energy_week_logged.empty else 0

workouts = 0
minutes_total = pd.NA
if not workout_week.empty and "workout" in workout_week.columns:
    wk = workout_week.copy()
    wk["session_key"] = wk["date"].dt.date.astype(str) + "|" + wk["workout"].astype(str).str.strip().str.lower()
    workouts = int(wk["session_key"].nunique())
    if "workout_duration" in wk.columns:
        mins = pd.to_numeric(wk.groupby("session_key")["workout_duration"].max(), errors="coerce") / 60.0
        minutes_total = float(mins.sum()) if mins.notna().any() else pd.NA

steps_avg = pd.NA
if not daily_energy_week_logged.empty and "steps" in daily_energy_week_logged.columns:
    steps_avg = pd.to_numeric(daily_energy_week_logged["steps"], errors="coerce").mean()

log_pct = (days_logged / days_elapsed * 100) if days_elapsed > 0 else pd.NA

s1, s2, s3, s4 = st.columns(4)
with s1:
    st.metric("Days elapsed", days_elapsed)
with s2:
    st.metric("Days logged", days_logged)
with s3:
    st.metric("Logged %", metric_or_dash(log_pct, "{:.0f}%"))
with s4:
    st.metric("Workouts", workouts)

t1, t2 = st.columns(2)
with t1:
    st.metric("Training minutes", metric_or_dash(minutes_total, "{:.0f}"))
with t2:
    st.metric("Avg steps", metric_or_dash(steps_avg, "{:.0f}"))
