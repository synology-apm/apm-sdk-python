"""APMClient — main entry point for the APM Python SDK."""
from __future__ import annotations

from collections.abc import Callable
from types import TracebackType

from ._http import WebAPISession
from .collections.activities import ActivityCollection
from .collections.backup_servers import BackupServerCollection
from .collections.hypervisors import HypervisorCollection
from .collections.logs import LogCollection
from .collections.m365 import M365Collection
from .collections.machine import MachineCollection
from .collections.protection_plans import ProtectionPlanCollection
from .collections.remote_storages import RemoteStorageCollection
from .collections.retirement_plans import RetirementPlanCollection
from .collections.saas import SaasCollection
from .collections.system import SystemCollection
from .collections.tiering_plans import TieringPlanCollection
from .enums import BackupServerRole
from .exceptions import AuthenticationError, NotManagementServerError, ResourceNotFoundError
from .models.backup_server import BackupServer as _BackupServer
from .models.system import SiteInfo


class APMClient:
    """Main entry point for the APM Python SDK — manages connections and authentication.

    Use the `async with` syntax to ensure the connection is properly closed and avoid session leaks.

    Args:
        host: APM hostname or IP, supports host:port, e.g. "apm.corp.com" or "apm.corp.com:10443".
              APM requires HTTPS; the SDK prepends the scheme automatically.
        username: Login account.
        password: Login password.
        verify_ssl: Whether to verify the SSL certificate. Defaults to True.
            Set to False for self-signed certificates in test environments.
        timeout: Per-request timeout in seconds. Defaults to 300.
        debug: When True, print every request and response to stderr. Defaults to False.

    Examples:
        >>> async with APMClient("apm.corp.com", "admin", "pass") as apm:
        ...     workloads, total = await apm.machine.workloads.list()
        ...     print(f"{total} machine workloads found")
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        verify_ssl: bool = True,
        timeout: float = 300.0,
        debug: bool = False,
    ) -> None:
        self._session = WebAPISession(
            host, username, password,
            verify_ssl=verify_ssl,
            timeout=timeout,
            debug=debug,
        )
        self._my_server: _BackupServer | None = None
        self._machine = MachineCollection(self._session)
        self._m365 = M365Collection(self._session)
        self._saas = SaasCollection(self._session)
        self._activities = ActivityCollection(self._session)
        self._backup_servers = BackupServerCollection(self._session)
        self._plans = ProtectionPlanCollection(self._session)
        self._retirement_plans = RetirementPlanCollection(self._session)
        self._tiering_plans = TieringPlanCollection(self._session)
        self._remote_storages = RemoteStorageCollection(self._session)
        self._hypervisors = HypervisorCollection(self._session)
        self._logs = LogCollection(self._session)
        self._system = SystemCollection(self._session, self._backup_servers)

    # ── Context manager ────────────────────────────────────────────────────

    async def __aenter__(self) -> APMClient:
        """Enter the async context manager; establish the connection and obtain a session."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the async context manager; clean up the session."""
        await self.disconnect()

    # ── Connection ─────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Establish the connection and initialize the session.

        Called automatically when using `async with`; no need to call manually.
        After login, verifies the host is the primary management server.

        Raises:
            AuthenticationError: Authentication failed.
            APIError: Cannot connect to the APM server.
            NotManagementServerError: Host is not an APM server, or is not the primary
                management server.
        """
        await self._session.connect()
        try:
            my_server = await self._backup_servers._get_me()
        except ResourceNotFoundError as exc:
            await self._session.disconnect()
            raise NotManagementServerError(
                "Host is not an APM server."
            ) from exc
        except BaseException:
            await self._session.disconnect()
            raise
        if my_server.role != BackupServerRole.PRIMARY:
            await self._session.disconnect()
            raise NotManagementServerError(
                f"'{my_server.name}' is not the primary management server."
            )
        self._my_server = my_server

    async def disconnect(self) -> None:
        """Log out and clean up the session. Safe to call multiple times (idempotent)."""
        await self._session.disconnect()

    @property
    def my_server(self) -> _BackupServer:
        """The primary management server that this client is connected to.

        Populated by connect(); always set after a successful connection.

        Raises:
            AuthenticationError: Not yet connected (connect() has not been called).
        """
        if self._my_server is None:
            raise AuthenticationError("Not connected. Call connect() first.")
        return self._my_server

    # ── Collection properties ──────────────────────────────────────────────

    @property
    def machine(self) -> MachineCollection:
        """Access MachineCollection, which manages Machine domain backup resources (workload + plan).

        apm.machine.workloads → MachineWorkloadCollection
        apm.machine.plans     → MachinePlanCollection
        """
        return self._machine

    @property
    def m365(self) -> M365Collection:
        """Access M365Collection, which manages M365 SaaS backup resources (workload + plan).

        apm.m365.workloads → M365WorkloadCollection
        apm.m365.plans     → M365PlanCollection
        """
        return self._m365

    @property
    def saas(self) -> SaasCollection:
        """Access SaasCollection, which lists all connected SaaS tenants (M365 + GWS).

        apm.saas.list() → list[SaasTenant]
        """
        return self._saas

    @property
    def activities(self) -> ActivityCollection:
        """Access the global ActivityCollection (site-wide activity records)."""
        return self._activities

    @property
    def backup_servers(self) -> BackupServerCollection:
        """Access BackupServerCollection, which manages backup servers in the cluster."""
        return self._backup_servers

    @property
    def retirement_plans(self) -> RetirementPlanCollection:
        """Access RetirementPlanCollection, which manages retirement plans.

        apm.retirement_plans.list()           → list[RetirementPlan]
        apm.retirement_plans.get(id)          → RetirementPlan
        apm.retirement_plans.get_by_name(name) → RetirementPlan
        """
        return self._retirement_plans

    @property
    def remote_storages(self) -> RemoteStorageCollection:
        """Access RemoteStorageCollection, which manages remote storage devices (External Vaults).

        apm.remote_storages.list() → list[RemoteStorage]
        apm.remote_storages.get(id) → RemoteStorage
        """
        return self._remote_storages

    @property
    def hypervisors(self) -> HypervisorCollection:
        """Access HypervisorCollection, which manages hypervisor inventory servers.

        apm.hypervisors.list() → list[Hypervisor]
        apm.hypervisors.get(id) → Hypervisor
        """
        return self._hypervisors

    @property
    def logs(self) -> LogCollection:
        """Access LogCollection, which queries server-scoped logs.

        All methods require a BackupServer to route to the target backup server.
        Obtain one via ``apm.backup_servers.get(id)`` or ``apm.backup_servers.get_by_name(name)``.

        apm.logs.list_activity(server) → list[APMActivityLog]
        apm.logs.list_drive(server)    → list[DriveLog]
        apm.logs.list_connection(server) → list[ConnectionLog]
        apm.logs.list_system(server)   → list[SystemLog]
        """
        return self._logs

    @property
    def tiering_plans(self) -> TieringPlanCollection:
        """Access TieringPlanCollection, which manages tiering plans.

        apm.tiering_plans.list()           → list[TieringPlan]
        apm.tiering_plans.get(id)          → TieringPlan
        apm.tiering_plans.get_by_name(name) → TieringPlan
        """
        return self._tiering_plans

    @property
    def plans(self) -> ProtectionPlanCollection:
        """Access ProtectionPlanCollection for cross-category plan queries.

        For category-specific CRUD (create / update / delete / apply), use
        APMClient.machine.plans or APMClient.m365.plans instead.

        apm.plans.list()             → list all plans
        apm.plans.list(category=...) → filter by WorkloadCategory
        apm.plans.get(id)            → direct UUID lookup (category-agnostic)
        apm.plans.get_by_name(name)  → exact name match (cross-category search)
        """
        return self._plans

    # ── File download ──────────────────────────────────────────────────────

    async def download_file(
        self,
        url: str,
        dest_path: str,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> None:
        """Download a file from an APM-issued URL using the current session credentials.

        Streams the file in 64 KB chunks so large PST files do not exhaust memory.
        The session cookie and SSL settings (verify_ssl) are applied automatically.
        A failed download never modifies an existing file at dest_path.

        Args:
            url:         Full HTTPS URL as returned by exchange_export.get_download_url_by_*().
            dest_path:   Local filesystem path to write the file to.
            on_progress: Optional callback invoked after each chunk.
                         Signature: on_progress(bytes_downloaded, total_bytes_or_none).

        Raises:
            AuthenticationError: Session is not connected.
            APIError: Server returned an error status or the connection failed.
        """
        await self._session.download_file(url, dest_path, on_progress=on_progress)

    # ── System ─────────────────────────────────────────────────────────────

    async def get_site_info(self) -> SiteInfo:
        """Fetch complete APM site information.

        Makes concurrent calls to retrieve license info, cluster info, storage statistics,
        and workload statistics; also scans all backup servers to locate the Primary and
        Secondary Management Servers.

        Returns:
            SiteInfo object containing site_uuid, external_address, port,
            primary_management_server (BackupServer or None), secondary_management_server (BackupServer
            or None), site_storage (SiteStorageStats), and workload_usage (WorkloadUsageSummary).

        Raises:
            AuthenticationError: Session has expired.
            PermissionDeniedError: Insufficient system administration permissions.
        """
        return await self._system.get_site_info()
