# -*- coding: utf-8 -*-
"""
Build the sales SKU -> factory cost SKU crosswalk.

Reads (never modified):
    china_cost_history     factory cost records (from import_china_to_sql.py)
    ProductSalesHistory    US sales records (opened read-only)

Writes:
    sku_match           one row per distinct sales SKU
    sku_match_override  manual SKU decisions; created empty if missing

This script only decides SKU identity. Costs are assigned downstream.

Matching order:
    1. Manual override, when present.
    2. Sales SKU keys, strictest first:
           raw -> description stripped -> parentheticals stripped
           -> embedded codes
    3. The first key that matches a cost alias decides the result.
       Candidates with a conflicting product-family code are removed.
       If more than one candidate remains, the result is Ambiguous.
       The script does not continue to a looser match or guess.

Confidence:
    exact:   normalized sales SKU equals an exact normalized cost SKU
    review:  matched through a looser alias or a customer/embedded code
    manual:  set in sku_match_override

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

COST_TABLE = "china_cost_history"
SALES_TABLE = "ProductSalesHistory"
MATCH_TABLE = "sku_match"
OVERRIDE_TABLE = "sku_match_override"

COST_COLUMNS = [
    "SKU",
    "Family",
    "Sales_Type",
]

SALES_COLUMNS = [
    "SKU",
    "ProductName",
    "ItemListID",
    "Quantity",
    "Amount",
    "TxnDate",
]

CJK = re.compile(r"[\u4e00-\u9fff]")


# ==================================================
# SKU TEXT HELPERS
# ==================================================

def clean_text(value):
    """Trim text and fold full-width characters to plain ASCII."""
    if value is None or pd.isna(value):
        return None

    value = unicodedata.normalize(
        "NFKC",
        str(value)
    ).strip()

    return value or None


def normalize(value):
    """Uppercase letters and digits only."""
    value = clean_text(value)

    if value is None:
        return None

    return re.sub(
        r"[^A-Z0-9]",
        "",
        value.upper()
    ) or None


def strip_qb_parent(value):
    """QuickBooks sub-item: 'Switches:AB12-345X' -> 'AB12-345X'."""
    value = clean_text(value)

    if value and ":" in value:
        value = value.rsplit(":", 1)[-1].strip()

    return value or None


def strip_description(value):
    """
    Remove a trailing English or Chinese description while retaining
    the meaningful SKU text.
    """
    value = clean_text(value)

    if value is None:
        return None

    value = value.split(" (")[0]
    value = CJK.split(value, maxsplit=1)[0]
    value = re.sub(
        r"\(UL\d+\)",
        "",
        value,
        flags=re.IGNORECASE
    )

    return value.strip() or None


def strip_parentheticals(value):
    """'AB12-345X(YZ)(123456)' -> 'AB12-345X'."""
    value = strip_description(value)

    if value is None:
        return None

    return re.sub(
        r"\([^)]*\)",
        "",
        value
    ).strip() or None


def customer_codes(value):
    """
    Return alternate part numbers inside parentheses.

    A candidate must normalize to at least four characters and contain
    at least one digit. UL certification markers are excluded.
    """
    value = clean_text(value)

    if value is None:
        return []

    codes = []

    for code in re.findall(r"\(([^()]*)\)", value):
        compact = normalize(code)

        if (
            compact
            and len(compact) >= 4
            and re.search(r"\d", compact)
            and not re.fullmatch(r"UL\d+", compact)
        ):
            codes.append(compact)

    return codes


def sales_keys(sku):
    """Return unique sales-SKU keys in strict-to-loose order."""

    sku = strip_qb_parent(sku)
    keys = []

    candidates = [
        ("raw", sku),
        ("description stripped", strip_description(sku)),
        ("parentheticals stripped", strip_parentheticals(sku)),
    ]

    for label, value in candidates:

        key = normalize(value)

        if key and key not in [existing for _, existing in keys]:
            keys.append((label, key))

    # Catch reordered or embedded customer/SKU codes
    for key in embedded_codes(sku):

        if key not in [existing for _, existing in keys]:
            keys.append(
                ("embedded code", key)
            )

    return keys

def embedded_codes(value):
    """
    Extract SKU/customer-code fragments from anywhere in a description.

    Examples:
        120-00012-01 (AB1100C)
            -> 1200001201, AB1100C

        AB1100C纸盒不体现120-00003
            -> AB1100C, 12000003

        1002030450(AB00012-23)LLC品牌
            -> 1002030450, AB0001223

        SP-000-0-C(DFG-11-11-A-GN) COMPANY品牌
            -> SP0000C, DFG1111AGN
    """

    value = clean_text(value)

    if value is None:
        return []

    candidates = re.findall(
        r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+"
        r"|[A-Za-z]+\d+[A-Za-z0-9]*"
        r"|\d+[A-Za-z]+[A-Za-z0-9]*"
        r"|\d{7,}",
        value
    )

    codes = []

    for candidate in candidates:

        compact = normalize(candidate)

        if (
            compact
            and len(compact) >= 4
            and re.search(r"\d", compact)
            and not re.fullmatch(r"UL\d+", compact)
        ):
            codes.append(compact)

    return list(dict.fromkeys(codes))


# ==================================================
# SCHEMA CHECKS
# ==================================================

def require_table(conn, table):
    found = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table,)
    ).fetchone()

    if found is None:
        raise ValueError(
            f"Required table not found: {table}"
        )


def require_columns(conn, table, required):
    existing = {
        row[1]
        for row in conn.execute(
            f'PRAGMA table_info("{table}")'
        )
    }

    missing = sorted(
        set(required) - existing
    )

    if missing:
        raise ValueError(
            f"Missing columns in {table}: {missing}"
        )


# ==================================================
# LOAD COST AND SALES SKU IDENTITIES
# ==================================================

def load_cost_skus(conn):
    """
    Load SKU identities from export-sales (国外销售) cost rows.

    Unit_Cost is intentionally not required here. This script identifies
    SKUs only; usable cost values are handled in the effective-cost layer.
    """
    costs = pd.read_sql_query(
        f"""
        SELECT
            rowid AS Row_ID,
            SKU,
            Family,
            Sales_Type
        FROM "{COST_TABLE}"
        """,
        conn
    )

    sales_type = costs["Sales_Type"].apply(
        clean_text
    )

    costs = costs.loc[
        sales_type.eq("国外销售")
    ].copy()

    costs["Cost_Key"] = costs["SKU"].apply(
        normalize
    )

    costs = costs.loc[
        costs["Cost_Key"].notna()
    ].copy()

    if costs.empty:
        raise ValueError(
            f"No usable cost SKU identities found in {COST_TABLE}."
        )

    return costs


def load_sales_skus(conn, first_cost_month):
    """Return one row per distinct sales SKU with review totals."""
    return pd.read_sql_query(
        f"""
        SELECT
            SKU                         AS Sales_SKU,
            MAX(ProductName)            AS ProductName,
            MIN(ItemListID)             AS ItemListID,
            COUNT(DISTINCT ItemListID)  AS ItemListID_Count,
            COUNT(*)                    AS Sales_Rows,
            SUM(Quantity)               AS Total_Quantity,
            SUM(Amount)                 AS Total_Amount,
            MIN(TxnDate)                AS First_Sale,
            MAX(TxnDate)                AS Last_Sale
        FROM "{SALES_TABLE}"
        WHERE SKU IS NOT NULL
             AND TRIM(SKU) <> ''
             AND TxnDate >= ?
        GROUP BY SKU
        """,
        conn,
        params=[first_cost_month]
    )


# ==================================================
# MANUAL OVERRIDES
# ==================================================

def load_overrides(conn, valid_keys):
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{OVERRIDE_TABLE}" (
            Sales_SKU TEXT PRIMARY KEY,
            Cost_SKU  TEXT NOT NULL,
            Note      TEXT
        )
        """
    )
    conn.commit()

    rows = conn.execute(
        f"""
        SELECT
            Sales_SKU,
            Cost_SKU
        FROM "{OVERRIDE_TABLE}"
        """
    ).fetchall()

    overrides = {}
    source = {}
    bad = []
    conflicts = []

    for sales_sku, cost_sku in rows:
        sales_key = normalize(
            strip_qb_parent(sales_sku)
        )
        cost_key = normalize(cost_sku)

        if sales_key is None:
            bad.append(
                f"blank or invalid sales SKU: {sales_sku!r}"
            )

        elif cost_key not in valid_keys:
            bad.append(
                f"{sales_sku} -> {cost_sku} "
                "(cost SKU not found)"
            )

        elif (
            sales_key in overrides
            and overrides[sales_key] != cost_key
        ):
            conflicts.append(
                f"{source[sales_key]} vs "
                f"{sales_sku} -> {cost_sku}"
            )

        else:
            overrides[sales_key] = cost_key
            source[sales_key] = (
                f"{sales_sku} -> {cost_sku}"
            )

    if bad:
        raise ValueError(
            f"{OVERRIDE_TABLE} has invalid rows: "
            + "; ".join(bad[:10])
        )

    if conflicts:
        raise ValueError(
            f"{OVERRIDE_TABLE} has conflicting rows for "
            "the same normalized sales SKU: "
            + "; ".join(conflicts[:10])
        )

    return overrides


# ==================================================
# COST ALIASES
# ==================================================

def build_aliases(costs):
    """
    Build alias -> Cost_Key candidates.

    For each alias, only its strictest alias level remains. If multiple
    Cost_Keys tie at that level, the alias remains ambiguous.
    """
    rows = []

    distinct_costs = costs[
        ["Cost_Key", "SKU"]
    ].drop_duplicates()

    for row in distinct_costs.itertuples(index=False):
        candidates = [
            (
                1,
                "cost SKU",
                normalize(row.SKU)
            ),
            (
                2,
                "cost SKU, description stripped",
                normalize(strip_description(row.SKU))
            ),
            (
                3,
                "cost SKU, parentheticals stripped",
                normalize(strip_parentheticals(row.SKU))
            ),
        ]

        candidates += [
            (4, "customer code", code)
            for code in customer_codes(row.SKU)
        ]
        
        candidates += [
            (4, "embedded code", code)
            for code in embedded_codes(row.SKU)
        ]

        for level, alias_type, alias in candidates:
            if alias:
                rows.append(
                    (
                        alias,
                        level,
                        alias_type,
                        row.Cost_Key
                    )
                )

    if not rows:
        raise ValueError(
            "No SKU aliases could be generated."
        )

    aliases = pd.DataFrame(
        rows,
        columns=[
            "Alias",
            "Level",
            "Alias_Type",
            "Cost_Key"
        ]
    ).drop_duplicates()

    strictest = (
        aliases.groupby("Alias")["Level"]
        .transform("min")
    )

    aliases = aliases.loc[
        aliases["Level"].eq(strictest)
    ].copy()

    return aliases.groupby("Alias").agg(
        Level=("Level", "first"),
        Alias_Type=("Alias_Type", "first"),
        Cost_Keys=(
            "Cost_Key",
            lambda values: sorted(set(values))
        ),
    )

def core_suns_code(value):
    """
    Return the leading SUNS-style product code when present.

    Examples:
        SND1234-SL3-C SD01-180 -> SND1234
        SND5678-SP-A           -> SND5678
        SN1234-SP-B3(H)        -> SN1234

    Returns None when the value does not start with
    a recognized SUNS-style family code.
    """

    value = clean_text(value)

    if value is None:
        return None

    match = re.match(
        r"^(SND\d+|SN\d+)",
        value.upper()
    )

    if match:
        return match.group(1)

    return None


# ==================================================
# MATCH SALES SKU TO COST IDENTITY
# ==================================================

def match_one(
    sku,
    aliases,
    overrides,
    cost_sku_by_key
    ):
    override_key = normalize(
        strip_qb_parent(sku)
    )

    if override_key in overrides:
        return (
            "Matched",
            "manual override",
            overrides[override_key],
            "manual",
            None
        )

    sales_core = core_suns_code(
        strip_qb_parent(sku)
    )

    for label, key in sales_keys(sku):

        if key not in aliases.index:
            continue

        alias = aliases.loc[key]

        original_keys = list(
            alias["Cost_Keys"]
        )

        # --------------------------------------------------
        # REMOVE CANDIDATES WITH A DIFFERENT SN / SND CORE
        # --------------------------------------------------

        compatible_keys = []

        for cost_key in original_keys:

            matched_cost_sku = cost_sku_by_key.get(
                cost_key
            )

            cost_core = core_suns_code(
                matched_cost_sku
            )

            if (
                sales_core is not None
                and cost_core is not None
                and sales_core != cost_core
            ):
                continue

            compatible_keys.append(
                cost_key
            )

        # The alias existed, but every candidate had a
        # conflicting SN/SND model number.
        if not compatible_keys:
            return (
                "Unmatched",
                "core SUNS SKU mismatch",
                None,
                None,
                None
            )

        # More than one compatible cost identity remains.
        if len(compatible_keys) > 1:
            return (
                "Ambiguous",
                f"sales {label} = "
                f"{alias['Alias_Type']}",
                None,
                None,
                compatible_keys
            )

        matched_cost_key = compatible_keys[0]

        confidence = (
            "exact"
            if label == "raw"
            and alias["Level"] == 1
            else "review"
        )

        return (
            "Matched",
            f"sales {label} = "
            f"{alias['Alias_Type']}",
            matched_cost_key,
            confidence,
            None
        )

    return (
        "Unmatched",
        None,
        None,
        None,
        None
    )


def display_names(costs):
    """
    Choose the most recently inserted display values for each Cost_Key.

    This affects Cost_SKU and Product_Family display only. It does not
    select an effective Unit_Cost or resolve monthly revisions.
    """
    latest = (
        costs
        .sort_values(["Cost_Key", "Row_ID"])
        .drop_duplicates(
            "Cost_Key",
            keep="last"
        )
        .set_index("Cost_Key")
    )

    return (
        latest["SKU"],
        latest["Family"]
    )


def match_sales(
    sales,
    costs,
    aliases,
    overrides
):
    cost_sku, cost_family = display_names(costs)
    
    cost_sku_by_key = cost_sku.to_dict()

    results = sales["Sales_SKU"].apply(
        lambda sku: match_one(
            sku,
            aliases,
            overrides,
            cost_sku_by_key
        )
    )

    sales = sales.copy()

    sales[
        [
            "Status",
            "Match_Method",
            "Cost_Key",
            "Confidence",
            "_candidates"
        ]
    ] = pd.DataFrame(
        results.tolist(),
        index=sales.index
    )

    sales["Cost_SKU"] = (
        sales["Cost_Key"].map(cost_sku)
    )

    sales["Product_Family"] = (
        sales["Cost_Key"].map(cost_family)
    )

    sales["Candidate_Cost_SKUs"] = (
        sales["_candidates"].apply(
            lambda keys: (
                " | ".join(
                    cost_sku.get(key, key)
                    for key in keys
                )
                if keys
                else None
            )
        )
    )

    status_order = {
        "Ambiguous": 0,
        "Unmatched": 1,
        "Matched": 2,
    }

    sales = (
        sales.assign(
            _order=sales["Status"].map(
                status_order
            )
        )
        .sort_values(
            [
                "_order",
                "Confidence",
                "Total_Amount"
            ],
            ascending=[
                True,
                False,
                False
            ]
        )
        .drop(
            columns=[
                "_order",
                "_candidates"
            ]
        )
    )

    columns = [
        "Sales_SKU",
        "ProductName",
        "Status",
        "Confidence",
        "Match_Method",
        "Cost_SKU",
        "Cost_Key",
        "Product_Family",
        "Candidate_Cost_SKUs",
        "ItemListID",
        "ItemListID_Count",
        "Sales_Rows",
        "Total_Quantity",
        "Total_Amount",
        "First_Sale",
        "Last_Sale",
    ]

    return sales[columns].reset_index(drop=True)


# ==================================================
# VALIDATE AND WRITE
# ==================================================

def validate(match, valid_keys):
    if match["Sales_SKU"].duplicated().any():
        raise ValueError(
            "Validation failed: a sales SKU appears "
            "more than once."
        )

    matched = match["Status"].eq(
        "Matched"
    )

    if match.loc[
        matched,
        "Cost_Key"
    ].isna().any():
        raise ValueError(
            "Validation failed: a matched SKU has "
            "no Cost_Key."
        )

    if not match.loc[
        matched,
        "Cost_Key"
    ].isin(valid_keys).all():
        raise ValueError(
            "Validation failed: a Cost_Key does not "
            "exist in the cost identity data."
        )


def write_match(conn, match):
    """Safely replace sku_match through a staging table."""
    staging = f"{MATCH_TABLE}__staging"

    match.to_sql(
        staging,
        conn,
        if_exists="replace",
        index=False
    )

    try:
        conn.execute("BEGIN")

        conn.execute(
            f'DROP TABLE IF EXISTS "{MATCH_TABLE}"'
        )

        conn.execute(
            f'ALTER TABLE "{staging}" '
            f'RENAME TO "{MATCH_TABLE}"'
        )

        conn.execute(
            f"""
            CREATE UNIQUE INDEX
                idx_sku_match_sales_sku
            ON "{MATCH_TABLE}" (
                Sales_SKU
            )
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
    for path in (
        ANALYTICS_DB,
        SALES_DB
    ):
        if not path.exists():
            raise FileNotFoundError(
                f"Database not found: {path}"
            )

    analytics = sqlite3.connect(
        str(ANALYTICS_DB)
    )

    sales_db = sqlite3.connect(
        f"file:{SALES_DB.as_posix()}?mode=ro",
        uri=True
    )

    match = None

    try:
        require_table(
            analytics,
            COST_TABLE
        )
        require_table(
            sales_db,
            SALES_TABLE
        )

        require_columns(
            analytics,
            COST_TABLE,
            COST_COLUMNS
        )
        require_columns(
            sales_db,
            SALES_TABLE,
            SALES_COLUMNS
        )

        costs = load_cost_skus(
            analytics
        )
        
        first_cost_month = pd.read_sql_query(
            f"""
            SELECT
                MIN(Source_Month) AS First_Cost_Month
            FROM "{COST_TABLE}"
            """,
            analytics
        ).iloc[0, 0]

        valid_keys = set(
            costs["Cost_Key"]
        )

        overrides = load_overrides(
            analytics,
            valid_keys
        )

        aliases = build_aliases(
            costs
        )

        sales = load_sales_skus(
            sales_db,
            first_cost_month
        )

        match = match_sales(
            sales,
            costs,
            aliases,
            overrides
        )

        validate(
            match,
            valid_keys
        )

        # Nothing is written until all calculations and checks pass.
        write_match(
            analytics,
            match
        )

    finally:
        sales_db.close()
        analytics.close()

    summary = (
        match.groupby(
            ["Status", "Confidence"],
            dropna=False
        )
        .agg(
            Sales_SKUs=("Sales_SKU", "count"),
            Sales_Rows=("Sales_Rows", "sum"),
            Total_Amount=("Total_Amount", "sum")
        )
        .reset_index()
    )

    print("\nSKU MATCH SUMMARY")
    print(summary.to_string(index=False))

    split = int(
        match["ItemListID_Count"].gt(1).sum()
    )

    if split:
        print(
            f"\nNote: {split} sales SKU(s) are used "
            "by more than one QuickBooks item."
        )

    print(
        f"\nWritten to {ANALYTICS_DB} -> "
        f"{MATCH_TABLE}"
    )
    print(
        "Fix Ambiguous or wrong matches in "
        f"{OVERRIDE_TABLE}, then rerun."
    )


if __name__ == "__main__":
    main()
