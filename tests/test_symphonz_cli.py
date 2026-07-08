from pathlib import Path
import subprocess
import tempfile
import unittest

from symphonz.install import (
    InstallConfig,
    collect_install_config,
    detect_git_defaults,
    install_project,
    read_config,
    write_config,
)
from symphonz.workflow import render_workflow


class ConfigTests(unittest.TestCase):
    def test_write_config_uses_env_var_for_linear_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            config = InstallConfig(
                runtime_mode="embedded",
                runtime_command=".symphonz/bin/symphony",
                linear_api_key_env="LINEAR_API_KEY",
                linear_project_slug="zhangjiahao-agi-186a15c896ac",
                git_provider="gitlab",
                repo_url="https://github.com/ilordhalo/zhangjiahao_agi.git",
                base_branch="main",
                mr_target="main",
                gitlab_base_url="https://zhangjiahao.me:9011",
                workspace_root=".symphonz/workspace",
                logs_root=".symphonz/logs",
            )

            write_config(path, config)

            content = path.read_text()
            self.assertIn('[runtime]', content)
            self.assertIn('mode = "embedded"', content)
            self.assertIn('api_key_env = "LINEAR_API_KEY"', content)
            self.assertNotIn("lin_api_", content)
            parsed = read_config(path)
            self.assertEqual(parsed["linear"]["project_slug"], "zhangjiahao-agi-186a15c896ac")
            self.assertEqual(parsed["git"]["gitlab_base_url"], "https://zhangjiahao.me:9011")


if __name__ == "__main__":
    unittest.main()


class InstallInputTests(unittest.TestCase):
    def test_detect_git_defaults_reads_remote_and_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://example.com/group/repo.git"],
                cwd=root,
                check=True,
            )

            defaults = detect_git_defaults(root)

            self.assertEqual(defaults["repo_url"], "https://example.com/group/repo.git")
            self.assertEqual(defaults["base_branch"], "main")

    def test_collect_install_config_uses_answers_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://example.com/group/repo.git"],
                cwd=root,
                check=True,
            )
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", "", ""])

            config = collect_install_config(root, "global", False, input_func=lambda prompt: next(answers))

            self.assertEqual(config.runtime_mode, "global")
            self.assertEqual(config.runtime_command, "symphony")
            self.assertEqual(config.linear_project_slug, "project-slug")
            self.assertEqual(config.git_provider, "gitlab")
            self.assertEqual(config.repo_url, "https://example.com/group/repo.git")
            self.assertEqual(config.base_branch, "main")
            self.assertEqual(config.mr_target, "main")


class WorkflowInstallTests(unittest.TestCase):
    def make_config(self) -> InstallConfig:
        return InstallConfig(
            runtime_mode="global",
            runtime_command="symphony",
            linear_api_key_env="LINEAR_API_KEY",
            linear_project_slug="project-slug",
            git_provider="gitlab",
            repo_url="https://example.com/group/repo.git",
            base_branch="main",
            mr_target="main",
            gitlab_base_url="https://gitlab.example.com",
            workspace_root=".symphonz/workspace",
            logs_root=".symphonz/logs",
        )

    def test_render_workflow_replaces_project_values(self):
        template = Path("WORKFLOW.md").read_text()

        rendered = render_workflow(template, self.make_config())

        self.assertIn('api_key: $LINEAR_API_KEY', rendered)
        self.assertIn('project_slug: "project-slug"', rendered)
        self.assertIn('workspace:\n  root: .symphonz/workspace', rendered)
        self.assertIn('SYMPHONZ_REPO_URL:-https://example.com/group/repo.git', rendered)
        self.assertIn('https://gitlab.example.com', rendered)
        self.assertIn("- `Done` -> implementation is considered complete", rendered)

    def test_install_project_global_creates_expected_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", "https://example.com/group/repo.git"], cwd=root, check=True)
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", "", ""])

            install_project(
                project_root=root,
                runtime_mode="global",
                assume_yes=False,
                skip_runtime_download=False,
                input_func=lambda prompt: next(answers),
            )

            self.assertTrue((root / ".symphonz" / "WORKFLOW.md").exists())
            self.assertTrue((root / ".symphonz" / "config.toml").exists())
            self.assertTrue((root / ".symphonz" / "workspace").is_dir())
            self.assertTrue((root / ".symphonz" / "logs").is_dir())
            self.assertFalse((root / ".symphonz" / "runtime").exists())
            self.assertIn(".symphonz/workspace/", (root / ".gitignore").read_text())
