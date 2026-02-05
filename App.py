/*******************************************************
 * GrindWorks Body Comp + MacroFactor (Energy + Training + Food)
 *
 * REQUIREMENTS
 * - Advanced Google Services: Drive API enabled (for Drive.Files.copy)
 * - Sheets that must exist (names must match CONFIG):
 *   Staging_Renpho
 *   Weekly_BodyComp
 *   Daily_Energy
 *   Weekly_Energy
 *   Exercise_Muscle_Map
 *   Staging_Workout_Log
 *   Staging_Muscle_Sets
 *   Staging_Muscle_Volume
 *   Weekly_Training
 *   Staging_MacroFactor_FoodLog   (10 columns, incl Serving Size)
 *
 * Exercise_Muscle_Map columns (row 1):
 * exercise | muscle_primary | muscle_secondary | muscle_tertiary |
 * secondary_weight | tertiary_weight | include_for_sets | include_for_volume | notes
 *
 * Muscle names (use exactly):
 * chest, back, legs, shoulders, biceps, triceps
 *
 * NOTE (Weekly_Training):
 * This script writes 8 columns:
 * date | sets_total | volume_total | workouts_completed | avg_RIR |
 * muscle_groups_hit_count | training_minutes_total | avg_workout_minutes
 *******************************************************/

// ===============================
// CONFIG
// ===============================

// Renpho folders
const RENPHO_INBOX_FOLDER_ID     = "1C7zJ6_RrAM6yDcl2Vs3ZKEB2CSTfHESR";
const RENPHO_PROCESSED_FOLDER_ID = "11tk6omigItgdAbSR4Rrm46biDW1I1WsJ";

// MacroFactor folders
const MF_INBOX_FOLDER_ID         = "1a6MlRckc6G5JzIL5QYfcODihqIV75WCC";
const MF_PROCESSED_FOLDER_ID     = "17pAZyHudFfmQE0lDZtQD5RfTMWtpyj_g";

// Sheets
const STAGING_RENPHO_SHEET_NAME = "Staging_Renpho";
const WEEKLY_BODYCOMP_SHEET     = "Weekly_BodyComp";

const DAILY_ENERGY_SHEET        = "Daily_Energy";
const WEEKLY_ENERGY_SHEET       = "Weekly_Energy";

const EXERCISE_MAP_SHEET        = "Exercise_Muscle_Map";

const STAGING_WORKOUT_LOG_SHEET = "Staging_Workout_Log";
const STAGING_MUSCLE_SETS_SHEET = "Staging_Muscle_Sets";
const STAGING_MUSCLE_VOL_SHEET  = "Staging_Muscle_Volume";
const WEEKLY_TRAINING_SHEET     = "Weekly_Training";

const FOOD_LOG_SHEET_NAME       = "Staging_MacroFactor_FoodLog";

// MacroFactor Food Log headers (exact from your export)
const MF_FOOD_HEADERS = {
  date: "Date",
  time: "Time",
  foodName: "Food Name",
  servingSize: "Serving Size",
  servingQty: "Serving Qty",
  servingWeightG: "Serving Weight (g)",
  calories: "Calories (kcal)",
  fat: "Fat (g)",
  carbs: "Carbs (g)",
  protein: "Protein (g)",
};

// Renpho CSV/XLS headers (alias-based, robust)
const RENPHO_HEADERS = {
  date: ["Date", "Measurement Date", "Date (Local)"],
  time: ["Time", "Measurement Time", "Time (Local)"],
  weight: ["Weight(lb)", "Weight (lb)", "Weight"],
  bodyFat: ["Body Fat(%)", "Body Fat %", "Body Fat"],
  ffm: ["Fat-Free Mass(lb)", "Fat-Free Mass (lb)", "FFM(lb)", "FFM"],
};

// Old MacroFactor Quick Export headers (legacy)
const MF_QE_HEADERS = {
  date: "Date",
  expenditure: "Expenditure",
  trendWeight: "Trend Weight (lbs)",
  weight: "Weight (lbs)",
  calories: "Calories (kcal)",
  protein: "Protein (g)",
  carbs: "Carbs (g)",
  fat: "Fat (g)",
  targetCalories: "Target Calories (kcal)",
  targetProtein: "Target Protein (g)",
  targetCarbs: "Target Carbs (g)",
  targetFat: "Target Fat (g)",
  steps: "Steps",
};

// Common MacroFactor sheet names (exact + fuzzy matching is also used)
const MF_SHEETS_V2 = {
  caloriesMacros: "Calories & Macros",
  expenditure: "Expenditure",
  weightTrend: "Weight Trend",
  programSettings: "Nutrition Program Settings",
  foodLog: "Food Log",
  workoutLog: "Workout Log",
  quickExport: "Quick Export", // legacy
};

// ===============================
// MAIN ENTRYPOINTS
// ===============================

/**
 * Normal run: process new files from inbox folders (Renpho + MF),
 * then rebuild all derived tabs.
 */
function processNewFiles() {
  // Renpho -> staging (moves files to processed)
  processRenphoInbox_();

  // MacroFactor (IMPORTANT ORDER)
  processMacroFactorInboxToDailyEnergy_(); // 1) daily energy (does NOT move files)
  processMacroFactorInboxToFoodLog_();     // 2) food log (does NOT move files)
  processMacroFactorInboxToWorkoutLog_();  // 3) workout log (moves file to processed)

  // Rebuild derived tables
  rebuildWeeklyBodyComp_();
  rebuildWeeklyEnergyFromDaily_();

  rebuildMuscleSets_();
  rebuildMuscleVolume_();
  rebuildWeeklyTraining_();

  Logger.log("✅ Done: Renpho + MacroFactor processed; energy + training + food rebuilt.");
}

/**
 * Full rebuild from ALL files (Inbox + Processed) for MacroFactor,
 * and (optionally) Renpho if you want to add it in.
 *
 * NOTE: This version clears MF destination tabs only (as you had it),
 * and rebuilds derived tables.
 */
function rebuildEverythingFromAllFiles() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // Clear destination/staging tabs (keep headers)
  clearSheetButKeepHeader_(mustGetSheet_(ss, DAILY_ENERGY_SHEET));
  clearSheetButKeepHeader_(mustGetSheet_(ss, FOOD_LOG_SHEET_NAME));
  clearSheetButKeepHeader_(mustGetSheet_(ss, STAGING_WORKOUT_LOG_SHEET));

  // Rebuild from ALL MF files (Processed first, then Inbox)
  processMacroFactorAllFilesToDailyEnergy_();
  processMacroFactorAllFilesToFoodLog_();
  processMacroFactorAllFilesToWorkoutLog_();

  // Rebuild derived tables
  rebuildWeeklyBodyComp_();
  rebuildWeeklyEnergyFromDaily_();

  rebuildMuscleSets_();
  rebuildMuscleVolume_();
  rebuildWeeklyTraining_();

  Logger.log("✅ Full rebuild complete: ALL files (Inbox + Processed) ingested and tables rebuilt.");
}

// ===============================
// CORE HELPERS
// ===============================

function mustGetSheet_(ss, name) {
  const sh = ss.getSheetByName(name);
  if (!sh) throw new Error(`Sheet not found: ${name}`);
  return sh;
}

function clearSheetButKeepHeader_(sheet) {
  const lastRow = sheet.getLastRow();
  if (lastRow >= 2) {
    sheet.getRange(2, 1, lastRow - 1, sheet.getMaxColumns()).clearContent();
  }
}

function round_(num, decimals) {
  const p = Math.pow(10, decimals);
  return Math.round(num * p) / p;
}

// ===============================
// RENPHO (FIXED + CLEAN)
// ===============================

/**
 * Renpho normal ingest:
 * - Reads files from Renpho inbox
 * - Appends rows to Staging_Renpho: [datetime, weight, bodyfat, ffm]
 * - Moves successfully parsed files to processed
 * - De-dupes by FILE ID via script properties
 */
function processRenphoInbox_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const staging = mustGetSheet_(ss, STAGING_RENPHO_SHEET_NAME);

  const inbox = DriveApp.getFolderById(RENPHO_INBOX_FOLDER_ID);
  const processed = DriveApp.getFolderById(RENPHO_PROCESSED_FOLDER_ID);

  const seen = getProcessedRenphoIds_();

  const files = inbox.getFiles();
  let processedCount = 0;
  let skippedCount = 0;
  let appended = 0;

  while (files.hasNext()) {
    const file = files.next();
    const id = file.getId();

    // ✅ dedupe by ID, not name
    if (seen.has(id)) {
      skippedCount++;
      continue;
    }

    try {
      const rows = parseRenphoFileToRows_(file);
      if (rows.length > 0) {
        staging.getRange(staging.getLastRow() + 1, 1, rows.length, 4).setValues(rows);
        appended += rows.length;
      }

      // mark processed + move
      seen.add(id);
      processed.addFile(file);
      inbox.removeFile(file);
      processedCount++;

    } catch (e) {
      Logger.log("❌ Failed on Renpho file: " + file.getName() + " | " + id);
      Logger.log(e && e.stack ? e.stack : e);
      // IMPORTANT: do NOT mark as processed if it failed
    }
  }

  saveProcessedRenphoIds_(seen);
  Logger.log(`Renpho: appended=${appended}, processed_files=${processedCount}, skipped_seen=${skippedCount}, seen_total=${seen.size}`);
}

/**
 * Parse Renpho file into staging rows:
 * Output rows: [ "YYYY-MM-DD HH:MM:SS", weight_lb, bodyfat_pct, ffm_lb ]
 */
function parseRenphoFileToRows_(file) {
  const mime = file.getMimeType();
  const name = String(file.getName() || "").toLowerCase();

  let values;

  // Excel -> convert to temp google sheet -> read values
  if (
    mime === MimeType.MICROSOFT_EXCEL ||
    mime === "application/vnd.ms-excel" ||
    mime === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" ||
    name.endsWith(".xlsx") ||
    name.endsWith(".xls")
  ) {
    const sheetId = excelToTempSheet_(file.getId());
    try {
      const ss = SpreadsheetApp.openById(sheetId);
      const ws = ss.getSheets()[0];
      values = ws.getDataRange().getValues();
    } finally {
      try { DriveApp.getFileById(sheetId).setTrashed(true); } catch (e) {}
    }
  }
  // CSV / text
  else if (mime === MimeType.CSV || mime === MimeType.PLAIN_TEXT || name.endsWith(".csv")) {
    const csv = file.getBlob().getDataAsString();
    values = Utilities.parseCsv(csv);
  } else {
    throw new Error("Unsupported RENPHO mime: " + mime);
  }

  if (!values || values.length < 2) return [];

  const header = values[0].map(h => String(h).trim());
  const idx = headerIndexesRenpho_(header);

  const out = [];

  for (let i = 1; i < values.length; i++) {
    const r = values[i];
    if (!r || r.length === 0) continue;

    const d = parseDateFlexible_(r[idx.date]);
    if (!d) continue;

    const timeStr = String(r[idx.time] ?? "").trim();
    if (!timeStr) continue;

    const w   = Number(r[idx.weight]);
    const bf  = Number(r[idx.bodyFat]);
    const ffm = Number(r[idx.ffm]);
    if (![w, bf, ffm].every(isFinite)) continue;

    const dtStr = `${formatDate_(d)} ${timeStr}`;
    out.push([dtStr, w, bf, ffm]);
  }

  return out;
}

function headerIndexesRenpho_(headerRow) {
  const norm = headerRow.map(h => normalizeHeader_(h));

  const findIndex = (aliases, label) => {
    const normAliases = aliases.map(a => normalizeHeader_(a));
    for (const a of normAliases) {
      const i = norm.indexOf(a);
      if (i !== -1) return i;
    }
    throw new Error(`Missing Renpho column: ${label}. Found: ${headerRow.join(", ")}`);
  };

  return {
    date:    findIndex(RENPHO_HEADERS.date, "Date"),
    time:    findIndex(RENPHO_HEADERS.time, "Time"),
    weight:  findIndex(RENPHO_HEADERS.weight, "Weight"),
    bodyFat: findIndex(RENPHO_HEADERS.bodyFat, "Body Fat"),
    ffm:     findIndex(RENPHO_HEADERS.ffm, "Fat-Free Mass"),
  };
}

function excelToTempSheet_(fileId) {
  // Requires Advanced Drive Service enabled (Drive API)
  const temp = Drive.Files.copy(
    { title: "TEMP_RENPHO_" + fileId, mimeType: MimeType.GOOGLE_SHEETS },
    fileId,
    { convert: true }
  );
  return temp.id;
}

function getProcessedRenphoIds_() {
  const props = PropertiesService.getScriptProperties();
  const raw = props.getProperty("RENPHO_PROCESSED_IDS");
  return raw ? new Set(JSON.parse(raw)) : new Set();
}

function saveProcessedRenphoIds_(set) {
  PropertiesService.getScriptProperties()
    .setProperty("RENPHO_PROCESSED_IDS", JSON.stringify([...set]));
}

// ===============================
// MACROFACTOR INBOX (normal run)
// ===============================

function processMacroFactorInboxToDailyEnergy_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const daily = mustGetSheet_(ss, DAILY_ENERGY_SHEET);
  const inbox = DriveApp.getFolderById(MF_INBOX_FOLDER_ID);

  // De-dupe by date in col A
  const existing = buildExistingKeySet_(daily, 1, "date");

  const files = inbox.getFiles();
  let appended = 0;

  while (files.hasNext()) {
    const file = files.next();
    const name = String(file.getName() || "");
    if (!name.toLowerCase().endsWith(".xlsx")) continue;

    const tempSheetId = convertXlsxToGoogleSheet_WithRetry_(file.getId(), `TEMP_MF_${name}`, 5);
    try {
      const tempSS = SpreadsheetApp.openById(tempSheetId);

      // Prefer legacy Quick Export if present, else use new v2 sheets
      const qe = tempSS.getSheetByName(MF_SHEETS_V2.quickExport);
      if (qe) appended += ingestDailyFromQuickExport_(qe, daily, existing);
      else    appended += ingestDailyFromV2Sheets_(tempSS, daily, existing, name);

    } finally {
      try { DriveApp.getFileById(tempSheetId).setTrashed(true); } catch (e) {}
    }
  }

  Logger.log(`MacroFactor → Daily_Energy: appended ${appended} rows.`);
}

function processMacroFactorInboxToFoodLog_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const foodSheet = mustGetSheet_(ss, FOOD_LOG_SHEET_NAME);
  const inbox = DriveApp.getFolderById(MF_INBOX_FOLDER_ID);

  const existing = buildFoodExistingKeySet_(foodSheet);

  const files = inbox.getFiles();
  let appended = 0;

  while (files.hasNext()) {
    const file = files.next();
    const name = String(file.getName() || "");
    if (!name.toLowerCase().endsWith(".xlsx")) continue;

    const tempSheetId = convertXlsxToGoogleSheet_WithRetry_(file.getId(), `TEMP_MF_${name}`, 5);
    try {
      const tempSS = SpreadsheetApp.openById(tempSheetId);

      const fl =
        tempSS.getSheetByName(MF_SHEETS_V2.foodLog) ||
        findSheetByKeywords_(tempSS, ["food", "log"], MF_SHEETS_V2.foodLog);

      if (!fl) {
        Logger.log(`MacroFactor: no Food Log-like sheet found in ${name} (skipping food ingest)`);
        continue;
      }

      appended += ingestFoodLogSheet_(fl, foodSheet, existing);

    } finally {
      try { DriveApp.getFileById(tempSheetId).setTrashed(true); } catch (e) {}
    }
  }

  Logger.log(`Food Log: appended ${appended} meal rows.`);
}

function processMacroFactorInboxToWorkoutLog_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const staging = mustGetSheet_(ss, STAGING_WORKOUT_LOG_SHEET);

  const inbox = DriveApp.getFolderById(MF_INBOX_FOLDER_ID);
  const processed = DriveApp.getFolderById(MF_PROCESSED_FOLDER_ID);

  const existing = buildWorkoutExistingKeySet_(staging);

  const files = inbox.getFiles();
  let appended = 0;

  while (files.hasNext()) {
    const file = files.next();
    const name = String(file.getName() || "");
    if (!name.toLowerCase().endsWith(".xlsx")) continue;

    const tempSheetId = convertXlsxToGoogleSheet_WithRetry_(file.getId(), `TEMP_MF_${name}`, 5);
    try {
      const tempSS = SpreadsheetApp.openById(tempSheetId);

      const wl =
        tempSS.getSheetByName(MF_SHEETS_V2.workoutLog) ||
        findSheetByKeywords_(tempSS, ["workout", "log"], MF_SHEETS_V2.workoutLog);

      if (!wl) {
        Logger.log(`MacroFactor: no Workout Log-like sheet found in ${name} (skipping workout ingest)`);
        // Your prior behaviour: move to processed even if WL missing
        moveToProcessed_(file, processed);
        continue;
      }

      appended += ingestWorkoutLogSheet_(wl, staging, existing);

      moveToProcessed_(file, processed);

    } finally {
      try { DriveApp.getFileById(tempSheetId).setTrashed(true); } catch (e) {}
    }
  }

  Logger.log(`Workout Log: appended ${appended} set rows.`);
}

// ===============================
// MACROFACTOR ALL FILES (full rebuild)
// ===============================

function processMacroFactorAllFilesToDailyEnergy_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const daily = mustGetSheet_(ss, DAILY_ENERGY_SHEET);

  const inbox = DriveApp.getFolderById(MF_INBOX_FOLDER_ID);
  const processed = DriveApp.getFolderById(MF_PROCESSED_FOLDER_ID);

  const existing = new Set(); // rebuilding after clear
  let appended = 0;

  appended += ingestDailyFromMacroFactorFolder_(processed, daily, existing, false);
  appended += ingestDailyFromMacroFactorFolder_(inbox, daily, existing, true);

  Logger.log(`MacroFactor (ALL) → Daily_Energy: appended ${appended} rows.`);
}

function ingestDailyFromMacroFactorFolder_(folder, dailySheet, existingSet, moveToProcessedAfter) {
  const processedFolder = DriveApp.getFolderById(MF_PROCESSED_FOLDER_ID);
  const files = folder.getFiles();
  let appended = 0;

  while (files.hasNext()) {
    const file = files.next();
    const name = String(file.getName() || "");

    if (!name.toLowerCase().endsWith(".xlsx")) {
      Logger.log(`MF Daily: skipping non-xlsx: ${name}`);
      if (moveToProcessedAfter) moveToProcessed_(file, processedFolder);
      continue;
    }

    const tempSheetId = convertXlsxToGoogleSheet_WithRetry_(file.getId(), `TEMP_MF_${name}`, 5);
    try {
      const tempSS = SpreadsheetApp.openById(tempSheetId);
      const qe = tempSS.getSheetByName(MF_SHEETS_V2.quickExport);

      if (qe) appended += ingestDailyFromQuickExport_(qe, dailySheet, existingSet);
      else    appended += ingestDailyFromV2Sheets_(tempSS, dailySheet, existingSet, name);

    } finally {
      try { DriveApp.getFileById(tempSheetId).setTrashed(true); } catch (e) {}
    }

    if (moveToProcessedAfter) moveToProcessed_(file, processedFolder);
  }

  return appended;
}

function processMacroFactorAllFilesToFoodLog_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const foodSheet = mustGetSheet_(ss, FOOD_LOG_SHEET_NAME);

  const inbox = DriveApp.getFolderById(MF_INBOX_FOLDER_ID);
  const processed = DriveApp.getFolderById(MF_PROCESSED_FOLDER_ID);

  const existing = new Set();
  let appended = 0;

  appended += ingestFoodFromMacroFactorFolder_(processed, foodSheet, existing, false);
  appended += ingestFoodFromMacroFactorFolder_(inbox, foodSheet, existing, true);

  Logger.log(`Food Log (ALL): appended ${appended} meal rows.`);
}

function ingestFoodFromMacroFactorFolder_(folder, foodSheet, existingSet, moveToProcessedAfter) {
  const processedFolder = DriveApp.getFolderById(MF_PROCESSED_FOLDER_ID);
  const files = folder.getFiles();
  let appended = 0;

  while (files.hasNext()) {
    const file = files.next();
    const name = String(file.getName() || "");

    if (!name.toLowerCase().endsWith(".xlsx")) {
      Logger.log(`MF Food: skipping non-xlsx: ${name}`);
      if (moveToProcessedAfter) moveToProcessed_(file, processedFolder);
      continue;
    }

    const tempSheetId = convertXlsxToGoogleSheet_WithRetry_(file.getId(), `TEMP_MF_${name}`, 5);
    try {
      const tempSS = SpreadsheetApp.openById(tempSheetId);

      const fl =
        tempSS.getSheetByName(MF_SHEETS_V2.foodLog) ||
        findSheetByKeywords_(tempSS, ["food", "log"], MF_SHEETS_V2.foodLog);

      if (!fl) {
        Logger.log(`MacroFactor: no Food Log-like sheet found in ${name} (skipping)`);
        if (moveToProcessedAfter) moveToProcessed_(file, processedFolder);
        continue;
      }

      appended += ingestFoodLogSheet_(fl, foodSheet, existingSet);

    } finally {
      try { DriveApp.getFileById(tempSheetId).setTrashed(true); } catch (e) {}
    }

    if (moveToProcessedAfter) moveToProcessed_(file, processedFolder);
  }

  return appended;
}

function processMacroFactorAllFilesToWorkoutLog_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const staging = mustGetSheet_(ss, STAGING_WORKOUT_LOG_SHEET);

  const inbox = DriveApp.getFolderById(MF_INBOX_FOLDER_ID);
  const processed = DriveApp.getFolderById(MF_PROCESSED_FOLDER_ID);

  const existing = new Set();
  let appended = 0;

  appended += ingestWorkoutFromMacroFactorFolder_(processed, staging, existing, false);
  appended += ingestWorkoutFromMacroFactorFolder_(inbox, staging, existing, true);

  Logger.log(`Workout Log (ALL): appended ${appended} set rows.`);
}

function ingestWorkoutFromMacroFactorFolder_(folder, stagingSheet, existingSet, moveToProcessedAfter) {
  const processedFolder = DriveApp.getFolderById(MF_PROCESSED_FOLDER_ID);
  const files = folder.getFiles();
  let appended = 0;

  while (files.hasNext()) {
    const file = files.next();
    const name = String(file.getName() || "");

    if (!name.toLowerCase().endsWith(".xlsx")) {
      Logger.log(`MF Workout: skipping non-xlsx: ${name}`);
      if (moveToProcessedAfter) moveToProcessed_(file, processedFolder);
      continue;
    }

    const tempSheetId = convertXlsxToGoogleSheet_WithRetry_(file.getId(), `TEMP_MF_${name}`, 5);
    try {
      const tempSS = SpreadsheetApp.openById(tempSheetId);

      const wl =
        tempSS.getSheetByName(MF_SHEETS_V2.workoutLog) ||
        findSheetByKeywords_(tempSS, ["workout", "log"], MF_SHEETS_V2.workoutLog);

      if (!wl) {
        Logger.log(`MacroFactor: no Workout Log-like sheet found in ${name} (skipping)`);
        if (moveToProcessedAfter) moveToProcessed_(file, processedFolder);
        continue;
      }

      appended += ingestWorkoutLogSheet_(wl, stagingSheet, existingSet);

    } finally {
      try { DriveApp.getFileById(tempSheetId).setTrashed(true); } catch (e) {}
    }

    if (moveToProcessedAfter) moveToProcessed_(file, processedFolder);
  }

  return appended;
}

// ===============================
// INGEST: DAILY ENERGY (Quick Export legacy)
// ===============================

function ingestDailyFromQuickExport_(qeSheet, dailySheet, existingSet) {
  const values = qeSheet.getDataRange().getValues();
  if (!values || values.length < 2) return 0;

  const header = values[0].map(h => String(h).trim());
  const idx = headerIndexesMFQuickExport_(header);

  const toAppend = [];

  for (let i = 1; i < values.length; i++) {
    const r = values[i];
    if (!r || r.length === 0) continue;

    const d = parseDateFlexible_(r[idx.date]);
    if (!d) continue;

    const dateKey = formatDate_(d);
    if (existingSet.has(dateKey)) continue;

    const expenditure = Number(r[idx.expenditure]);
    const trendW      = Number(r[idx.trendWeight]);
    const weight      = Number(r[idx.weight]);

    const calories    = Number(r[idx.calories]);
    const protein     = Number(r[idx.protein]);
    const carbs       = Number(r[idx.carbs]);
    const fat         = Number(r[idx.fat]);

    const calTarget   = Number(r[idx.targetCalories]);
    const protTarget  = Number(r[idx.targetProtein]);
    const carbTarget  = Number(r[idx.targetCarbs]);
    const fatTarget   = Number(r[idx.targetFat]);

    const steps       = Number(r[idx.steps]);

    const nums = [
      expenditure, trendW, weight,
      calories, protein, carbs, fat,
      calTarget, protTarget, carbTarget, fatTarget,
      steps
    ];
    if (!nums.every(isFinite)) continue;

    const logged = (calories > 0 ? 1 : 0);

    const calorieDelta = logged ? (calories - expenditure) : "";
    const proteinAdh   = (logged && protTarget > 0) ? (protein / protTarget) : "";
    const energyAdh    = (logged && calTarget > 0)  ? (calories / calTarget) : "";

    toAppend.push([
      dateKey,
      logged,
      calories,
      expenditure,
      calTarget,
      (calorieDelta === "" ? "" : round_(calorieDelta, 0)),
      protein,
      carbs,
      fat,
      protTarget,
      carbTarget,
      fatTarget,
      (proteinAdh === "" ? "" : round_(proteinAdh, 3)),
      (energyAdh === "" ? "" : round_(energyAdh, 3)),
      weight,
      trendW,
      steps
    ]);

    existingSet.add(dateKey);
  }

  if (toAppend.length > 0) {
    dailySheet.getRange(dailySheet.getLastRow() + 1, 1, toAppend.length, 17).setValues(toAppend);
  }

  return toAppend.length;
}

function headerIndexesMFQuickExport_(headerRow) {
  const getIndex = (name) => {
    const i = headerRow.indexOf(name);
    if (i === -1) throw new Error(`Missing MacroFactor Quick Export column: ${name}`);
    return i;
  };

  return {
    date: getIndex(MF_QE_HEADERS.date),
    expenditure: getIndex(MF_QE_HEADERS.expenditure),
    trendWeight: getIndex(MF_QE_HEADERS.trendWeight),
    weight: getIndex(MF_QE_HEADERS.weight),
    calories: getIndex(MF_QE_HEADERS.calories),
    protein: getIndex(MF_QE_HEADERS.protein),
    carbs: getIndex(MF_QE_HEADERS.carbs),
    fat: getIndex(MF_QE_HEADERS.fat),
    targetCalories: getIndex(MF_QE_HEADERS.targetCalories),
    targetProtein: getIndex(MF_QE_HEADERS.targetProtein),
    targetCarbs: getIndex(MF_QE_HEADERS.targetCarbs),
    targetFat: getIndex(MF_QE_HEADERS.targetFat),
    steps: getIndex(MF_QE_HEADERS.steps),
  };
}

// ===============================
// INGEST: DAILY ENERGY (v2 exports)
// ===============================

function ingestDailyFromV2Sheets_(tempSS, dailySheet, existingSet, fileName) {
  const shCM  = findSheetByKeywords_(tempSS, ["calories", "macros"], MF_SHEETS_V2.caloriesMacros);
  if (!shCM) throw new Error(`MacroFactor export missing Calories/Macros sheet in: ${fileName}`);

  const shExp = findSheetByKeywords_(tempSS, ["expenditure"], MF_SHEETS_V2.expenditure);
  const shWT  = findSheetByKeywords_(tempSS, ["weight", "trend"], MF_SHEETS_V2.weightTrend);
  const shPS  =
    findSheetByKeywords_(tempSS, ["program", "settings"], MF_SHEETS_V2.programSettings) ||
    findSheetByKeywords_(tempSS, ["nutrition", "program"], MF_SHEETS_V2.programSettings);

  const cm  = readTable_(shCM);
  const exp = shExp ? readTable_(shExp) : null;
  const wt  = shWT  ? readTable_(shWT)  : null;

  const targetsTimeline = shPS ? buildTargetsTimelineFromProgramSettings_(shPS) : [];

  const colAny_ = (hdr, names, requiredLabel) => {
    for (const n of names) {
      const i = hdr.indexOf(n);
      if (i !== -1) return i;
    }
    if (requiredLabel) {
      throw new Error(`MacroFactor sheet is missing expected column: ${requiredLabel}. Found: ${hdr.join(", ")}`);
    }
    return -1;
  };

  // Expenditure + Steps map + date universe
  const expMap = new Map();
  const allDates = new Set();

  if (exp && exp.values.length > 1) {
    const hdr = exp.header;
    const idxDate  = colAny_(hdr, ["Date"], "Date");
    const idxExp   = colAny_(hdr, ["Expenditure", "Expenditure (kcal)", "Energy Expenditure", "Energy Expenditure (kcal)"], "Expenditure");
    const idxSteps = colAny_(hdr, ["Steps"], null);

    for (let i = 0; i < exp.body.length; i++) {
      const r = exp.body[i];
      const d = parseDateFlexible_(r[idxDate]);
      if (!d) continue;

      const k = formatDate_(d);
      const expenditure = Number(r[idxExp]);
      const steps = (idxSteps !== -1) ? Number(r[idxSteps]) : "";

      expMap.set(k, {
        expenditure: isFinite(expenditure) ? expenditure : "",
        steps: isFinite(steps) ? steps : ""
      });

      allDates.add(k);
    }
  }

  // Weight + Trend maps
  const weightMap = new Map();
  const trendMap  = new Map();

  if (wt && wt.values.length > 1) {
    const hdr = wt.header;
    const idxDate  = colAny_(hdr, ["Date"], "Date");
    const idxTrend = colAny_(hdr, ["Trend Weight (lbs)", "Trend Weight", "Trend Weight (lb)"], null);
    const idxW     = colAny_(hdr, ["Weight (lbs)", "Scale Weight (lbs)", "Weight", "Scale Weight"], null);

    for (let i = 0; i < wt.body.length; i++) {
      const r = wt.body[i];
      const d = parseDateFlexible_(r[idxDate]);
      if (!d) continue;

      const k = formatDate_(d);

      if (idxTrend !== -1) {
        const tw = Number(r[idxTrend]);
        if (isFinite(tw)) trendMap.set(k, tw);
      }

      if (idxW !== -1) {
        const w = Number(r[idxW]);
        if (isFinite(w)) weightMap.set(k, w);
      }

      allDates.add(k);
    }
  }

  // Calories & Macros map
  const cmHdr = cm.header;
  const iDate = colAny_(cmHdr, ["Date"], "Date");
  const iCals = colAny_(cmHdr, ["Calories (kcal)", "Calories"], "Calories (kcal)");
  const iP    = colAny_(cmHdr, ["Protein (g)", "Protein"], "Protein (g)");
  const iCarb = colAny_(cmHdr, ["Carbs (g)", "Carbs"], "Carbs (g)");
  const iFat  = colAny_(cmHdr, ["Fat (g)", "Fat"], "Fat (g)");
  const iW    = colAny_(cmHdr, ["Weight (lbs)", "Scale Weight (lbs)", "Weight", "Scale Weight"], null);

  const macrosMap = new Map();

  for (let i = 0; i < cm.body.length; i++) {
    const r = cm.body[i];
    const d = parseDateFlexible_(r[iDate]);
    if (!d) continue;

    const k = formatDate_(d);

    const calories = Number(r[iCals]);
    const protein  = Number(r[iP]);
    const carbs    = Number(r[iCarb]);
    const fat      = Number(r[iFat]);

    let w = "";
    if (iW !== -1) {
      const w0 = Number(r[iW]);
      w = isFinite(w0) ? w0 : "";
    }

    macrosMap.set(k, {
      calories: isFinite(calories) ? calories : 0,
      protein:  isFinite(protein)  ? protein  : 0,
      carbs:    isFinite(carbs)    ? carbs    : 0,
      fat:      isFinite(fat)      ? fat      : 0,
      weight:   w
    });

    allDates.add(k);
  }

  const allDatesSorted = Array.from(allDates).sort();
  if (allDatesSorted.length === 0) return 0;

  const toAppend = [];

  for (const dateKey of allDatesSorted) {
    if (existingSet.has(dateKey)) continue;

    const d = parseDateFlexible_(dateKey);
    if (!d) continue;

    const expRow = expMap.get(dateKey) || {};
    const expenditure = (expRow.expenditure !== undefined) ? expRow.expenditure : "";
    const steps       = (expRow.steps !== undefined) ? expRow.steps : "";

    const macroRow = macrosMap.get(dateKey);
    const logged = macroRow ? 1 : 0;

    const calories = logged ? macroRow.calories : 0;
    const protein  = logged ? macroRow.protein  : 0;
    const carbs    = logged ? macroRow.carbs    : 0;
    const fat      = logged ? macroRow.fat      : 0;

    const targ = logged ? pickTargetsForDate_(targetsTimeline, d) : null;

    const calTarget  = (targ && isFinite(targ.calories)) ? targ.calories : 0;
    const protTarget = (targ && isFinite(targ.protein))  ? targ.protein  : 0;
    const carbTarget = (targ && isFinite(targ.carbs))    ? targ.carbs    : 0;
    const fatTarget  = (targ && isFinite(targ.fat))      ? targ.fat      : 0;

    const calorieDelta = (logged && isFinite(expenditure)) ? (calories - expenditure) : "";
    const proteinAdh   = (logged && protTarget > 0) ? (protein / protTarget) : "";
    const energyAdh    = (logged && calTarget > 0)  ? (calories / calTarget) : "";

    // Scale weight: prefer C&M weight, then Weight Trend sheet weight, else 0
    let scaleW = 0;
    if (logged && macroRow.weight !== "" && isFinite(Number(macroRow.weight))) {
      scaleW = Number(macroRow.weight);
    } else if (weightMap.has(dateKey)) {
      scaleW = weightMap.get(dateKey);
    } else {
      scaleW = 0;
    }

    const trendW = trendMap.has(dateKey) ? trendMap.get(dateKey) : 0;

    toAppend.push([
      dateKey,
      logged,
      calories,
      expenditure,
      calTarget,
      (calorieDelta === "" ? "" : round_(calorieDelta, 0)),
      protein,
      carbs,
      fat,
      protTarget,
      carbTarget,
      fatTarget,
      (proteinAdh === "" ? "" : round_(proteinAdh, 3)),
      (energyAdh === "" ? "" : round_(energyAdh, 3)),
      scaleW,
      trendW,
      steps
    ]);

    existingSet.add(dateKey);
  }

  if (toAppend.length > 0) {
    dailySheet.getRange(dailySheet.getLastRow() + 1, 1, toAppend.length, 17).setValues(toAppend);
  }

  return toAppend.length;
}

// ===============================
// INGEST: FOOD LOG
// ===============================

function ingestFoodLogSheet_(flSheet, foodSheet, existingSet) {
  const values = flSheet.getDataRange().getValues();
  if (!values || values.length < 2) return 0;

  const header = values[0].map(h => String(h).trim());
  const idx = headerIndexesFoodLog_(header);

  const toAppend = [];

  for (let i = 1; i < values.length; i++) {
    const r = values[i];
    if (!r || r.length === 0) continue;

    const d = parseDateFlexible_(r[idx.date]);
    if (!d) continue;
    const dateKey = formatDate_(d);

    const timeStr  = String(r[idx.time] ?? "").trim();
    const foodName = String(r[idx.foodName] ?? "").trim();
    if (!timeStr || !foodName) continue;

    const servingSize = String(r[idx.servingSize] ?? "").trim();

    const servingQty     = Number(r[idx.servingQty]);
    const servingWeightG = Number(r[idx.servingWeightG]);

    const calories = Number(r[idx.calories]);
    const fat      = Number(r[idx.fat]);
    const carbs    = Number(r[idx.carbs]);
    const protein  = Number(r[idx.protein]);

    if (![calories, fat, carbs, protein].every(isFinite)) continue;

    const sSize = servingSize || "";
    const sQty  = isFinite(servingQty) ? servingQty : "";
    const sWg   = isFinite(servingWeightG) ? servingWeightG : "";

    const key = makeFoodKey_(dateKey, timeStr, foodName, calories, sSize, sQty);
    if (existingSet.has(key)) continue;

    toAppend.push([dateKey, timeStr, foodName, sSize, sQty, sWg, calories, fat, carbs, protein]);
    existingSet.add(key);
  }

  if (toAppend.length > 0) {
    foodSheet.getRange(foodSheet.getLastRow() + 1, 1, toAppend.length, 10).setValues(toAppend);
  }

  return toAppend.length;
}

function headerIndexesFoodLog_(headerRow) {
  const getIndex = (name) => {
    const i = headerRow.indexOf(name);
    if (i === -1) throw new Error(`Missing Food Log column: ${name}`);
    return i;
  };

  return {
    date:           getIndex(MF_FOOD_HEADERS.date),
    time:           getIndex(MF_FOOD_HEADERS.time),
    foodName:       getIndex(MF_FOOD_HEADERS.foodName),
    servingSize:    getIndex(MF_FOOD_HEADERS.servingSize),
    servingQty:     getIndex(MF_FOOD_HEADERS.servingQty),
    servingWeightG: getIndex(MF_FOOD_HEADERS.servingWeightG),
    calories:       getIndex(MF_FOOD_HEADERS.calories),
    fat:            getIndex(MF_FOOD_HEADERS.fat),
    carbs:          getIndex(MF_FOOD_HEADERS.carbs),
    protein:        getIndex(MF_FOOD_HEADERS.protein),
  };
}

function buildFoodExistingKeySet_(sheet) {
  const s = new Set();
  const last = sheet.getLastRow();
  if (last < 2) return s;

  const values = sheet.getRange(2, 1, last - 1, 10).getValues();
  values.forEach(r => {
    const dateKey  = String(r[0] ?? "").trim();
    const timeStr  = String(r[1] ?? "").trim();
    const foodName = String(r[2] ?? "").trim();
    const sSize    = String(r[3] ?? "").trim();
    const sQty     = (r[4] === "" || r[4] === null || r[4] === undefined) ? "" : Number(r[4]);
    const calories = Number(r[6]);

    if (!dateKey || !timeStr || !foodName || !isFinite(calories)) return;
    if (sQty !== "" && !isFinite(sQty)) return;

    s.add(makeFoodKey_(dateKey, timeStr, foodName, calories, sSize, sQty));
  });

  return s;
}

function makeFoodKey_(dateKey, timeStr, foodName, calories, servingSize, servingQty) {
  return [
    String(dateKey).trim(),
    String(timeStr).trim().toLowerCase(),
    String(foodName).trim().toLowerCase(),
    String(calories),
    String(servingSize ?? "").trim().toLowerCase(),
    String(servingQty === "" ? "" : servingQty)
  ].join("|");
}

// ===============================
// INGEST: WORKOUT LOG
// ===============================

function ingestWorkoutLogSheet_(wlSheet, stagingSheet, existingSet) {
  const values = wlSheet.getDataRange().getValues();
  if (!values || values.length < 2) return 0;

  const header = values[0].map(h => String(h).trim());
  const idx = headerIndexesWorkoutLog_(header);

  const toAppend = [];

  for (let i = 1; i < values.length; i++) {
    const r = values[i];
    if (!r || r.length === 0) continue;

    const d = parseDateFlexible_(r[idx.date]);
    if (!d) continue;
    const dateKey = formatDate_(d);

    const workoutDuration = Number(r[idx.workoutDuration]); // seconds
    const workout  = String(r[idx.workout] ?? "").trim();
    const exercise = String(r[idx.exercise] ?? "").trim();
    const setType  = String(r[idx.setType] ?? "").trim();

    const weight = Number(r[idx.weight]);
    const reps   = Number(r[idx.reps]);
    const rir    = (r[idx.rir] === "" || r[idx.rir] === null || r[idx.rir] === undefined) ? "" : Number(r[idx.rir]);

    if (!workout || !exercise || !setType) continue;
    if (!isFinite(workoutDuration)) continue;

    // Allow timed / bodyweight oddities: require at least one of weight/reps numeric
    const wOk = isFinite(weight);
    const rOk = isFinite(reps);
    if (!wOk && !rOk) continue;

    const weightOut = wOk ? weight : "";
    const repsOut   = rOk ? reps   : "";

    if (rir !== "" && !isFinite(rir)) continue;

    const key = makeWorkoutKey_(dateKey, workout, exercise, setType, weightOut, repsOut, rir);
    if (existingSet.has(key)) continue;

    toAppend.push([dateKey, workoutDuration, workout, exercise, setType, weightOut, repsOut, rir]);
    existingSet.add(key);
  }

  if (toAppend.length > 0) {
    stagingSheet.getRange(stagingSheet.getLastRow() + 1, 1, toAppend.length, 8).setValues(toAppend);
  }

  return toAppend.length;
}

function headerIndexesWorkoutLog_(headerRow) {
  const hdr = headerRow.map(h => String(h).trim());
  const low = hdr.map(h => h.toLowerCase());

  const findAny = (candidates, label, required = true) => {
    for (const c of candidates) {
      const i = hdr.indexOf(c);
      if (i !== -1) return i;
    }
    const candLow = candidates.map(c => c.toLowerCase());
    for (let i = 0; i < low.length; i++) {
      for (const c of candLow) {
        if (low[i] === c || low[i].includes(c)) return i;
      }
    }
    if (required) throw new Error(`Missing Workout Log column: ${label}. Found: ${hdr.join(", ")}`);
    return -1;
  };

  return {
    date:            findAny(["Date"], "Date", true),
    workoutDuration: findAny(["Workout Duration", "Workout Duration (s)", "Workout Duration (sec)", "Duration"], "Workout Duration", true),
    workout:         findAny(["Workout"], "Workout", true),
    exercise:        findAny(["Exercise"], "Exercise", true),
    setType:         findAny(["Set Type", "SetType"], "Set Type", true),
    weight:          findAny(["Weight (lbs)", "Weight", "Load", "Weight (kg)"], "Weight", true),
    reps:            findAny(["Reps", "Repetitions"], "Reps", true),
    rir:             findAny(["RIR", "Reps in Reserve"], "RIR", false),
  };
}

function buildWorkoutExistingKeySet_(sheet) {
  const s = new Set();
  const last = sheet.getLastRow();
  if (last < 2) return s;

  const values = sheet.getRange(2, 1, last - 1, 8).getValues();
  values.forEach(r => {
    const dateKey  = String(r[0] ?? "").trim();
    const workout  = String(r[2] ?? "").trim();
    const exercise = String(r[3] ?? "").trim();
    const setType  = String(r[4] ?? "").trim();

    const weight = (r[5] === "" || r[5] === null || r[5] === undefined) ? "" : Number(r[5]);
    const reps   = (r[6] === "" || r[6] === null || r[6] === undefined) ? "" : Number(r[6]);
    const rir    = (r[7] === "" || r[7] === null || r[7] === undefined) ? "" : Number(r[7]);

    if (!dateKey || !workout || !exercise || !setType) return;

    const w = (isFinite(weight) ? weight : "");
    const rp = (isFinite(reps) ? reps : "");
    const rr = (rir === "" || isFinite(rir) ? rir : "");

    s.add(makeWorkoutKey_(dateKey, workout, exercise, setType, w, rp, rr));
  });

  return s;
}

function makeWorkoutKey_(dateKey, workout, exercise, setType, weight, reps, rir) {
  const w  = (weight === "" ? "" : String(weight));
  const r  = (reps === ""   ? "" : String(reps));
  const rr = (rir === ""    ? "" : String(rir));
  return [
    String(dateKey).trim(),
    String(workout).trim().toLowerCase(),
    String(exercise).trim().toLowerCase(),
    String(setType).trim().toLowerCase(),
    w,
    r,
    rr
  ].join("|");
}

// ===============================
// WEEKLY REBUILDS (BODYCOMP + ENERGY)
// ===============================

function rebuildWeeklyBodyComp_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const staging = mustGetSheet_(ss, STAGING_RENPHO_SHEET_NAME);
  const weekly  = mustGetSheet_(ss, WEEKLY_BODYCOMP_SHEET);

  const lastRow = staging.getLastRow();
  if (lastRow < 2) {
    clearSheetButKeepHeader_(weekly);
    return;
  }

  const raw = staging.getRange(2, 1, lastRow - 1, 4).getValues();
  const groups = new Map();

  raw.forEach(r => {
    const dt = r[0];
    if (!dt) return;

    const d = parseDateOnly_(dt);
    if (!d) return;

    const reportMonday = getReportMonday_(d);
    const key = formatDate_(reportMonday);

    const w   = Number(r[1]);
    const bf  = Number(r[2]);
    const ffm = Number(r[3]);
    if (![w, bf, ffm].every(isFinite)) return;

    if (!groups.has(key)) groups.set(key, { w: 0, bf: 0, ffm: 0, n: 0 });
    const g = groups.get(key);
    g.w += w; g.bf += bf; g.ffm += ffm; g.n += 1;
  });

  const keys = Array.from(groups.keys()).sort();
  const out = keys.map(k => {
    const g = groups.get(k);
    return [k, round_(g.w / g.n, 2), round_(g.bf / g.n, 2), round_(g.ffm / g.n, 2)];
  });

  clearSheetButKeepHeader_(weekly);
  if (out.length > 0) weekly.getRange(2, 1, out.length, 4).setValues(out);
}

function rebuildWeeklyEnergyFromDaily_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const daily  = mustGetSheet_(ss, DAILY_ENERGY_SHEET);
  const weekly = mustGetSheet_(ss, WEEKLY_ENERGY_SHEET);

  const lastRow = daily.getLastRow();
  if (lastRow < 2) {
    clearSheetButKeepHeader_(weekly);
    return;
  }

  const raw = daily.getRange(2, 1, lastRow - 1, 17).getValues();
  const groups = new Map();

  raw.forEach(r => {
    const d = parseDateFlexible_(r[0]);
    if (!d) return;

    const calories = Number(r[2]);
    const calTarget = Number(r[4]);

    // Only count days where calories logged
    if (!isFinite(calories) || calories <= 0) return;

    const key = formatDate_(getReportMonday_(d));

    const expenditure = Number(r[3]);
    const protein = Number(r[6]);
    const carbs = Number(r[7]);
    const fat = Number(r[8]);
    const pAdh = Number(r[12]);
    const eAdh = Number(r[13]);
    const weight = Number(r[14]);
    const trendW = Number(r[15]);
    const steps = Number(r[16]);

    if (!groups.has(key)) {
      groups.set(key, {
        n: 0,
        cal: 0, exp: 0, calT: 0, calTN: 0,
        p: 0, c: 0, f: 0,
        pAdh: 0, eAdh: 0, adhN: 0,
        w: 0, wN: 0,
        tw: 0, twN: 0,
        steps: 0, stepsN: 0
      });
    }

    const g = groups.get(key);
    g.n += 1;

    g.cal += calories;

    if (isFinite(expenditure)) g.exp += expenditure;
    if (isFinite(calTarget) && calTarget > 0) { g.calT += calTarget; g.calTN += 1; }

    if (isFinite(protein)) g.p += protein;
    if (isFinite(carbs))   g.c += carbs;
    if (isFinite(fat))     g.f += fat;

    if (isFinite(weight) && weight > 0) { g.w += weight; g.wN += 1; }
    if (isFinite(trendW) && trendW > 0) { g.tw += trendW; g.twN += 1; }

    if (isFinite(steps)) { g.steps += steps; g.stepsN += 1; }

    if (isFinite(pAdh)) g.pAdh += pAdh;
    if (isFinite(eAdh)) g.eAdh += eAdh;
    g.adhN += 1;
  });

  const keys = Array.from(groups.keys()).sort();
  if (keys.length === 0) {
    clearSheetButKeepHeader_(weekly);
    return;
  }

  const out = keys.map(k => {
    const g = groups.get(k);

    const avgCalories = g.cal / g.n;
    const avgExp      = (g.n > 0) ? (g.exp / g.n) : 0;
    const avgTarget   = (g.calTN > 0) ? (g.calT / g.calTN) : 0;
    const avgScaleW   = (g.wN > 0) ? (g.w / g.wN) : 0;
    const avgTrendW   = (g.twN > 0) ? (g.tw / g.twN) : 0;
    const avgSteps    = (g.stepsN > 0) ? (g.steps / g.stepsN) : 0;

    const avgPAdh = (g.adhN > 0) ? (g.pAdh / g.adhN) : "";
    const avgEAdh = (g.adhN > 0) ? (g.eAdh / g.adhN) : "";

    return [
      k,
      g.n,
      round_(avgCalories, 0),
      round_(avgExp, 0),
      round_(avgTarget, 0),
      (avgExp ? round_(avgCalories - avgExp, 0) : ""),
      round_(g.p / g.n, 1),
      round_(g.c / g.n, 1),
      round_(g.f / g.n, 1),
      (avgPAdh === "" ? "" : round_(avgPAdh, 3)),
      (avgEAdh === "" ? "" : round_(avgEAdh, 3)),
      round_(avgScaleW, 2),
      round_(avgTrendW, 2),
      round_(avgSteps, 0),
    ];
  });

  clearSheetButKeepHeader_(weekly);
  weekly.getRange(2, 1, out.length, 14).setValues(out);
}

// ===============================
// TRAINING REBUILDS (SETS/VOLUME/WEEKLY)
// ===============================

function rebuildMuscleSets_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const wl = mustGetSheet_(ss, STAGING_WORKOUT_LOG_SHEET);
  const mapSheet = mustGetSheet_(ss, EXERCISE_MAP_SHEET);
  const outSheet = mustGetSheet_(ss, STAGING_MUSCLE_SETS_SHEET);

  const map = loadExerciseMap_(mapSheet);

  const last = wl.getLastRow();
  clearSheetButKeepHeader_(outSheet);
  if (last < 2) return;

  const rows = wl.getRange(2, 1, last - 1, 8).getValues();
  const daily = new Map();

  rows.forEach(r => {
    const d = parseDateFlexible_(r[0]);
    if (!d) return;
    const dateKey = formatDate_(d);

    const exercise = String(r[3] ?? "").trim();
    const setType  = String(r[4] ?? "").trim();
    if (!isWorkingSetType_(setType)) return;

    const m = map.get(normalizeExercise_(exercise));
    if (!m || !m.includeForSets) return;

    if (!daily.has(dateKey)) daily.set(dateKey, blankMuscleAgg_());
    const agg = daily.get(dateKey);

    if (m.primary)   agg[m.primary] += 1;
    if (m.secondary) agg[m.secondary] += m.secondaryWeight;
    if (m.tertiary)  agg[m.tertiary] += m.tertiaryWeight;
  });

  const dates = Array.from(daily.keys()).sort();
  const out = dates.map(dateKey => {
    const a = daily.get(dateKey);
    return [
      dateKey,
      round_(a.chest, 2),
      round_(a.back, 2),
      round_(a.legs, 2),
      round_(a.shoulders, 2),
      round_(a.biceps, 2),
      round_(a.triceps, 2),
    ];
  });

  if (out.length > 0) outSheet.getRange(2, 1, out.length, 7).setValues(out);
}

function rebuildMuscleVolume_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const wl = mustGetSheet_(ss, STAGING_WORKOUT_LOG_SHEET);
  const mapSheet = mustGetSheet_(ss, EXERCISE_MAP_SHEET);
  const outSheet = mustGetSheet_(ss, STAGING_MUSCLE_VOL_SHEET);

  const map = loadExerciseMap_(mapSheet);

  const last = wl.getLastRow();
  clearSheetButKeepHeader_(outSheet);
  if (last < 2) return;

  const rows = wl.getRange(2, 1, last - 1, 8).getValues();
  const daily = new Map();

  rows.forEach(r => {
    const d = parseDateFlexible_(r[0]);
    if (!d) return;
    const dateKey = formatDate_(d);

    const exercise = String(r[3] ?? "").trim();
    const setType  = String(r[4] ?? "").trim();
    if (!isWorkingSetType_(setType)) return;

    const weight = Number(r[5]);
    const reps   = Number(r[6]);
    if (![weight, reps].every(isFinite)) return;

    const m = map.get(normalizeExercise_(exercise));
    if (!m || !m.includeForVolume) return;

    const tonnage = weight * reps;

    if (!daily.has(dateKey)) daily.set(dateKey, blankMuscleAgg_());
    const agg = daily.get(dateKey);

    if (m.primary)   agg[m.primary] += tonnage;
    if (m.secondary) agg[m.secondary] += tonnage * m.secondaryWeight;
    if (m.tertiary)  agg[m.tertiary] += tonnage * m.tertiaryWeight;
  });

  const dates = Array.from(daily.keys()).sort();
  const out = dates.map(dateKey => {
    const a = daily.get(dateKey);
    return [
      dateKey,
      round_(a.chest, 1),
      round_(a.back, 1),
      round_(a.legs, 1),
      round_(a.shoulders, 1),
      round_(a.biceps, 1),
      round_(a.triceps, 1),
    ];
  });

  if (out.length > 0) outSheet.getRange(2, 1, out.length, 7).setValues(out);
}

function rebuildWeeklyTraining_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const wl = mustGetSheet_(ss, STAGING_WORKOUT_LOG_SHEET);
  const setsDaily = mustGetSheet_(ss, STAGING_MUSCLE_SETS_SHEET);
  const volDaily  = mustGetSheet_(ss, STAGING_MUSCLE_VOL_SHEET);
  const weeklyOut = mustGetSheet_(ss, WEEKLY_TRAINING_SHEET);

  clearSheetButKeepHeader_(weeklyOut);

  const wlLast = wl.getLastRow();
  if (wlLast < 2) return;

  const wlRows = wl.getRange(2, 1, wlLast - 1, 8).getValues();

  const weekly = new Map();           // weekKey -> agg
  const sessionDurations = new Map(); // sessionKey -> max seconds

  wlRows.forEach(r => {
    const d = parseDateFlexible_(r[0]);
    if (!d) return;

    const dateKey = formatDate_(d);
    const durationSec = Number(r[1]);
    const workout = String(r[2] ?? "").trim();

    // Track workout session durations (take max per session)
    if (workout && isFinite(durationSec)) {
      const sk = makeSessionKey_(dateKey, workout);
      const prev = sessionDurations.get(sk) ?? 0;
      if (durationSec > prev) sessionDurations.set(sk, durationSec);
    }

    const setType = String(r[4] ?? "").trim();
    if (!isWorkingSetType_(setType)) return;

    const weekKey = formatDate_(getReportMonday_(d));
    const rir = (r[7] === "" || r[7] === null || r[7] === undefined) ? null : Number(r[7]);

    if (!weekly.has(weekKey)) weekly.set(weekKey, blankWeeklyAgg_());
    const g = weekly.get(weekKey);

    g.sets_total += 1;
    g.workoutKeys.add(makeSessionKey_(dateKey, workout));

    if (rir !== null && isFinite(rir)) {
      g.rirSum += rir;
      g.rirN += 1;
    }
  });

  // Add minutes totals from sessionDurations
  for (const [sessionKey, seconds] of sessionDurations.entries()) {
    const datePart = sessionKey.split("|")[0];
    const d = parseDateFlexible_(datePart);
    if (!d) continue;

    const weekKey = formatDate_(getReportMonday_(d));
    if (!weekly.has(weekKey)) weekly.set(weekKey, blankWeeklyAgg_());

    weekly.get(weekKey).training_minutes_total += (seconds / 60);
  }

  // Add sets/volume from daily muscle sheets
  addDailySheetToWeekly_(volDaily, weekly, "volume");
  addDailySheetToWeekly_(setsDaily, weekly, "sets");

  const HIT_THRESHOLD = 4;
  const keys = Array.from(weekly.keys()).sort();

  const out = keys.map(k => {
    const g = weekly.get(k);
    const workoutsCompleted = g.workoutKeys.size;
    const avgRIR = g.rirN > 0 ? (g.rirSum / g.rirN) : "";

    const musclesHit = ["chest","back","legs","shoulders","biceps","triceps"]
      .filter(m => g.muscles[m] >= HIT_THRESHOLD).length;

    const minutesTotal = g.training_minutes_total;
    const avgWorkoutMinutes = workoutsCompleted > 0 ? (minutesTotal / workoutsCompleted) : 0;

    return [
      k,
      g.sets_total,
      round_(g.volume_total, 1),
      workoutsCompleted,
      (avgRIR === "" ? "" : round_(avgRIR, 2)),
      musclesHit,
      round_(minutesTotal, 1),
      round_(avgWorkoutMinutes, 1)
    ];
  });

  if (out.length > 0) weeklyOut.getRange(2, 1, out.length, 8).setValues(out);
}

function addDailySheetToWeekly_(sheet, weeklyMap, mode) {
  const last = sheet.getLastRow();
  if (last < 2) return;

  const rows = sheet.getRange(2, 1, last - 1, 7).getValues();

  rows.forEach(r => {
    const d = parseDateFlexible_(r[0]);
    if (!d) return;

    const weekKey = formatDate_(getReportMonday_(d));
    if (!weeklyMap.has(weekKey)) weeklyMap.set(weekKey, blankWeeklyAgg_());
    const g = weeklyMap.get(weekKey);

    const chest     = Number(r[1]) || 0;
    const back      = Number(r[2]) || 0;
    const legs      = Number(r[3]) || 0;
    const shoulders = Number(r[4]) || 0;
    const biceps    = Number(r[5]) || 0;
    const triceps   = Number(r[6]) || 0;

    if (mode === "volume") {
      g.volume_total += (chest + back + legs + shoulders + biceps + triceps);
    } else if (mode === "sets") {
      g.muscles.chest     += chest;
      g.muscles.back      += back;
      g.muscles.legs      += legs;
      g.muscles.shoulders += shoulders;
      g.muscles.biceps    += biceps;
      g.muscles.triceps   += triceps;
    }
  });
}

// ===============================
// EXERCISE MAP
// ===============================

function loadExerciseMap_(mapSheet) {
  const last = mapSheet.getLastRow();
  const map = new Map();
  if (last < 2) return map;

  const values = mapSheet.getRange(2, 1, last - 1, mapSheet.getLastColumn()).getValues();

  values.forEach(r => {
    const exercise = String(r[0] ?? "").trim();
    if (!exercise) return;

    const primary   = String(r[1] ?? "").trim().toLowerCase();
    const secondary = String(r[2] ?? "").trim().toLowerCase();
    const tertiary  = String(r[3] ?? "").trim().toLowerCase();

    const secondaryWeight = isFinite(Number(r[4])) ? Number(r[4]) : 0;
    const tertiaryWeight  = isFinite(Number(r[5])) ? Number(r[5]) : 0;

    const includeForSets   = truthy_(r[6]);
    const includeForVolume = truthy_(r[7]);

    map.set(normalizeExercise_(exercise), {
      primary: validMuscle_(primary) ? primary : "",
      secondary: validMuscle_(secondary) ? secondary : "",
      tertiary: validMuscle_(tertiary) ? tertiary : "",
      secondaryWeight: secondaryWeight || 0,
      tertiaryWeight: tertiaryWeight || 0,
      includeForSets,
      includeForVolume
    });
  });

  return map;
}

function normalizeExercise_(s) {
  return String(s || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ");
}

function validMuscle_(m) {
  return ["chest","back","legs","shoulders","biceps","triceps"].includes(m);
}

function blankMuscleAgg_() {
  return { chest: 0, back: 0, legs: 0, shoulders: 0, biceps: 0, triceps: 0 };
}

function blankWeeklyAgg_() {
  return {
    sets_total: 0,
    volume_total: 0,
    workoutKeys: new Set(),
    rirSum: 0,
    rirN: 0,
    muscles: blankMuscleAgg_(),
    training_minutes_total: 0
  };
}

function isWorkingSetType_(setType) {
  const t = String(setType || "").trim().toLowerCase();
  if (!t) return false;
  return !t.includes("warm") && !t.includes("cool") && !t.includes("cardio");
}

function makeSessionKey_(dateKey, workout) {
  return `${String(dateKey).trim()}|${String(workout).trim().toLowerCase()}`;
}

// ===============================
// DEDUPE HELPERS
// ===============================

function buildExistingKeySet_(sheet, keyCol1Based, mode) {
  const s = new Set();
  const last = sheet.getLastRow();
  if (last < 2) return s;

  const col = keyCol1Based;
  const values = sheet.getRange(2, col, last - 1, 1).getValues();

  values.forEach(r => {
    const v = r[0];
    if (v === "" || v === null || v === undefined) return;

    if (mode === "date") {
      const d = parseDateFlexible_(v);
      if (!d) return;
      s.add(formatDate_(d));
    } else {
      s.add(String(v).trim());
    }
  });

  return s;
}

// ===============================
// FILE + DRIVE HELPERS (XLSX -> Google Sheet)
// ===============================

function convertXlsxToGoogleSheet_WithRetry_(fileId, newTitle, retries) {
  let lastErr = null;

  for (let i = 0; i < retries; i++) {
    try {
      return convertXlsxToGoogleSheet_(fileId, newTitle);
    } catch (e) {
      lastErr = e;
      Utilities.sleep(750 * (i + 1));
    }
  }
  throw lastErr || new Error("convertXlsxToGoogleSheet_WithRetry_: unknown error");
}

function convertXlsxToGoogleSheet_(fileId, newTitle) {
  // Requires Advanced Google Services -> Drive API enabled
  const resource = { title: newTitle, mimeType: MimeType.GOOGLE_SHEETS };
  const copied = Drive.Files.copy(resource, fileId);
  return copied.id;
}

function moveToProcessed_(file, processedFolder) {
  try {
    file.moveTo(processedFolder);
  } catch (e) {
    Logger.log(`moveToProcessed_ failed for ${file.getName()}: ${e}`);
  }
}

// ===============================
// SHEET-FINDING + TABLE-READ HELPERS
// ===============================

function findSheetByKeywords_(ss, keywords, fallbackName) {
  const sheets = ss.getSheets();
  const want = keywords.map(k => String(k).toLowerCase());

  // exact fallback first
  if (fallbackName) {
    const exact = ss.getSheetByName(fallbackName);
    if (exact) return exact;
  }

  // keyword match
  for (const sh of sheets) {
    const name = String(sh.getName() || "").toLowerCase();
    let ok = true;
    for (const k of want) {
      if (!name.includes(k)) { ok = false; break; }
    }
    if (ok) return sh;
  }
  return null;
}

function readTable_(sheet) {
  const values = sheet.getDataRange().getValues();
  if (!values || values.length === 0) {
    return { header: [], body: [], values: [] };
  }
  const header = values[0].map(h => String(h).trim());
  const body = values.slice(1);
  return { header, body, values };
}

// ===============================
// PROGRAM SETTINGS -> TARGETS TIMELINE
// ===============================

function buildTargetsTimelineFromProgramSettings_(psSheet) {
  const values = psSheet.getDataRange().getValues();
  if (!values || values.length < 2) return [];

  const header = values[0].map(h => String(h).trim());
  const lower  = header.map(h => h.toLowerCase());

  const idxDate = lower.indexOf("date");
  const idxCals = lower.findIndex(h => h.includes("calories"));
  const idxP    = lower.findIndex(h => h.includes("protein"));
  const idxC    = lower.findIndex(h => h.includes("carb"));
  const idxF    = lower.findIndex(h => h.includes("fat"));

  if (idxDate === -1 || idxCals === -1 || idxP === -1 || idxC === -1 || idxF === -1) {
    return [];
  }

  const out = [];

  for (let i = 1; i < values.length; i++) {
    const r = values[i];
    const d = parseDateFlexible_(r[idxDate]);
    if (!d) continue;

    const cals = Number(r[idxCals]);
    const p    = Number(r[idxP]);
    const c    = Number(r[idxC]);
    const f    = Number(r[idxF]);

    if (![cals, p, c, f].every(isFinite)) continue;

    out.push({
      startDate: new Date(d.getFullYear(), d.getMonth(), d.getDate()),
      calories: cals, protein: p, carbs: c, fat: f
    });
  }

  out.sort((a, b) => a.startDate.getTime() - b.startDate.getTime());
  return out;
}

function pickTargetsForDate_(timeline, dateObj) {
  if (!timeline || timeline.length === 0) return null;

  const d0 = new Date(dateObj.getFullYear(), dateObj.getMonth(), dateObj.getDate()).getTime();

  let best = null;
  for (const t of timeline) {
    const t0 = t.startDate.getTime();
    if (t0 <= d0) best = t;
    else break;
  }
  return best;
}

// ===============================
// DATE + NORMALISATION HELPERS
// ===============================

function normalizeHeader_(h) {
  return String(h || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[^\w\s()%.-]/g, "");
}

function parseDateFlexible_(v) {
  if (v instanceof Date && !isNaN(v.getTime())) return v;

  const s = String(v ?? "").trim();
  if (!s) return null;

  // ISO-ish yyyy-mm-dd or yyyy/mm/dd
  const m1 = s.match(/^(\d{4})[-\/](\d{1,2})[-\/](\d{1,2})/);
  if (m1) {
    const y = Number(m1[1]), mo = Number(m1[2]) - 1, da = Number(m1[3]);
    const d = new Date(y, mo, da);
    return isNaN(d.getTime()) ? null : d;
  }

  const d2 = new Date(s);
  if (!isNaN(d2.getTime())) return d2;

  return null;
}

function parseDateOnly_(dtValue) {
  if (dtValue instanceof Date && !isNaN(dtValue.getTime())) {
    return new Date(dtValue.getFullYear(), dtValue.getMonth(), dtValue.getDate());
  }

  const s = String(dtValue || "").trim();
  if (!s) return null;

  const datePart = s.split(" ")[0];
  return parseDateFlexible_(datePart);
}

function formatDate_(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const da = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${da}`;
}

function getReportMonday_(d) {
  const dt = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const day = dt.getDay(); // 0 Sun, 1 Mon, ...
  const diff = (day === 0) ? -6 : (1 - day);
  dt.setDate(dt.getDate() + diff);
  return dt;
}

function truthy_(v) {
  if (v === true) return true;
  const s = String(v ?? "").trim().toLowerCase();
  return (s === "true" || s === "yes" || s === "1" || s === "y");
}

// ===============================
// DEBUG HELPERS
// ===============================

function debugRenphoInboxList10() {
  const folder = DriveApp.getFolderById(RENPHO_INBOX_FOLDER_ID);

  const arr = [];
  const it = folder.getFiles();
  while (it.hasNext()) {
    const f = it.next();
    arr.push({
      name: f.getName(),
      updated: f.getLastUpdated(),
      id: f.getId(),
      mime: f.getMimeType()
    });
  }

  arr.sort((a,b) => b.updated - a.updated);
  arr.slice(0, 10).forEach((x,i) => {
    Logger.log(`#${i+1} ${x.updated} | ${x.mime} | ${x.name} | ${x.id}`);
  });
}

function debugRenphoInboxFiles() {
  const folder = DriveApp.getFolderById(RENPHO_INBOX_FOLDER_ID);

  const files = folder.getFiles();
  let n = 0;
  let newest = null;
  let newestName = "";
  let newestId = "";

  while (files.hasNext()) {
    const f = files.next();
    n++;
    const t = f.getLastUpdated();
    if (!newest || t > newest) {
      newest = t;
      newestName = f.getName();
      newestId = f.getId();
    }
  }

  Logger.log("Inbox file count: " + n);
  Logger.log("Newest file updated: " + newest);
  Logger.log("Newest file name: " + newestName);
  Logger.log("Newest file id: " + newestId);
}
