# Task 5E-3: Runner Failure and Cleanup Lifecycle

Status: DONE

Commit: `4456c78`

## Root Cause

`run_service` used nested `finally` blocks. An exception from
`Orchestrator.shutdown()` replaced an active startup or polling exception, and
an exception from `DashboardServer.stop()` replaced either of those. Cleanup
exceptions also escaped before `service_stopped` and cleanup diagnostics could
be persisted.

## Implementation

- `run_service` now captures a primary startup or runtime exception without
  returning from the protected body.
- Cleanup runs in deterministic order: `Orchestrator.shutdown()` exactly once,
  `DashboardServer.stop()` when the dashboard object was constructed, then the
  persisted `service_stopped` event.
- Each failed cleanup operation emits a structured `service_cleanup_failed`
  event with error severity, cleanup stage, operation, exception type, and
  exception message. Later cleanup still runs.
- The primary exception is re-raised with its original identity and traceback.
  If there is no primary exception, the first cleanup exception is re-raised
  after every cleanup attempt and `service_stopped` persistence.
- Normal one-shot and `KeyboardInterrupt` paths return `0` only after cleanup.
- Runner delegates publisher cleanup solely to `Orchestrator.shutdown()` and
  contains no publisher close operation.

## Test Coverage

`RunnerCompositionTests` adds deterministic fake boundaries for:

1. Dashboard partial-start failure combined with both cleanup failures.
2. One-shot polling failure combined with Orchestrator shutdown failure and no
   dashboard.
3. A successful one-shot body followed by both cleanup failures, proving the
   first cleanup exception wins only when no primary exists.
4. Polling `KeyboardInterrupt`, proving cleanup precedes the zero return.
5. Successful one-shot completion with an Orchestrator-owned publisher,
   proving shutdown closes it once and runner does not double-close it.

Every lifecycle test reopens `RuntimeStore` before asserting persisted events.
No dashboard socket or nondeterministic worker boundary is used.

## RED Evidence

After adding the lifecycle tests, the corrected focused run failed in the
expected missing-behavior cases: Dashboard start and poll exceptions were
masked by cleanup exceptions, the last cleanup exception replaced the first,
and no `service_cleanup_failed` events were persisted. The initial test run
also exposed and corrected the fixture assumption that `list_events` is
newest-first; assertions now normalize events to chronological order.

## GREEN Evidence

1. `python3 -m unittest tests.test_symphonz_service.RunnerCompositionTests`:
   PASS, 17 tests in 2.131s.
2. `python3 -m unittest tests.test_symphonz_service`: PASS, 129 tests in
   5.745s. Existing event-sink isolation tests printed their expected failure
   diagnostics; the suite exited successfully.
3. `env PYTHONPYCACHEPREFIX=/private/tmp/symphonz-task-5e3-pycache python3 -m
   py_compile symphonz/service/runner.py tests/test_symphonz_service.py`: PASS,
   exit code 0.
4. `git diff --check`: PASS, exit code 0.

## Self-Review

- Confirmed exception object identity is preserved for primary and first
  cleanup failures.
- Confirmed a dashboard object is stopped even when `start()` raises.
- Confirmed cleanup failures cannot prevent later cleanup attempts or the
  final lifecycle event.
- Confirmed `Orchestrator.shutdown()` is called exactly once on every tested
  service exit and runner contains no publisher close call.
- Removed an accidental, unrelated event-ordering edit before final review.

## Concerns

- A persistence backend failure is isolated and printed by
  `RuntimeEventRouter`; as before, a physically unavailable store cannot
  guarantee durable diagnostics.
- Pre-composition failures that occur before `service_started` and
  Orchestrator construction remain outside this lifecycle, matching the task
  scope.

This report is ignored and intentionally excluded from the commit.

## Review Fixes

The follow-up review identified two remaining lifecycle boundaries:

- `service_started` persistence occurred after Orchestrator composition but
  before primary-exception capture, so a sink `BaseException` skipped shutdown.
- Cleanup diagnostics and `service_stopped` used unguarded `add_event` calls,
  so a sink `BaseException` could skip later cleanup or replace the primary.

The runner now protects `service_started` with the same primary-exception
lifecycle as startup and polling. Cleanup diagnostics and `service_stopped` use
a runner-local safe persistence helper that catches `BaseException`, emits a
non-raising stderr fallback, and lets all remaining cleanup and persistence
attempts proceed. The primary exception still wins; without a primary, the
first cleanup or lifecycle-persistence failure wins.

Regression tests additionally prove that:

- The original event-sink or `poll_once` raising frame remains in the
  propagated traceback.
- Cleanup failure events derive matching `RuntimeStore` runtime-error rows,
  including their cleanup operation context.
- A failed cleanup diagnostic write does not prevent Dashboard stop, the next
  diagnostic, or the `service_stopped` attempt.
- A failed `service_stopped` write cannot mask a primary and becomes the
  propagated error only when no earlier failure exists.

## Review RED Evidence

1. `service_started` persistence regression: 1 focused test failed because
   `lifecycle_order` was empty instead of containing `orchestrator.shutdown`.
2. Cleanup diagnostic persistence regression: 1 focused test errored when the
   injected `BaseException` escaped from `state.add_event`, before Dashboard
   stop and `service_stopped`.
3. `service_stopped` persistence regressions: 2 focused tests produced one
   error and one failure. The sink `BaseException` masked the primary polling
   error, and no stderr fallback was emitted when persistence was the only
   failure.

## Review GREEN Evidence

1. Each focused RED test or pair passed after its minimal runner change.
2. `python3 -m unittest tests.test_symphonz_service.RunnerCompositionTests`:
   PASS, 21 tests in 2.671s.
3. `python3 -m unittest tests.test_symphonz_service`: PASS, 133 tests in
   6.384s. The three printed sink-failure lines are expected diagnostics from
   existing isolation tests.
4. `env PYTHONPYCACHEPREFIX=/private/tmp/symphonz-task-5e3-review-pycache
   python3 -m py_compile symphonz/service/runner.py
   tests/test_symphonz_service.py`: PASS, exit code 0.
5. `git diff --check`: PASS, exit code 0.

The review task explicitly requested this appended evidence be included with
the fix, superseding the earlier note that the initial report was excluded.
