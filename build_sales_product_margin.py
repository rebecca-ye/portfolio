# -*- coding: utf-8 -*-
"""
Calculate material margin for each sale.

Reads:
    ProductSalesHistory           US sales records (opened read-only)
    sku_match                     sales SKU -> cost SKU (from build_sku_match.py)
    latest_china_cost_by_month    monthly factory cost (from import_china_to_sql.py)

Writes:
    sales_product_margin_analysis    one row per sales line

Each sale is assigned the most recent factory cost on or before its sale
month (not today's cost), converted from RMB to USD at that cost month's
exchange rate. Sales that cannot be costed are labeled with the reason.

Author: Rebecca Ye
"""

import re
import sqlite3
import unicodedata
from pathlib import Path

import pandas as pd


# ==================================================
# CONFIG
# ==================================================

ANALYTICS_DB = Path(r"[DB_NAME]")
SALES_DB = Path(r"[SALESDB_NAME]")

SALES_TABLE = "ProductSalesHistory"
MATCH_TABLE = "sku_match"
COST_VIEW = "latest_china_cost_by_month"
OUTPUT_TABLE = "sales_product_margin_analysis"

# RMB (CNY) per USD by cost month. Add each new month's actual rate;
# months without a rate are flagged rather than estimated.
FX_RATES = {
    "2026-01": 6.9693,
    "2026-02": 6.9081,
    "2026-03": 6.8957,
    "2026-04": 6.8344,
    "2026-05": 6.7978,
    "2026-06": 6.7760,
    "2026-07": 6.7765,
    "2026-08": 6.7361,
    "2026-09": 6.7089,
}

# Months of sales history to include before the latest cost month
SALES_LOOKBACK_MONTHS = 2


# ==================================================
# TEXT HELPERS
# ==================================================

def clean_text(value):
    """Trim text and fold full-width characters to plain ASCII."""
    if value is None or pd.isna(value):
        return None

    value = unicodedata.normalize("NFKC", str(value)).strip()

    return value or None


def normalize(value):
    """Uppercase letters and digits only."""
    value = clean_text(value)

    if value is None:
        return None

    return re.sub(r"[^A-Z0-9]", "", value.upper()) or None


# ==================================================
# SCHEMA CHECKS
# ==================================================

def require_object(conn, name):
    """Raise if a required table or view is missing."""
    found = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE name = ?
          AND type IN ('table', 'view')
        """,
        (name,)
    ).fetchone()

    if found is None:
        raise ValueError(f"Required table/view not found: {name}")


def require_columns(df, source, required):
    """Raise if a loaded DataFrame is missing required columns."""
    missing = sorted(set(required) - set(df.columns))

    if missing:
        raise ValueError(f"Missing columns in {source}: {missing}")


# ==================================================
# LOAD SALES, MATCHES, AND COSTS
# ==================================================

def load_sales(conn, first_month):
    """Load sales lines on or after first_month."""
    df = pd.read_sql_query(
        f"""
        SELECT
            rowid AS Sales_Row_ID,
            *
        FROM "{SALES_TABLE}"
        WHERE SKU IS NOT NULL
          AND TRIM(SKU) <> ''
          AND date(TxnDate) >= date(?)
        """,
        conn,
        params=[first_month.strftime("%Y-%m-%d")]
    )

    require_columns(
        df,
        SALES_TABLE,
        ["Sales_Row_ID", "TxnDate", "SKU", "Quantity", "Amount"]
    )

    df["Sale_Date"] = pd.to_datetime(df["TxnDate"], errors="coerce")
    df["Sale_Month"] = df["Sale_Date"].dt.to_period("M").dt.to_timestamp()

    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce")
    df["Sales_Amount_USD"] = pd.to_numeric(df["Amount"], errors="coerce")
    df["Sales_Unit_Price_USD"] = (
        df["Sales_Amount_USD"] / df["Quantity"].replace(0, pd.NA)
    )

    return df.rename(columns={"SKU": "Sales_SKU"})


def load_matches(conn):
    """Load the sales SKU -> cost SKU crosswalk."""
    df = pd.read_sql_query(
        f"""
        SELECT
            Sales_SKU,
            Status       AS SKU_Match_Status,
            Confidence   AS SKU_Match_Confidence,
            Match_Method AS SKU_Match_Method,
            Cost_Key,
            Cost_SKU
        FROM "{MATCH_TABLE}"
        """,
        conn
    )

    if df["Sales_SKU"].duplicated().any():
        raise ValueError(f"Duplicate Sales_SKU in {MATCH_TABLE}")

    return df


def load_costs(conn):
    """Load one factory unit cost per cost SKU per month."""
    df = pd.read_sql_query(f'SELECT * FROM "{COST_VIEW}"', conn)

    require_columns(df, COST_VIEW, ["Source_Month", "SKU", "Unit_Cost"])

    df["Cost_Month"] = pd.to_datetime(
        df["Source_Month"] + "-01",
        errors="coerce"
    )
    df["Cost_Key"] = df["SKU"].apply(normalize)
    df["Factory_Unit_Cost_RMB"] = pd.to_numeric(
        df["Unit_Cost"],
        errors="coerce"
    )

    df = df.loc[
        df["Cost_Month"].notna()
        & df["Cost_Key"].notna()
        & df["Factory_Unit_Cost_RMB"].notna()
    ]

    df = (
        df.sort_values(["Cost_Key", "Cost_Month"])
        .drop_duplicates(subset=["Cost_Key", "Cost_Month"], keep="last")
        .rename(columns={"SKU": "Factory_SKU"})
    )

    return df[
        ["Cost_Key", "Cost_Month", "Factory_SKU", "Factory_Unit_Cost_RMB"]
    ]


# ==================================================
# ASSIGN COSTS AND CALCULATE MARGIN
# ==================================================

def assign_cost(sales, costs):
    """
    Give each sale the most recent cost on or before its sale month.

    Sales whose SKU has no cost history keep empty cost columns.
    """
    pieces = []
    valid_keys = set(costs["Cost_Key"])
    cost_columns = [c for c in costs.columns if c != "Cost_Key"]

    for key, group in sales.groupby("Cost_Key", dropna=False, sort=False):

        group = group.copy()

        if pd.isna(key) or key not in valid_keys:
            for column in cost_columns:
                group[column] = pd.NA
            pieces.append(group)
            continue

        history = (
            costs.loc[costs["Cost_Key"].eq(key)]
            .sort_values("Cost_Month")
            .drop(columns="Cost_Key")
        )

        pieces.append(
            pd.merge_asof(
                group.sort_values("Sale_Month"),
                history,
                left_on="Sale_Month",
                right_on="Cost_Month",
                direction="backward",
                allow_exact_matches=True
            )
        )

    return (
        pd.concat(pieces, ignore_index=True)
        .sort_values("Sales_Row_ID")
    )


def calculate(df):
    """Convert costs to USD, calculate margin, and label each row."""
    df["Cost_Month"] = pd.to_datetime(df["Cost_Month"], errors="coerce")
    df["Cost_Month_Key"] = df["Cost_Month"].dt.strftime("%Y-%m")
    df["RMB_Per_USD"] = df["Cost_Month_Key"].map(FX_RATES)

    df["Factory_Unit_Cost_USD"] = (
        df["Factory_Unit_Cost_RMB"] / df["RMB_Per_USD"]
    )
    df["Extended_Factory_Cost_USD"] = (
        df["Quantity"] * df["Factory_Unit_Cost_USD"]
    )
    df["Unit_Material_Margin_USD"] = (
        df["Sales_Unit_Price_USD"] - df["Factory_Unit_Cost_USD"]
    )
    df["Extended_Material_Margin_USD"] = (
        df["Sales_Amount_USD"] - df["Extended_Factory_Cost_USD"]
    )
    df["Material_Margin_Pct"] = (
        df["Extended_Material_Margin_USD"]
        / df["Sales_Amount_USD"].replace(0, pd.NA)
    )

    df["Cost_Match_Status"] = "Matched"
    df.loc[
        df["Cost_Key"].isna(),
        "Cost_Match_Status"
    ] = "No SKU match"
    df.loc[
        df["Cost_Key"].notna() & df["Cost_Month"].isna(),
        "Cost_Match_Status"
    ] = "No eligible historical cost"
    df.loc[
        df["Cost_Month"].notna() & df["RMB_Per_USD"].isna(),
        "Cost_Match_Status"
    ] = "Missing FX rate"

    return df


# ==================================================
# VALIDATE AND WRITE
# ==================================================

OUTPUT_COLUMNS = [
    "Sales_Row_ID",
    "CustomerName",
    "Sales_SKU",
    "Quantity",
    "SalesPrice",
    "Amount",
    "Sale_Date",
    "Sale_Month",
    "SKU_Match_Status",
    "SKU_Match_Confidence",
    "SKU_Match_Method",
    "Cost_Key",
    "Cost_SKU",
    "Factory_Unit_Cost_RMB",
    "Cost_Month_Key",
    "RMB_Per_USD",
    "Factory_Unit_Cost_USD",
    "Material_Margin_Pct",
    "Cost_Match_Status",
]


def keep_output_columns(df):
    """Keep the reporting columns, failing if any are missing."""
    require_columns(df, "margin analysis", OUTPUT_COLUMNS)

    return df[OUTPUT_COLUMNS].copy()


def write_output(conn, df):
    """Safely replace the output table through a staging table."""
    staging = f"{OUTPUT_TABLE}__staging"
    out = df.copy()

    for column in ["Sale_Date", "Sale_Month"]:
        out[column] = (
            pd.to_datetime(out[column], errors="coerce")
            .dt.strftime("%Y-%m-%d")
        )

    out.to_sql(staging, conn, if_exists="replace", index=False)

    try:
        conn.execute("BEGIN")

        conn.execute(f'DROP TABLE IF EXISTS "{OUTPUT_TABLE}"')
        conn.execute(f'ALTER TABLE "{staging}" RENAME TO "{OUTPUT_TABLE}"')

        conn.execute(
            f"""
            CREATE UNIQUE INDEX idx_margin_sales_row
            ON "{OUTPUT_TABLE}" (Sales_Row_ID)
            """
        )
        conn.execute(
            f"""
            CREATE INDEX idx_margin_sku_date
            ON "{OUTPUT_TABLE}" (Sales_SKU, Sale_Date)
            """
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise


# ==================================================
# MAIN
# ==================================================

def main():
    for path in (ANALYTICS_DB, SALES_DB):
        if not path.exists():
            raise FileNotFoundError(f"Database not found: {path}")

    analytics = sqlite3.connect(str(ANALYTICS_DB))
    sales_db = sqlite3.connect(
        f"file:{SALES_DB.as_posix()}?mode=ro",
        uri=True
    )

    try:
        for name in (MATCH_TABLE, COST_VIEW):
            require_object(analytics, name)
        require_object(sales_db, SALES_TABLE)

        costs = load_costs(analytics)

        if costs.empty:
            raise ValueError("No usable monthly costs found.")

        first_month = (
            costs["Cost_Month"].max()
            - pd.DateOffset(months=SALES_LOOKBACK_MONTHS)
        )

        sales = load_sales(sales_db, first_month)

        print(
            f"Sales included: {sales['Sale_Date'].min():%Y-%m-%d} "
            f"to {sales['Sale_Date'].max():%Y-%m-%d}"
        )

        analysis = sales.merge(
            load_matches(analytics),
            on="Sales_SKU",
            how="left",
            validate="many_to_one"
        )

        analysis = calculate(assign_cost(analysis, costs))

        # Nothing is written until all calculations and checks pass.
        write_output(analytics, keep_output_columns(analysis))

    finally:
        sales_db.close()
        analytics.close()

    summary = (
        analysis.groupby("Cost_Match_Status", dropna=False)
        .agg(
            Sales_Rows=("Sales_Row_ID", "count"),
            Sales_Amount_USD=("Sales_Amount_USD", "sum")
        )
        .reset_index()
    )

    print("\nSALES PRODUCT MARGIN SUMMARY")
    print(summary.to_string(index=False))

    missing_fx = sorted(
        analysis.loc[
            analysis["Cost_Match_Status"].eq("Missing FX rate"),
            "Cost_Month_Key"
        ]
        .dropna()
        .unique()
    )

    if missing_fx:
        print("\nAdd FX_RATES for: " + ", ".join(missing_fx))

    print(f"\nWritten to {ANALYTICS_DB} -> {OUTPUT_TABLE}")


if __name__ == "__main__":
    main()
