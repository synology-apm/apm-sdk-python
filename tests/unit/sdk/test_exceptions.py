"""Unit tests for synology_apm.sdk.exceptions — APMError.to_dict() and subclasses."""
from __future__ import annotations

import itertools

from synology_apm.sdk.exceptions import (
    ERROR_CODES,
    APIError,
    APMError,
    InvalidOperationError,
    PlanInUseError,
    RemoteStorageUnmanagedCatalogError,
    ResourceNotFoundError,
    classify_error,
)


def test_apm_error_to_dict() -> None:
    exc = APMError("something went wrong", error_code=500)
    assert exc.to_dict() == {"message": "something went wrong"}


def test_resource_error_subclass_inherits_to_dict_unchanged() -> None:
    """A _ResourceError subclass that adds no fields needs no to_dict() override."""
    for exc in (
        ResourceNotFoundError("not found", resource_type="Workload", resource_id="wl-123"),
        InvalidOperationError("bad state", resource_type="Workload", resource_id="wl-123"),
    ):
        assert exc.to_dict() == {
            "message": exc.message,
            "resource_type": "Workload",
            "resource_id": "wl-123",
        }


def test_plan_in_use_error_to_dict() -> None:
    exc = PlanInUseError(
        "plan in use",
        resource_type="ProtectionPlan",
        resource_id="plan-123e4567",
        has_workloads=True,
        has_server_template=False,
        has_backup_servers=True,
    )
    assert exc.to_dict() == {
        "message": "plan in use",
        "resource_type": "ProtectionPlan",
        "resource_id": "plan-123e4567",
        "has_workloads": True,
        "has_server_template": False,
        "has_backup_servers": True,
    }


def test_remote_storage_unmanaged_catalog_error_to_dict() -> None:
    exc = RemoteStorageUnmanagedCatalogError("unmanaged catalogs found", vault_name="MyVault", catalog_count=3)
    assert exc.to_dict() == {
        "message": "unmanaged catalogs found",
        "vault_name": "MyVault",
        "catalog_count": 3,
    }


def _leaf_subclasses(cls: type[APMError]) -> set[type[APMError]]:
    """Recursively collect concrete (leaf) subclasses of cls — classes with no
    subclasses of their own. Intermediate bases like ``_ResourceError`` are excluded
    structurally (they still have subclasses), without needing to name them."""
    subclasses = cls.__subclasses__()
    if not subclasses:
        return {cls}
    leaves: set[type[APMError]] = set()
    for sub in subclasses:
        leaves |= _leaf_subclasses(sub)
    return leaves


def test_error_codes_covers_every_individually_classified_exception() -> None:
    """ERROR_CODES is the single source of truth both synology-apm-mcp and
    synology-apm-cli dispatch off of — a new APMError subclass that should get its
    own classification (as opposed to falling into the generic APIError/bare-APMError
    fallback both consumers already handle separately) must be added here, or this
    test fails as a reminder.

    Computed from the actual class hierarchy (via __subclasses__()) rather than a
    hand-typed literal, so adding a new leaf subclass and forgetting to add it to
    ERROR_CODES can no longer stay green by also forgetting a parallel entry here.
    APIError is the only leaf excluded (both consumers have their own generic
    fallback for it — see ERROR_CODES's docstring); bare APMError and intermediate
    bases like _ResourceError are excluded automatically because they are not leaves.
    """
    expected = _leaf_subclasses(APMError) - {APIError}
    assert set(ERROR_CODES) == expected


def test_error_codes_keys_have_no_subclass_relationships() -> None:
    """classify_error() does an exact type() lookup rather than an isinstance walk,
    which is only correct as long as no two ERROR_CODES keys are related by
    subclassing. This enforces in code the invariant that classify_error()'s
    docstring currently only asserts in prose."""
    for a, b in itertools.combinations(ERROR_CODES, 2):
        assert not issubclass(a, b), f"{a.__name__} must not subclass {b.__name__}"
        assert not issubclass(b, a), f"{b.__name__} must not subclass {a.__name__}"


def test_classify_error_returns_code_for_classified_exception() -> None:
    exc = ResourceNotFoundError("not found", resource_type="Workload", resource_id="wl-123")
    assert classify_error(exc) == "not_found"


def test_classify_error_returns_none_for_unclassified_exception() -> None:
    assert classify_error(APIError("unexpected")) is None
    assert classify_error(APMError("generic")) is None
