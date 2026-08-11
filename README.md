# Gold Fund Valuation Report

Daily valuation pipeline for Iranian gold ETFs (Turquoise Asset Management).

Reads funds from a BI Excel export, pulls market data from TSETMC (via `pytse_client` + CDN), calculates **legal buy volume × selected NAV**, and renders a Turquoise-branded HTML report with two sorted tables, day-over-day deltas, market as-of banner, and an error summary.

## Project layout

```
gold_cursor/
├── main.py                 # CLI entrypoint
├── config.yaml             # all runtime configuration
├── config.py               # loads config.yaml (+ ${ENV_VAR} expansion)
├── requirements.txt
├── .env.example
├── data/
│   ├── طلا.xlsx            # BI gold-fund export
│   ├── inscode_cache.json  # resolved insCodes (speeds up runs)
│   └── history/            # daily value snapshots for DoD %
├── report/
│   └── template.html       # Jinja2 HTML (Turquoise IDM styling)
├── src/
├── tests/
├── output/                 # generated HTML reports (gitignored)
└── logs/                   # rotating run logs (gitignored)
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

`config.yaml` reads this via `${TSETMC_PROXY}`. Leave empty when your machine can reach `cdn.tsetmc.com` / `old.tsetmc.com` directly. `--health-check` prints a proxy hint when probes time out without a proxy.

## Commands

```powershell
python main.py --health-check
python main.py --symbol آتش
python main.py --mock-report
python main.py --all
python main.py --all --require-open-market
python -m pytest
```

| Command | Purpose |
|---------|---------|
| `--health-check` | DNS / HTTPS / API latency; suggests `TSETMC_PROXY` on timeout |
| `--symbol SYMBOL` | Value one fund + write `data/debug/<slug>_result.json` |
| `--mock-report` | Offline HTML layout check → `output/gold_fund_report_mock.html` |
| `--all` | Batch all BI funds → `output/gold_fund_report_YYYY-MM-DD.html` |
| `--all --require-open-market` | Skip batch when gold session is closed (override with `--force`) |

### Schedule (recommended)

Run the batch **during** the gold session (Sat–Wed **12:00–18:00** Tehran), e.g. daily at **12:15**:

**Windows Task Scheduler** (PowerShell example):

```powershell
$action = New-ScheduledTaskAction -Execute "D:\gold_cursor\.venv\Scripts\python.exe" `
  -Argument "main.py --all --require-open-market" -WorkingDirectory "D:\gold_cursor"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday,Sunday,Monday,Tuesday,Wednesday -At 12:15PM
Register-ScheduledTask -TaskName "GoldFundValuation" -Action $action -Trigger $trigger
```

Or set `batch.require_open_market: true` in `config.yaml`.

## Configuration

Edit `config.yaml` for paths, timeouts, retries, provider, mock data, validation, and **market hours**. Secrets / proxies stay in environment variables (see `.env.example`).

Important keys:

- `api.provider`: `preferred` (pytse) or `cdn`
- `api.proxy`: `${TSETMC_PROXY}`
- `features.use_mock_data`: offline calculation without TSETMC
- `batch.enabled` / `batch.max_workers` / `batch.require_open_market`
- `logging.max_bytes` / `logging.backup_count` — rotating file logs
- `market_hours`: gold ETF session (default Sat–Wed **12:00–18:00** `Asia/Tehran`)

## Valuation rules

Use **two boards** per fund:

| Board | TSETMC label | Used for |
|--------|--------------|----------|
| **Main** | بازار معاملات اصلی (BI Excel row) | Last price, NAV, category / selected price |
| **Retail** | **خرده فروشی** (separate `insCode`) | Legal volume only (`buy_N_Volume`) |

Do **not** take legal volume from the main board, and do **not** use `{symbol}2` / بازارگردان / آد-لات / جبرانی / بلوکی.

| Field | Source |
|--------|--------|
| Last trade price | **Main** board last price |
| NAV redemption | **Main** ETF `pRedTran` |
| NAV issue/redemption | **Main** ETF `pSubTran` |
| Legal volume | **Retail** حقیقی/حقوقی → **`buy_N_Volume`** |

Business logic:

1. **Legal volume** = retail `buy_N_Volume`. If buy ≠ sell, still use buy; log sell and add a warning.
2. **Category** (from main-board last vs NAV)
   - If `last_price <= nav_redemption` → **Redemption** → selected price = redemption NAV
   - Else → **Issue/Redemption** → selected price = **issue/redemption NAV** (`pSubTran`)
3. **Fund value** = `retail_legal_buy_volume × selected_price`
4. **insCode**: Excel `TseId` is the **main** board (often float-corrupted). Resolve main via search/cache (prefer اصلی). Resolve retail separately (prefer خرده فروشی). Cached as `symbol` and `symbol#retail`.
5. **Batch**: process **primary** BI rows only; never abort on one fund failure; always write HTML with Error Summary. Skip NULL `TseId` rows and list them as errors.
6. **Live mode**: forbid mock TseIds (`999…`, `888…`).
7. **HTML**: market OPEN/CLOSED banner + legal-volume as-of; `Calculated Value (Rial)` and **DoD %** vs prior `data/history/values_*.json`.

### Gold market hours

Gold funds trade **Saturday–Wednesday, 12:00–18:00 Tehran time**. Outside that window, live TSETMC boards often show zeros/blank fields. The pipeline logs session status and prefers **ClientType / price history** for the last session with data.

### Performance knobs

- `batch.max_workers` — parallel fund valuation (default `4`; set `1` for sequential)
- `api.save_raw_responses` — off by default (enable only when debugging)
- `api.search_legacy_fallback` — legacy `search.aspx` only when CDN misses the exact symbol
- Offline pytse symbol map / `data/inscode_cache.json` used first when near Excel float tolerance
- `api.fail_fast_on_timeout` — stop cascading endpoints after the first network timeout

## Data access

- Prefer CDN `GetClientType` for legal volume (same as the TSETMC retail Client Type table); pytse `instinfofast` only as fallback
- Prefer `pytse_client` for last price / history search with CDN httpx fallback
- ETF dual NAV via CDN `GetETFByInsCode`
- Do not invent prices, NAVs, or volumes when live/history data is missing — fail that fund and continue the batch

## Adding funds

Add **main-board** rows to `data/طلا.xlsx` (must include `InstrumentId`, `Instrument`, `AssetId`, `InstrumentCode`, `TseId`). Board variants ending in `2`/`3`/`4` are ignored automatically. Rows with NULL `TseId` are skipped and listed as errors.

## License / use

Internal Turquoise Asset Management reporting tool.
