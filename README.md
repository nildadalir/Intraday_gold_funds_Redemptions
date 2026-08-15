# Intra-Day Gold Redemptions

GitLab: `intra_day_gold_redemptions`

Daily intraday gold-fund redemption valuation for Turquoise Asset Management.

Groups BI Excel rows by **AssetId**, uses the **main** board for last price + NAV,
and the **\*2 / آد-لات** board for live legal (حقوقی) buy volume.

## Layout

```
intra_day_gold_redemptions/
├── main.py
├── config.yaml
├── config.py
├── requirements.txt
├── .env.example
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
├── tests/
└── output/
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

If TSETMC is blocked, set `TSETMC_PROXY` in `.env`.

## Commands

```powershell
python main.py --poc          # آتش only (AssetId 30018)
python main.py --asset-id 30018
python main.py                # all AssetIds
python -m pytest
```

Report file (hyphens, dated):

```
output/intra-day-gold-redemptions-YYYY-MM-DD.html
```

## Logic

| Input | Source |
|-------|--------|
| Last trade, NAV | Main instrument (`بازار معاملات اصلی`) |
| Legal volume | Live ClientType on `*2` / آد-لات (`buy_N_Volume` — page value, no history) |

- `Last <= NAV Redemption` → **Redemption** → Value = Legal × NAV Redemption  
- `Last > NAV Redemption` → **Issue/Redemption** → Value = Legal × Issue NAV  

Missing data → Error Summary; report still written.
