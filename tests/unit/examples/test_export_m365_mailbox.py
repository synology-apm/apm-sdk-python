"""Unit tests for examples/export_m365_mailbox.py — pure functions only."""
from __future__ import annotations

from pathlib import Path

import export_m365_mailbox as em
import pytest

from synology_apm.sdk import M365ExportStartResult, M365Info, M365WorkloadType
from tests.unit.examples._fixtures import (
    make_m365_group_info,
    make_m365_user_info,
    make_m365_workload,
    make_version_location,
    make_workload_version,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_start_result(
    *,
    execution_id: str = "exec-0001",
    identity: str = "alice@contoso.com",
) -> M365ExportStartResult:
    wl = make_m365_workload(name=identity)
    version = make_workload_version()
    location = make_version_location()
    return M365ExportStartResult(
        execution_id=execution_id,
        ready_to_download=False,
        export_name=f"{identity}_mailbox.pst",
        location=location,
        workload=wl,
        version=version,
    )


def _make_job(
    *,
    identity: str = "alice@contoso.com",
    unit_label: str = "mailbox",
    outcome: str = "ok",
    outcome_msg: str = "",
    bytes_saved: int | None = 10_485_760,
    dest_path: str = "/exports/contoso.com/alice@contoso.com/mailbox.pst",
    execution_id: str = "exec-0001",
) -> em.MailExportJob:
    return em.MailExportJob(
        start_result=_make_start_result(execution_id=execution_id, identity=identity),
        identity=identity,
        unit_label=unit_label,
        dest_path=dest_path,
        outcome=outcome,
        outcome_msg=outcome_msg,
        bytes_saved=bytes_saved,
    )


# ── _mail_domain ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "addr,expected",
    [
        ("alice@contoso.com", "contoso.com"),
        ("bob@example.org", "example.org"),
        ("no-at-sign", "unknown_domain"),
        ("", "unknown_domain"),
        # rsplit("@", 1) takes the trailing segment after the last "@"
        ("user@foo@bar.com", "bar.com"),
    ],
)
def test_mail_domain(addr: str, expected: str) -> None:
    assert em._mail_domain(addr) == expected


# ── _upn ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,info,expected",
    [
        # UPN from the user info wins over the workload name
        ("Alice", make_m365_user_info(user_principal_name="alice@contoso.com"), "alice@contoso.com"),
        # empty UPN falls back to the workload name
        ("alice@contoso.com", make_m365_user_info(user_principal_name=""), "alice@contoso.com"),
        # group info carries no UPN — falls back to the workload name
        ("marketing@contoso.com", make_m365_group_info(), "marketing@contoso.com"),
    ],
)
def test_upn(name: str, info: M365Info, expected: str) -> None:
    wl = make_m365_workload(name=name, info=info)
    assert em._upn(wl) == expected


# ── _group_mail ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,info,expected",
    [
        # mail from the group info wins over the workload name
        ("Marketing", make_m365_group_info(mail="marketing@contoso.com"), "marketing@contoso.com"),
        # empty mail falls back to the workload name
        ("marketing@contoso.com", make_m365_group_info(mail=""), "marketing@contoso.com"),
        # user info carries no group mail — falls back to the workload name
        ("alice@contoso.com", make_m365_user_info(), "alice@contoso.com"),
    ],
)
def test_group_mail(name: str, info: M365Info, expected: str) -> None:
    wl = make_m365_workload(name=name, info=info)
    assert em._group_mail(wl) == expected


# ── MailExportJob.final_status ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("ok", "downloaded"),
        ("failed", "failed"),
        ("canceled", "canceled"),
        ("interrupted", "interrupted"),
        ("", "unknown"),
        ("something_else", "unknown"),
    ],
)
def test_final_status(outcome: str, expected: str) -> None:
    job = _make_job(outcome=outcome)
    assert job.final_status == expected


# ── MailExportJob.log_label ───────────────────────────────────────────────────


def test_log_label_with_unit_label() -> None:
    job = _make_job(identity="alice@contoso.com", unit_label="mailbox")
    assert job.log_label == "alice@contoso.com (mailbox)"


def test_log_label_with_archive_unit_label() -> None:
    job = _make_job(identity="alice@contoso.com", unit_label="archive mailbox")
    assert job.log_label == "alice@contoso.com (archive mailbox)"


def test_log_label_without_unit_label() -> None:
    job = _make_job(identity="marketing@contoso.com", unit_label="")
    assert job.log_label == "marketing@contoso.com"


# ── _build_exchange_domain ────────────────────────────────────────────────────


def test_build_exchange_domain_attributes() -> None:
    domain = em._build_exchange_domain("both")
    assert domain.noun == "user"
    assert domain.type_label == "Exchange"
    assert domain.id_field == "upn"
    assert domain.unit_field == "mailbox_type"
    assert domain.workload_type == M365WorkloadType.EXCHANGE
    assert domain.summary_noun == "mailbox"


def test_build_exchange_domain_csv_fields() -> None:
    domain = em._build_exchange_domain("both")
    assert domain.csv_fields == [
        "upn",
        "domain",
        "mailbox_type",
        "execution_id",
        "status",
        "size_bytes",
        "error",
        "dest_path",
    ]


@pytest.mark.parametrize(
    "scope,expected_note",
    [
        ("both", " — primary + archive"),
        ("primary", " — primary only"),
        ("archive", " — archive only"),
    ],
)
def test_build_exchange_domain_extra_note(scope: str, expected_note: str) -> None:
    domain = em._build_exchange_domain(scope)
    assert domain.extra_note == expected_note


@pytest.mark.parametrize(
    "scope,expected_units",
    [
        # (archive flag, unit label, dest-path suffix) per planned unit
        (
            "both",
            [(False, "mailbox", "_mailbox.pst"), (True, "archive mailbox", "_archive_mailbox.pst")],
        ),
        ("primary", [(False, "mailbox", "_mailbox.pst")]),
        ("archive", [(True, "archive mailbox", "_archive_mailbox.pst")]),
    ],
)
def test_build_exchange_domain_plan_units(
    tmp_path: Path,
    scope: str,
    expected_units: list[tuple[bool, str, str]],
) -> None:
    domain = em._build_exchange_domain(scope)
    wl = make_m365_workload()
    units = domain.plan_units(wl, "alice@contoso.com", str(tmp_path))
    assert [(u.archive, u.unit_label) for u in units] == [
        (archive, label) for archive, label, _ in expected_units
    ]
    for unit, (_, _, suffix) in zip(units, expected_units, strict=True):
        assert unit.dest_path.endswith(suffix)


# ── _group_plan_units ─────────────────────────────────────────────────────────


def test_group_plan_units_returns_single_unit(tmp_path: Path) -> None:
    wl = make_m365_workload(info=make_m365_group_info(mail="marketing@contoso.com"))
    units = em._group_plan_units(wl, "marketing@contoso.com", str(tmp_path))
    assert len(units) == 1
    unit = units[0]
    assert unit.archive is False
    assert unit.unit_label == ""
    assert unit.dest_path.endswith(".pst")


def test_group_plan_units_dest_path_contains_domain(tmp_path: Path) -> None:
    wl = make_m365_workload(info=make_m365_group_info(mail="marketing@contoso.com"))
    units = em._group_plan_units(wl, "marketing@contoso.com", str(tmp_path))
    dest = units[0].dest_path
    # domain directory component
    assert "contoso.com" in dest
    # identity segment — safe_path preserves "@", so the filename retains the full address
    assert "marketing@contoso.com" in dest


# ── Row builders: Exchange ────────────────────────────────────────────────────


def test_exchange_job_row_downloaded() -> None:
    job = _make_job(
        identity="alice@contoso.com",
        unit_label="mailbox",
        outcome="ok",
        outcome_msg="",
        bytes_saved=10_485_760,
        dest_path="/exports/contoso.com/alice@contoso.com/alice@contoso.com_20260514_mailbox.pst",
        execution_id="exec-0001",
    )
    row = em._exchange_job_row(job)
    assert row["upn"] == "alice@contoso.com"
    assert row["domain"] == "contoso.com"
    assert row["mailbox_type"] == "mailbox"
    assert row["execution_id"] == "exec-0001"
    assert row["status"] == "downloaded"
    assert row["size_bytes"] == "10485760"
    assert row["error"] == ""
    assert row["dest_path"] == "/exports/contoso.com/alice@contoso.com/alice@contoso.com_20260514_mailbox.pst"


def test_exchange_job_row_failed_has_error_and_no_size() -> None:
    job = _make_job(
        identity="alice@contoso.com",
        unit_label="archive mailbox",
        outcome="failed",
        outcome_msg="download error: network timeout",
        bytes_saved=None,
        dest_path="/exports/contoso.com/alice@contoso.com/alice@contoso.com_20260514_archive_mailbox.pst",
        execution_id="exec-0002",
    )
    row = em._exchange_job_row(job)
    assert row["status"] == "failed"
    assert row["size_bytes"] == ""
    assert row["error"] == "download error: network timeout"
    assert row["mailbox_type"] == "archive mailbox"


def test_exchange_failure_row() -> None:
    f = em.MailExportFailure(
        identity="alice@contoso.com",
        unit_label="mailbox",
        error="no backup version found",
    )
    row = em._exchange_failure_row(f)
    assert row["upn"] == "alice@contoso.com"
    assert row["domain"] == "contoso.com"
    assert row["mailbox_type"] == "mailbox"
    assert row["execution_id"] == ""
    assert row["status"] == "skipped"
    assert row["size_bytes"] == ""
    assert row["error"] == "no backup version found"
    assert row["dest_path"] == ""


# ── Row builders: Group ───────────────────────────────────────────────────────


def test_group_job_row_downloaded() -> None:
    job = _make_job(
        identity="marketing@contoso.com",
        unit_label="",
        outcome="ok",
        outcome_msg="",
        bytes_saved=5_242_880,
        dest_path="/exports/contoso.com/marketing_contoso.com_20260514.pst",
        execution_id="exec-0002",
    )
    row = em._group_job_row(job)
    assert row["group_mail"] == "marketing@contoso.com"
    assert row["domain"] == "contoso.com"
    assert row["execution_id"] == "exec-0002"
    assert row["status"] == "downloaded"
    assert row["size_bytes"] == "5242880"
    assert row["error"] == ""
    assert row["dest_path"] == "/exports/contoso.com/marketing_contoso.com_20260514.pst"


def test_group_job_row_failed_has_error() -> None:
    job = _make_job(
        identity="marketing@contoso.com",
        unit_label="",
        outcome="failed",
        outcome_msg="start failed: permission denied",
        bytes_saved=None,
        dest_path="/exports/contoso.com/marketing_contoso.com_20260514.pst",
        execution_id="exec-0003",
    )
    row = em._group_job_row(job)
    assert row["status"] == "failed"
    assert row["size_bytes"] == ""
    assert row["error"] == "start failed: permission denied"


def test_group_failure_row() -> None:
    f = em.MailExportFailure(
        identity="marketing@contoso.com",
        unit_label="",
        error="resource not found",
    )
    row = em._group_failure_row(f)
    assert row["group_mail"] == "marketing@contoso.com"
    assert row["domain"] == "contoso.com"
    assert row["execution_id"] == ""
    assert row["status"] == "skipped"
    assert row["size_bytes"] == ""
    assert row["error"] == "resource not found"
    assert row["dest_path"] == ""


# ── write_report + load_resume_csv round-trip ─────────────────────────────────


def test_roundtrip_downloaded_job_appears_in_downloaded_pairs(tmp_path: Path) -> None:
    csv_path = str(tmp_path / "report.csv")
    domain = em._build_exchange_domain("both")

    downloaded_job = _make_job(
        identity="alice@contoso.com",
        unit_label="mailbox",
        outcome="ok",
        bytes_saved=10_485_760,
        dest_path="/exports/contoso.com/alice@contoso.com/alice@contoso.com_20260514_mailbox.pst",
        execution_id="exec-0001",
    )
    em.write_report(csv_path, domain, [downloaded_job], [], None)
    state = em.load_resume_csv(csv_path, domain)

    assert ("alice@contoso.com", "mailbox") in state.downloaded_pairs
    assert len(state.pending_identities) == 0
    assert len(state.carried_rows) == 1
    assert state.carried_rows[0]["upn"] == "alice@contoso.com"
    assert state.carried_rows[0]["status"] == "downloaded"


def test_roundtrip_failed_job_appears_in_pending_identities(tmp_path: Path) -> None:
    csv_path = str(tmp_path / "report.csv")
    domain = em._build_exchange_domain("both")

    failed_job = _make_job(
        identity="bob@contoso.com",
        unit_label="mailbox",
        outcome="failed",
        outcome_msg="download error: network timeout",
        bytes_saved=None,
        dest_path="/exports/contoso.com/bob@contoso.com/bob@contoso.com_20260514_mailbox.pst",
        execution_id="exec-0002",
    )
    em.write_report(csv_path, domain, [failed_job], [], None)
    state = em.load_resume_csv(csv_path, domain)

    assert "bob@contoso.com" in state.pending_identities
    assert len(state.downloaded_pairs) == 0
    assert len(state.carried_rows) == 0


def test_roundtrip_mixed_jobs_split_correctly(tmp_path: Path) -> None:
    csv_path = str(tmp_path / "report.csv")
    domain = em._build_exchange_domain("both")

    downloaded_job = _make_job(
        identity="alice@contoso.com",
        unit_label="mailbox",
        outcome="ok",
        bytes_saved=10_485_760,
        dest_path="/exports/contoso.com/alice@contoso.com/alice@contoso.com_20260514_mailbox.pst",
        execution_id="exec-0001",
    )
    failed_job = _make_job(
        identity="bob@contoso.com",
        unit_label="mailbox",
        outcome="failed",
        outcome_msg="download error: network timeout",
        bytes_saved=None,
        dest_path="/exports/contoso.com/bob@contoso.com/bob@contoso.com_20260514_mailbox.pst",
        execution_id="exec-0002",
    )
    em.write_report(csv_path, domain, [downloaded_job, failed_job], [], None)
    state = em.load_resume_csv(csv_path, domain)

    assert ("alice@contoso.com", "mailbox") in state.downloaded_pairs
    assert "bob@contoso.com" in state.pending_identities
    assert len(state.carried_rows) == 1
    assert state.carried_rows[0]["upn"] == "alice@contoso.com"


def test_roundtrip_failure_row_also_lands_in_pending(tmp_path: Path) -> None:
    csv_path = str(tmp_path / "report.csv")
    domain = em._build_exchange_domain("primary")

    failure = em.MailExportFailure(
        identity="alice@contoso.com",
        unit_label="mailbox",
        error="no backup version found",
    )
    em.write_report(csv_path, domain, [], [failure], None)
    state = em.load_resume_csv(csv_path, domain)

    assert "alice@contoso.com" in state.pending_identities
    assert len(state.downloaded_pairs) == 0
    assert len(state.carried_rows) == 0


def test_roundtrip_carried_rows_are_preserved(tmp_path: Path) -> None:
    csv_path = str(tmp_path / "report.csv")
    domain = em._build_exchange_domain("both")

    existing_carried: list[dict[str, str]] = [
        {
            "upn": "carol@contoso.com",
            "domain": "contoso.com",
            "mailbox_type": "mailbox",
            "execution_id": "exec-old-01",
            "status": "downloaded",
            "size_bytes": "2097152",
            "error": "",
            "dest_path": "/exports/contoso.com/carol@contoso.com/carol@contoso.com_20260513_mailbox.pst",
        }
    ]
    new_job = _make_job(
        identity="alice@contoso.com",
        unit_label="archive mailbox",
        outcome="ok",
        bytes_saved=5_242_880,
        dest_path="/exports/contoso.com/alice@contoso.com/alice@contoso.com_20260514_archive_mailbox.pst",
        execution_id="exec-0003",
    )
    em.write_report(csv_path, domain, [new_job], [], existing_carried)
    state = em.load_resume_csv(csv_path, domain)

    assert ("carol@contoso.com", "mailbox") in state.downloaded_pairs
    assert ("alice@contoso.com", "archive mailbox") in state.downloaded_pairs
    assert len(state.carried_rows) == 2
    assert len(state.pending_identities) == 0
