# APM MCP Server — Full Coverage Smoke Test Prompt

> **Note:** Paste everything below the separator into a chat with an agent that has the
> `synology-apm` MCP server connected. Run the server in `admin` mode
> (`APM_MCP_MODE=admin`) so every tool is registered and reachable — lower modes hide
> some of the actions this prompt asks for. Point it at a test/staging APM instance,
> not a production one: this prompt intentionally creates and deletes small disposable
> objects, and starts/cancels a few on-demand jobs.

> **Note:** Unlike `tests/smoke/cli/` and `tests/smoke/sdk/`, this is not an automated
> harness — the MCP server's value is in letting a natural-language agent choose the
> right tool for a described task, so the smoke test here is a prompt an agent runs
> against its own connected tools, not a Python driver. Each numbered step below
> describes an action rather than naming a tool, so the exercise reflects how an agent
> actually discovers and calls tools in practice.

> **Note:** Phase 1 front-loads a set of intentionally inert, disposable objects (a
> remote storage, four kinds of plans, and — where possible — a brand-new file-server
> workload) so that most of the phases after it exercise real create/read/update/delete
> paths without depending on whatever happens to already be configured on this system.
> A few actions still unavoidably need at least one real backup server, hypervisor,
> already-protected workload with backup history, or connected M365 tenant — those are
> called out individually below and are skipped cleanly when absent, rather than
> blocking the run.

---

You are connected to a live backup-management system through this session's tools and
reference resources. Your job is to walk through the checklist below, end to end, and
use whichever connected tool or reference resource is the natural fit for each step —
don't skip a step because you're not sure which one applies; try the one that best
matches the description. Work through the phases in order: later phases reuse objects
(server names, workload IDs, plan names) discovered or created in earlier ones, and the
Cleanup phase at the end depends on everything before it having run.

Keep a running checklist as you go, one line per step, and print it at the very end in
this form:

```
[PASS]    <step>  — <one-line result>
[FAIL]    <step>  — <what went wrong>
[SKIPPED] <step>  — <why: no such data on this system / precondition not met>
```

General rules for the whole run:

- Anything you create for this test must have a name that clearly marks it as
  disposable, e.g. prefixed with `smoke-test-`, so it's unmistakable and easy to clean
  up if a later step fails partway through.
- Every object created in Phase 1 must be removed again by the end of the Cleanup phase,
  unless its removal step itself is what failed (note that in the report). Some fixtures
  reference each other (a plan attached to a workload, a plan pointing at a remote
  storage), so Cleanup has a required order — follow it as written there rather than
  deleting fixtures as soon as you're done reading them.
- For any action that is destructive and cannot be undone (permanently retiring or
  removing a piece of protected data): where Phase 1 was able to create a disposable
  stand-in object for it (currently, this applies to the machine file-server workload),
  exercise the real action against that disposable object instead of against anything
  real. Where no disposable stand-in exists (all M365 workload types, or a machine
  workload if Phase 1's file-server fixture couldn't be created), call the action with
  an identifier you make up that clearly cannot exist, and confirm you get a proper
  "not found"-style error back rather than a crash or a silent success — that still
  proves the action is wired up correctly, just without a real round trip. Note in your
  report which of the two approaches you used for each such action.
- Steps that temporarily reassign a real, pre-existing object to a disposable plan (the
  backup-server tiering-plan switch in Phase 2, and the workload protection-plan
  switches in Phases 4 and 5) must fully complete their "switch back" half before you
  consider the step done. If switching back fails for any reason, stop and report it as
  a failure immediately — do not proceed to Phase 8's deletion of the disposable plan
  involved, since the real object would then be left referencing a plan that no longer
  exists. Deleting that disposable plan is safe only once the real object has
  confirmably been switched back to its original assignment.
- If a whole category of data doesn't exist on this system (no appliance of a given
  type, no workload of a given kind, nothing in a queue), record every step that
  depends on it as skipped with the reason, and move on — don't stop the run.
- Don't guess at destinations for anything (email, external URLs, file paths); if a
  step would require one, use an obviously fake placeholder (e.g. an RFC 5737 example
  address such as `203.0.113.50`) and note that in the report instead of skipping the
  step outright, where that's possible.

---

## Phase 0 — Reference resources

Before calling any actions, open every small reference page this server publishes:
the environment overview, the list of backup servers, the three plan-type reference
lists (protection, retirement, tiering), and the tenant list. Then open the
single-server reference page for one specific server ID drawn from the server list.
These should be available as direct reference reads, separate from the search/list
actions you'll use in later phases — use whichever mechanism your client exposes for
reading a resource by its address rather than calling a tool.

## Phase 1 — Fixture setup

Create a self-contained set of disposable objects so that most of the rest of this run
does not depend on whatever happens to already exist on this system. Keep every one of
these alive until the Cleanup phase at the end.

1. Create a disposable remote (off-box) storage destination (pick whichever storage
   type is simplest to register, with placeholder/fake endpoint details). Keep it —
   don't remove it yet.
2. Create a disposable protection plan scoped to PC/server/VM-type workloads with a
   deliberately inert configuration: not immutable, on-demand/manual trigger only (no
   automatic schedule), a "keep for N days" retention policy set to a very large number
   of days (e.g. 9999) so nothing in it is ever eligible for automatic cleanup during
   this run, and no copy-to-remote-storage step configured.
3. Create a second disposable protection plan with the same inert configuration
   (not immutable, manual trigger only, keep for a very large number of days, no copy
   step), this time scoped to mailbox/M365-type workloads.
4. Create a disposable retirement/archive plan with its retention set to a very large
   number of days (e.g. 9999), so it can never actually cause anything to be purged
   during this run.
5. Create a disposable storage-tiering plan whose "move data after N days" setting is
   a very large number of days (e.g. 9999), so it never actually triggers during this
   run, pointing at the disposable remote storage from step 1 as its destination.
6. List backup servers and pick one real, existing server; fetch its full detail record
   and find one of its existing storage namespaces there. Using that namespace, try to
   register a brand-new disposable file-server-type machine workload: a clearly-fake
   host address (an RFC 5737 example address such as `203.0.113.50`), any connection/
   share type, placeholder credentials, and the protection plan from step 2. Do not
   request an immediate on-demand backup as part of registration. If this is accepted,
   look it up (by its disposable name) to find its new workload identifier — it stands
   in for a fully disposable machine workload for the rest of this run. If the system
   instead rejects it (for example because it actively validates connectivity to the
   placeholder host), record this step as failed with the reason, and fall back to the
   not-found-identifier probe approach for the retire/delete steps in Phase 4 instead.

## Phase 2 — Environment and infrastructure

7. Get the overall summary/overview information for this environment (site identity,
   management servers, storage, workload counts).
8. List every backup server registered in the cluster.
9. Fetch the full detail record for one specific server from that list (reuse the one
   from Phase 1's step 6 if it's the same one, rather than picking a new one).
10. Reassign that server to the disposable tiering plan from Phase 1's step 5, confirm
    the change, then switch it back to its original tiering policy (or "none" if it had
    none). This step necessarily mutates a real backup server's configuration — it is
    fully reverted by the end of the step.
11. List every connected remote (off-box) storage destination — the disposable one
    from Phase 1's step 1 should appear here.
12. Fetch full detail for the disposable remote storage from Phase 1's step 1, then
    update one of its editable settings. Leave it in place — it's still referenced by
    the disposable tiering plan and gets removed in Cleanup.
13. List every registered hypervisor/virtualization host.
14. Fetch full detail for one specific hypervisor from that list. Skip this and the
    previous step if none is registered — there is no way to create a disposable one.

## Phase 3 — Plans

15. List every protection plan across all workload categories — the two disposable
    ones from Phase 1 should appear.
16. Fetch full detail for the disposable machine-category protection plan from Phase
    1's step 2, then change one of its settings.
17. Fetch full detail for the disposable M365-category protection plan from Phase 1's
    step 3, then change one of its settings.
18. List every retirement/archive plan — the disposable one from Phase 1 should appear.
19. Fetch full detail for the disposable retirement plan from Phase 1's step 4, then
    update one of its settings.
20. List every storage-tiering plan — the disposable one from Phase 1 should appear.
21. Fetch full detail for the disposable tiering plan from Phase 1's step 5, then
    update one of its settings other than its destination.

Deletion of these four plan fixtures happens in the Cleanup phase, not here — the
machine-category plan and the tiering plan are still referenced by the disposable
file-server workload and the disposable remote storage, respectively.

## Phase 4 — Machine workloads (PCs, physical servers, VMs, file servers)

22. List the protected machine-type workloads — the disposable file-server workload
    from Phase 1's step 6 should appear here if it was created.
23. Fetch full detail for one specific *pre-existing, already-protected* workload from
    that list (not the disposable one, which has no backup history).
24. List that workload's backup version/history entries.
25. Fetch full detail for one specific version, and separately fetch its most recent
    version without naming a specific one.
26. If that workload is a VM or physical server with recovery verification enabled,
    fetch the link to its verification recording. Skip if none qualifies.
27. Kick off an on-demand backup for it, then cancel it while it's still in progress.
28. Lock one of its backup versions so it can't be cleaned up, confirm the lock took
    effect, then unlock it again.
29. Temporarily reassign it to the disposable machine-category protection plan from
    Phase 1's step 2, confirm the reassignment, then switch it back to its original
    plan.
30. If the disposable file-server workload from Phase 1's step 6 was created, register
    a second shared-folder backup target under it, then update one of that target's
    settings — the whole workload, and everything registered under it, is removed in
    step 31 below, so nothing here needs its own cleanup. Skip this step if Phase 1's
    step 6 wasn't able to create that workload: there is no tool to remove a
    shared-folder backup target once it's added, so doing this against a real,
    pre-existing file-server workload would have no way to be undone.
31. Retire the disposable file-server workload created in Phase 1's step 6, confirm it
    now shows as retired, then permanently delete it. If Phase 1's step 6 wasn't able to
    create that workload, instead pick a workload identifier you make up that clearly
    doesn't exist and confirm both actions return a proper not-found-style error.
    Either way, do not run either action against a real, already-protected workload.

## Phase 5 — Microsoft 365 workloads

Cover as many of the following kinds of M365 data as exist on this system: user
mailboxes, OneDrive accounts, Teams chat data, SharePoint sites, Teams, and M365
groups/shared mailboxes.

32. List connected M365/SaaS tenants, then fetch full detail for one of them.
33. List the protected M365-type workloads (across whichever of the kinds above
    exist).
34. Fetch full detail for one specific M365 workload.
35. List that workload's backup version/history entries.
36. Fetch full detail for one specific version, and separately its most recent
    version without naming one.
37. Kick off an on-demand backup for one M365 workload, then cancel it while still
    in progress.
38. Lock one of its backup versions, confirm the lock, then unlock it again.
39. Temporarily reassign that workload to the disposable M365-category protection plan
    from Phase 1's step 3, confirm it, then switch it back to its original plan.
40. Pick made-up identifiers and confirm that permanently retiring and permanently
    deleting an M365 workload both return proper not-found errors. Unlike machine
    workloads, there is no way to create a disposable M365 workload — M365 workloads
    are discovered from the connected tenant rather than registered directly through
    this system — so this pair always uses the not-found probe; do not run either
    against a real workload.
41. List the automatic-backup-inclusion rules configured for M365. Create a
    disposable one, update one of its settings, then remove it.
42. Read and record the tenant's current value for one collaboration-data backup
    setting (e.g. a chat or site inclusion toggle), change it, confirm the change, then
    restore exactly the recorded value — don't rely on there having been an explicit
    prior value to fall back to.
43. If a mailbox with backup history exists, start an export of its mailbox
    contents to an offline archive format, list in-progress exports and confirm
    yours appears, fetch its download link, then cancel the export before it
    finishes. Skip if no eligible mailbox exists.
44. Do the same export round trip (start, list, get download link, cancel) for a
    group/shared mailbox instead, if one with backup history exists.

## Phase 6 — Activity history

45. List recent backup job activity across the environment.
46. Fetch full detail for one specific backup activity from that list.
47. If any backup you started earlier in this run is still active, cancel it here
    (if it wasn't already handled in its own phase). Otherwise, confirm that
    cancelling a made-up, nonexistent backup-activity identifier returns a proper
    not-found error.
48. List recent restore job activity across the environment.
49. Fetch full detail for one specific restore activity from that list.
50. Confirm that cancelling a made-up, nonexistent restore-activity identifier
    returns a proper not-found error (there should be no real restore in progress
    to cancel from this run).

## Phase 7 — Server-side logs (appliance-hosted backup servers only)

If none of the backup servers from Phase 2 are the appliance-hosted type, skip this
whole phase and say so in the report.

51. Pull recent protection/system/access activity log entries for one such server.
52. Pull recent drive/disk information log entries for that server.
53. Pull recent connection log entries for that server.
54. Pull recent advanced system log entries for that server.

## Phase 8 — Cleanup

Remove every fixture created in Phase 1 that is still around, in this order — later
steps depend on earlier ones having already run:

55. If the disposable file-server workload from Phase 1's step 6 still exists (Phase
    4's step 31 may already have removed it), retire and then permanently delete it now.
56. Delete the disposable machine-category protection plan from Phase 1's step 2 (must
    come after step 55 — a plan still referenced by a workload cannot be removed).
57. Delete the disposable M365-category protection plan from Phase 1's step 3.
58. Delete the disposable retirement plan from Phase 1's step 4.
59. Delete the disposable tiering plan from Phase 1's step 5 (must come before the next
    step — it references the remote storage below as its destination).
60. Delete the disposable remote storage from Phase 1's step 1.

Confirm each removal and call out in the final report anything that could not be
cleaned up (and would need manual removal afterward).

---

When every phase is done, print the full checklist described above, then a one-line
summary: how many steps passed, failed, and were skipped, and call out anything you
had to clean up manually because a Phase 8 removal step failed.
