"""Tests for _helpers.py: resolve_* and pagination utilities."""
from __future__ import annotations

import pytest

from synology_apm.sdk import ResourceNotFoundError
from tests.unit.mcp.conftest import (
    make_backup_server,
    make_export_activity,
    make_m365_workload,
    make_machine_workload,
    make_workload_version,
)


class TestListResult:
    @pytest.mark.asyncio
    async def test_returns_items_and_total(self, mock_apm):
        from synology_apm.mcp._helpers import list_result

        bs = make_backup_server()
        mock_apm.backup_servers.list.return_value = ([bs], 1)

        result = await list_result(mock_apm.backup_servers.list(limit=100), lambda x: x.to_dict())
        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["name"] == "apm-server-01"

    @pytest.mark.asyncio
    async def test_empty_list(self, mock_apm):
        from synology_apm.mcp._helpers import list_result

        mock_apm.backup_servers.list.return_value = ([], 0)

        result = await list_result(mock_apm.backup_servers.list(limit=100), lambda x: x.to_dict())
        assert result["total"] == 0
        assert result["items"] == []

    @pytest.mark.asyncio
    async def test_truncated_flag_when_items_less_than_total(self, mock_apm):
        from synology_apm.mcp._helpers import list_result

        bs = make_backup_server()
        mock_apm.backup_servers.list.return_value = ([bs], 5)

        result = await list_result(mock_apm.backup_servers.list(limit=1), lambda x: x.to_dict())
        assert result["total"] == 5
        assert len(result["items"]) == 1
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_no_truncated_flag_when_all_items_returned(self, mock_apm):
        from synology_apm.mcp._helpers import list_result

        bs = make_backup_server()
        mock_apm.backup_servers.list.return_value = ([bs], 1)

        result = await list_result(mock_apm.backup_servers.list(limit=100), lambda x: x.to_dict())
        assert "truncated" not in result

    @pytest.mark.asyncio
    async def test_none_total_infers_truncated_from_full_page(self, mock_apm):
        """Some endpoints (e.g. log listing) never report a real total; when the
        coroutine's total is None, list_result() falls back to inferring truncation
        from a full page."""
        from synology_apm.mcp._helpers import list_result

        bs = make_backup_server()
        mock_apm.backup_servers.list.return_value = ([bs] * 25, None)

        result = await list_result(
            mock_apm.backup_servers.list(limit=25),
            lambda x: x.to_dict(),
            limit=25,
        )
        assert result["total"] is None
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_none_total_no_truncated_when_page_not_full(self, mock_apm):
        from synology_apm.mcp._helpers import list_result

        bs = make_backup_server()
        mock_apm.backup_servers.list.return_value = ([bs], None)

        result = await list_result(
            mock_apm.backup_servers.list(limit=25),
            lambda x: x.to_dict(),
            limit=25,
        )
        assert result["total"] is None
        assert "truncated" not in result

    @pytest.mark.asyncio
    async def test_no_truncated_flag_on_last_page_with_offset(self, mock_apm):
        """offset=90, limit=10, total=95 -> 5 items on the true last page must not be
        flagged truncated (regression test for offset-unaware truncation)."""
        from synology_apm.mcp._helpers import list_result

        bs = make_backup_server()
        mock_apm.backup_servers.list.return_value = ([bs] * 5, 95)

        result = await list_result(
            mock_apm.backup_servers.list(limit=10, offset=90),
            lambda x: x.to_dict(),
            offset=90,
        )
        assert result["total"] == 95
        assert "truncated" not in result

    @pytest.mark.asyncio
    async def test_truncated_flag_with_offset_when_more_remain(self, mock_apm):
        from synology_apm.mcp._helpers import list_result

        bs = make_backup_server()
        mock_apm.backup_servers.list.return_value = ([bs] * 10, 95)

        result = await list_result(
            mock_apm.backup_servers.list(limit=10, offset=80),
            lambda x: x.to_dict(),
            offset=80,
        )
        assert result["truncated"] is True


class TestGetResult:
    @pytest.mark.asyncio
    async def test_serializes_single_item(self, mock_apm):
        from synology_apm.mcp._helpers import get_result

        bs = make_backup_server()
        mock_apm.backup_servers.get.return_value = bs

        result = await get_result(mock_apm.backup_servers.get("srv-001"), lambda x: x.to_dict())
        assert result["name"] == "apm-server-01"
        assert result["backup_server_id"] == "srv-001"




class TestToEnumList:
    def test_none_returns_none(self):
        from synology_apm.mcp._helpers import to_enum_list
        from synology_apm.sdk import LogLevel

        assert to_enum_list(LogLevel, None) is None

    def test_empty_list_returns_none(self):
        from synology_apm.mcp._helpers import to_enum_list
        from synology_apm.sdk import LogLevel

        assert to_enum_list(LogLevel, []) is None

    def test_converts_valid_values(self):
        from synology_apm.mcp._helpers import to_enum_list
        from synology_apm.sdk import LogLevel

        assert to_enum_list(LogLevel, ["info", "warning"]) == [LogLevel.INFO, LogLevel.WARNING]

    def test_invalid_value_raises(self):
        from synology_apm.mcp._helpers import to_enum_list
        from synology_apm.sdk import LogLevel

        with pytest.raises(ValueError):
            to_enum_list(LogLevel, ["bogus"])


class TestResolveExportActivity:
    @pytest.mark.asyncio
    async def test_resolves_matching_activity(self, mock_apm):
        from synology_apm.mcp._helpers import resolve_export_activity

        act = make_export_activity(activity_id="exp-001")
        wl = make_m365_workload()
        mock_apm.m365.exchange_export.list.return_value = ([act], 1)

        result = await resolve_export_activity(mock_apm.m365.exchange_export, wl, "exp-001")
        assert result.activity_id == "exp-001"
        mock_apm.m365.exchange_export.list.assert_called_once_with(wl, limit=500, offset=0)

    @pytest.mark.asyncio
    async def test_raises_resource_not_found_when_absent(self, mock_apm):
        from synology_apm.mcp._helpers import resolve_export_activity

        wl = make_m365_workload()
        mock_apm.m365.exchange_export.list.return_value = ([], 0)

        with pytest.raises(ResourceNotFoundError) as exc_info:
            await resolve_export_activity(mock_apm.m365.exchange_export, wl, "exp-missing")
        assert exc_info.value.resource_type == "M365ExportActivity"
        assert exc_info.value.resource_id == "exp-missing"

    @pytest.mark.asyncio
    async def test_finds_activity_beyond_the_first_page(self, mock_apm):
        from synology_apm.mcp._helpers import resolve_export_activity

        wl = make_m365_workload()
        page1 = [make_export_activity(activity_id=f"exp-{i}", execution_id=f"exec-{i}") for i in range(500)]
        page2 = [make_export_activity(activity_id="exp-500", execution_id="exec-500")]
        mock_apm.m365.exchange_export.list.side_effect = [
            (page1, 501),
            (page2, 501),
        ]

        result = await resolve_export_activity(mock_apm.m365.exchange_export, wl, "exp-500")
        assert result.activity_id == "exp-500"
        assert mock_apm.m365.exchange_export.list.call_count == 2
        _, second_kwargs = mock_apm.m365.exchange_export.list.call_args_list[1]
        assert second_kwargs["offset"] == 500

    @pytest.mark.asyncio
    async def test_raises_resource_not_found_after_exhausting_all_pages(self, mock_apm):
        from synology_apm.mcp._helpers import resolve_export_activity

        act = make_export_activity(activity_id="exp-0", execution_id="exec-exp-0")
        wl = make_m365_workload()
        mock_apm.m365.exchange_export.list.side_effect = [
            ([act], 1),
        ]

        with pytest.raises(ResourceNotFoundError):
            await resolve_export_activity(mock_apm.m365.exchange_export, wl, "exp-missing")
        mock_apm.m365.exchange_export.list.assert_called_once()


class TestCoerceJsonEncodedList:
    def test_json_array_string_of_strings_is_parsed(self):
        from synology_apm.mcp._helpers import coerce_json_encoded_list

        assert coerce_json_encoded_list('["mon","wed"]') == ["mon", "wed"]

    def test_json_array_string_of_dicts_is_parsed(self):
        """Mirrors the update_machine_file_server `selectors` shape."""
        from synology_apm.mcp._helpers import coerce_json_encoded_list

        raw = '[{"path": "", "excluded_paths": []}, {"path": "/share2", "excluded_paths": []}]'
        assert coerce_json_encoded_list(raw) == [
            {"path": "", "excluded_paths": []},
            {"path": "/share2", "excluded_paths": []},
        ]

    def test_non_string_value_passes_through_unchanged(self):
        from synology_apm.mcp._helpers import coerce_json_encoded_list

        value = ["mon", "wed"]
        assert coerce_json_encoded_list(value) is value

    @pytest.mark.parametrize(
        "raw",
        ["[1,2", '{"a":1}', '"just a string"', "42"],
        ids=["malformed_json", "json_object", "json_quoted_string", "json_number"],
    )
    def test_non_list_value_passes_through_unchanged(self, raw):
        """So real pydantic validation still reports the original error downstream."""
        from synology_apm.mcp._helpers import coerce_json_encoded_list

        assert coerce_json_encoded_list(raw) == raw


class TestResolveMachineVersion:
    @pytest.mark.asyncio
    async def test_resolves_workload_and_version(self, mock_apm):
        from synology_apm.mcp._helpers import resolve_machine_version

        wl = make_machine_workload()
        version = make_workload_version()
        mock_apm.machine.workloads.get.return_value = wl
        mock_apm.machine.workloads.get_version.return_value = version

        workload, ver = await resolve_machine_version(
            mock_apm, workload_id="123e4567-e89b-12d3-a456-426614174001", namespace="default", version_id="ver-001",
        )
        assert workload.name == "vm-web-01"
        assert ver.version_id == "ver-001"
        mock_apm.machine.workloads.get_latest_version.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolves_latest_version_when_version_id_omitted(self, mock_apm):
        from synology_apm.mcp._helpers import resolve_machine_version

        wl = make_machine_workload()
        version = make_workload_version()
        mock_apm.machine.workloads.get.return_value = wl
        mock_apm.machine.workloads.get_latest_version.return_value = version

        workload, ver = await resolve_machine_version(
            mock_apm, workload_id="123e4567-e89b-12d3-a456-426614174001", namespace="default", version_id=None,
        )
        assert workload.name == "vm-web-01"
        assert ver.version_id == version.version_id
        mock_apm.machine.workloads.get_latest_version.assert_called_once_with(wl)
        mock_apm.machine.workloads.get_version.assert_not_called()
