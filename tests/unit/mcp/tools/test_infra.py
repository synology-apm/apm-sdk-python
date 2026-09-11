"""Tests for tools/infra.py."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from synology_apm.sdk import (
    AmazonS3ChinaStorageAddRequest,
    AmazonS3StorageAddRequest,
    APVStorageAddRequest,
    AzureBlobChinaStorageAddRequest,
    AzureBlobStorageAddRequest,
    C2ObjectStorageAddRequest,
    GenericS3StorageAddRequest,
    WasabiCloudStorageAddRequest,
)
from tests.unit.mcp.conftest import (
    assert_destructive_preview_then_execute,
    call_tool,
    make_backup_server,
    make_hypervisor,
    make_remote_storage,
    make_retirement_plan,
    make_tiering_plan,
)

_STORAGE_CASES = [
    ("s3_compatible",        GenericS3StorageAddRequest,     True,  True),
    ("active_protect_vault", APVStorageAddRequest,           True,  False),
    ("amazon_s3",            AmazonS3StorageAddRequest,      False, True),
    ("amazon_s3_china",      AmazonS3ChinaStorageAddRequest, False, True),
    ("c2_object_storage",    C2ObjectStorageAddRequest,      False, True),
    ("wasabi",               WasabiCloudStorageAddRequest,   False, True),
]


class TestGetSiteInfo:
    @pytest.mark.asyncio
    async def test_calls_sdk_and_returns_json(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        from synology_apm.sdk import SiteInfo, SiteStorageStats, WorkloadUsageSummary

        mock_apm.get_site_info.return_value = SiteInfo(
            site_uuid="uuid-001",
            external_address="apm.corp.com",
            port="443",
            primary_management_server=None,
            secondary_management_server=None,
            site_storage=SiteStorageStats(logical_backup_data_bytes=0, physical_backup_data_bytes=0),
            workload_usage=WorkloadUsageSummary(by_type=()),
        )

        result = await call_tool(admin_server, "get_site_info", mock_ctx)

        assert result["site_uuid"] == "uuid-001"
        assert result["external_address"] == "apm.corp.com"


_LIST_CASES = [
    # (tool_name, collection_attr, resource_factory, check_field, check_value)
    ("list_backup_servers", "backup_servers", make_backup_server, "name", "apm-server-01"),
    ("list_remote_storages", "remote_storages", make_remote_storage, "name", "DSM-Storage"),
    ("list_hypervisors", "hypervisors", make_hypervisor, "hostname", "esxi1.example.com"),
]

_GET_CASES = [
    # (tool_name, collection_attr, resource_factory, id_kwarg, id_value, check_field, check_value)
    ("get_backup_server", "backup_servers", make_backup_server, "server_id", "srv-001", "name", "apm-server-01"),
    ("get_remote_storage", "remote_storages", make_remote_storage, "storage_id", "stor-001", "name", "DSM-Storage"),
    ("get_hypervisor", "hypervisors", make_hypervisor, "hypervisor_id", "hyp-001", "hypervisor_id", "hyp-001"),
]


class TestListInfraResources:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool_name,collection_attr,resource_factory,check_field,check_value",
        _LIST_CASES, ids=[c[0] for c in _LIST_CASES],
    )
    async def test_returns_items_and_total(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP, tool_name: str,
        collection_attr: str, resource_factory: Callable[[], Any], check_field: str, check_value: str,
    ) -> None:
        resource = resource_factory()
        getattr(mock_apm, collection_attr).list.return_value = ([resource], 1)

        result = await call_tool(admin_server, tool_name, mock_ctx)

        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0][check_field] == check_value


class TestListBackupServersFilters:
    @pytest.mark.asyncio
    async def test_forwards_name_status_and_type_filters(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        from synology_apm.sdk import BackupServerType, ServerStatus

        mock_apm.backup_servers.list.return_value = ([], 0)

        await call_tool(
            admin_server, "list_backup_servers", mock_ctx,
            keyword="apm-server", status=["healthy", "warning"], server_type=["dp"],
        )

        _, kwargs = mock_apm.backup_servers.list.call_args
        assert kwargs["keyword"] == "apm-server"
        assert kwargs["status_filter"] == [ServerStatus.HEALTHY, ServerStatus.WARNING]
        assert kwargs["type_filter"] == [BackupServerType.DP]

    @pytest.mark.asyncio
    async def test_filters_default_to_none(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        mock_apm.backup_servers.list.return_value = ([], 0)

        await call_tool(admin_server, "list_backup_servers", mock_ctx)

        _, kwargs = mock_apm.backup_servers.list.call_args
        assert kwargs["keyword"] is None
        assert kwargs["status_filter"] is None
        assert kwargs["type_filter"] is None


class TestGetInfraResource:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool_name,collection_attr,resource_factory,id_kwarg,id_value,check_field,check_value",
        _GET_CASES, ids=[c[0] for c in _GET_CASES],
    )
    async def test_resolves_by_id_and_returns_dict(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP, tool_name: str,
        collection_attr: str, resource_factory: Callable[[], Any], id_kwarg: str, id_value: str,
        check_field: str, check_value: str,
    ) -> None:
        resource = resource_factory()
        getattr(mock_apm, collection_attr).get.return_value = resource

        result = await call_tool(admin_server, tool_name, mock_ctx, **{id_kwarg: id_value})

        assert result[check_field] == check_value
        getattr(mock_apm, collection_attr).get.assert_called_once_with(id_value)


class TestChangeTieringPlan:
    @pytest.mark.asyncio
    async def test_removes_tiering_plan_when_no_plan_provided(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        server = make_backup_server()
        mock_apm.backup_servers.get.return_value = server
        mock_apm.backup_servers.change_tiering_plan.return_value = None

        result = await call_tool(admin_server, "change_backup_server_tiering_plan", mock_ctx, server_id="srv-001")

        assert result["ok"] is True
        assert result["tiering_plan_id"] is None
        mock_apm.backup_servers.change_tiering_plan.assert_called_once_with(server, None)

    @pytest.mark.asyncio
    async def test_sets_tiering_plan_when_provided(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        server = make_backup_server()
        plan = make_tiering_plan()
        mock_apm.backup_servers.get.return_value = server
        mock_apm.tiering_plans.get.return_value = plan

        result = await call_tool(
            admin_server, "change_backup_server_tiering_plan", mock_ctx,
            server_id="srv-001", tiering_plan_id="tier-001",
        )

        assert result["ok"] is True
        assert result["tiering_plan_id"] == "tier-001"
        mock_apm.backup_servers.change_tiering_plan.assert_called_once_with(server, plan)

    @pytest.mark.asyncio
    async def test_plan_not_found_returns_structured_error(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        from synology_apm.sdk import ResourceNotFoundError

        server = make_backup_server()
        mock_apm.backup_servers.get.return_value = server
        mock_apm.tiering_plans.get.side_effect = ResourceNotFoundError(
            "not found", resource_type="TieringPlan", resource_id="tier-missing",
        )

        with pytest.raises(ToolError) as exc_info:
            await call_tool(
                admin_server, "change_backup_server_tiering_plan", mock_ctx,
                server_id="srv-001", tiering_plan_id="tier-missing",
            )
        result = json.loads(str(exc_info.value))

        assert result["error"] == "not_found"
        mock_apm.backup_servers.change_tiering_plan.assert_not_called()


class TestBuildStorageRequest:
    @pytest.mark.parametrize(
        "storage_type,cls,has_endpoint,has_vault",
        _STORAGE_CASES,
        ids=[c[0] for c in _STORAGE_CASES],
    )
    def test_storage_type(self, storage_type: str, cls: type[Any], has_endpoint: bool, has_vault: bool) -> None:
        from synology_apm.mcp.tools.infra import _build_storage_request
        req = _build_storage_request(storage_type, "key", "secret", "vault", "ep:8080", False, "", False)
        assert isinstance(req, cls)
        assert req.access_key == "key"
        assert req.secret_key == "secret"
        if has_vault:
            assert req.vault_name == "vault"
        if has_endpoint:
            assert req.endpoint == "ep:8080"

    def test_unknown_type_raises(self) -> None:
        from synology_apm.mcp.tools.infra import _build_storage_request
        with pytest.raises(ValueError, match="Unsupported storage_type"):
            _build_storage_request("ftp", "key", "secret", "vault", "", False, "", False)

    def test_unmanaged_retirement_plan_defaults_to_none(self) -> None:
        from synology_apm.mcp.tools.infra import _build_storage_request
        req = _build_storage_request("s3_compatible", "key", "secret", "vault", "ep:8080", False, "", False)
        assert req.unmanaged_retirement_plan is None

    def test_unmanaged_retirement_plan_passthrough(self) -> None:
        from synology_apm.mcp.tools.infra import _build_storage_request
        plan = make_retirement_plan()
        req = _build_storage_request(
            "s3_compatible", "key", "secret", "vault", "ep:8080", False, "", False, plan
        )
        assert req.unmanaged_retirement_plan is plan

    def test_azure_blob(self) -> None:
        from synology_apm.mcp.tools.infra import _build_storage_request
        req = _build_storage_request(
            "azure_blob", "", "", "my-container", "", False, "", False,
            tenant_id="tenant-1", client_id="client-1", azure_secret="secret-1", account_name="acct-1",
        )
        assert isinstance(req, AzureBlobStorageAddRequest)
        assert req.tenant_id == "tenant-1"
        assert req.client_id == "client-1"
        assert req.secret == "secret-1"
        assert req.account_name == "acct-1"
        assert req.vault_name == "my-container"

    def test_azure_blob_china(self) -> None:
        from synology_apm.mcp.tools.infra import _build_storage_request
        req = _build_storage_request(
            "azure_blob_china", "", "", "my-container", "", False, "", False,
            tenant_id="tenant-1", client_id="client-1", azure_secret="secret-1", account_name="acct-1",
        )
        assert isinstance(req, AzureBlobChinaStorageAddRequest)

    def test_non_azure_missing_access_key_raises(self) -> None:
        """Only the actually-missing field is named, not both unconditionally."""
        from synology_apm.mcp.tools.infra import _build_storage_request
        with pytest.raises(ValueError, match=r"s3_compatible requires: access_key\.$"):
            _build_storage_request("s3_compatible", "", "secret", "vault", "ep:8080", False, "", False)

    def test_non_azure_missing_secret_key_raises(self) -> None:
        from synology_apm.mcp.tools.infra import _build_storage_request
        with pytest.raises(ValueError, match=r"amazon_s3 requires: secret_key\.$"):
            _build_storage_request("amazon_s3", "key", "", "vault", "", False, "", False)

    def test_non_azure_missing_both_credentials_raises(self) -> None:
        from synology_apm.mcp.tools.infra import _build_storage_request
        with pytest.raises(ValueError, match=r"amazon_s3 requires: access_key, secret_key\.$"):
            _build_storage_request("amazon_s3", "", "", "vault", "", False, "", False)

    def test_azure_blob_missing_fields_raises(self) -> None:
        from synology_apm.mcp.tools.infra import _build_storage_request
        with pytest.raises(
            ValueError,
            match=r"azure_blob requires: client_id, azure_secret, account_name",
        ):
            _build_storage_request(
                "azure_blob", "", "", "my-container", "", False, "", False,
                tenant_id="tenant-1",
            )


class TestAddRemoteStorage:
    @pytest.mark.asyncio
    async def test_calls_sdk_and_returns_dict(self, mock_apm: MagicMock, mock_ctx: MagicMock) -> None:
        from synology_apm.mcp.tools.infra import _add_remote_storage
        from synology_apm.sdk import RemoteStorageAddResult

        storage = make_remote_storage(storage_id="stor-new")
        mock_apm.remote_storages.add.return_value = RemoteStorageAddResult(
            storage=storage, encryption_key=None
        )
        result = await _add_remote_storage(mock_apm, "s3_compatible", "key", "secret", "vault", "endpoint", False, "", False)
        assert result["storage"]["storage_id"] == "stor-new"
        assert result["encryption_key"] is None

    @pytest.mark.asyncio
    async def test_resolves_retirement_plan_and_forwards_to_request(self, mock_apm: MagicMock, mock_ctx: MagicMock) -> None:
        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import RemoteStorageAddResult

        storage = make_remote_storage(storage_id="stor-new")
        plan = make_retirement_plan()
        mock_apm.retirement_plans.get.return_value = plan
        mock_apm.remote_storages.add.return_value = RemoteStorageAddResult(
            storage=storage, encryption_key=None
        )

        server = create_server(mode="admin")
        await call_tool(
            server, "add_remote_storage", mock_ctx,
            storage_type="s3_compatible",
            access_key="key",
            secret_key="secret",
            vault_name="vault",
            endpoint="ep:8080",
            retirement_plan_id="ret-001",
        )

        mock_apm.retirement_plans.get.assert_called_once_with("ret-001")
        mock_apm.remote_storages.add.assert_called_once()
        (request,), _ = mock_apm.remote_storages.add.call_args
        assert request.unmanaged_retirement_plan is plan

    @pytest.mark.asyncio
    async def test_no_retirement_plan_when_not_provided(self, mock_apm: MagicMock, mock_ctx: MagicMock) -> None:
        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import RemoteStorageAddResult

        storage = make_remote_storage(storage_id="stor-new")
        mock_apm.remote_storages.add.return_value = RemoteStorageAddResult(
            storage=storage, encryption_key=None
        )

        server = create_server(mode="admin")
        await call_tool(
            server, "add_remote_storage", mock_ctx,
            storage_type="s3_compatible",
            access_key="key",
            secret_key="secret",
            vault_name="vault",
            endpoint="ep:8080",
        )

        mock_apm.retirement_plans.get.assert_not_called()
        (request,), _ = mock_apm.remote_storages.add.call_args
        assert request.unmanaged_retirement_plan is None

    @pytest.mark.asyncio
    async def test_audit_log_records_storage_type(self, mock_apm: MagicMock, mock_ctx: MagicMock, tmp_path: Path) -> None:
        import os
        from unittest.mock import patch

        from synology_apm.mcp._server import create_server
        from synology_apm.sdk import RemoteStorageAddResult

        storage = make_remote_storage(storage_id="stor-new")
        mock_apm.remote_storages.add.return_value = RemoteStorageAddResult(storage=storage, encryption_key=None)

        server = create_server(mode="admin")

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            await call_tool(
                server, "add_remote_storage", mock_ctx,
                storage_type="s3_compatible",
                access_key="key",
                secret_key="secret",
                vault_name="vault",
                endpoint="ep:8080",
            )

        entry = json.loads(log_file.read_text().strip())
        assert entry["tool"] == "add_remote_storage"
        assert entry["params"] == {"storage_type": "s3_compatible"}
        assert entry["outcome"] == "ok"

    @pytest.mark.asyncio
    async def test_azure_storage_builds_azure_add_request(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP
    ) -> None:
        from synology_apm.sdk import RemoteStorageAddResult

        storage = make_remote_storage(storage_id="stor-new")
        mock_apm.remote_storages.add.return_value = RemoteStorageAddResult(storage=storage, encryption_key=None)

        await call_tool(
            admin_server, "add_remote_storage", mock_ctx,
            storage_type="azure_blob",
            vault_name="my-container",
            tenant_id="tenant-1", client_id="client-1", azure_secret="secret-1", account_name="acct-1",
        )

        (request,), _ = mock_apm.remote_storages.add.call_args
        assert isinstance(request, AzureBlobStorageAddRequest)
        assert request.tenant_id == "tenant-1"
        assert request.client_id == "client-1"
        assert request.secret == "secret-1"
        assert request.account_name == "acct-1"
        assert request.vault_name == "my-container"


class TestUpdateRemoteStorage:
    @pytest.mark.asyncio
    async def test_resolves_and_updates(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        storage = make_remote_storage()
        updated = make_remote_storage(endpoint="new-endpoint:9000")
        mock_apm.remote_storages.get.return_value = storage
        mock_apm.remote_storages.update.return_value = updated

        result = await call_tool(
            admin_server, "update_remote_storage", mock_ctx,
            storage_id="stor-001", access_key="key", secret_key="secret",
            endpoint="new-endpoint:9000", trust_self_signed=False,
        )

        assert result["name"] == "DSM-Storage"
        mock_apm.remote_storages.get.assert_called_once_with("stor-001")

    @pytest.mark.asyncio
    async def test_audit_log_records_storage_id(self, mock_apm: MagicMock, mock_ctx: MagicMock, tmp_path: Path) -> None:
        import os
        from unittest.mock import patch

        from synology_apm.mcp._server import create_server

        storage = make_remote_storage()
        mock_apm.remote_storages.get.return_value = storage
        mock_apm.remote_storages.update.return_value = storage

        server = create_server(mode="admin")

        log_file = tmp_path / "audit.jsonl"
        with patch.dict(os.environ, {"APM_MCP_AUDIT_LOG": str(log_file)}):
            await call_tool(
                server, "update_remote_storage", mock_ctx,
                storage_id="stor-001", access_key="key", secret_key="secret",
                endpoint="new-endpoint:9000", trust_self_signed=False,
            )

        entry = json.loads(log_file.read_text().strip())
        assert entry["tool"] == "update_remote_storage"
        assert entry["params"] == {"storage_id": "stor-001"}
        assert entry["outcome"] == "ok"

    @pytest.mark.asyncio
    async def test_azure_storage_builds_azure_update_request(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP
    ) -> None:
        from synology_apm.sdk import AzureBlobStorageUpdateRequest, RemoteStorageType

        storage = make_remote_storage(storage_type=RemoteStorageType.AZURE_BLOB)
        mock_apm.remote_storages.get.return_value = storage
        mock_apm.remote_storages.update.return_value = storage

        await call_tool(
            admin_server, "update_remote_storage", mock_ctx,
            storage_id="stor-001", tenant_id="tenant-1", client_id="client-1", azure_secret="secret-1",
        )

        (_, request), _ = mock_apm.remote_storages.update.call_args
        assert isinstance(request, AzureBlobStorageUpdateRequest)
        assert request.tenant_id == "tenant-1"
        assert request.client_id == "client-1"
        assert request.secret == "secret-1"

    @pytest.mark.asyncio
    async def test_non_azure_missing_credentials_raises(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP
    ) -> None:
        """Omitting access_key/secret_key for a non-Azure storage must fail loudly rather
        than silently wiping its live credentials with empty strings."""
        storage = make_remote_storage()
        mock_apm.remote_storages.get.return_value = storage

        with pytest.raises(ToolError) as exc_info:
            await call_tool(admin_server, "update_remote_storage", mock_ctx, storage_id="stor-001")

        parsed = json.loads(str(exc_info.value))
        assert parsed["error"] == "invalid_argument"
        mock_apm.remote_storages.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_azure_storage_missing_fields_raises(
        self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP
    ) -> None:
        from synology_apm.sdk import RemoteStorageType

        storage = make_remote_storage(storage_type=RemoteStorageType.AZURE_BLOB)
        mock_apm.remote_storages.get.return_value = storage

        with pytest.raises(ToolError) as exc_info:
            await call_tool(
                admin_server, "update_remote_storage", mock_ctx,
                storage_id="stor-001", tenant_id="tenant-1",
            )

        parsed = json.loads(str(exc_info.value))
        assert parsed["error"] == "invalid_argument"
        mock_apm.remote_storages.update.assert_not_called()


class TestDeleteRemoteStorage:
    @pytest.mark.asyncio
    async def test_preview_then_execute(self, mock_apm: MagicMock, mock_ctx: MagicMock, admin_server: FastMCP) -> None:
        storage = make_remote_storage()
        mock_apm.remote_storages.get.return_value = storage
        mock_apm.remote_storages.delete.return_value = None

        await assert_destructive_preview_then_execute(
            admin_server,
            mock_ctx,
            "delete_remote_storage",
            {"storage_id": "stor-001"},
            mock_apm.remote_storages.delete,
            expected_target={"name": storage.name, "storage_id": storage.storage_id, "storage_type": storage.storage_type.value},
        )

        mock_apm.remote_storages.delete.assert_called_once_with(storage)
