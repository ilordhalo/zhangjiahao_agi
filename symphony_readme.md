你还需要补齐这些配置：
在 Linear 里创建/确认项目，并拿到 project_slug
右键 Linear Project 复制 URL，slug 通常在 URL 末尾。
替换模板里的：
project_slug: "REPLACE_WITH_LINEAR_PROJECT_SLUG"

在 shell 里设置新的 Linear key
export LINEAR_API_KEY="新的_lin_api_xxx"

配置 GitHub 权限
Codex/Symphony 要能 push branch、开 PR。
建议先本机跑：
gh auth login
gh repo view ilordhalo/zhangjiahao_agi

把模板放到目标 repo 根目录，命名为：
WORKFLOW.md

启动 Symphony
git clone https://github.com/openai/symphony
cd symphony/elixir
mise trust
mise install
mise exec -- mix setup
mise exec -- mix build
mise exec -- ./bin/symphony /path/to/zhangjiahao_agi/WORKFLOW.md --port 4000

Linear issue 使用方式
issue 必须在配置的 Linear Project 里。
issue 加标签：codex-ready
状态设为：Todo
Symphony 会轮询后交给 Codex 执行。
