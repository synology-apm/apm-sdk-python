"""Tests for tools/plans/_builders.py."""
from __future__ import annotations

import json

import pytest

from tests.unit.mcp.conftest import make_backup_server, make_remote_storage


class TestScheduleFrequencyChoices:
    def test_after_backup_not_a_valid_main_schedule_frequency(self):
        """AFTER_BACKUP is only valid for Backup Copy schedules, not main plan schedules
        (see ProtectionSchedule._validate_plan_schedule_and_retention in the SDK); it must
        not be offered as a schedule_frequency choice on these tools.
        """
        import typing

        from synology_apm.mcp.tools.plans._builders_common import _FREQUENCY

        assert "after_backup" not in typing.get_args(_FREQUENCY)


class TestBuildHelpers:
    def test_parse_time_hhmm(self):
        from synology_apm.mcp.tools.plans._builders_common import _parse_time
        t = _parse_time("02:30")
        assert t.hour == 2
        assert t.minute == 30

    def test_parse_time_none(self):
        from synology_apm.mcp.tools.plans._builders_common import _parse_time
        assert _parse_time(None) is None

    def test_parse_required_time_hhmm(self):
        from synology_apm.mcp.tools.plans._builders_common import _parse_required_time
        t = _parse_required_time("21:00")
        assert t.hour == 21
        assert t.minute == 0

    def test_parse_required_time_empty_string_raises(self):
        from synology_apm.mcp.tools.plans._builders_common import _parse_required_time
        with pytest.raises(ValueError, match="Unrecognized time: ''"):
            _parse_required_time("")

    def test_parse_weekdays(self):
        from synology_apm.mcp.tools.plans._builders_common import _parse_weekdays
        from synology_apm.sdk import WeekDay
        days = _parse_weekdays(["mon", "wed", "fri"])
        assert len(days) == 3
        assert WeekDay(1) in days  # mon
        assert WeekDay(3) in days  # wed
        assert WeekDay(5) in days  # fri

    def test_parse_weekdays_empty(self):
        from synology_apm.mcp.tools.plans._builders_common import _parse_weekdays
        assert _parse_weekdays(None) == ()

    def test_parse_weekdays_unrecognized_token_raises(self):
        from synology_apm.mcp.tools.plans._builders_common import _parse_weekdays
        with pytest.raises(ValueError, match="Unrecognized weekday: 'weekend'"):
            _parse_weekdays(["mon", "weekend"])

    def test_parse_weekdays_covers_all_sdk_weekdays(self):
        """WeekDayLiteral's 3-letter tokens must resolve to every WeekDay member -- this
        is the behavioral half of the _enums.py/SDK Enum parity check (see
        test_weekday_literal_has_seven_distinct_days in test_enums.py), since WeekDay's
        int values can't be compared to the Literal's strings directly."""
        from typing import get_args

        from synology_apm.mcp._enums import WeekDayLiteral
        from synology_apm.mcp.tools.plans._builders_common import _parse_weekdays
        from synology_apm.sdk import WeekDay

        days = _parse_weekdays(list(get_args(WeekDayLiteral)))
        assert set(days) == set(WeekDay)

    def test_build_retention_keep_days(self):
        from synology_apm.mcp.tools.plans._builders_common import _build_retention
        from synology_apm.sdk import RetentionType
        ret = _build_retention("keep_days", retention_days=30, retention_versions=None)
        assert ret.retention_type == RetentionType.KEEP_DAYS
        assert ret.days == 30
        assert ret.versions is None

    def test_build_retention_keep_versions(self):
        from synology_apm.mcp.tools.plans._builders_common import _build_retention
        from synology_apm.sdk import RetentionType
        ret = _build_retention("keep_versions", retention_days=None, retention_versions=10)
        assert ret.retention_type == RetentionType.KEEP_VERSIONS
        assert ret.versions == 10

    def test_build_schedule_daily(self):
        from synology_apm.mcp.tools.plans._builders_common import _build_schedule
        from synology_apm.sdk import ScheduleFrequency
        sched = _build_schedule("daily", schedule_time="03:00", weekdays=None)
        assert sched.frequency == ScheduleFrequency.DAILY
        assert sched.start_time is not None
        assert sched.start_time.hour == 3

    def test_parse_time_hour_only(self):
        from synology_apm.mcp.tools.plans._builders_common import _parse_time
        t = _parse_time("20")
        assert t is not None
        assert t.hour == 20
        assert t.minute == 0

    def test_parse_time_with_seconds_raises(self):
        from synology_apm.mcp.tools.plans._builders_common import _parse_time
        with pytest.raises(ValueError, match="Unrecognized time: '20:00:00'"):
            _parse_time("20:00:00")

    def test_build_schedule_weekly_with_weekdays(self):
        from synology_apm.mcp.tools.plans._builders_common import _build_schedule
        from synology_apm.sdk import ScheduleFrequency, WeekDay
        sched = _build_schedule("weekly", schedule_time=None, weekdays=["mon", "fri"])
        assert sched.frequency == ScheduleFrequency.WEEKLY
        assert WeekDay(1) in sched.weekdays
        assert WeekDay(5) in sched.weekdays

    def test_build_retention_keep_advanced_builds_gfs(self):
        from synology_apm.mcp.tools.plans._builders_common import _build_retention
        from synology_apm.sdk import RetentionType
        ret = _build_retention(
            "keep_advanced", retention_days=None, retention_versions=None,
            gfs_daily_versions=7, gfs_weekly_versions=4, gfs_monthly_versions=12, gfs_yearly_versions=5,
        )
        assert ret.retention_type == RetentionType.KEEP_ADVANCED
        assert ret.gfs.daily_versions == 7
        assert ret.gfs.weekly_versions == 4
        assert ret.gfs.monthly_versions == 12
        assert ret.gfs.yearly_versions == 5

    def test_build_retention_keep_advanced_missing_field_raises(self):
        from synology_apm.mcp.tools.plans._builders_common import _build_retention
        with pytest.raises(ValueError, match="keep_advanced requires"):
            _build_retention(
                "keep_advanced", retention_days=None, retention_versions=None,
                gfs_daily_versions=7, gfs_weekly_versions=None, gfs_monthly_versions=12, gfs_yearly_versions=5,
            )

    def test_build_retention_keep_days_ignores_gfs_params(self):
        from synology_apm.mcp.tools.plans._builders_common import _build_retention
        ret = _build_retention("keep_days", retention_days=30, retention_versions=None)
        assert ret.gfs is None

    def test_build_vm_config_all_none_returns_none(self):
        from synology_apm.mcp.tools.plans._builders_machine import _build_vm_config
        assert _build_vm_config(None, None, None, None, None) is None

    def test_build_vm_config_partial_fills_declared_defaults(self):
        from synology_apm.mcp.tools.plans._builders_machine import _build_vm_config
        cfg = _build_vm_config(None, True, None, None, None)
        assert cfg.enable_verification is True
        assert cfg.enable_app_aware_bkp is True  # dataclass default, untouched

    def test_build_pc_config_all_none_returns_none(self):
        from synology_apm.mcp.tools.plans._builders_machine import _build_pc_config
        assert _build_pc_config(None, None, None) is None

    def test_build_pc_config_partial(self):
        from synology_apm.mcp.tools.plans._builders_machine import _build_pc_config
        cfg = _build_pc_config(True, None, None)
        assert cfg.shutdown_after_backup is True
        assert cfg.wake_for_backup is False  # dataclass default

    def test_build_ps_config_all_none_returns_none(self):
        from synology_apm.mcp.tools.plans._builders_machine import _build_ps_config
        assert _build_ps_config(None, None, None, None, None, None) is None

    def test_build_ps_config_partial(self):
        from synology_apm.mcp.tools.plans._builders_machine import _build_ps_config
        cfg = _build_ps_config(False, True, 60, True, True, True)
        assert cfg.enable_app_aware_bkp is False
        assert cfg.enable_verification is True
        assert cfg.verification_video_duration_seconds == 60
        assert cfg.shutdown_after_backup is True
        assert cfg.wake_for_backup is True
        assert cfg.prevent_sleep_during_backup is True

    def test_build_db_config_all_none_returns_none(self):
        from synology_apm.mcp.tools.plans._builders_machine import _build_db_config
        assert _build_db_config(None, None, None) is None

    def test_build_db_config_converts_enums(self):
        from synology_apm.mcp.tools.plans._builders_machine import _build_db_config
        from synology_apm.sdk import DbActionOnError, MssqlLogSetting, OracleLogSetting
        cfg = _build_db_config("stop", "truncate", "delete")
        assert cfg.action_on_error == DbActionOnError.STOP
        assert cfg.mssql_log_setting == MssqlLogSetting.TRUNCATE
        assert cfg.oracle_log_setting == OracleLogSetting.DELETE

    def test_parse_backup_window_disabled_and_no_spec_returns_none(self):
        from synology_apm.mcp.tools.plans._builders_machine import _parse_backup_window
        assert _parse_backup_window(False, None) is None

    def test_parse_backup_window_parses_ranges(self):
        from synology_apm.mcp.tools.plans._builders_machine import _parse_backup_window
        from synology_apm.sdk import WeekDay
        bw = _parse_backup_window(True, "mon:0-8,13-18;tue:0-23")
        assert bw.enabled is True
        assert bw.allowed_hours[WeekDay.MONDAY] == frozenset(list(range(0, 9)) + list(range(13, 19)))
        assert bw.allowed_hours[WeekDay.TUESDAY] == frozenset(range(0, 24))

    def test_parse_backup_window_unrecognized_weekday_raises(self):
        from synology_apm.mcp.tools.plans._builders_machine import _parse_backup_window
        with pytest.raises(ValueError, match="Unrecognized weekday"):
            _parse_backup_window(True, "weekend:0-8")

    def test_parse_backup_window_missing_colon_raises(self):
        from synology_apm.mcp.tools.plans._builders_machine import _parse_backup_window
        with pytest.raises(ValueError, match="Unrecognized backup window entry"):
            _parse_backup_window(True, "mon-0-8")

    def test_parse_backup_window_single_hours_no_range(self):
        from synology_apm.mcp.tools.plans._builders_machine import _parse_backup_window
        from synology_apm.sdk import WeekDay
        bw = _parse_backup_window(True, "wed:9,12,15")
        assert bw.allowed_hours[WeekDay.WEDNESDAY] == frozenset({9, 12, 15})

    def test_parse_tasks_json_none_returns_none(self):
        from synology_apm.mcp.tools.plans._builders_machine import _parse_tasks_json
        assert _parse_tasks_json(None) is None

    def test_parse_tasks_json_invalid_json_raises(self):
        from synology_apm.mcp.tools.plans._builders_machine import _parse_tasks_json
        with pytest.raises(ValueError, match="not valid JSON"):
            _parse_tasks_json("not json")

    def test_parse_tasks_json_not_a_list_raises(self):
        from synology_apm.mcp.tools.plans._builders_machine import _parse_tasks_json
        with pytest.raises(ValueError, match="JSON array"):
            _parse_tasks_json(json.dumps({"workload_type": "pc"}))

    def test_parse_tasks_json_entry_not_a_dict_raises(self):
        from synology_apm.mcp.tools.plans._builders_machine import _parse_tasks_json
        with pytest.raises(ValueError, match="must be a JSON object"):
            _parse_tasks_json(json.dumps(["not-a-dict"]))

    def test_parse_tasks_json_with_schedule_and_event_trigger(self):
        from synology_apm.mcp.tools.plans._builders_machine import _parse_tasks_json
        from synology_apm.sdk import MachineOsType, MachineWorkloadType, ScheduleFrequency

        raw = json.dumps([
            {
                "workload_type": "pc",
                "os_type": "windows",
                "use_main_schedule": False,
                "schedule": {
                    "time_schedule": {"frequency": "daily", "start_time": "03:00"},
                    "event_trigger": {"on_lock": True, "min_interval_seconds": 1800},
                },
            }
        ])
        tasks = _parse_tasks_json(raw)
        assert len(tasks) == 1
        task = tasks[0]
        assert task.workload_type == MachineWorkloadType.PC
        assert task.os_type == MachineOsType.WINDOWS
        assert task.use_main_schedule is False
        assert task.schedule.time_schedule.frequency == ScheduleFrequency.DAILY
        assert task.schedule.time_schedule.start_time.hour == 3
        assert task.schedule.event_trigger.on_lock is True
        assert task.schedule.event_trigger.min_interval.total_seconds() == 1800

    def test_parse_tasks_json_with_schedule_weekdays(self):
        """Regression test: tasks_json's nested time_schedule.weekdays is a JSON array of
        day tokens that must reach _parse_weekdays as a list, not be re-joined into a string."""
        from synology_apm.mcp.tools.plans._builders_machine import _parse_tasks_json
        from synology_apm.sdk import WeekDay

        raw = json.dumps([
            {
                "workload_type": "pc",
                "os_type": "windows",
                "schedule": {
                    "time_schedule": {"frequency": "weekly", "weekdays": ["mon", "wed"]},
                },
            }
        ])
        tasks = _parse_tasks_json(raw)
        assert WeekDay(1) in tasks[0].schedule.time_schedule.weekdays  # mon
        assert WeekDay(3) in tasks[0].schedule.time_schedule.weekdays  # wed


_MACHINE_PLAN_REQUEST_KWARGS: dict = dict(
    name="Daily Backup", retention_type="keep_days", retention_days=30, retention_versions=None,
    gfs_daily_versions=None, gfs_weekly_versions=None, gfs_monthly_versions=None, gfs_yearly_versions=None,
    schedule_frequency="daily", schedule_time="02:00", weekdays=None,
    description="", is_immutable=False, run_schedule_by_controller_time=False,
    vm_enable_app_aware_bkp=None, vm_enable_verification=None, vm_verification_video_duration_seconds=None,
    vm_enable_datastore_usage_detection=None, vm_datastore_min_free_space_percent=None,
    pc_shutdown_after_backup=None, pc_wake_for_backup=None, pc_prevent_sleep_during_backup=None,
    ps_enable_app_aware_bkp=None, ps_enable_verification=None, ps_verification_video_duration_seconds=None,
    ps_shutdown_after_backup=None, ps_wake_for_backup=None, ps_prevent_sleep_during_backup=None,
    db_action_on_error=None, db_mssql_log_setting=None, db_oracle_log_setting=None,
    backup_window_enabled=False, backup_window_allowed_hours=None, tasks_json=None,
    backup_copy_destination_type=None, backup_copy_destination_id=None,
    backup_copy_retention_type=None, backup_copy_retention_days=None, backup_copy_retention_versions=None,
    backup_copy_gfs_daily_versions=None, backup_copy_gfs_weekly_versions=None,
    backup_copy_gfs_monthly_versions=None, backup_copy_gfs_yearly_versions=None,
    backup_copy_schedule_frequency=None, backup_copy_schedule_time=None, backup_copy_weekdays=None,
)

_M365_PLAN_REQUEST_KWARGS: dict = dict(
    name="Daily Backup", retention_type="keep_days", retention_days=30, retention_versions=None,
    gfs_daily_versions=None, gfs_weekly_versions=None, gfs_monthly_versions=None, gfs_yearly_versions=None,
    schedule_frequency="daily", schedule_time="02:00", weekdays=None,
    description="", is_immutable=False, run_schedule_by_controller_time=False,
    backup_copy_destination_type=None, backup_copy_destination_id=None,
    backup_copy_retention_type=None, backup_copy_retention_days=None, backup_copy_retention_versions=None,
    backup_copy_gfs_daily_versions=None, backup_copy_gfs_weekly_versions=None,
    backup_copy_gfs_monthly_versions=None, backup_copy_gfs_yearly_versions=None,
    backup_copy_schedule_frequency=None, backup_copy_schedule_time=None, backup_copy_weekdays=None,
)


class TestBuildMachinePlanRequest:
    @pytest.mark.asyncio
    async def test_builds_request_with_retention_and_schedule(self, mock_apm):
        from synology_apm.mcp.tools.plans._builders_machine import _build_machine_plan_request
        from synology_apm.sdk import RetentionType, ScheduleFrequency

        request = await _build_machine_plan_request(mock_apm, **_MACHINE_PLAN_REQUEST_KWARGS)

        assert request.name == "Daily Backup"
        assert request.retention.retention_type == RetentionType.KEEP_DAYS
        assert request.retention.days == 30
        assert request.schedule.frequency == ScheduleFrequency.DAILY
        assert request.backup_copy is None
        assert request.vm_config is None
        assert request.tasks is None

    @pytest.mark.asyncio
    async def test_builds_request_with_device_configs_and_backup_copy(self, mock_apm):
        from synology_apm.mcp.tools.plans._builders_machine import _build_machine_plan_request

        server = make_backup_server(backup_server_id="srv-002")
        mock_apm.backup_servers.get.return_value = server

        kwargs = dict(_MACHINE_PLAN_REQUEST_KWARGS)
        kwargs.update(
            vm_enable_verification=True,
            backup_copy_destination_type="backup_server",
            backup_copy_destination_id="srv-002",
            backup_copy_retention_type="keep_days",
            backup_copy_retention_days=90,
            backup_copy_schedule_frequency="daily",
            backup_copy_schedule_time="23:00",
        )
        request = await _build_machine_plan_request(mock_apm, **kwargs)

        assert request.vm_config.enable_verification is True
        assert request.backup_copy.destination is server
        assert request.backup_copy.retention.days == 90


class TestBuildM365PlanRequest:
    @pytest.mark.asyncio
    async def test_builds_request_with_retention_and_schedule(self, mock_apm):
        from synology_apm.mcp.tools.plans._builders_common import _build_m365_plan_request
        from synology_apm.sdk import RetentionType, ScheduleFrequency

        request = await _build_m365_plan_request(mock_apm, **_M365_PLAN_REQUEST_KWARGS)

        assert request.name == "Daily Backup"
        assert request.retention.retention_type == RetentionType.KEEP_DAYS
        assert request.retention.days == 30
        assert request.schedule.frequency == ScheduleFrequency.DAILY
        assert request.backup_copy is None

    @pytest.mark.asyncio
    async def test_builds_request_with_backup_copy(self, mock_apm):
        from synology_apm.mcp.tools.plans._builders_common import _build_m365_plan_request

        storage = make_remote_storage(storage_id="stor-002")
        mock_apm.remote_storages.get.return_value = storage

        kwargs = dict(_M365_PLAN_REQUEST_KWARGS)
        kwargs.update(
            backup_copy_destination_type="remote_storage",
            backup_copy_destination_id="stor-002",
            backup_copy_retention_type="keep_versions",
            backup_copy_retention_versions=10,
            backup_copy_schedule_frequency="after_backup",
        )
        request = await _build_m365_plan_request(mock_apm, **kwargs)

        assert request.backup_copy.destination is storage
        assert request.backup_copy.retention.versions == 10


class TestCreateUpdatePlanParamsMatchBuilder:
    """Guard against drift between a create/update tool's locals()-forwarded
    parameter set and _build_*_plan_request's signature (tools/plans/machine.py
    and tools/plans/m365.py forward via locals() rather than a mypy-checked
    keyword list — see the comment above each build_kwargs assignment)."""

    @pytest.mark.asyncio
    async def test_machine_create_params_match_builder(self, admin_server):
        import inspect

        from synology_apm.mcp.tools.plans._builders_machine import _build_machine_plan_request

        tool = await admin_server.get_tool("create_machine_protection_plan")
        tool_params = set(inspect.signature(tool.fn).parameters) - {"ctx"}
        builder_params = set(inspect.signature(_build_machine_plan_request).parameters) - {"apm"}
        assert tool_params == builder_params

    @pytest.mark.asyncio
    async def test_machine_update_params_match_builder(self, admin_server):
        import inspect

        from synology_apm.mcp.tools.plans._builders_machine import _build_machine_plan_request

        tool = await admin_server.get_tool("update_machine_protection_plan")
        tool_params = set(inspect.signature(tool.fn).parameters) - {"ctx", "plan_id"}
        builder_params = set(inspect.signature(_build_machine_plan_request).parameters) - {"apm"}
        assert tool_params == builder_params

    @pytest.mark.asyncio
    async def test_m365_create_params_match_builder(self, admin_server):
        import inspect

        from synology_apm.mcp.tools.plans._builders_common import _build_m365_plan_request

        tool = await admin_server.get_tool("create_m365_protection_plan")
        tool_params = set(inspect.signature(tool.fn).parameters) - {"ctx"}
        builder_params = set(inspect.signature(_build_m365_plan_request).parameters) - {"apm"}
        assert tool_params == builder_params

    @pytest.mark.asyncio
    async def test_m365_update_params_match_builder(self, admin_server):
        import inspect

        from synology_apm.mcp.tools.plans._builders_common import _build_m365_plan_request

        tool = await admin_server.get_tool("update_m365_protection_plan")
        tool_params = set(inspect.signature(tool.fn).parameters) - {"ctx", "plan_id"}
        builder_params = set(inspect.signature(_build_m365_plan_request).parameters) - {"apm"}
        assert tool_params == builder_params


class TestBuildBackupCopy:
    @pytest.mark.asyncio
    async def test_returns_none_without_destination_id(self, mock_apm):
        from synology_apm.mcp.tools.plans._builders_common import _build_backup_copy

        result = await _build_backup_copy(
            mock_apm, None, None, None, None, None, None, None, None, None, None, None, None,
        )
        assert result is None
        mock_apm.backup_servers.get.assert_not_called()
        mock_apm.remote_storages.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolves_backup_server_destination(self, mock_apm):
        from synology_apm.mcp.tools.plans._builders_common import _build_backup_copy

        server = make_backup_server(backup_server_id="srv-002")
        mock_apm.backup_servers.get.return_value = server

        result = await _build_backup_copy(
            mock_apm, "backup_server", "srv-002", "keep_days", 90, None, None, None, None, None, "daily", "23:00", None,
        )
        assert result.destination is server
        assert result.retention.days == 90
        mock_apm.remote_storages.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolves_remote_storage_destination(self, mock_apm):
        from synology_apm.mcp.tools.plans._builders_common import _build_backup_copy

        storage = make_remote_storage(storage_id="stor-002")
        mock_apm.remote_storages.get.return_value = storage

        result = await _build_backup_copy(
            mock_apm, "remote_storage", "stor-002", "keep_versions", None, 10, None, None, None, None, "after_backup", None, None,
        )
        assert result.destination is storage
        assert result.retention.versions == 10
        mock_apm.backup_servers.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_destination_type_raises(self, mock_apm):
        from synology_apm.mcp.tools.plans._builders_common import _build_backup_copy

        with pytest.raises(ValueError, match="backup_copy_destination_type is required"):
            await _build_backup_copy(
                mock_apm, None, "srv-002", "keep_days", 90, None, None, None, None, None, "daily", "23:00", None,
            )

    @pytest.mark.asyncio
    async def test_missing_retention_type_raises(self, mock_apm):
        from synology_apm.mcp.tools.plans._builders_common import _build_backup_copy

        with pytest.raises(ValueError, match="backup_copy_retention_type is required"):
            await _build_backup_copy(
                mock_apm, "backup_server", "srv-002", None, 90, None, None, None, None, None, "daily", "23:00", None,
            )

    @pytest.mark.asyncio
    async def test_missing_schedule_frequency_raises(self, mock_apm):
        from synology_apm.mcp.tools.plans._builders_common import _build_backup_copy

        with pytest.raises(ValueError, match="backup_copy_schedule_frequency is required"):
            await _build_backup_copy(
                mock_apm, "backup_server", "srv-002", "keep_days", 90, None, None, None, None, None, None, "23:00", None,
            )

    @pytest.mark.asyncio
    async def test_unknown_destination_type_raises(self, mock_apm):
        from synology_apm.mcp.tools.plans._builders_common import _build_backup_copy

        with pytest.raises(ValueError, match="Unsupported backup_copy_destination_type"):
            await _build_backup_copy(
                mock_apm, "ftp", "srv-002", "keep_days", 90, None, None, None, None, None, "daily", "23:00", None,
            )
        mock_apm.backup_servers.get.assert_not_called()
        mock_apm.remote_storages.get.assert_not_called()
