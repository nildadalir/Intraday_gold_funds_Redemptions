# Orchestration

How this project runs from `python run.py` through generate → validate → send → log.

| Field | Value |
| --- | --- |
| Entry | `python run.py` |
| Orchestrator | [`src/orchestration.py`](../src/orchestration.py) |
| Settings | [`config.yaml`](../config.yaml) |
| Log | [`logs/orchestration_log.txt`](../logs/orchestration_log.txt) |

Formulas: [`Logic_Documentation.md`](Logic_Documentation.md). Modules: [`Architecture.md`](Architecture.md).

---

## 1. How to run

```powershell
python run.py
python run.py --no-send
python run.py --force --no-send
```

| Flag | Meaning |
| --- | --- |
| `--no-send` | Skip SMTP for this run |
| `--force` | Rebuild HTML even if this HH:MM snapshot is already logged complete. Email is still skipped if `IsSuccessfulEmail` is already `yes` for that stamp. |

There is no `--date`. Each run is stamped with Tehran **date and time** (`YYYY-MM-DD HH:MM`). Gold is a live TSETMC snapshot, not a warehouse `DateKey`.

Unlike ShareHolding, this job **does not** walk the log and regenerate missed **past** calendar days. TSETMC data is live: a late run would fetch *now*, not last Saturday’s tape. Skip/reuse apply only to the **same** HH:MM stamp (re-running in the same minute). A later run writes a new HTML file.

---

## 2. Pipeline

```text
python run.py
        │
        ▼
 load config.yaml + .env
        │
        ▼
 prune orchestration log (retention_days)
        │
        ▼
 prune HTML in output/ and output_error/ (report.retention_days)
        │
        ▼
 Tehran YYYY-MM-DD HH:MM
        │
        ├── this stamp already generate+email yes → skip
        └── else generate (or reuse HTML for this stamp) → validate → send → log
```

| Step | Where |
| --- | --- |
| CLI | [`src/main.py`](../src/main.py) via [`run.py`](../run.py) |
| Generate | [`src/pipeline.py`](../src/pipeline.py) `run_batch()` |
| Validate / send / log | [`src/orchestration.py`](../src/orchestration.py) |
| SMTP | [`Send_Email/Email.py`](../Send_Email/Email.py) |

---

## 3. Generate

`run_batch()` loads funds, calls TSETMC, classifies, writes HTML.

- At least one non-zero institutional value → `output/`
- All values 0 (or no valued funds) → `output_error/`; generate logged **no**; email **skipped**

If generate already succeeded and `output/` still has **this stamp’s** file, the next run **reuses** it (SMTP retry) unless `--force`. A later HH:MM always writes a new file.

---

## 4. Validate

The HTML file must:

- exist
- not be empty / whitespace-only
- not be an empty `<html></html>` shell

Failure → file moved to `output_error/`; generate **no**; no email.

---

## 5. Send

Runs only after generate + validate succeed (file in `output/`).

Skipped when:

- `--no-send`
- `email.send` is not `true`
- `email.to` is empty
- latest log row for this HH:MM stamp already has `IsSuccessfulEmail=yes`

The subject is `gold_redemptions YYYY-MM-DD HH:MM` (both stamps if a previous unsent file is attached). `{gold_redemptions}`, `{TIME}`, and `{DATE}` in the body are filled from the Tehran stamp. The SMTP helper sends the body as RTL Persian.

SMTP failure: generate **yes**, email **no**. HTML stays in `output/`. The **next** successful generate attaches that file with the new report (at most two attachments: previous failed send + current). A later run does not keep stacking older unsent files — only the most recent failed send rides along.

Example: 12:30 generate yes / email no → 13:00 sends 12:30 and 13:00. If that also fails → 13:30 sends 13:00 and 13:30 (not 12:30). When a combined send succeeds, both attached reports are logged `IsSuccessfulEmail=yes`.

---

## 6. Log

File: `logs/orchestration_log.txt` (TSV).

| Column | Meaning |
| --- | --- |
| `ReportName` | `gold_redemptions YYYY-MM-DD HH:MM` |
| `SendDate` | Gregorian run stamp `YYYY-MM-DD HH:MM` |
| `IsSuccessfulGenerate` | `yes` if HTML in `output/` and validated |
| `IsSuccessfulEmail` | `yes` / `no` / `skipped` |
| `Isnonworking` | `yes` if Tehran weekday is Thu/Fri; else `no` |

A stamp is **complete** when generate is `yes` and, if email is required, email is `yes`. Then the job skips **that HH:MM only**. Incomplete rows for older stamps in the log are **not** replayed.

Rows older than `logging.retention_days` (100) are dropped each run. HTML in `output/` and `output_error/` older than `report.retention_days` (10) is deleted each run.

TSETMC detail stays in `logs/gold_redemptions.log`. Each line has Tehran date and time.
