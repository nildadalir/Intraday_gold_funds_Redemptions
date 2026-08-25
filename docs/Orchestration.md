# Orchestration

How this project runs from `python run.py` through generate → validate → send → log.

| Field | Value |
| --- | --- |
| Entry | `python run.py` |
| Orchestrator | [`src/orchestration.py`](../src/orchestration.py) |
| Settings | [`config.yaml`](../config.yaml) |
| Log | [`data/orchestration_log.txt`](../data/orchestration_log.txt) |

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
| `--force` | Rebuild HTML even if today is already logged complete. Email is still skipped if `IsSuccessfulEmail` is already `yes`. |

There is no `--date`. The session is **today** (Gregorian, Tehran). Gold is a live TSETMC snapshot, not a warehouse `DateKey`.

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
 today's Gregorian date (Asia/Tehran)
        │
        ├── generate+email already yes → skip
        └── else generate (or reuse HTML) → validate → send → log
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

If generate already succeeded and `output/` still has today’s file, the next run **reuses** it (SMTP retry) unless `--force`.

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
- latest log row for today already has `IsSuccessfulEmail=yes`

`{{Date}}` in the body is replaced with the Gregorian session date. Attachment is the HTML in `output/`.

SMTP failure: generate **yes**, email **no**. HTML stays in `output/`. Next `python run.py` retries email only.

---

## 6. Log

File: `data/orchestration_log.txt` (TSV).

| Column | Meaning |
| --- | --- |
| `ReportName` | `GOLD_{YYYY-MM-DD}` |
| `SendDate` | Gregorian run date |
| `IsSuccessfulGenerate` | `yes` if HTML in `output/` and validated |
| `IsSuccessfulEmail` | `yes` / `no` / `skipped` |
| `Isnonworking` | `yes` if Tehran weekday is Thu/Fri; else `no` |

A day is **complete** when generate is `yes` and, if email is required, email is `yes`. Then the job skips.

Rows older than `logging.retention_days` (100) are dropped each run.

TSETMC detail stays in `logs/intra_day_gold_redemptions.log`.
