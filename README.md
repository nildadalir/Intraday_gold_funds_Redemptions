# Gold Fund Valuation Report

Daily valuation pipeline for Iranian gold ETFs (Turquoise Asset Management).

Reads funds from a BI Excel export, pulls market data from TSETMC (via `pytse_client` / `finpy_tse` with CDN fallback), calculates legal-volume × selected NAV, and renders an HTML report.

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
│   ├── main.py             # CLI implementation
│   ├── bi_loader.py
│   ├── tsetmc_client.py
│   ├── tsetmc_libs.py
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

### Iran proxy (required with Cursor VPN)

Cursor’s VPN typically blocks TSETMC. Set an **Iran-exit** proxy in `.env`:

```env
TSETMC_PROXY=http://user:pass@host:port
# or
TSETMC_PROXY=socks5://127.0.0.1:1080
```

`config.yaml` reads this via `${TSETMC_PROXY}`. Leave empty only when your machine can reach `cdn.tsetmc.com` directly.

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

Edit `config.yaml` for paths, timeouts, retries, provider, mock data, and validation rules. Secrets / proxies stay in environment variables (see `.env.example`).

Important keys:

- `api.provider`: `preferred` (libs) or `cdn`
- `api.proxy`: `${TSETMC_PROXY}`
- `features.use_mock_data`: offline calculation without TSETMC
- `batch.enabled`: allow `--all`

## Valuation rules

- Legal volume = `buy_N_Volume` (log sell if different)
- If last price ≤ NAV redemption → **Redemption** (use redemption NAV)
- Else → **Issue/Redemption** (use issue NAV)
- Market instrument resolved as `{symbol}2` / `{symbol}۲` with validation
- Mock market TseIds are forbidden in live mode
- One fund failure never aborts the batch; errors appear in the HTML Error Summary

## Adding funds

Add rows to `data/طلا.xlsx` (must include `InstrumentId`, `Instrument`, `AssetId`, `InstrumentCode`, `TseId`). No code change required. Rows with NULL `TseId` are skipped and listed as errors.

## License / use

Internal Turquoise Asset Management reporting tool.
