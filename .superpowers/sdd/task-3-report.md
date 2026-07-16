# Task 3 Report: Structured Reports and Safe HTML

## Delivered

- Added `symphonz.service.reporting` with an allowlisted report contract,
  immutable document model, bounded strings and collections, active issue
  identity checks, and HTTP(S)-only URLs.
- Reports render as deterministic escaped HTML/CSS, including architecture,
  validation, review metadata, sticky navigation, collapsible decisions, and
  print styles. Agent-supplied markup is never treated as HTML.
- Publication replaces same-directory `report.json` and `report.html` through
  fsynced temporary files and stable issue report URLs.
- RuntimeStore records report state before Linear synchronization. The
  runtime-owned `## Symphonz Implementation Report` comment is created once,
  updated on subsequent publication, and failed synchronization stays pending
  with bounded exponential retry state and an error record.
- Added dynamic-tool specifications and explicit routing for both
  `linear_graphql` and `symphonz_report`.

## TDD Evidence

1. Added `tests/test_symphonz_reporting.py` before production code.
2. Ran `python3 -m unittest tests.test_symphonz_reporting -v` and observed all
   six tests fail with `ModuleNotFoundError` for the missing reporting module.
3. Implemented the minimum module and routing needed for the contract.
4. Re-ran focused, service, bytecode, diff, and full-suite verification.

## Verification

- `python3 -m unittest tests.test_symphonz_reporting -v`: 6 passed.
- `python3 -m unittest tests.test_symphonz_service -v`: 76 passed.
- `env PYTHONPYCACHEPREFIX=/private/tmp/symphonz-pycache python3 -m py_compile symphonz/service/reporting.py symphonz/service/dynamic_tools.py`: passed.
- `git diff --check`: passed.
- `python3 -m unittest discover -v`: 149 passed on the final serial run.

## Follow-up Risk

`CodexAppServerTests.test_never_approval_policy_rejects_unexpected_approval_request`
is timing-sensitive: it failed intermittently during an earlier service/full
run and passed in the final focused service and serial full-suite runs. This
task does not modify the app-server path.
