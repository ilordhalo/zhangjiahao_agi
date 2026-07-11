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
                linear_project_slug="REPLACE_WITH_LINEAR_PROJECT_SLUG",
                git_provider="gitlab",
                repo_url="https://example.com/your-org/your-repo.git",
                base_branch="main",
                mr_target="main",
                gitlab_base_url="https://gitlab.example.com",
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
            self.assertEqual(parsed["linear"]["project_slug"], "REPLACE_WITH_LINEAR_PROJECT_SLUG")
            self.assertEqual(parsed["git"]["gitlab_base_url"], "https://gitlab.example.com")


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

    def test_collect_install_config_accepts_github_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/example/project.git"],
                cwd=root,
                check=True,
            )
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", ""])

            config = collect_install_config(root, "global", False, input_func=lambda prompt: next(answers))

            self.assertEqual(config.git_provider, "github")
            self.assertEqual(config.gitlab_base_url, "")
            self.assertEqual(config.repo_url, "https://github.com/example/project.git")


class WorkflowInstallTests(unittest.TestCase):
    personal_values = [
        bytes.fromhex("7a68616e676a696168616f2d6167692d313836613135633839366163").decode(),
        bytes.fromhex("7a68616e676a696168616f2e6d65").decode(),
        bytes.fromhex("696c6f726468616c6f2f7a68616e676a696168616f5f616769").decode(),
    ]

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
        self.assertIn("Review provider is configured by `SYMPHONZ_GIT_PROVIDER`", rendered)
        self.assertIn("- `Done` -> implementation is considered complete", rendered)

    def test_default_workflow_template_contains_no_personal_values(self):
        template = Path("WORKFLOW.md").read_text()

        for value in self.personal_values:
            self.assertNotIn(value, template)

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

            rendered = (root / ".symphonz" / "WORKFLOW.md").read_text()
            for value in self.personal_values:
                self.assertNotIn(value, rendered)

    def test_install_project_embedded_uses_internal_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/project.git"], cwd=root, check=True)
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", ""])

            install_project(
                project_root=root,
                runtime_mode="embedded",
                assume_yes=False,
                skip_runtime_download=False,
                input_func=lambda prompt: next(answers),
            )

            config = read_config(root / ".symphonz" / "config.toml")
            self.assertEqual(config["runtime"]["mode"], "embedded")
            self.assertEqual(config["runtime"]["command"], "symphonz-internal")
            self.assertFalse((root / ".symphonz" / "runtime").exists())
            self.assertFalse((root / ".symphonz" / "bin" / "symphony").exists())


class RuntimeTests(unittest.TestCase):
    def test_install_embedded_runtime_is_noop_because_runtime_is_builtin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            install_embedded_runtime(root, skip_download=True)

            self.assertFalse((root / ".symphonz" / "runtime").exists())
            self.assertFalse((root / ".symphonz" / "bin" / "symphony").exists())

    def test_build_run_command_embedded_exports_expected_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = InstallConfig(
                runtime_mode="embedded",
                runtime_command="symphonz-internal",
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

            self.assertEqual(command, ["symphonz", "service", ".symphonz/WORKFLOW.md", "--logs-root", ".symphonz/logs"])
            self.assertEqual(env["SYMPHONZ_REPO_URL"], "https://example.com/group/repo.git")
            self.assertEqual(env["SYMPHONZ_BASE_BRANCH"], "main")
            self.assertEqual(env["SYMPHONZ_MR_TARGET"], "main")
            self.assertEqual(env["SYMPHONZ_GIT_PROVIDER"], "gitlab")
            self.assertEqual(env["GITLAB_BASE_URL"], "https://gitlab.example.com")

    def test_build_run_command_with_port_includes_dashboard_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = InstallConfig(
                runtime_mode="embedded",
                runtime_command="symphonz-internal",
                linear_api_key_env="LINEAR_API_KEY",
                linear_project_slug="project-slug",
                git_provider="github",
                repo_url="https://github.com/example/project.git",
                base_branch="main",
                mr_target="main",
                gitlab_base_url="",
                workspace_root=".symphonz/workspace",
                logs_root=".symphonz/logs",
            )
            write_config(root / ".symphonz" / "config.toml", config)

            command, _env = build_run_command(root, port=4100)

            self.assertEqual(
                command,
                ["symphonz", "service", ".symphonz/WORKFLOW.md", "--logs-root", ".symphonz/logs", "--port", "4100"],
            )


class CliSmokeTests(unittest.TestCase):
    def test_main_version_prints_package_version(self):
        from contextlib import redirect_stdout
        from io import StringIO

        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["version"])

        self.assertEqual(exit_code, 0)
        self.assertIn("symphonz 0.2.1", output.getvalue())

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
        self.assertNotIn("service", result.stdout)

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
    def test_install_sh_documents_direct_curl_cli_install(self):
        script = Path("install.sh").read_text()

        self.assertIn('DEFAULT_REPO_URL="https://github.com/ilordhalo/zhangjiahao_agi"', script)
        self.assertIn(
            "curl -fsSL https://raw.githubusercontent.com/ilordhalo/zhangjiahao_agi/main/install.sh | sh",
            script,
        )

    def test_install_sh_installs_cli_into_writable_path_without_project_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path.cwd()
            home = Path(tmp) / "home"
            path_bin = home / ".local" / "bin"
            project = Path(tmp) / "project"
            home.mkdir()
            path_bin.mkdir(parents=True)
            project.mkdir()
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["PATH"] = f"{path_bin}:/usr/bin:/bin"

            result = subprocess.run(
                ["sh", str(repo / "install.sh"), "--source", str(repo)],
                cwd=project,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((path_bin / "symphonz").exists())
            self.assertTrue((home / ".symphonz" / "lib" / "symphonz" / "cli.py").exists())
            self.assertFalse((project / ".symphonz").exists())

            version = subprocess.run(
                ["symphonz", "version"],
                cwd=project,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertIn("symphonz 0.2.1", version.stdout)

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
            self.assertIn("symphonz 0.2.1", result.stdout)

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
            self.assertIn("symphonz 0.2.1", version.stdout)
            self.assertTrue((prefix / "lib" / "WORKFLOW.md").exists())
            self.assertTrue((prefix / "lib" / "symphonz" / "service" / "runner.py").exists())

    def test_installed_cli_can_install_project_from_packaged_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            project = Path(tmp) / "project"
            project.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=project, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/example/project.git"],
                cwd=project,
                check=True,
            )
            install = subprocess.run(
                ["sh", "install.sh", "--prefix", str(prefix), "--source", str(Path.cwd())],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr)

            answers = "\n".join(["LINEAR_API_KEY", "project-slug", "", "", "", ""]) + "\n"
            result = subprocess.run(
                [str(prefix / "bin" / "symphonz"), "install", "--runtime", "global"],
                cwd=project,
                input=answers,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((project / ".symphonz" / "WORKFLOW.md").exists())
            config = read_config(project / ".symphonz" / "config.toml")
            self.assertEqual(config["git"]["provider"], "github")
            self.assertEqual(config["git"]["gitlab_base_url"], "")


if __name__ == "__main__":
    unittest.main()
