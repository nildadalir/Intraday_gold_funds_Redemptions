# Intra-Day Gold Redemptions

Daily gold-fund redemption valuation. Layout follows the Turquoise BI orchestrator: generate → validate → send → log.

| Field | Value |
| --- | --- |
| Entry | `python run.py` |
| Orchestrator | [`src/orchestration.py`](src/orchestration.py) |
| Settings | [`config.yaml`](config.yaml) |
| Last updated | 2026-08-25 |

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
| `--force` | Rebuild even if today is already logged successful |

The session date is **today** (Gregorian, `Asia/Tehran`). A day is finished only when **both** `IsSuccessfulGenerate` and `IsSuccessfulEmail` are `yes` (email not required if `email.send` is false or `--no-send`). `python run.py` does not email a completed day again. If generate succeeded and SMTP failed, existing HTML is reused and only email is retried.

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
output/                      HTML on success
output_error/                all-zero or validation failure
data/orchestration_log.txt   ReportName, SendDate, IsSuccessfulGenerate, IsSuccessfulEmail, Isnonworking
logs/                        TSETMC run log
```

Log rows older than **100 days** (`logging.retention_days`) are dropped on each run.

---

## Email

All email settings live in [`config.yaml`](config.yaml) under `email:`:

```yaml
email:
  send: true
  to:
    - someone@iidic.com
  cc:
    - manager@iidic.com
  subject: "Intra-Day Gold Redemptions"
  body: |
    Dear colleague,

    Please find attached the intra-day gold redemptions report for {{Date}}.

    Best regards,
    FirouzehBI
  smtp_server: "192.168.200.6"
  smtp_port: 25
  sender: "dalirnia@iidic.com"
```

`{{Date}}` is replaced with the session date. `--no-send` still skips SMTP for that run. The pipeline calls `Send_Email/Email.py` and attaches the HTML from `output/` only.

---

## Related documents

- [`docs/Orchestration.md`](docs/Orchestration.md)
- [`docs/Logic_Documentation.md`](docs/Logic_Documentation.md)
- [`docs/Architecture.md`](docs/Architecture.md)
- [`src/SQL/FundsList.sql`](src/SQL/FundsList.sql)
