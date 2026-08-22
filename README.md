# Intra-Day Gold Redemptions

GitLab: `intra_day_gold_redemptions`

Daily intraday gold-fund redemption valuation for Turquoise Asset Management.

Groups gold-fund instruments by **AssetId**, uses the **main** board for last price + NAV,
and the **\*2 / آد-لات** board for live institutional (حقوقی) buy volume.

Instrument source is selected in `config.yaml`:

- `db.use_db: false` → Excel (`data/طلا.xlsx` via `src/excel_reader.py`)
- `db.use_db: true` → SQL Server (`src/db_reader.py`, query in `src/SQL/FundsList.sql`)

## Layout

```
intra_day_gold_redemptions/
├── main.py
├── config.yaml
├── config.py
├── .env.example
├── requirements.txt
├── data/طلا.xlsx
├── report/template.html
├── src/
│   ├── excel_reader.py
│   ├── db_reader.py
│   ├── fund_mapper.py
│   ├── tsetmc_client.py
│   ├── calculator.py
│   ├── pipeline.py
│   ├── report_generator.py
│   ├── SQL/FundsList.sql
│   └── main.py
└── output/
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Fill `.env` with `DB_SERVER`, `DB_USERNAME`, and `DB_PASSWORD`. `.env` is gitignored.

SQL Server source also requires **ODBC Driver 17 for SQL Server** on the machine.

If TSETMC is blocked, also set `TSETMC_PROXY` in `.env`.

## Commands

```powershell
python main.py
```

Report file:

```
output/intra-day-gold-redemptions-YYYY-MM-DD.html
```

## Logic

| Input | Source |
|-------|--------|
| Last trade, NAV | Main instrument (`بازار معاملات اصلی`) |
| Institutional volume | Live ClientType on `*2` / آد-لات (`buy_N_Volume`) |

- `Last <= NAV Redemption` → **Redemption** → Institutional value = volume × NAV Redemption  
- `Last > NAV Redemption` → **Issue/Redemption** → Institutional value = volume × Issue NAV  

TSETMC prices and NAV are Rial. The report converts display units as:

| Column | Display unit |
|--------|----------------|
| Price | Toman (Rial / 10) |
| Institutional volume | Unit count (not converted) |
| Institutional value | Billion Toman (Rial / 10 / 1,000,000,000) |

Funds without a market board (`*2`) are skipped. The Error Summary section is omitted when there are no errors.
