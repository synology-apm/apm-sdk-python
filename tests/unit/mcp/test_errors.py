"""Tests for _errors.py: sdk_error_to_dict error-code mapping.

Field correctness (what each exception carries) is the SDK's responsibility and is
covered by tests/unit/sdk/test_exceptions.py — these tests only verify that
sdk_error_to_dict picks the right "error" code per exception type and flattens
exc.to_dict() into the result unchanged.
"""
from __future__ import annotations

import pytest


def _convert(exc: Exception) -> dict:
    from synology_apm.mcp._errors import sdk_error_to_dict
    return sdk_error_to_dict(exc)


class TestSdkErrorToDictCodes:
    @pytest.mark.parametrize(
        "make_exc,expected_code",
        [
            (lambda: _sdk("InvalidOperationError")("cannot do this", "workload", "wl-001"), "invalid_operation"),
            (lambda: _sdk("DuplicateWorkloadError")("already exists", "workload", "wl-dup"), "duplicate_workload"),
            (lambda: _sdk("PlanNameConflictError")("name taken", "plan", "plan-001"), "plan_name_conflict"),
            (lambda: _sdk("PlanInUseError")("plan in use", "plan", "plan-002"), "plan_in_use"),
            (lambda: _sdk("ResourceNotFoundError")("not found", "workload", "wl-001"), "not_found"),
            (lambda: _sdk("RemoteStorageConflictError")("conflict", "storage", "stor-001"), "remote_storage_conflict"),
            (
                lambda: _sdk("RemoteStorageEncryptionMismatchError")("mismatch", "storage", "stor-002"),
                "remote_storage_encryption_mismatch",
            ),
            (lambda: _sdk("RemoteStorageInUseError")("in use", "storage", "stor-003"), "remote_storage_in_use"),
            (lambda: _sdk("ResourceNotReadyError")("not ready yet"), "resource_not_ready"),
            (lambda: _sdk("AuthenticationError")("bad credentials"), "authentication_error"),
            (lambda: _sdk("PermissionDeniedError")("no permission"), "permission_denied"),
            (lambda: _sdk("NotSupportedError")("not supported"), "not_supported"),
            (lambda: _sdk("NotManagementServerError")("not the management server"), "not_management_server"),
            (
                lambda: _sdk("BackupServerDisconnectedError")("backup server disconnected"),
                "backup_server_disconnected",
            ),
            (lambda: _sdk("ConnectionTimeoutError")("timed out"), "connection_timeout"),
            (lambda: _sdk("APMError")("something went wrong"), "apm_error"),
        ],
    )
    def test_error_code(self, make_exc, expected_code):
        exc = make_exc()
        result = _convert(exc)
        assert result["error"] == expected_code
        assert result["message"] == exc.message

    def test_remote_storage_unmanaged_catalog_error_flattens_to_dict(self):
        from synology_apm.sdk import RemoteStorageUnmanagedCatalogError
        exc = RemoteStorageUnmanagedCatalogError("unmanaged", vault_name="MyVault", catalog_count=3)
        result = _convert(exc)
        assert result == {"error": "remote_storage_unmanaged_catalog", **exc.to_dict()}

    def test_resource_error_flattens_to_dict(self):
        from synology_apm.sdk import ResourceNotFoundError
        exc = ResourceNotFoundError("not found", "workload", "wl-001")
        result = _convert(exc)
        assert result == {"error": "not_found", **exc.to_dict()}

    def test_plan_in_use_error_flattens_extra_fields(self):
        from synology_apm.sdk import PlanInUseError
        exc = PlanInUseError(
            "plan in use", "plan", "plan-002",
            has_workloads=True, has_server_template=False, has_backup_servers=True,
        )
        result = _convert(exc)
        assert result == {"error": "plan_in_use", **exc.to_dict()}
        assert result["has_workloads"] is True

    def test_value_error_maps_to_invalid_argument(self):
        exc = ValueError("parameter 'host_ip' is required")
        result = _convert(exc)
        assert result["error"] == "invalid_argument"
        assert "host_ip" in result["message"]

    def test_unexpected_exception(self):
        exc = RuntimeError("something completely unexpected")
        result = _convert(exc)
        assert result["error"] == "unexpected_error"
        assert "completely unexpected" in result["message"]

    @pytest.mark.parametrize(
        "make_exc,expected_code",
        [
            (lambda: _sdk("AuthenticationError")("bad credentials"), "authentication_error"),
            (lambda: _sdk("NotManagementServerError")("not the management server"), "not_management_server"),
            (lambda: _sdk("ConnectionTimeoutError")("timed out"), "connection_timeout"),
        ],
    )
    def test_hint_present_for_reconfigure_codes(self, make_exc, expected_code):
        exc = make_exc()
        result = _convert(exc)
        assert result["error"] == expected_code
        assert "synology-apm-cli config set" in result["hint"]

    def test_hint_absent_for_non_reconfigure_codes(self):
        from synology_apm.sdk import ResourceNotFoundError
        exc = ResourceNotFoundError("not found", "workload", "wl-001")
        result = _convert(exc)
        assert "hint" not in result

    @pytest.mark.parametrize(
        "message,expected_code",
        [
            ("SSL certificate verification failed for apm.corp.com", "ssl_error"),
            ("Cannot connect to apm.corp.com: Connection refused", "connection_error"),
        ],
        ids=["ssl_certificate_failure", "cannot_connect"],
    )
    def test_message_pattern_gets_specific_error_code_and_hint(self, message, expected_code):
        from synology_apm.sdk import APIError
        exc = APIError(message)
        result = _convert(exc)
        assert result["error"] == expected_code
        assert "synology-apm-cli config set" in result["hint"]

    def test_unrelated_api_error_falls_back_to_apm_error_without_hint(self):
        from synology_apm.sdk import APIError
        exc = APIError("Backup rejected by target")
        result = _convert(exc)
        assert result["error"] == "apm_error"
        assert "hint" not in result

    def test_message_excludes_raw_response_body(self):
        """APMError.__str__ appends the raw response body when set; the "message" field
        must use exc.message (the sanitized description) instead of str(exc), or raw API
        response data would leak into the MCP tool's JSON output.
        """
        from synology_apm.sdk import ResourceNotFoundError

        exc = ResourceNotFoundError(
            "workload not found", "workload", "wl-001",
            response_body={"errorCode": 1002, "internalField": "should-not-leak"},
        )
        result = _convert(exc)
        assert result["message"] == "workload not found"
        assert "Response body" not in result["message"]
        assert "internalField" not in result["message"]


def _sdk(name: str):
    import synology_apm.sdk as sdk_module
    return getattr(sdk_module, name)
