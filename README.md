# symphonz

`symphonz` is a dependency-light CLI and built-in Python runtime that turns Linear issues into isolated Codex development sessions. It does not require OpenAI Symphony, `mise`, Elixir, Erlang, or `escript`.

The built-in runtime is the only supported execution mode. Legacy project configs that reference an external `symphony` command are automatically run by the internal service instead.

## Install the CLI

```bash
curl -fsSL https://raw.githubusercontent.com/ilordhalo/zhangjiahao_agi/main/install.sh | sh
symphonz version
```

The curl command installs only the CLI. Initialize a Git repository separately:

```bash
cd /path/to/project
symphonz install
export LINEAR_API_KEY="your-linear-api-key"
symphonz run
```

The interactive installer asks for the Linear project, API-key environment variable name, GitHub or GitLab provider, repository URL, branch settings, dashboard host and port, LAN/public base URL, username, password, and session duration. Password input uses the terminal's hidden `getpass` prompt. The installer writes `.symphonz/WORKFLOW.md`, non-secret settings in `.symphonz/config.toml`, a private password record in `.symphonz/auth.toml`, and the artifacts, logs, and workspace directories. It never stores the Linear API key or plaintext dashboard password.

An initial dashboard section uses string TOML values:

```toml
[dashboard]
host = "127.0.0.1"
port = "4000"
public_base_url = "http://127.0.0.1:4000"
username = "admin"
session_days = "30"
```

The generated `.gitignore` excludes `.symphonz/artifacts/`, `.symphonz/logs/`, `.symphonz/workspace/`, and `.symphonz/auth.toml`. Keep `auth.toml` private; it is created atomically with mode `0600` and contains a password hash and random session secret, not a plaintext password.

If the referenced API-key environment variable is already set, installation performs a read-only Linear connection check. Otherwise it prints the exact `export` command needed before `symphonz run`. Use `--skip-linear-preflight` only for an intentionally offline setup.

For CI or scripted setup, provide every project-specific value with flags or `SYMPHONZ_*` environment variables:

```bash
symphonz install --yes \
  --linear-project engineering \
  --git-provider github \
  --repo-url https://github.com/example/project.git \
  --base-branch main \
  --target-branch main
```

`SYMPHONZ_LINEAR_PROJECT`, `SYMPHONZ_LINEAR_API_KEY_ENV`, `SYMPHONZ_GIT_PROVIDER`, `SYMPHONZ_REPO_URL`, `SYMPHONZ_BASE_BRANCH`, `SYMPHONZ_TARGET_BRANCH`, and `SYMPHONZ_GITLAB_BASE_URL` are the project environment equivalents. Non-interactive `--yes` installation also accepts `SYMPHONZ_DASHBOARD_HOST`, `SYMPHONZ_DASHBOARD_PORT`, `SYMPHONZ_DASHBOARD_PUBLIC_BASE_URL`, `SYMPHONZ_DASHBOARD_USERNAME`, `SYMPHONZ_DASHBOARD_PASSWORD`, and `SYMPHONZ_DASHBOARD_SESSION_DAYS`. `SYMPHONZ_DASHBOARD_PASSWORD` is required with `--yes` and is never written to `config.toml` or logs.

## Dashboard access

The dashboard has one configured user. Login creates an HTTP-only, same-site session cookie lasting `session_days`; sessions survive restarts until they expire. Changing the username or regenerating `auth.toml` invalidates existing sessions. All task, report, error, and API routes require authentication; only login and health checks are public.

The default bind is loopback-only. To use a trusted LAN, configure a LAN bind and a URL reachable by reviewers:

```bash
symphonz configure-dashboard \
  --host 0.0.0.0 \
  --port 4000 \
  --public-base-url http://192.0.2.20:4000 \
  --username admin \
  --session-days 30
```

The command obtains the missing password from `SYMPHONZ_DASHBOARD_PASSWORD` or a hidden `getpass` prompt. It changes only `[dashboard]`, `.symphonz/auth.toml`, and `.gitignore`; it does not regenerate `WORKFLOW.md`, run Git commands, contact Linear, or alter `[linear]`, `[git]`, and other config sections.

Version 0.4.0 permits HTTP only for a trusted LAN and displays an unencrypted-connection warning. Do not expose the service to the public Internet without a separately managed HTTPS reverse proxy and an explicit security review. Set `public_base_url` to the reviewer-reachable HTTPS URL when TLS terminates at a proxy.

Configured host and port are used by `symphonz run`. `--host` and `--port` temporarily override the process bind without rewriting configuration:

```bash
symphonz run --host 127.0.0.1 --port 4100
```

Report links always use `public_base_url`, even when a temporary port differs; startup warns about that mismatch. Legacy configs without `[dashboard]` retain loopback-only behavior and the existing explicit `symphonz run --port 4000` flow.

## Workflow

The default lifecycle is:

```text
Todo -> In Progress -> Ready to Publish -> Human Review
     -> Rework -> Human Review -> Merging -> Done
```

`Ready to Publish`, not `Done`, pushes the deterministic issue branch and opens or updates a GitHub pull request or GitLab merge request. After that review request exists, Codex must publish a structured implementation report through `symphonz_report`. Symphonz keeps the stable authenticated report URL and review URL synchronized in a dedicated Linear comment. Missing or failed report publication blocks the move to `Human Review`. `Done`, `Closed`, `Cancelled`, `Canceled`, and `Duplicate` are terminal and trigger workspace cleanup.

The runtime polls Linear with pagination, claims eligible issues in priority order, suppresses blocked issues, and runs a bounded worker pool. Each worker reuses one Codex app-server thread for its configured turns, exposes a guaranteed `linear_graphql` dynamic tool, records events in `.symphonz/logs/runtime.jsonl`, and retries transient failures with bounded exponential backoff. The default workflow allows at most five Codex invocations for one unchanged active state; reaching that limit leaves the issue Blocked until its Linear state changes. This budget survives service restarts and temporary label or blocker changes through `.symphonz/logs/attempts.sqlite3`; Linear polling failures do not consume it.

The generated workflow assumes a trusted automation environment: Codex is allowed network access and unattended approvals so it can update Linear and the Git provider. Use a dedicated machine or container with least-privilege Linear and Git credentials.

## Upgrade

Run the same installer again. It stages the new CLI and library before replacing the existing installation:

```bash
curl -fsSL https://raw.githubusercontent.com/ilordhalo/zhangjiahao_agi/main/install.sh | sh
symphonz version
```

An upgrade does not overwrite project-local `.symphonz/WORKFLOW.md` or configuration. Existing projects should enable 0.4.0 dashboard authentication with `symphonz configure-dashboard`; this preserves the existing workflow and non-dashboard config. Review the new repository `WORKFLOW.md` and copy its mandatory report-publication rules into a customized project workflow. Rerun `symphonz install` only when you intentionally want to regenerate the entire project configuration and workflow.

## Development

```bash
python3 -m unittest discover -v
./install.sh --prefix /tmp/symphonz-dev --source .
./bin/symphonz version
```

The Chinese developer guide is available at [`docs/index.html`](docs/index.html).
