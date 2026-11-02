# -*- coding: utf-8 -*-
"""
Import monthly factory cost reports (password-protected Excel) into SQLite.

Each file is fingerprinted with SHA-256 so reruns never load the same report
twice, and every imported row records its source file. Also builds a
volume-weighted monthly cost view used by the downstream margin scripts.

Author: Rebecca Ye
"""

import pandas as pd
import getpass
import msoffcrypto
from io import BytesIO
import sqlite3
import re

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

from pathlib import Path

SOURCE_FOLDER = Path(
    r"[FOLDER PATH]"
)

excel_files = sorted(
    SOURCE_FOLDER.rglob("*经营情况汇报表*.xlsx"),
    key=lambda p: str(p).lower()
)

print(f"Found {len(excel_files)} files")

password = getpass.getpass("Password: ")

sheet_name = "销售获利情况表明细"

# ==================================================
# CLEAN ONE MONTHLY COST FILE
# ==================================================

def calculate_file_hash(file_path):
    """Calculate SHA-256 hash to identify the exact file contents."""

    sha256 = hashlib.sha256()

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha256.update(chunk)

    return sha256.hexdigest()

def extract_source_month(file_path):

    file_stem = file_path.stem

    patterns = [
        # 2026年9月 / 2026年09月
        r"(?<!\d)(20\d{2})年(\d{1,2})月",

        # 26年9月 / 26年09月
        r"(?<!\d)(\d{2})年(\d{1,2})月",

        # 202609
        r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])",

        # 2609
        r"(?<!\d)(\d{2})(0[1-9]|1[0-2])",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            file_stem
        )

        if match:

            year_text = match.group(1)
            month_number = int(match.group(2))

            if not 1 <= month_number <= 12:
                continue

            if len(year_text) == 2:
                year_text = f"20{year_text}"

            return (
                f"{year_text}-"
                f"{month_number:02d}"
            )

    raise ValueError(
        f"Could not determine source month from "
        f"filename: {file_path.name}"
    )

def process_cost_file(file_path, source_month):
    
    decrypted = BytesIO()

    with open(file_path, "rb") as f:
        office_file = msoffcrypto.OfficeFile(f)

        office_file.load_key(password=password)

        office_file.decrypt(decrypted)

    # Reset stream position
    decrypted.seek(0)

    df = pd.read_excel(
        decrypted,
        sheet_name=sheet_name,
        engine="openpyxl"
    )
    
    # Keep only expected columns (ignore any extras as the end)
    df = df.iloc[:, :12]

    df.columns = [
        "Family",
        "Sales_Type",
        "SKU",
        "Sales_Volume",
        "Sales_Amount",
        "Gross_Profit",
        "Unit_Cost",
        "Gross_Margin",
        "Sales_Share",
        "Last_Month_Cost",
        "Cost_Change_Pct",
        "Cost_Explanation"
    ]

    # --------------------------------------------------
    # FILL FAMILY DOWN
    # --------------------------------------------------

    df["Family"] = df["Family"].ffill()

    # --------------------------------------------------
    # REMOVE FAMILY TOTAL ROWS
    # --------------------------------------------------

    df = df[
        df["Sales_Type"].astype(str).str.strip() != "汇总"
    ]

    # --------------------------------------------------
    # KEEP ONLY SKU ROWS
    # --------------------------------------------------

    df = df[
        df["SKU"].notna()
    ]

    # --------------------------------------------------
    # REMOVE HEADER ROWS
    # --------------------------------------------------

    df = df[
        df["SKU"].astype(str).str.strip() != "物料编码"
    ]

    # --------------------------------------------------
    # CLEAN STRINGS
    # --------------------------------------------------

    for col in ["Family", "Sales_Type", "SKU"]:
        df[col] = (
            df[col]
            .astype(str)
            .str.strip()
        )

    # --------------------------------------------------
    # NUMERIC COLUMNS
    # --------------------------------------------------

    numeric_cols = [
        "Sales_Volume",
        "Sales_Amount",
        "Gross_Profit",
        "Unit_Cost",
        "Gross_Margin",
        "Sales_Share",
        "Last_Month_Cost",
        "Cost_Change_Pct"
    ]

    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # --------------------------------------------------
    # TRANSFER PRICE
    # --------------------------------------------------

    df["Transfer_Price"] = (
        df["Sales_Amount"] /
        df["Sales_Volume"]
    )

    df["Source_Month"] = source_month

    return df.reset_index(drop=True)

# ==================================================
# OPEN DATABASE
# ==================================================

conn = sqlite3.connect(r"[DB_NAME]")

conn.execute(
    """
    CREATE TABLE IF NOT EXISTS china_cost_history (
        Family TEXT,
        Sales_Type TEXT,
        SKU TEXT,
        Sales_Volume REAL,
        Sales_Amount REAL,
        Gross_Profit REAL,
        Unit_Cost REAL,
        Gross_Margin REAL,
        Sales_Share REAL,
        Last_Month_Cost REAL,
        Cost_Change_Pct REAL,
        Cost_Explanation TEXT,
        Transfer_Price REAL,
        Source_Month TEXT,
        Source_File_Hash TEXT,
        Source_File_Path TEXT,
        Cost_Record_Imported_At TEXT
    )
    """
)

conn.commit()

# Add columns if they do not already exist

existing_columns = {
    row[1]
    for row in conn.execute(
        "PRAGMA table_info(china_cost_history)"
    ).fetchall()
}

required_columns = {
    "Source_File_Hash": "TEXT",
    "Source_File_Path": "TEXT",
    "Cost_Record_Imported_At": "TEXT"
}

for column_name, column_type in required_columns.items():

    if column_name not in existing_columns:

        conn.execute(
            f"""
            ALTER TABLE china_cost_history
            ADD COLUMN "{column_name}" {column_type}
            """
        )

        print(
            f"Added column to china_cost_history: "
            f"{column_name}"
        )

conn.commit()

# ==================================================
# CREATE FILE UPLOAD HISTORY TABLE
# ==================================================

conn.execute(
    """
    CREATE TABLE IF NOT EXISTS file_upload_history (
        Upload_ID INTEGER PRIMARY KEY AUTOINCREMENT,
        File_Name TEXT NOT NULL,
        File_Path TEXT NOT NULL,
        File_Hash TEXT NOT NULL UNIQUE,
        File_Size_Bytes INTEGER NOT NULL,
        File_Modified_At TEXT NOT NULL,
        Source_Month TEXT NOT NULL,
        Uploaded_At TEXT NOT NULL,
        Rows_Loaded INTEGER NOT NULL
    )
    """
)

conn.execute(
    """
    CREATE INDEX IF NOT EXISTS idx_upload_history_file_name
    ON file_upload_history (File_Name)
    """
)

# ==================================================
# VOLUME-WEIGHTED MONTHLY COST VIEW
# ==================================================
# Multiple legitimate rows may exist for the same SKU/month
# ==================================================

conn.execute(
    """
    DROP VIEW IF EXISTS latest_china_cost_by_month
    """
)

conn.execute(
    """
    CREATE VIEW latest_china_cost_by_month AS

    SELECT
        MAX(Family) AS Family,
        '国外销售' AS Sales_Type,
        SKU,

        SUM(Sales_Volume) AS Sales_Volume,
        SUM(Sales_Amount) AS Sales_Amount,
        SUM(Gross_Profit) AS Gross_Profit,

        SUM(Unit_Cost * Sales_Volume)
            / NULLIF(SUM(Sales_Volume), 0)
            AS Unit_Cost,

        SUM(Gross_Profit)
            / NULLIF(SUM(Sales_Amount), 0)
            AS Gross_Margin,

        MAX(Sales_Share) AS Sales_Share,

        SUM(Last_Month_Cost * Sales_Volume)
            / NULLIF(SUM(Sales_Volume), 0)
            AS Last_Month_Cost,

        (
            (
                SUM(Unit_Cost * Sales_Volume)
                / NULLIF(SUM(Sales_Volume), 0)
            )
            /
            NULLIF(
                SUM(Last_Month_Cost * Sales_Volume)
                / NULLIF(SUM(Sales_Volume), 0),
                0
            )
            - 1
        ) AS Cost_Change_Pct,

        MAX(Cost_Explanation) AS Cost_Explanation,

        SUM(Transfer_Price * Sales_Volume)
            / NULLIF(SUM(Sales_Volume), 0)
            AS Transfer_Price,

        Source_Month,

        date(Source_Month || '-01')
            AS Source_Month_Date

    FROM china_cost_history

    WHERE TRIM(Sales_Type) = '国外销售'
      AND SKU IS NOT NULL
      AND TRIM(SKU) <> ''

    GROUP BY
        Source_Month,
        SKU
    """
)

conn.commit()


# ==================================================
# PROCESS ALL MONTHLY FILES
# ==================================================

for file_path in excel_files:

    print(f"\nProcessing: {file_path.name}")

    # ==================================================
    # COLLECT FILE INFORMATION
    # ==================================================

    resolved_file_path = Path(file_path).resolve()

    file_hash = calculate_file_hash(resolved_file_path)
    file_name = resolved_file_path.name
    file_size = resolved_file_path.stat().st_size

    source_month = None
    
    eastern = ZoneInfo("America/New_York")

    file_modified_at = datetime.fromtimestamp(
        resolved_file_path.stat().st_mtime,
        tz=eastern
    ).isoformat()

    # ==================================================
    # CHECK WHETHER EXACT FILE WAS ALREADY UPLOADED
    # ==================================================

    previous_upload = conn.execute(
        """
        SELECT
            Uploaded_At,
            File_Modified_At,
            Rows_Loaded
        FROM file_upload_history
        WHERE File_Hash = ?
        """,
        (file_hash,)
    ).fetchone()

    if previous_upload is not None:

        uploaded_at, previous_modified_at, rows_loaded = previous_upload

        print(
            f"Already uploaded "
            f"({uploaded_at}) - Skipping"
        )

        continue

    # ==================================================
    # LOAD AND CLEAN ONE MONTH
    # ==================================================

    source_month = extract_source_month(
        resolved_file_path
    )

    df = process_cost_file(
        file_path=resolved_file_path,
        source_month=source_month
    )

    print(df.shape)
    
    # ==================================================
    # ATTACH SOURCE-FILE METADATA TO EVERY IMPORTED ROW
    # ==================================================
    
    uploaded_at = datetime.now(eastern).isoformat()
    
    df["Source_File_Hash"] = file_hash
    df["Source_File_Path"] = str(resolved_file_path)
    df["Cost_Record_Imported_At"] = uploaded_at
    
    # ==================================================
    # LOAD DATA AND RECORD UPLOAD
    # ==================================================
    
    try:

        df.to_sql(
            "china_cost_history",
            conn,
            if_exists="append",
            index=False
        )

        conn.execute(
            """
            INSERT INTO file_upload_history (
                File_Name,
                File_Path,
                File_Hash,
                File_Size_Bytes,
                File_Modified_At,
                Source_Month,
                Uploaded_At,
                Rows_Loaded
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_name,
                str(resolved_file_path),
                file_hash,
                file_size,
                file_modified_at,
                source_month,
                uploaded_at,
                len(df)
            )
        )

        conn.commit()

        print(
            f"Loaded {len(df)} rows "
            f"from {file_name}"
        )

    except Exception:

        conn.rollback()
        raise
        
del password

count = pd.read_sql(
    """
    SELECT COUNT(*)
    AS rows_loaded
    FROM china_cost_history
    """,
    conn
)

print(count)

conn.close()
