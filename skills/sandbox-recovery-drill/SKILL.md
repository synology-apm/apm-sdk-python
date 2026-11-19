---
name: sandbox-recovery-drill
description: "Run a recovery drill: boot a PS/VM backup in the APM built-in sandbox, check its screen, then clean up. Use when the user asks to test-restore a machine, verify a backup boots, or run a DR drill."
---

# Sandbox Recovery Drill

When the user asks to test-restore a Physical Server (PS) or VM backup, check that a backup
boots, or run a disaster-recovery drill:

See [apm-mcp-conventions](../apm-mcp-conventions/SKILL.md) for shared conventions this skill
relies on (list vs. get field completeness, pagination, permission modes).

A sandbox is an instant restore of one backup version into a VM on the APM appliance's
built-in hypervisor. It is tracked as a restore activity: its status, logs and stopping all go
through the restore-activity tools.

## Steps

1. **Check the requirements.**
   - Operator mode: if `start_machine_sandbox` is not among your tools, the server is not in
     operator mode. Tell the user and stop.
   - A PS or VM workload: `list_machine_workloads` with `workload_types=["ps", "vm"]` and
     `is_retired=false`, then take its `workload_id` and `namespace`.
   - The version: the one the user names; otherwise the newest version from
     `list_machine_versions` with `status=["success", "partial"]` that has a location not on
     remote storage (`locations[].location_info.is_remote_storage == false`). Pass its
     `version_id` in every later call.

2. **Choose the settings.** Call `start_machine_sandbox` with `workload_id`, `namespace`,
   `version_id`, and `dry_run=true`. It starts nothing and returns the `version_id` and the
   final `settings`: the location's defaults with the keys you passed in `settings` applied.
   Every key you omit keeps its default.
   - Free memory on the appliance is shared by every running sandbox: if the returned
     `memory_mb` is above 4096, use `settings={"memory_mb": 4096}` unless the user asks for more.
   - `network_adapters` and `disks` replace the whole list: copy the list from the returned
     `settings` and change only the field you need in each entry.
   - Change other settings only when the user asks or when retrying (see
     [Troubleshooting](#troubleshooting)). Allowed values per location (CPU and memory limits and
     networks) come from `get_machine_sandbox_resources`.

3. **Start.** Tell the user what you are about to start (workload, version time, memory),
   then call `start_machine_sandbox` with `workload_id`, `namespace`, `version_id`, and your
   `settings` changes, without `dry_run`. It returns the same shape as the dry run;
   `settings.name` is the sandbox's VM name.

4. **Wait until ready.** Call `list_restore_activities` (`workload_id`, `workload_namespace`)
   and find the entry whose `restore_type` is `instant_sandbox` and whose `restore_destination`
   is `settings.name`. If it is not listed, it has already ended: search again with
   `history=true`. Keep its `activity_id`, then call `get_restore_activity` (`activity_id`,
   `include_logs=false`) every 10-15 seconds until `status` is `sandbox_ready`, for up to about
   3 minutes.
   - `preparing` / `restoring`: keep waiting.
   - Any other status: stop, get the activity once more with `include_logs=true`, and report
     its `log_entries` messages.

5. **Check the screen.** Call `get_machine_sandbox_screenshot` (`activity_id`); it returns an
   image of the screen and can be called any number of times.
   - A firmware screen, OS boot splash, or black screen with only a cursor means the machine
     is still booting: take another screenshot every 30 seconds or so, for up to about
     5 minutes.
   - **Pass** = an OS login screen or desktop. Keep that screenshot as evidence.
   - Some screens need a key first. Pass it in `keys`; the image is taken 2 seconds later
     (`delay_seconds` changes that):
     - a boot menu waiting for a choice → `["enter"]`;
     - a Windows lock screen → `["space"]`, or `["ctrl_alt_delete"]` when it asks for it
       (never on a text console: there it restarts the guest);
     - a black screen after a login screen was already seen (the idle display blanked) →
       `["shift"]`.

     Press only these keys during a boot check; without `keys` no input is sent to the guest.
   - To watch or use the sandbox (for example, when the user asks to sign in), call
     `get_machine_sandbox_console_link` and open its `url` in a browser tool right away. Treat
     the URL as a password: never paste it into the chat, logs or files. Don't type credentials
     unless the user supplies them and asks you to.

6. **Clean up.** Tell the user, then call `cancel_restore_activity` with the `activity_id`.
   This stops the sandbox and deletes its VM. Poll `get_restore_activity`
   (`include_logs=false`) until `status` is `canceled`, for up to about 2 minutes. Clean up
   even when the drill fails.

7. **Report:** workload, version time, settings used (CPU, memory, machine type, disk
   controllers, network), time from start to ready, boot result with the screenshot, and
   cleanup status.

## Troubleshooting

| Symptom | Action |
|---|---|
| "Version ... has no copy on a backup server" | Pick another version with a location not on remote storage |
| "No storage on the backup's volume ... can hold a sandbox" | Pick another location (`settings.location_id`, from `get_machine_sandbox_resources`) or another version |
| "Not enough free memory" when starting | Lower `settings.memory_mb`. Running sandboxes share the memory (`list_restore_activities` with `status=["sandbox_ready"]`); ask the user before stopping any that the drill did not start |
| A setting is rejected (dry run or start) | The error's `field` (and `other_field` for a rule about two settings) names the setting; pick an allowed value from `get_machine_sandbox_resources` |
| "Could not be powered on" | Stop the sandbox; retry with the default settings |
| "No bootable device", or a boot-device blue screen | Stop the sandbox; retry with another `controller` in `settings.disks`: `sata` → `ide` (needs `machine_type` `pc`), or `scsi` → `sata` |
| Black screen / no display after 5 minutes | Stop the sandbox; retry with `settings.video_card` `vga` |
| The guest has no network | An empty `settings.network_adapters` means the location has no network. Otherwise retry with `model` `e1000`; use `virtio` only when the guest has its drivers |
| Opening the console URL a second time fails | The URL is single-use; get a new one |

Retrying means: cancel the current sandbox, wait for `canceled` (its VM is then deleted and its
name is free again), and start a new one.
