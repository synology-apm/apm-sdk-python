"""Shared smoke test helpers used by both machine and M365 phase files."""
from __future__ import annotations

from typing import Any, Protocol

from synology_apm.sdk import WorkloadVersion

ZERO_UUID = "00000000-0000-0000-0000-000000000000"
SENTINEL_NAME = "no-match-smoke-sentinel"


class _WorkloadCol(Protocol):
    async def lock_version(self, version: WorkloadVersion) -> None: ...
    async def unlock_version(self, version: WorkloadVersion) -> None: ...
    async def get_version(self, workload: Any, version_id: str) -> WorkloadVersion: ...
    async def backup_now(self, workload: Any) -> None: ...
    async def cancel_backup(self, workload: Any) -> None: ...


async def lock_unlock_roundtrip(
    col: _WorkloadCol, w0: Any, v0: WorkloadVersion
) -> tuple[WorkloadVersion, WorkloadVersion, bool, bool]:
    """Toggle v0's lock state and back; returns (after_first, after_second, first_expected, second_expected)."""
    was_locked = v0.locked
    if was_locked:
        await col.unlock_version(v0)
        after_first = await col.get_version(w0, v0.version_id)
        await col.lock_version(v0)
        after_second = await col.get_version(w0, v0.version_id)
        return after_first, after_second, False, True
    await col.lock_version(v0)
    after_first = await col.get_version(w0, v0.version_id)
    await col.unlock_version(v0)
    after_second = await col.get_version(w0, v0.version_id)
    return after_first, after_second, True, False


async def backup_cancel_roundtrip(col: _WorkloadCol, w0: Any) -> None:
    await col.backup_now(w0)
    await col.cancel_backup(w0)
