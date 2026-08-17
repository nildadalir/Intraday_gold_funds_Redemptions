# Intra-Day Gold Redemptions

GitLab: `intra_day_gold_redemptions`

Daily intraday gold-fund redemption valuation for Turquoise Asset Management.

Groups BI Excel rows by **AssetId**, uses the **main** board for last price + NAV,
and the **\*2 / آد-لات** board for live institutional (حقوقی) buy volume.

## Layout

```
intra_day_gold_redemptions/
├── main.py
├── config.yaml
├── config.py
├── requirements.txt
├── data/طلا.xlsx
├── report/template.html
├── src/
│   ├── excel_reader.py
│   ├── fund_mapper.py
│   ├── tsetmc_client.py
│   ├── calculator.py
│   ├── pipeline.py
│   ├── report_generator.py
│   └── main.py
└── output/
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If TSETMC is blocked, set `TSETMC_PROXY` in the environment or a local `.env` file
(e.g. `TSETMC_PROXY=http://user:pass@host:port`).

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
