## symphonz CLI

This repository owns the `symphonz` installer, project initializer, and built-in Python runtime for project-local agent orchestration.

It does not require OpenAI Symphony, `mise`, Elixir, Erlang, `escript`, or a runtime clone.

Install to your shell:

```bash
curl -fsSL https://raw.githubusercontent.com/ilordhalo/zhangjiahao_agi/main/install.sh | sh
symphonz version
```

Install to a custom prefix:

```bash
curl -fsSL https://raw.githubusercontent.com/ilordhalo/zhangjiahao_agi/main/install.sh | sh -s -- --prefix "$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"
```

Local development:

```bash
python3 -m unittest discover -v
./install.sh --prefix /tmp/symphonz-dev --source .
./bin/symphonz install
./bin/symphonz run --print-command
```

Install into a target project:

```bash
cd /path/to/your-project
symphonz install
```

Start the built-in runtime:

```bash
export LINEAR_API_KEY="REPLACE_WITH_LINEAR_API_KEY"
symphonz run --port 4000
```

Required project setup:

- Create or confirm a Linear project and provide its slug/ID during `symphonz install`.
- Authenticate GitHub or GitLab so Codex can push branches and create review requests.
- Ensure `codex` is installed and can run `codex app-server`.
- Linear issues must be in one of the workflow active states, such as `Todo`.
