"""Formatted output utilities — table / json / yaml / csv."""
from __future__ import annotations

import asyncio
import csv
import dataclasses
import json
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any, TypeVar

import typer
from rich.markup import escape as _markup_escape
from rich.table import Table

from synology_apm.cli.errors import EXIT_ERROR, _dynamic_console

console = _dynamic_console(soft_wrap=True)

_T = TypeVar("_T")

_PAGE_FETCH_DELAY_SECONDS: float = 0.5
"""Delay between page fetches in --page-all mode."""


class OutputFormat(StrEnum):
    TABLE = "table"
    JSON = "json"
    YAML = "yaml"


class ListOutputFormat(StrEnum):
    TABLE = "table"
    JSON = "json"
    YAML = "yaml"
    CSV = "csv"


def to_local_iso(dt: datetime | None) -> str | None:
    """Format a datetime as a local-timezone ISO 8601 string; returns None when None.

    Canonical implementation shared by _display.fmt_datetime_iso and the serializers.
    """
    return dt.astimezone().isoformat() if dt is not None else None


def _to_serializable(obj: Any) -> Any:
    """Recursively convert dataclasses / datetimes / Enums to JSON-safe types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_serializable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return to_local_iso(obj)
    if isinstance(obj, (list, tuple)):
        return [_to_serializable(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    return obj


def print_json(data: Any) -> None:
    """Print JSON-formatted output."""
    console.print_json(json.dumps(_to_serializable(data), ensure_ascii=False))


def print_yaml(data: Any) -> None:
    """Print YAML-formatted output."""
    try:
        import yaml
    except ImportError:
        typer.echo("YAML output requires PyYAML: pip install pyyaml", err=True)
        raise typer.Exit(code=EXIT_ERROR) from None
    console.print(yaml.dump(_to_serializable(data), allow_unicode=True, sort_keys=False))


def print_ndjson_item(data: Any) -> None:
    """Print a single record as one line of compact JSON (NDJSON)."""
    sys.stdout.write(json.dumps(_to_serializable(data), ensure_ascii=False, separators=(",", ":")) + "\n")


def print_csv(
    data: list[Any],
    headers: list[str] | None = None,
    write_header: bool = True,
) -> list[str] | None:
    """Print CSV-formatted output; nested dicts/lists are serialized as JSON strings as a fallback.

    By default, the header row is derived from the union of keys across ``data`` and
    written before the data rows. Pass ``headers`` to reuse a header set computed from
    a previous page (e.g. for streaming multi-page output), and ``write_header=False``
    to omit the header row on subsequent pages.

    Returns the headers used, or None if ``data`` is empty and ``headers`` was not given.
    """
    rows = [_to_serializable(item) for item in data]
    if headers is None:
        if not rows:
            return None
        headers = list(dict.fromkeys(k for row in rows for k in row))

    def _cell(v: Any) -> Any:
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return "" if v is None else v

    writer = csv.writer(sys.stdout, lineterminator="\n")
    if write_header:
        writer.writerow(headers)
    for row in rows:
        writer.writerow([_cell(row.get(h)) for h in headers])
    return headers


def print_table(table: Table) -> None:
    """Print a Rich Table."""
    console.print(table)


def dispatch_list_output(
    items: Sequence[_T],
    output: ListOutputFormat,
    to_dict: Callable[[_T], dict[str, Any]],
    to_csv_row: Callable[[_T], dict[str, Any]] | None = None,
) -> bool:
    """Print a list in the requested non-table format and return True; return False for TABLE.

    Usage::

        if dispatch_list_output(items, output, _item_to_dict, _item_to_csv_row):
            return
        # render table here
    """
    if output == ListOutputFormat.JSON:
        print_json([to_dict(i) for i in items])
    elif output == ListOutputFormat.YAML:
        print_yaml([to_dict(i) for i in items])
    elif output == ListOutputFormat.CSV:
        print_csv([(to_csv_row or to_dict)(i) for i in items])
    else:
        return False
    return True


async def dispatch_paginated_list(
    fetch_page: Callable[[int, int], Awaitable[tuple[Sequence[_T], int | None]]],
    *,
    limit: int,
    offset: int,
    page_all: bool,
    output: ListOutputFormat,
    to_dict: Callable[[_T], dict[str, Any]],
    to_csv_row: Callable[[_T], dict[str, Any]] | None = None,
) -> tuple[list[_T], int | None] | None:
    """Fetch one page, or all pages, and dispatch output for non-table formats.

    When ``page_all`` is False, fetches a single page via ``fetch_page(offset, limit)``.
    When ``page_all`` is True, repeatedly calls ``fetch_page`` starting at ``offset``,
    advancing by the number of items returned each time, with a delay of
    :data:`_PAGE_FETCH_DELAY_SECONDS` between fetches, until the dataset is exhausted.

    For TABLE output, all fetched items are accumulated and returned (along with the
    most recently reported total) for the caller to render as a single table. For
    JSON, YAML, and CSV output, each page is dispatched as it is fetched (NDJSON,
    a YAML multi-document stream, and a CSV with a header on the first page only,
    respectively) and this function returns None.

    ``fetch_page(offset, limit)`` returns ``(items, total)``; ``total`` may be
    ``None``, zero, or negative when the API does not report a total count, in
    which case pagination stops once a page shorter than ``limit`` is returned.
    """
    def _done(cur_offset: int, n: int, total: int | None) -> bool:
        return n == 0 or n < limit or (total is not None and total > 0 and cur_offset + n >= total)

    if not page_all:
        items, total = await fetch_page(offset, limit)
        if dispatch_list_output(items, output, to_dict, to_csv_row):
            return None
        return list(items), total

    if output == ListOutputFormat.TABLE:
        all_items: list[_T] = []
        total = None
        cur = offset
        while True:
            items, total = await fetch_page(cur, limit)
            all_items.extend(items)
            if _done(cur, len(items), total):
                break
            cur += len(items)
            await asyncio.sleep(_PAGE_FETCH_DELAY_SECONDS)
        return all_items, total

    cur = offset
    csv_headers: list[str] | None = None
    while True:
        items, total = await fetch_page(cur, limit)
        if output == ListOutputFormat.JSON:
            for item in items:
                print_ndjson_item(to_dict(item))
        elif output == ListOutputFormat.YAML:
            if items or cur == offset:
                console.print("---")
                print_yaml([to_dict(i) for i in items])
        elif output == ListOutputFormat.CSV:
            rows = [(to_csv_row or to_dict)(i) for i in items]
            if csv_headers is None:
                csv_headers = print_csv(rows, write_header=True)
            else:
                print_csv(rows, headers=csv_headers, write_header=False)
        if _done(cur, len(items), total):
            break
        cur += len(items)
        await asyncio.sleep(_PAGE_FETCH_DELAY_SECONDS)
    return None


def dispatch_output(
    item: _T,
    output: OutputFormat,
    to_dict: Callable[[_T], dict[str, Any]],
) -> bool:
    """Print a single item in the requested non-table format and return True; return False for TABLE.

    Usage::

        if dispatch_output(item, output, _item_to_dict):
            return
        # render detail view here
    """
    if output == OutputFormat.JSON:
        print_json(to_dict(item))
    elif output == OutputFormat.YAML:
        print_yaml(to_dict(item))
    else:
        return False
    return True


def cell(v: str | None, fallback: str = "-", *, styled: bool = False) -> str:
    """Escape an API-sourced string for safe display as a Rich table cell.

    By default, treats v as plain text — Rich markup syntax in brackets is
    escaped so it is displayed literally rather than interpreted as a style tag.
    Pass styled=True for values that already contain intentional Rich markup
    (e.g. formatter outputs like ``[green]✓ Success[/green]``).
    Returns fallback when v is None or empty.
    """
    if not v:
        return fallback
    return v if styled else _markup_escape(v)

def new_table(*, expand: bool = False) -> Table:
    """Create a Rich table with the CLI's standard styling (bold header, no edge)."""
    return Table(show_header=True, header_style="bold", show_edge=False, padding=(0, 1), expand=expand)
