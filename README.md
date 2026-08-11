# Gold Fund Valuation Report

Daily valuation pipeline for Iranian gold ETFs (Turquoise Asset Management).

Reads funds from a BI Excel export, pulls market data from TSETMC (via `pytse_client` / `finpy_tse` with CDN fallback), calculates **legal buy volume × selected NAV**, and renders a Turquoise-branded HTML report with two sorted tables plus an error summary.

## Project layout

```
gold_cursor/
├── main.py                 # CLI entrypoint
├── config.yaml             # all runtime configuration
├── config.py               # loads config.yaml (+ ${ENV_VAR} expansion)
├── requirements.txt
├── .env.example
├── data/
│   └── طلا.xlsx            # BI gold-fund export
├── report/
│   └── template.html       # Jinja2 HTML (Turquoise IDM styling)
├── src/
│   ├── main.py
│   ├── bi_loader.py
│   ├── tsetmc_client.py
│   ├── tsetmc_libs.py
│   ├── market_hours.py     # Tehran gold-session calendar
│   ├── fund_calculator.py
│   ├── batch.py
│   └── report_generator.py
├── output/                 # generated HTML reports (gitignored)
└── logs/                   # run logs (gitignored)
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

### Iran proxy (when Cursor VPN blocks TSETMC)

Set an **Iran-exit** proxy in `.env`:

```env
TSETMC_PROXY=http://user:pass@host:port
# or
TSETMC_PROXY=socks5://127.0.0.1:1080
```

`config.yaml` reads this via `${TSETMC_PROXY}`. Leave empty when your machine can reach `cdn.tsetmc.com` / `old.tsetmc.com` directly.

## Commands

```powershell
python main.py --health-check
python main.py --symbol آتش
python main.py --mock-report
python main.py --all
```

| Command | Purpose |
|---------|---------|
| `--health-check` | DNS / HTTPS / API latency to TSETMC |
| `--symbol SYMBOL` | Value one fund + write `data/debug/<slug>_result.json` |
| `--mock-report` | Offline HTML layout check → `output/gold_fund_report_mock.html` |
| `--all` | Batch all BI funds → `output/gold_fund_report_YYYY-MM-DD.html` |

## Configuration

Edit `config.yaml` for paths, timeouts, retries, provider, mock data, validation, and **market hours**. Secrets / proxies stay in environment variables (see `.env.example`).

Important keys:

- `api.provider`: `preferred` (libs) or `cdn`
- `api.proxy`: `${TSETMC_PROXY}`
- `features.use_mock_data`: offline calculation without TSETMC
- `batch.enabled`: allow `--all`
- `market_hours`: gold ETF session (default Sat–Wed **11:45–18:00** `Asia/Tehran`)

## Valuation rules

All fields come from the **main / retail board** of the instrument
(`Market` = **بازار معاملات اصلی** / خرده فروشی), e.g. `آتش` — **not** from
`{symbol}2` / `{symbol}3` / `{symbol}4` boards (آد-لات / جبرانی / بلوکی).

| Field | Source |
|--------|--------|
| Last trade price | Main/retail board last price |
| NAV redemption | ETF `pRedTran` |
| NAV issue/redemption | ETF `pSubTran` |
| Legal volume | Main/retail حقیقی/حقوقی → **`buy_N_Volume`** |

Business logic:

1. **Legal volume** = `buy_N_Volume`. If buy ≠ sell, still use buy; log sell and add a warning.
2. **Category**
   - If `last_price <= nav_redemption` → **Redemption** → selected price = redemption NAV
   - Else → **Issue/Redemption** → selected price = **issue/redemption NAV** (`pSubTran`)
3. **Fund value** = `legal_buy_volume × selected_price`
4. **insCode**: Excel `TseId` is often float64-corrupted. Resolve via pytse map (when near Excel) or TSETMC search (prefer خرده/اصلی); Excel is a near-match hint.
5. **Batch**: process **primary** BI rows only; never abort on one fund failure; always write HTML with Error Summary. Skip NULL `TseId` rows and list them as errors.
6. **Live mode**: forbid mock TseIds (`999…`, `888…`).
7. **HTML**: column `Calculated Value (Rial)`; internal valuation footer. Tables: Redemption (ابطال‌ها) and Issue/Redemption (صدور/ابطال‌ها), sorted by value descending.

### Gold market hours

Gold funds trade **Saturday–Wednesday, 11:45–18:00 Tehran time**. Outside that window, live TSETMC boards often show zeros/blank fields. The pipeline logs session status and prefers **ClientType / price history** for the last session with data.

### Performance knobs

- `batch.max_workers` — parallel fund valuation (default `4`; set `1` for sequential)
- `api.save_raw_responses` — off by default (enable only when debugging)
- `api.search_legacy_fallback` — legacy `search.aspx` only when CDN misses the exact symbol
- Offline pytse symbol map used first when it is within Excel float tolerance

## Data access

- Prefer `pytse_client` / `finpy_tse`-style calls (`instinfofast`, `clienttype.aspx`, CDN search) with CDN httpx fallback
- ETF dual NAV via CDN `GetETFByInsCode` when libs do not expose it cleanly
- Do not invent prices, NAVs, or volumes when live/history data is missing — fail that fund and continue the batch

## Adding funds

Add **main-board** rows to `data/طلا.xlsx` (must include `InstrumentId`, `Instrument`, `AssetId`, `InstrumentCode`, `TseId`). Board variants ending in `2`/`3`/`4` are ignored automatically. Rows with NULL `TseId` are skipped and listed as errors.

## License / use

Internal Turquoise Asset Management reporting tool.
