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
from report_generator import TEHRAN, session_log_stamp, session_stamp

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


def report_display_name(log_stamp: str) -> str:
    return f"{config.REPORT_BASENAME} {log_stamp}"


def report_name_for(log_stamp: str) -> str:
    return report_display_name(log_stamp)


def html_path_for(file_stamp: str) -> Path:
    return config.OUTPUT_DIR / f"{config.REPORT_BASENAME}-{file_stamp}.html"


def html_path_for_report_name(report_name: str) -> Path:
    stamp = report_name.strip()
    for prefix in (config.REPORT_BASENAME, "gold_fund_redemption"):
        if stamp.startswith(prefix):
            stamp = stamp[len(prefix) :].strip()
            break
    else:
        if stamp.upper().startswith("GOLD_"):
            stamp = stamp[5:].strip()
    file_stamp = stamp.replace(":", "-").replace(" ", "-")
    return html_path_for(file_stamp)


def _parse_send_datetime(send_date: str) -> datetime | None:
    text = send_date.strip()
    for fmt, width in (("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:width], fmt)
        except ValueError:
            continue
    return None


def previous_failed_email_row(path: Path, *, current_name: str) -> LogRow | None:
    """Most recent generate-ok / email-failed snapshot other than current. At most one."""
    latest_by_name: dict[str, LogRow] = {}
    for row in read_log(path):
        latest_by_name[row.report_name] = row

    candidates: list[tuple[datetime, LogRow]] = []
    for name, row in latest_by_name.items():
        if name == current_name:
            continue
        if row.generate_successful != "yes" or row.email_successful != "no":
            continue
        stamp = _parse_send_datetime(row.send_date) or datetime.min
        candidates.append((stamp, row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


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


def _drop_html_for_session(file_stamp: str) -> None:
    for folder in (config.OUTPUT_DIR, config.OUTPUT_ERROR_DIR):
        target = folder / f"{config.REPORT_BASENAME}-{file_stamp}.html"
        if target.is_file():
            target.unlink()


def _split_log_stamp(log_stamp: str) -> tuple[str, str]:
    text = log_stamp.strip()
    if " " in text:
        date_part, time_part = text.split(" ", 1)
        return date_part, time_part
    return text, ""


def format_email_body(template: str, stamps: list[str]) -> str:
    dates: list[str] = []
    times: list[str] = []
    for stamp in stamps:
        date_part, time_part = _split_log_stamp(stamp)
        dates.append(date_part)
        if time_part:
            times.append(time_part)
    date_text = " و ".join(dict.fromkeys(dates))
    time_text = " و ".join(times)
    return (
        template.replace("{gold_redemptions}", config.REPORT_BASENAME)
        .replace("{gold_fund_redemption}", config.REPORT_BASENAME)
        .replace("{DATE}", date_text)
        .replace("{TIME}", time_text)
    )


def send_report_email(html_paths: list[Path], stamps: list[str]) -> bool:
    if not config.EMAIL_SEND:
        return False
    if not config.EMAIL_TO:
        logger.warning("email.to is empty; email skipped")
        return False
    from Send_Email.Email import send_email

    if not html_paths:
        raise ValueError("send_report_email requires at least one HTML path")
    body = format_email_body(config.EMAIL_BODY, stamps)
    subject = ", ".join(report_display_name(stamp) for stamp in stamps)
    send_email(
        email_receiver=config.EMAIL_TO,
        email_subject=subject,
        email_cc=config.EMAIL_CC or None,
        email_directory=str(html_paths[0].parent),
        email_files=[path.name for path in html_paths],
        body=body,
    )
    return True


def attachments_for_send(html_path: Path, current_name: str) -> tuple[list[Path], LogRow | None]:
    """Current file plus at most one earlier generate-ok / email-failed file still in output/."""
    carry = previous_failed_email_row(config.ORCHESTRATION_LOG, current_name=current_name)
    if carry is None:
        return [html_path], None
    previous_path = html_path_for_report_name(carry.report_name)
    if not previous_path.is_file() or previous_path.resolve() == html_path.resolve():
        return [html_path], None
    logger.info(
        "Including unsent report %s with this send (last two only)",
        carry.report_name,
    )
    return [previous_path, html_path], carry


def _log(
    report_name: str,
    send_date: str,
    *,
    generate_ok: bool | str,
    email_ok: bool | str,
) -> None:
    append_log_row(
        config.ORCHESTRATION_LOG,
        report_name,
        send_date,
        generate_ok,
        email_ok,
        isnonworking=isnonworking_label(),
    )


def run_daily_attempt(
    *,
    no_send: bool = False,
    force: bool = False,
) -> PipelineAttemptResult:
    """Run generate → validate → send for this Tehran HH:MM stamp (no historic catch-up)."""
    prune_log(config.ORCHESTRATION_LOG, config.LOG_RETENTION_DAYS)
    now = datetime.now(tz=TEHRAN)
    file_stamp = session_stamp(now)
    log_stamp = session_log_stamp(now)
    name = report_name_for(log_stamp)
    require_email = bool(config.EMAIL_SEND) and not no_send
    prior = latest_row_for_session(config.ORCHESTRATION_LOG, name)

    if force:
        _drop_html_for_session(file_stamp)

    if day_complete(prior, require_email=require_email) and not force:
        logger.info("%s already complete; skip", name)
        return PipelineAttemptResult(
            log_stamp, name, True, output_path=html_path_for(file_stamp), skipped=True
        )

    output_html = html_path_for(file_stamp)
    reuse = (
        prior is not None
        and prior.generate_successful == "yes"
        and output_html.is_file()
        and not force
    )

    if reuse:
        html_path = output_html
        logger.info("Reusing HTML for %s", log_stamp)
    else:
        summary = run_batch(generated_at=now)
        html_path = summary.report_path
        if html_path is None:
            _log(name, log_stamp, generate_ok=False, email_ok="skipped")
            return PipelineAttemptResult(
                log_stamp, name, False, errors=["No report path after generate"]
            )
        if html_path.resolve().parent.resolve() == config.OUTPUT_ERROR_DIR.resolve():
            _log(name, log_stamp, generate_ok=False, email_ok="skipped")
            return PipelineAttemptResult(
                log_stamp,
                name,
                False,
                error_path=html_path,
                errors=["All calculated values are 0"],
            )

    problems = validate_report_file(html_path)
    if problems:
        failed = _move_to_error(html_path)
        _log(name, log_stamp, generate_ok=False, email_ok="skipped")
        return PipelineAttemptResult(
            log_stamp,
            name,
            False,
            error_path=failed,
            errors=problems,
            validation_failed=True,
        )

    if prior is not None and prior.email_successful == "yes" and require_email:
        logger.info("Email already sent for %s; not sending again", log_stamp)
        _log(name, log_stamp, generate_ok=True, email_ok=True)
        return PipelineAttemptResult(
            log_stamp, name, True, output_path=html_path, email_sent=False
        )

    if not require_email:
        _log(name, log_stamp, generate_ok=True, email_ok="skipped")
        return PipelineAttemptResult(log_stamp, name, True, output_path=html_path)

    html_paths, carry = attachments_for_send(html_path, name)
    stamps = [carry.send_date, log_stamp] if carry is not None else [log_stamp]
    try:
        sent = send_report_email(html_paths, stamps)
    except Exception as exc:
        _log(name, log_stamp, generate_ok=True, email_ok=False)
        return PipelineAttemptResult(
            log_stamp,
            name,
            False,
            output_path=html_path,
            errors=[str(exc)],
            email_failed=True,
        )

    email_ok: bool | str = True if sent else "skipped"
    _log(name, log_stamp, generate_ok=True, email_ok=email_ok)
    if sent and carry is not None:
        _log(carry.report_name, carry.send_date, generate_ok=True, email_ok=True)
    return PipelineAttemptResult(
        log_stamp, name, True, output_path=html_path, email_sent=bool(sent)
    )
