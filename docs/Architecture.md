# Architecture

> **Role of this document:** Living reference for the intended system. When requirements change, update this file first. Formulas and classification rules live in [`Logic_Documentation.md`](Logic_Documentation.md). Pipeline steps live in [`Orchestration.md`](Orchestration.md).

| Field | Value |
| --- | --- |
| Document status | Current |
| Last updated | 2026-08-25 |
| Related documents | [`Logic_Documentation.md`](Logic_Documentation.md), [`Orchestration.md`](Orchestration.md) |

---

## 1. How to maintain this document

Update this file when purpose, modules, data flow, or input/output contracts change. Do **not** put detailed calculation rules here.

---

## 2. Project purpose

Daily **intraday gold-ETF redemption valuation** for Turquoise Asset Management.

Instruments are grouped by **AssetId**. The **main** board supplies last trade and NAV. The **\*2 / آد-لات** board supplies live institutional (حقوقی) buy volume. Each fund is classified as **Redemption** or **Issue/Redemption**, then valued as volume × selected NAV.

### 2.1 In scope

- Load gold ETF instruments from Excel or SQL Server (`config.yaml` `db.use_db`)
- Fetch last price, ETF NAV, and ClientType volume from TSETMC CDN
- Classify and value funds; write one HTML report
- Route all-zero reports to `output_error/`; successful reports to `output/`
- Validate HTML, optionally email, write an orchestration log

### 2.2 Out of scope (current)

- Shamsi warehouse session catch-up (ShareHolding-style `--date` lists)
- Last-trade as the report NAV column (last trade is only for classification)
- `pytse-client` (HTTP is `httpx` to `cdn.tsetmc.com`)

---

## 3. Core concepts

| Term | Meaning |
| --- | --- |
| **AssetId** | Warehouse key that groups main + market-board rows of one gold fund |
| **Main board** | Name without a `2` suffix and market text containing `اصلی` — last price + NAV |
| **Market board** | Instrument ending in `2`, or market containing `آد` — حقوقی volume |
| **Institutional value** | `buy_N_Volume ×` selected NAV (Rial internally) |

---

## 4. Inputs

| Input | Source |
| --- | --- |
| Instrument list | Excel `data/طلا.xlsx` or `src/SQL/FundsList.sql` against DW `DimInstrument` |
| Last trade, NAV | TSETMC `GetClosingPriceInfo`, `GetETFByInsCode` on main `TseId` |
| Legal volume | TSETMC `GetClientType` on market `TseId` (`buy_N_Volume`) |
| Secrets | `.env` (`DB_SERVER`, `DB_USERNAME`, `DB_PASSWORD`, optional `TSETMC_PROXY`) |
| Runtime settings | [`config.yaml`](../config.yaml) via [`config.py`](../config.py) |

SQL filter (do not change without a product decision) is in [`src/SQL/FundsList.sql`](../src/SQL/FundsList.sql): gold ETFs, exclude اختیار / شمش.

---

## 5. Modules

```text
run.py                      trampoline → src/main.py
src/main.py                 CLI, logging, flags
src/orchestration.py        skip / generate / validate / send / log
src/pipeline.py             TSETMC batch (parallel workers, shared client)
src/fund_mapper.py         AssetId → FundPair
src/excel_reader.py         Excel rows
src/db_reader.py            SQL rows
src/tsetmc_client.py        CDN HTTP
src/calculator.py           classify + value
src/report_generator.py     Jinja HTML, Rial→display units
src/market_hours.py         Tehran Sat–Wed 12:00–18:00
Send_Email/Email.py         SMTP
report/template.html        Turquoise layout
```

---

## 6. Outputs

| Path | When |
| --- | --- |
| `output/*.html` | At least one non-zero institutional value, validate OK |
| `output_error/*.html` | All values 0, or HTML validation failed |
| `logs/orchestration_log.txt` | Every pipeline attempt |
| `logs/gold_redemptions.log` | TSETMC / processing log (all runs; each line has Tehran date and time) |

Filename: `gold_redemptions-YYYY-MM-DD-HH-MM.html` (Gregorian, Tehran; hyphens in the time so Windows can store the file). Display / email subject: `gold_redemptions YYYY-MM-DD HH:MM`. The HTML page title inside the file is unchanged.

---

## 7. Decision log

| Date | Decision |
| --- | --- |
| 2026-08 | TSETMC money stays Rial in the calculator; report converts to Toman / billion Toman |
| 2026-08 | All-zero reports go to `output_error/` and are not emailed |
| 2026-08 | Orchestration matches ShareHolding order: generate → validate → send → log |
