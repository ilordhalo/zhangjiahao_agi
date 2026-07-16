# Task 6: Installation, Migration, Workflow, and Symphonz 0.4.0

Status: DONE

## Scope

Implemented the 0.4.0 installation and migration surface for the authenticated
dashboard and task reports. The change covers initial installation,
`configure-dashboard`, configured runtime propagation, legacy configuration
compatibility, generated workflow publication rules, release documentation,
and version reporting.

## Implementation

- Added dashboard host, port, public base URL, username, and session duration
  fields to `InstallConfig`. `write_config` emits them as strings in a
  `[dashboard]` TOML section.
- Extended interactive installation with dashboard prompts and an injected,
  testable `getpass` boundary. Passwords never pass through `input`.
- Extended `--yes` installation with all six `SYMPHONZ_DASHBOARD_*`
  environment variables and made `SYMPHONZ_DASHBOARD_PASSWORD` mandatory.
- Initial installation now writes the existing atomic private `auth.toml`,
  creates `.symphonz/artifacts`, and ignores artifacts, logs, workspace, and
  auth material.
- Added public `configure_dashboard(...)` and the `symphonz
  configure-dashboard` command. It resolves a password from the explicit API,
  environment, or `getpass`, replaces only the `[dashboard]` section, rotates
  `auth.toml`, and updates `.gitignore`.
- Dashboard migration preserves all TOML content outside `[dashboard]` and
  does not call Git, Linear preflight, or workflow generation.
- `symphonz run` now loads configured dashboard values and supports temporary
  `--host` and `--port` overrides. The internal `service` parser exposes host,
  port, public base URL, dashboard username, and session duration and forwards
  all values to `run_service`.
- Legacy configs without `[dashboard]` retain the original no-dashboard
  command shape and exact explicit `--port` behavior. Their effective host
  remains the runner's loopback default.
- Updated the repository workflow used by `render_workflow`: Codex must create
  the review request first, call `symphonz_report`, require a report URL,
  confirm both report and review URLs are synchronized to Linear, and only
  then move to `Human Review`. Missing, failed, or pending publication remains
  in `Ready to Publish`.
- Updated README installation, migration, LAN HTTP boundary, overrides,
  authentication/session behavior, and report-link documentation.
- Set `symphonz.__version__` to `0.4.0`.

## Security and Compatibility

- Plaintext dashboard passwords are not written to config, workflow, logs, or
  documentation. Auth material continues to use the existing atomic `0600`
  writer and versioned password hashing.
- Interactive installation always obtains the password through `getpass`,
  even if a password environment variable is present. Environment password
  intake is limited to non-interactive install and dashboard migration.
- `configure-dashboard` does not regenerate project-specific workflow content
  or modify `[linear]`, `[git]`, and other configuration sections.
- Positive integer validation rejects explicit zero for dashboard port and
  session duration instead of silently applying defaults.
- Configs lacking `[dashboard]` do not acquire public URL or non-loopback
  service arguments. Temporary host/port values never rewrite configuration.

## TDD Evidence

The initial focused RED run failed to import `configure_dashboard`, confirming
the migration API did not exist. After the first implementation pass, focused
tests exposed only the intentionally pending workflow, documentation, and
version behavior. A later self-review added three regression contracts and
observed all expected failures before correction:

1. Explicit numeric zero was silently replaced by defaults.
2. A legacy explicit-port command gained new internal flags.
3. Two old provider fallback rules still allowed `Human Review` without a
   report.

Each regression passed after its minimal implementation or workflow change.

## Test Coverage

Added or expanded coverage for:

- Interactive dashboard answers and secure `getpass` injection.
- Non-interactive environment values and required password failure.
- String TOML serialization and no plaintext password persistence.
- Initial artifacts/auth layout and complete `.gitignore` entries.
- Existing and missing `[dashboard]` migration with byte preservation outside
  the section.
- Migration password environment/getpass behavior and no Git, Linear, or
  workflow side effects.
- Public API and CLI dispatch for `configure-dashboard`.
- Configured runtime propagation, temporary overrides, internal service
  dispatch, invalid numeric values, and legacy explicit-port compatibility.
- Report publication ordering, publication blocking, Linear link
  synchronization, generated workflow privacy, README requirements, and
  version output.

## Validation

1. Baseline: `python3 -m unittest tests.test_symphonz_cli
   tests.test_symphonz_service.WorkflowServiceTests -v`: PASS, 43 tests.
2. Focused final: `python3 -m unittest tests.test_symphonz_cli
   tests.test_symphonz_service.WorkflowServiceTests -v`: PASS, 55 tests.
3. Complete required modules: `python3 -m unittest tests.test_symphonz_cli
   tests.test_symphonz_service -v`: PASS, 186 tests in 11.413s.
4. `PYTHONPYCACHEPREFIX=/tmp/symphonz-task6-pyc python3 -m py_compile
   symphonz/*.py symphonz/service/*.py tests/test_symphonz_cli.py
   tests/test_symphonz_service.py`: PASS.
5. `./bin/symphonz version`: PASS, exact output `symphonz 0.4.0`.
6. `sh -n install.sh`: PASS.
7. `git diff --check`: PASS.
8. Version, workflow personal-value, and implementation plaintext-password
   scans returned no matches in their intended scopes.

The existing event-sink isolation tests printed their expected diagnostic
messages while the complete suite exited successfully.

## Self-Review

- Confirmed the final changed implementation files are within Task 6
  ownership and no unrelated working-tree changes were present.
- Confirmed `configure-dashboard` leaves project workflow bytes unchanged and
  preserves non-dashboard TOML bytes in tests.
- Confirmed password values used by tests do not appear in production source,
  README, or WORKFLOW.
- Confirmed workflow fallback and guardrail rules no longer permit transition
  to `Human Review` without a review request and synchronized report.
- Confirmed legacy command construction retains the prior explicit `--port`
  argument shape while direct runtime invocation uses the loopback default.

## Concerns

- The separate static Chinese developer guide in `docs/index.html` still
  identifies itself as 0.3.1. That file was outside Task 6 ownership; README
  contains the 0.4.0 upgrade and dashboard guidance required by this task.
- HTTP remains intentionally limited to trusted LAN use. Public Internet
  exposure still requires separately managed TLS termination and security
  review, as documented.
