# gold_redemptions

Daily gold-fund redemption valuation. Layout follows the Turquoise BI orchestrator: generate → validate → send → log.

| Field | Value |
| --- | --- |
| Entry | `python run.py` |
| Orchestrator | [`src/orchestration.py`](src/orchestration.py) |
| Settings | [`config.yaml`](config.yaml) |
| Last updated | 2026-08-26 |

Formulas: [`docs/Logic_Documentation.md`](docs/Logic_Documentation.md). Modules: [`docs/Architecture.md`](docs/Architecture.md). Pipeline: [`docs/Orchestration.md`](docs/Orchestration.md). SQL: [`src/SQL/FundsList.sql`](src/SQL/FundsList.sql).

---

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Put `DB_SERVER`, `DB_USERNAME`, and `DB_PASSWORD` in `.env`. Do not commit `.env`. SQL Server source also needs **ODBC Driver 17 for SQL Server**. If TSETMC is blocked, set `TSETMC_PROXY` in `.env`.

Instrument source: `db.use_db` in `config.yaml` (`false` = Excel `data/طلا.xlsx`, `true` = SQL).

---

## Run

```powershell
python run.py
python run.py --force --no-send
```

| Flag | Meaning |
| --- | --- |
| `--no-send` | Skip SMTP |
| `--force` | Rebuild HTML even if this HH:MM snapshot is already logged successful |

Each run uses Tehran **date and time** (`YYYY-MM-DD HH:MM`). The HTML file is `intra-day-gold-redemptions-YYYY-MM-DD-HH-MM.html`, so later runs do not overwrite earlier ones. Skip/reuse apply only to that exact timestamp (re-running in the same minute). `python run.py` does **not** backfill old dates from the log (live TSETMC, not warehouse sessions). If generate succeeded and SMTP failed, the next run attaches that HTML with the new report (at most those two files). Older unsent reports are not carried further.

All-zero values (closed market or before open): HTML goes to `output_error/`; generate is logged failed; email is not sent.

---

## Layout

```text
run.py                       the file to run
src/main.py                  CLI + logging
src/orchestration.py         generate → validate → send → log
config.yaml
.env                         DB / proxy secrets (gitignored)
Send_Email/Email.py          SMTP send
src/pipeline.py              TSETMC batch
src/calculator.py            classify + value
src/report_generator.py      HTML
output/                      HTML on success (intra-day-gold-redemptions-YYYY-MM-DD-HH-MM.html)
output_error/                all-zero or validation failure
logs/orchestration_log.txt   ReportName, SendDate (both include HH:MM), generate/email flags
logs/                        gold_redemptions.log (all runs; each line has Tehran date and time)
```

Log rows older than **100 days** (`logging.retention_days`) are dropped on each run. HTML in `output/` and `output_error/` older than **10 days** (`report.retention_days`) is deleted on each run.

---

## Email

All email settings live in [`config.yaml`](config.yaml) under `email:`:

```yaml
email:
  send: true
  to:
    - solhjoo@iidic.com
  cc:
    - navabzadeh@iidic.com
    - dalirnia@iidic.com
  body: |
    با سلام و احترام
    گزارش {gold_redemptions} اجرا شده در ساعت {TIME} تاریخ {DATE} پیوست شده است.
    تیم BI
  smtp_server: "192.168.200.6"
  smtp_port: 25
  sender: "dalirnia@iidic.com"
```

The subject is the report name (`gold_redemptions YYYY-MM-DD HH:MM`). `{DATE}` and `{TIME}` are filled from the Tehran stamp. The SMTP helper wraps the body as RTL Persian. `--no-send` still skips SMTP for that run. The pipeline calls `Send_Email/Email.py` and attaches the HTML from `output/` only.

---

## Related documents

- [`docs/Orchestration.md`](docs/Orchestration.md)
- [`docs/Logic_Documentation.md`](docs/Logic_Documentation.md)
- [`docs/Architecture.md`](docs/Architecture.md)
- [`src/SQL/FundsList.sql`](src/SQL/FundsList.sql)
