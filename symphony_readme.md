你还需要补齐这些配置：
在 Linear 里创建/确认项目，并拿到 project_slug
右键 Linear Project 复制 URL，slug 通常在 URL 末尾。
替换模板里的：
project_slug: "REPLACE_WITH_LINEAR_PROJECT_SLUG"

在 shell 里设置新的 Linear key
export LINEAR_API_KEY="REPLACE_WITH_LINEAR_API_KEY"

配置 GitHub/GitLab 权限
Codex/Symphony 要能 push branch，并创建 GitHub PR 或 GitLab MR。
建议先本机跑：
gh auth login
gh repo view your-org/your-repo
glab auth login

把模板放到目标 repo 根目录，命名为：
WORKFLOW.md

启动 Symphony
git clone https://github.com/openai/symphony
cd symphony/elixir
mise trust
mise install
mise exec -- mix setup
mise exec -- mix build
mise exec -- ./bin/symphony /path/to/your-repo/WORKFLOW.md --port 4000

Linear issue 使用方式
issue 必须在配置的 Linear Project 里。
issue 加标签：codex-ready
状态设为：Todo
Symphony 会轮询后交给 Codex 执行。

## symphonz CLI

This repository now owns the `symphonz` installer/launcher for project-local Symphony orchestration.

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
./bin/symphonz install --runtime global
./bin/symphonz run --print-command
```

Use embedded runtime mode for a self-contained project install:

```bash
./bin/symphonz install
```

Use global runtime mode when `symphony` is already available on the machine:

```bash
./bin/symphonz install --runtime global
```
