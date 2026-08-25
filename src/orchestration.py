"""Generate → validate → send → log (ShareHolding-style, gold-specific)."""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import config
from market_hours import is_gold_trading_weekday
from pipeline import run_batch
from report_generator import session_date

logger = logging.getLogger(__name__)

LOG_HEADER = (
    "ReportName",
    "SendDate",
    "IsSuccessfulGenerate",
    "IsSuccessfulEmail",
    "Isnonworking",
)
LOG_SEP = "\t"
_EMPTY_HTML = re.compile(r"^\s*<html[^>]*>\s*</html>\s*$", re.I | re.S)


@dataclass(frozen=True)
class LogRow:
    report_name: str
    send_date: str
    generate_successful: str
    email_successful: str
    isnonworking: str = "unknown"


@dataclass
class PipelineAttemptResult:
    report_date: str
    report_name: str
    success: bool
    output_path: Path | None = None
    error_path: Path | None = None
    errors: list[str] | None = None
    email_sent: bool = False
    email_failed: bool = False
    validation_failed: bool = False
    skipped: bool = False


def _yes_no(value: bool | str) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value).strip().lower()
    if text == "skipped":
        return "skipped"
    return "yes" if text in ("yes", "y", "true", "1") else "no"


def _email_status(value: bool | str) -> str:
    if isinstance(value, str) and value.strip().lower() == "skipped":
        return "skipped"
    return _yes_no(value)


def _row_line(row: LogRow) -> str:
    return LOG_SEP.join(
        [
            row.report_name,
            row.send_date,
            row.generate_successful,
            row.email_successful,
            row.isnonworking,
        ]
    )


def _parse_log_parts(parts: list[str]) -> LogRow | None:
    if len(parts) < 3:
        return None
    if len(parts) >= 5:
        return LogRow(
            report_name=parts[0].strip(),
            send_date=parts[1].strip(),
            generate_successful=_yes_no(parts[2]),
            email_successful=_email_status(parts[3]),
            isnonworking=parts[4].strip() if parts[4].strip() else "unknown",
        )
    old_ok = _yes_no(parts[2])
    return LogRow(
        report_name=parts[0].strip(),
        send_date=parts[1].strip(),
        generate_successful=old_ok,
        email_successful=old_ok,
        isnonworking="unknown",
    )


def _ensure_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = LOG_SEP.join(LOG_HEADER)
    if not path.exists() or path.stat().st_size == 0:
        path.write_text(header + "\n", encoding="utf-8")


def read_log(path: Path) -> list[LogRow]:
    if not path.exists():
        return []
    rows: list[LogRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("ReportName"):
            continue
        parsed = _parse_log_parts(line.split(LOG_SEP))
        if parsed is not None:
            rows.append(parsed)
    return rows


def append_log_row(
    path: Path,
    report_name: str,
    send_date: str,
    generate_successful: bool | str,
    email_successful: bool | str,
    isnonworking: str = "unknown",
) -> None:
    _ensure_log(path)
    line = LOG_SEP.join(
        [
            report_name,
            send_date,
            _yes_no(generate_successful),
            _email_status(email_successful),
            isnonworking,
        ]
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def prune_log(path: Path, retention_days: int) -> None:
    if retention_days <= 0 or not path.exists():
        return
    cutoff = date.today() - timedelta(days=retention_days)
    header = LOG_SEP.join(LOG_HEADER)
    kept = [header]
    for row in read_log(path):
        try:
            send_date = datetime.strptime(row.send_date.strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            kept.append(_row_line(row))
            continue
        if send_date >= cutoff:
            kept.append(_row_line(row))
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def latest_row_for_session(path: Path, report_name: str) -> LogRow | None:
    latest: LogRow | None = None
    for row in read_log(path):
        if row.report_name == report_name:
            latest = row
    return latest


def day_complete(row: LogRow | None, *, require_email: bool) -> bool:
    if row is None:
        return False
    if row.generate_successful != "yes":
        return False
    if not require_email:
        return True
    return row.email_successful == "yes"


def report_name_for(session: str) -> str:
    return f"GOLD_{session}"


def html_path_for(session: str) -> Path:
    return config.OUTPUT_DIR / f"intra-day-gold-redemptions-{session}.html"


def isnonworking_label() -> str:
    return "no" if is_gold_trading_weekday() else "yes"


def validate_report_file(path: Path) -> list[str]:
    if not path.exists():
        return [f"Report file does not exist: {path}"]
    if path.stat().st_size == 0:
        return ["Report file is empty (0 bytes)"]
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return ["Report file contains only whitespace"]
    if _EMPTY_HTML.match(content):
        return ["Report file is an empty HTML shell"]
    return []


def _move_to_error(path: Path) -> Path:
    config.OUTPUT_ERROR_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.OUTPUT_ERROR_DIR / path.name
    if path.resolve() != dest.resolve():
        if dest.exists():
            dest.unlink()
        shutil.move(str(path), str(dest))
    return dest


def _drop_html_for_session(session: str) -> None:
    for folder in (config.OUTPUT_DIR, config.OUTPUT_ERROR_DIR):
        target = folder / f"intra-day-gold-redemptions-{session}.html"
        if target.is_file():
            target.unlink()


def format_email_body(template: str, report_date: str) -> str:
    return template.replace("{{Date}}", report_date)


def send_report_email(html_path: Path, report_date: str) -> bool:
    if not config.EMAIL_SEND:
        return False
    if not config.EMAIL_TO:
        logger.warning("email.to is empty; email skipped")
        return False
    from Send_Email.Email import send_email

    body = format_email_body(config.EMAIL_BODY, report_date)
    send_email(
        email_receiver=config.EMAIL_TO,
        email_subject=config.EMAIL_SUBJECT,
        email_cc=config.EMAIL_CC or None,
        email_directory=str(html_path.parent),
        email_files=html_path.name,
        body=body,
    )
    return True


def _log(
    report_name: str,
    session: str,
    *,
    generate_ok: bool | str,
    email_ok: bool | str,
) -> None:
    append_log_row(
        config.ORCHESTRATION_LOG,
        report_name,
        date.today().isoformat(),
        generate_ok,
        email_ok,
        isnonworking=isnonworking_label(),
    )


def run_daily_attempt(
    *,
    no_send: bool = False,
    force: bool = False,
) -> PipelineAttemptResult:
    """Run generate → validate → send for Tehran *today* only (no historic catch-up)."""
    prune_log(config.ORCHESTRATION_LOG, config.LOG_RETENTION_DAYS)
    session = session_date()
    name = report_name_for(session)
    require_email = bool(config.EMAIL_SEND) and not no_send
    prior = latest_row_for_session(config.ORCHESTRATION_LOG, name)

    if force:
        _drop_html_for_session(session)

    if day_complete(prior, require_email=require_email) and not force:
        logger.info("%s already complete; skip", name)
        return PipelineAttemptResult(
            session, name, True, output_path=html_path_for(session), skipped=True
        )

    output_html = html_path_for(session)
    reuse = (
        prior is not None
        and prior.generate_successful == "yes"
        and output_html.is_file()
        and not force
    )

    if reuse:
        html_path = output_html
        logger.info("Reusing HTML for %s", session)
    else:
        summary = run_batch()
        html_path = summary.report_path
        if html_path is None:
            _log(name, session, generate_ok=False, email_ok="skipped")
            return PipelineAttemptResult(
                session, name, False, errors=["No report path after generate"]
            )
        if html_path.resolve().parent.resolve() == config.OUTPUT_ERROR_DIR.resolve():
            _log(name, session, generate_ok=False, email_ok="skipped")
            return PipelineAttemptResult(
                session,
                name,
                False,
                error_path=html_path,
                errors=["All calculated values are 0"],
            )

    problems = validate_report_file(html_path)
    if problems:
        failed = _move_to_error(html_path)
        _log(name, session, generate_ok=False, email_ok="skipped")
        return PipelineAttemptResult(
            session,
            name,
            False,
            error_path=failed,
            errors=problems,
            validation_failed=True,
        )

    if prior is not None and prior.email_successful == "yes" and require_email:
        logger.info("Email already sent for %s; not sending again", session)
        _log(name, session, generate_ok=True, email_ok=True)
        return PipelineAttemptResult(
            session, name, True, output_path=html_path, email_sent=False
        )

    if not require_email:
        _log(name, session, generate_ok=True, email_ok="skipped")
        return PipelineAttemptResult(session, name, True, output_path=html_path)

    try:
        sent = send_report_email(html_path, session)
    except Exception as exc:
        _log(name, session, generate_ok=True, email_ok=False)
        return PipelineAttemptResult(
            session,
            name,
            False,
            output_path=html_path,
            errors=[str(exc)],
            email_failed=True,
        )

    email_ok: bool | str = True if sent else "skipped"
    _log(name, session, generate_ok=True, email_ok=email_ok)
    return PipelineAttemptResult(
        session, name, True, output_path=html_path, email_sent=bool(sent)
    )
