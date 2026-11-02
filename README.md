# Product Margin Pipeline

A three-stage pipeline that estimates material margin for each sale at a
mid-sized electromechanical manufacturer by matching monthly overseas factory cost
reports to US sales records.

## The problem

Factory cost reports and the US sales system describe the same products
differently: different SKU formats, embedded customer part numbers, and
bilingual (Chinese/English) descriptions. Margin can't be calculated until
each sale is reliably tied to the right factory cost for the right month.

## Pipeline

| Step | Script | What it does |
|------|--------|--------------|
| 1 | `import_china_to_sql.py` | Loads monthly password-protected cost reports into SQLite and builds a volume-weighted monthly cost view. |
| 2 | `build_sku_match.py` | Maps each sales SKU to a factory SKU and records how and how confidently it matched. |
| 3 | `build_sales_product_margin.py` | Assigns each sale the factory cost in effect in its month, converts RMB to USD, and calculates margin. |

Run all three in order with `run_marg_pipeline.bat`.

## Key design decisions

- **Rerunnable imports.** Each report is fingerprinted with SHA-256, so the
  same file is never loaded twice, and every row records its source file.
- **Strict-to-loose matching without guessing.** SKUs are compared at
  increasing looseness (exact, description removed, parentheticals removed,
  embedded codes). The first level that matches decides; if it points to
  more than one product, the result is marked Ambiguous for review.
- **Product family guard.** Candidates whose core model code conflicts with
  the sales SKU are rejected, even when a looser alias matches.
- **Validated manual overrides.** Corrections live in a separate table;
  invalid or conflicting overrides stop the run.
- **Point-in-time costing.** Each sale uses the most recent cost on or
  before its sale month, not today's cost.
- **Safe writes.** Outputs are built in a staging table and swapped in only
  after validation; the sales database is opened read-only.
- **Explicit failure labels.** Sales that can't be costed are labeled with
  the reason (no SKU match, no eligible cost, missing exchange rate).

## Setup

```bash
pip install -r requirements.txt
```

Set the database and folder paths at the top of each script, then run
`run_marg_pipeline.bat`. The import script prompts for the report password.

## Data

No data is included. Cost reports and sales records are confidential.

## Known limitations

- The match step considers sales from the first cost month onward, while
  the margin step looks back two months before the latest cost month, so a
  sale older than all cost data is labeled "No SKU match" rather than
  "No eligible historical cost."
- Exchange rates are entered manually by month.

## Next steps

- Estimate loss size and lag between sensitive commodity moves and factory cost changes to determine potential hedging strategy.

## Author

Rebecca Ye
