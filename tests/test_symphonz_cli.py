from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from symphonz.cli import main
from symphonz.install import (
    InstallConfig,
    collect_install_config,
    detect_git_defaults,
    install_project,
    read_config,
    write_config,
)
from symphonz.runtime import build_run_command, install_embedded_runtime
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


class RuntimeTests(unittest.TestCase):
    def test_install_embedded_runtime_skip_download_creates_shim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            install_embedded_runtime(root, skip_download=True)

            shim = root / ".symphonz" / "bin" / "symphony"
            self.assertTrue(shim.exists())
            self.assertIn("runtime download was skipped", shim.read_text())
            self.assertTrue(shim.stat().st_mode & 0o111)

    def test_build_run_command_embedded_exports_expected_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = InstallConfig(
                runtime_mode="embedded",
                runtime_command=".symphonz/bin/symphony",
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
            write_config(root / ".symphonz" / "config.toml", config)

            command, env = build_run_command(root)

            self.assertEqual(command, [".symphonz/bin/symphony", ".symphonz/WORKFLOW.md", "--logs-root", ".symphonz/logs"])
            self.assertEqual(env["SYMPHONZ_REPO_URL"], "https://example.com/group/repo.git")
            self.assertEqual(env["SYMPHONZ_BASE_BRANCH"], "main")
            self.assertEqual(env["SYMPHONZ_MR_TARGET"], "main")
            self.assertEqual(env["SYMPHONZ_GIT_PROVIDER"], "gitlab")
            self.assertEqual(env["GITLAB_BASE_URL"], "https://gitlab.example.com")


class CliSmokeTests(unittest.TestCase):
    def test_main_version_prints_package_version(self):
        from contextlib import redirect_stdout
        from io import StringIO

        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["version"])

        self.assertEqual(exit_code, 0)
        self.assertIn("symphonz 0.1.0", output.getvalue())

    def test_bin_symphonz_help_runs_from_repo_root(self):
        result = subprocess.run(
            ["./bin/symphonz", "--help"],
            cwd=Path.cwd(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("install", result.stdout)
        self.assertIn("run", result.stdout)

    def test_main_rejects_missing_command(self):
        with self.assertRaises(SystemExit) as raised:
            main([])
        self.assertEqual(raised.exception.code, 2)

    def test_install_global_with_answers_creates_project_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", "https://example.com/group/repo.git"], cwd=root, check=True)
            old_cwd = Path.cwd()
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", "", ""])

            try:
                os.chdir(root)
                from unittest.mock import patch

                with patch("builtins.input", lambda prompt: next(answers)):
                    exit_code = main(["install", "--runtime", "global"])
            finally:
                os.chdir(old_cwd)

            self.assertEqual(exit_code, 0)
            self.assertTrue((root / ".symphonz" / "config.toml").exists())
            self.assertTrue((root / ".symphonz" / "WORKFLOW.md").exists())


class ShellInstallerTests(unittest.TestCase):
    def test_installed_symphonz_runs_from_prefix_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            (prefix / "bin").mkdir(parents=True)
            (prefix / "lib").mkdir(parents=True)
            subprocess.run(["cp", "bin/symphonz", str(prefix / "bin" / "symphonz")], check=True)
            subprocess.run(["cp", "-R", "symphonz", str(prefix / "lib" / "symphonz")], check=True)

            result = subprocess.run(
                [str(prefix / "bin" / "symphonz"), "version"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("symphonz 0.1.0", result.stdout)

    def test_install_sh_installs_from_local_source_to_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"

            result = subprocess.run(
                ["sh", "install.sh", "--prefix", str(prefix), "--source", str(Path.cwd())],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((prefix / "bin" / "symphonz").exists())
            self.assertTrue((prefix / "lib" / "symphonz" / "cli.py").exists())

            version = subprocess.run(
                [str(prefix / "bin" / "symphonz"), "version"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertIn("symphonz 0.1.0", version.stdout)


if __name__ == "__main__":
    unittest.main()
