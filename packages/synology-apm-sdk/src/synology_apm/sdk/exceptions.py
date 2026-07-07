"""APM SDK custom exception hierarchy."""
from __future__ import annotations

import json
from typing import Any


class APMError(Exception):
    """Base class for all APM SDK exceptions.

    Attributes:
        message: Human-readable error description.
        error_code: Synology WebAPI or APM REST API error code (if any).
        response_body: Full JSON response body (for debugging or reporting).
    """

    def __init__(
        self,
        message: str,
        error_code: int | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.response_body = response_body

    def __str__(self) -> str:
        if not self.response_body:
            return self.message
        try:
            body_str = json.dumps(self.response_body, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            body_str = repr(self.response_body)
        return f"{self.message}\nResponse body:\n{body_str}"

    def __repr__(self) -> str:
        parts = [f"message={self.message!r}", f"error_code={self.error_code!r}"]
        if self.response_body is not None:
            parts.append(f"response_body={self.response_body!r}")
        return f"{self.__class__.__name__}({', '.join(parts)})"


class AuthenticationError(APMError):
    """Login failed or session has expired.

    Common causes:
    - Incorrect username or password
    - Session expired
    - Account is locked
    """


class _ResourceError(APMError):
    """Base for errors that reference a specific resource by type and id.

    Attributes:
        resource_type: Resource type name, e.g. "Workload", "ProtectionPlan".
        resource_id:   The ID or name used in the lookup.
    """

    def __init__(
        self,
        message: str,
        resource_type: str,
        resource_id: str,
        error_code: int | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(message, error_code, response_body)
        self.resource_type = resource_type
        self.resource_id = resource_id


class ResourceNotFoundError(_ResourceError):
    """The requested resource does not exist.

    Attributes:
        resource_type: Resource type name, e.g. "Workload", "ProtectionPlan".
        resource_id:   The ID or name used in the lookup.
    """


class PermissionDeniedError(APMError):
    """The user lacks sufficient permission for this operation."""


class NotSupportedError(APMError):
    """Feature not supported by this APM version."""


class InvalidOperationError(_ResourceError):
    """The operation is not valid for the resource's current state.

    Attributes:
        resource_type: Resource type name, e.g. "Workload".
        resource_id:   The ID of the resource.
    """


class ConnectionTimeoutError(APMError):
    """Raised when APM did not respond within the configured timeout.

    Distinct from a connection failure (unreachable host / refused connection):
    the request was dispatched but no complete response arrived before the timeout expired.
    """


class BackupServerDisconnectedError(APMError):
    """The operation failed because the designated backup server is disconnected."""


class NotManagementServerError(APMError):
    """The host is not running APM or is not the primary management server."""


class ResourceNotReadyError(APMError):
    """The resource exists but is not yet in a state where the operation can be performed.

    Raised when an operation requires the resource to be ready, such as calling
    get_download_url_by_ready_result() on a result whose ready_to_download is False.
    """


class PlanNameConflictError(_ResourceError):
    """A plan with this name already exists.

    Raised by create() and update() when the plan name is already taken.

    Attributes:
        resource_type: Plan type — "ProtectionPlan", "RetirementPlan", or "TieringPlan".
        resource_id:   The conflicting plan name.
    """


class PlanInUseError(_ResourceError):
    """Cannot delete the plan because it is still assigned to workloads or backup servers.

    Attributes:
        resource_type:       Plan type — "ProtectionPlan", "RetirementPlan", or "TieringPlan".
        resource_id:         The plan UUID.
        has_workloads:       Workloads are assigned to this plan.
        has_server_template: The plan is the default template for a backup server
                             (protection plans only).
        has_backup_servers:  Backup servers are assigned to this tiering plan
                             (tiering plans only).
    """

    def __init__(
        self,
        message: str,
        resource_type: str,
        resource_id: str,
        *,
        has_workloads: bool = False,
        has_server_template: bool = False,
        has_backup_servers: bool = False,
        error_code: int | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(message, resource_type, resource_id, error_code, response_body)
        self.has_workloads = has_workloads
        self.has_server_template = has_server_template
        self.has_backup_servers = has_backup_servers


class DuplicateWorkloadError(_ResourceError):
    """A workload with the same identity already exists.

    Raised by add_file_server() and update_file_server() when a file server at the
    given IP address is already registered with the same plan on the same backup server.

    Attributes:
        resource_type: Always "file_server".
        resource_id:   The conflicting file server IP address.
    """


class RemoteStorageConflictError(_ResourceError):
    """A remote storage with this vault is already registered.

    Raised by add() when the vault is already registered with this APM instance.

    Attributes:
        resource_type: Always "RemoteStorage".
        resource_id:   The vault name that caused the conflict.
    """


class RemoteStorageInUseError(_ResourceError):
    """Cannot delete the storage because it is referenced by active plans.

    Raised by delete() when the storage is still assigned to protection or tiering plans.

    Attributes:
        resource_type: Always "RemoteStorage".
        resource_id:   The storage UUID.
    """


class RemoteStorageEncryptionMismatchError(_ResourceError):
    """The vault was originally registered with encryption; relink_encryption_key is required.

    Raised by add() when the vault's encryption mode does not match the current request —
    the vault was previously set up with client-side encryption, but the request does not
    include the encryption key from the original registration.

    Attributes:
        resource_type: Always "RemoteStorage".
        resource_id:   The vault name.
    """


class RemoteStorageUnmanagedCatalogError(APMError):
    """The vault contains pre-existing backup catalogs not linked to any plan.

    Raised by add() when pre-existing catalogs are detected and no retirement plan was
    provided in the request. Provide unmanaged_retirement_plan in the add request to
    relink those catalogs to a retirement plan.

    Attributes:
        vault_name:    Vault or bucket name where unmanaged catalogs were found.
        catalog_count: Number of unmanaged catalog entries detected.
    """

    def __init__(self, message: str, *, vault_name: str, catalog_count: int) -> None:
        super().__init__(message)
        self.vault_name = vault_name
        self.catalog_count = catalog_count


class APIError(APMError):
    """APM REST API returned an error not covered by a more specific exception class.

    Report the error_code to the SDK maintainers for finer-grained handling.
    """
