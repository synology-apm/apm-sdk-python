"""Unit tests for synology_apm.cli/output.py — formatting utilities."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, call, patch

import pytest
import typer
from rich.table import Table

from synology_apm.cli.output import (
    ListOutputFormat,
    cell,
    dispatch_paginated_list,
    print_csv,
    print_json,
    print_ndjson_item,
    print_table,
    print_yaml,
)

# ── _to_serializable: datetime branch ─────────────────────────────────────


def test_print_json_serializes_datetime() -> None:
    dt = datetime(2026, 4, 21, 9, 0, tzinfo=UTC)
    # Trigger the datetime branch in _to_serializable via a plain dict value
    captured: list[str] = []
    with patch("synology_apm.cli.output.console") as mock_console:
        mock_console.print_json.side_effect = lambda s: captured.append(s)
        print_json({"ts": dt})
    assert len(captured) == 1
    assert "2026-04-21" in captured[0]


# ── print_yaml: ImportError ────────────────────────────────────────────────


def test_print_yaml_no_pyyaml_exits_1() -> None:
    with patch.dict("sys.modules", {"yaml": None}):
        with pytest.raises(typer.Exit) as exc_info:
            print_yaml({"key": "value"})
    assert exc_info.value.exit_code == 1


# ── print_csv: empty list returns immediately ──────────────────────────────


def test_print_csv_empty_list_produces_no_output(capsys: pytest.CaptureFixture[str]) -> None:
    print_csv([])
    captured = capsys.readouterr()
    assert captured.out == ""


# ── print_csv: nested dict/list cell ──────────────────────────────────────


def test_print_csv_nested_dict_serialized_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    print_csv([{"name": "x", "meta": {"a": 1}}])
    captured = capsys.readouterr()
    assert '{""a"": 1}' in captured.out  # nested dict cell is JSON-encoded (CSV doubles the quotes)


# ── print_table ────────────────────────────────────────────────────────────


def test_print_table_does_not_raise() -> None:
    t = Table()
    t.add_column("Col")
    t.add_row("value")
    print_table(t)  # should not raise


# ── cell helper ───────────────────────────────────────────────────────────


def test_cell_returns_fallback_for_none() -> None:
    assert cell(None) == "-"


def test_cell_returns_fallback_for_empty_string() -> None:
    assert cell("") == "-"


def test_cell_escapes_markup_by_default() -> None:
    result = cell("[bold]text[/bold]")
    assert "[bold]" not in result or result == r"\[bold]text\[/bold]"


def test_cell_passes_through_styled_markup() -> None:
    result = cell("[green]ok[/green]", styled=True)
    assert "[green]" in result


# ── print_ndjson_item ───────────────────────────────────────────────────────


def test_print_ndjson_item_writes_compact_json_line(capsys: pytest.CaptureFixture[str]) -> None:
    print_ndjson_item({"a": 1, "b": "x"})
    captured = capsys.readouterr()
    assert captured.out == '{"a":1,"b":"x"}\n'


# ── print_csv: headers / write_header ───────────────────────────────────────


def test_print_csv_returns_headers(capsys: pytest.CaptureFixture[str]) -> None:
    headers = print_csv([{"a": 1, "b": 2}])
    assert headers == ["a", "b"]
    captured = capsys.readouterr()
    assert captured.out == "a,b\n1,2\n"


def test_print_csv_empty_with_no_headers_returns_none(capsys: pytest.CaptureFixture[str]) -> None:
    assert print_csv([]) is None
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_csv_streaming_header_once(capsys: pytest.CaptureFixture[str]) -> None:
    headers = print_csv([{"a": 1, "b": 2}], write_header=True)
    print_csv([{"a": 3, "b": 4}], headers=headers, write_header=False)
    captured = capsys.readouterr()
    assert captured.out == "a,b\n1,2\n3,4\n"


# ── dispatch_paginated_list ──────────────────────────────────────────────────


def _to_dict(item: str) -> dict[str, str]:
    return {"id": item}


async def test_dispatch_paginated_list_single_page_table_returns_items() -> None:
    fetch = AsyncMock(return_value=(["a", "b"], 5))
    result = await dispatch_paginated_list(
        fetch, limit=2, offset=0, page_all=False, output=ListOutputFormat.TABLE,
        to_dict=_to_dict,
    )
    assert result == (["a", "b"], 5)
    fetch.assert_awaited_once_with(0, 2)


async def test_dispatch_paginated_list_single_page_json_dispatches_and_returns_none(capsys: pytest.CaptureFixture[str]) -> None:
    fetch = AsyncMock(return_value=(["a"], 5))
    result = await dispatch_paginated_list(
        fetch, limit=2, offset=0, page_all=False, output=ListOutputFormat.JSON,
        to_dict=_to_dict,
    )
    assert result is None
    fetch.assert_awaited_once_with(0, 2)
    captured = capsys.readouterr()
    assert '"id"' in captured.out and '"a"' in captured.out


async def test_dispatch_paginated_list_page_all_table_accumulates_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    fetch = AsyncMock(side_effect=[(["a", "b"], 4), (["c", "d"], 4)])
    result = await dispatch_paginated_list(
        fetch, limit=2, offset=0, page_all=True, output=ListOutputFormat.TABLE,
        to_dict=_to_dict,
    )
    assert result == (["a", "b", "c", "d"], 4)
    assert fetch.await_args_list == [call(0, 2), call(2, 2)]


async def test_dispatch_paginated_list_page_all_json_streams_ndjson(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    fetch = AsyncMock(side_effect=[(["a", "b"], 3), (["c"], 3)])
    result = await dispatch_paginated_list(
        fetch, limit=2, offset=0, page_all=True, output=ListOutputFormat.JSON,
        to_dict=_to_dict,
    )
    assert result is None
    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()
    assert lines == ['{"id":"a"}', '{"id":"b"}', '{"id":"c"}']


async def test_dispatch_paginated_list_page_all_csv_header_once(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    fetch = AsyncMock(side_effect=[(["a", "b"], 3), (["c"], 3)])
    result = await dispatch_paginated_list(
        fetch, limit=2, offset=0, page_all=True, output=ListOutputFormat.CSV,
        to_dict=_to_dict,
    )
    assert result is None
    captured = capsys.readouterr()
    assert captured.out == "id\na\nb\nc\n"


async def test_dispatch_paginated_list_page_all_yaml_multi_doc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    fetch = AsyncMock(side_effect=[(["a", "b"], 3), (["c"], 3)])
    captured: list[str] = []
    with patch("synology_apm.cli.output.console") as mock_console:
        mock_console.print.side_effect = lambda s="": captured.append(s)
        result = await dispatch_paginated_list(
            fetch, limit=2, offset=0, page_all=True, output=ListOutputFormat.YAML,
            to_dict=_to_dict,
        )
    assert result is None
    assert captured.count("---") == 2


async def test_dispatch_paginated_list_terminates_when_total_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    fetch = AsyncMock(return_value=(["a", "b"], 2))
    result = await dispatch_paginated_list(
        fetch, limit=2, offset=0, page_all=True, output=ListOutputFormat.TABLE,
        to_dict=_to_dict,
    )
    assert result == (["a", "b"], 2)
    fetch.assert_awaited_once_with(0, 2)


async def test_dispatch_paginated_list_terminates_on_short_page_no_total(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    fetch = AsyncMock(return_value=(["a"], None))
    result = await dispatch_paginated_list(
        fetch, limit=5, offset=0, page_all=True, output=ListOutputFormat.TABLE,
        to_dict=_to_dict,
    )
    assert result == (["a"], None)
    fetch.assert_awaited_once_with(0, 5)


async def test_dispatch_paginated_list_page_all_yaml_no_trailing_empty_doc(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the dataset size is an exact multiple of --limit and total is unknown (None/0),
    the final, empty fetch must not produce a spurious trailing '---\\n[]\\n' YAML document."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    fetch = AsyncMock(side_effect=[(["a", "b"], 0), ([], 0)])
    captured: list[str] = []
    with patch("synology_apm.cli.output.console") as mock_console:
        mock_console.print.side_effect = lambda s="": captured.append(s)
        result = await dispatch_paginated_list(
            fetch, limit=2, offset=0, page_all=True, output=ListOutputFormat.YAML,
            to_dict=_to_dict,
        )
    assert result is None
    assert captured.count("---") == 1
    assert fetch.await_args_list == [call(0, 2), call(2, 2)]


async def test_dispatch_paginated_list_continues_past_full_page_when_total_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """total=0 (the sentinel some log endpoints return for "no total available") must not be
    mistaken for "zero results total" — pagination should keep fetching full pages and stop
    only once a short page is returned, just like total=None."""
    monkeypatch.setattr("synology_apm.cli.output._PAGE_FETCH_DELAY_SECONDS", 0)
    fetch = AsyncMock(side_effect=[(["a", "b"], 0), (["c"], 0)])
    result = await dispatch_paginated_list(
        fetch, limit=2, offset=0, page_all=True, output=ListOutputFormat.TABLE,
        to_dict=_to_dict,
    )
    assert result == (["a", "b", "c"], 0)
    assert fetch.await_args_list == [call(0, 2), call(2, 2)]


def test_print_json_serializes_dataclass_and_enum(capsys: pytest.CaptureFixture[str]) -> None:
    """print_json converts nested dataclasses to dicts and enums to their values."""
    import dataclasses
    import json

    from synology_apm.sdk.enums import WorkloadCategory

    @dataclasses.dataclass
    class Item:
        name: str
        category: WorkloadCategory

    print_json(Item(name="vm-web-01", category=WorkloadCategory.MACHINE))
    out = json.loads(capsys.readouterr().out)
    assert out == {"name": "vm-web-01", "category": "machine"}
