# symphonz

`symphonz` is a dependency-light CLI and built-in Python runtime that turns Linear issues into isolated Codex development sessions. It does not require OpenAI Symphony, `mise`, Elixir, Erlang, or `escript`.

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
symphonz run --port 4000
```

The interactive installer asks for the Linear project, API-key environment variable name, GitHub or GitLab provider, repository URL, and branch settings. It writes `.symphonz/WORKFLOW.md`, `.symphonz/config.toml`, and the workspace/log directories. It never writes the API-key value to disk.

For CI or scripted setup, provide every project-specific value with flags or `SYMPHONZ_*` environment variables:

```bash
symphonz install --yes \
  --linear-project engineering \
  --git-provider github \
  --repo-url https://github.com/example/project.git \
  --base-branch main \
  --target-branch main
```

`SYMPHONZ_LINEAR_PROJECT`, `SYMPHONZ_LINEAR_API_KEY_ENV`, `SYMPHONZ_GIT_PROVIDER`, `SYMPHONZ_REPO_URL`, `SYMPHONZ_BASE_BRANCH`, `SYMPHONZ_TARGET_BRANCH`, and `SYMPHONZ_GITLAB_BASE_URL` are the environment equivalents.

## Workflow

The default lifecycle is:

```text
Todo -> In Progress -> Ready to Publish -> Human Review
     -> Rework -> Human Review -> Merging -> Done
```

`Ready to Publish`, not `Done`, pushes the deterministic issue branch and opens or updates a GitHub pull request or GitLab merge request. `Done`, `Closed`, `Cancelled`, `Canceled`, and `Duplicate` are terminal and trigger workspace cleanup.

The runtime polls Linear with pagination, claims eligible issues in priority order, suppresses blocked issues, and runs a bounded worker pool. Each worker reuses one Codex app-server thread for its configured turns, exposes a guaranteed `linear_graphql` dynamic tool, records events in `.symphonz/logs/runtime.jsonl`, and retries transient failures with bounded exponential backoff.

The generated workflow assumes a trusted automation environment: Codex is allowed network access and unattended approvals so it can update Linear and the Git provider. Use a dedicated machine or container with least-privilege Linear and Git credentials.

## Upgrade

Run the same installer again. It stages the new CLI and library before replacing the existing installation:

```bash
curl -fsSL https://raw.githubusercontent.com/ilordhalo/zhangjiahao_agi/main/install.sh | sh
symphonz version
```

An upgrade does not overwrite project-local `.symphonz/WORKFLOW.md` or configuration. Review the new repository `WORKFLOW.md` and rerun `symphonz install` only when you intentionally want to regenerate project configuration.

## Development

```bash
python3 -m unittest discover -v
./install.sh --prefix /tmp/symphonz-dev --source .
./bin/symphonz version
```

The Chinese developer guide is available at [`docs/index.html`](docs/index.html).
