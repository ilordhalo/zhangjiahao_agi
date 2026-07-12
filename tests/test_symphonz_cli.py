from pathlib import Path
import json
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
    linear_preflight,
    read_config,
    write_config,
)
from symphonz.runtime import build_run_command, run_installed
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
    def test_linear_preflight_explains_missing_environment_value(self):
        config = InstallConfig(
            runtime_mode="embedded",
            runtime_command="symphonz-internal",
            linear_api_key_env="LINEAR_TEST_KEY",
            linear_project_slug="project",
            git_provider="github",
            repo_url="https://github.com/example/project.git",
            base_branch="main",
            mr_target="main",
            gitlab_base_url="",
            workspace_root=".symphonz/workspace",
            logs_root=".symphonz/logs",
        )

        message = linear_preflight(config, environ={})

        self.assertIn('export LINEAR_TEST_KEY="<linear-api-key>"', message)

    def test_linear_preflight_uses_configured_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            (fixture / "responses.json").write_text(
                '{"SymphonzInstallPreflight":{"data":{"viewer":{"id":"viewer-1"}}}}'
            )
            config = InstallConfig(
                runtime_mode="embedded",
                runtime_command="symphonz-internal",
                linear_api_key_env="LINEAR_TEST_KEY",
                linear_project_slug="project",
                git_provider="github",
                repo_url="https://github.com/example/project.git",
                base_branch="main",
                mr_target="main",
                gitlab_base_url="",
                workspace_root=".symphonz/workspace",
                logs_root=".symphonz/logs",
            )

            message = linear_preflight(
                config,
                environ={"LINEAR_TEST_KEY": "secret", "SYMPHONZ_LINEAR_ENDPOINT": fixture.as_uri()},
            )

            self.assertIn("Linear connection verified", message)
            request = json.loads((fixture / "requests.jsonl").read_text())
            self.assertEqual(request["operation"], "SymphonzInstallPreflight")

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

            config = collect_install_config(root, False, input_func=lambda prompt: next(answers))

            self.assertEqual(config.runtime_mode, "embedded")
            self.assertEqual(config.runtime_command, "symphonz-internal")
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

            config = collect_install_config(root, False, input_func=lambda prompt: next(answers))

            self.assertEqual(config.git_provider, "github")
            self.assertEqual(config.gitlab_base_url, "")
            self.assertEqual(config.repo_url, "https://github.com/example/project.git")

    def test_collect_install_config_noninteractive_uses_explicit_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://example.com/group/repo.git"],
                cwd=root,
                check=True,
            )

            config = collect_install_config(
                root,
                True,
                linear_project_slug="quality-project",
                git_provider="github",
                repo_url="https://github.com/example/quality.git",
                base_branch="develop",
                mr_target="release",
            )

            self.assertEqual(config.linear_project_slug, "quality-project")
            self.assertEqual(config.git_provider, "github")
            self.assertEqual(config.repo_url, "https://github.com/example/quality.git")
            self.assertEqual(config.base_branch, "develop")
            self.assertEqual(config.mr_target, "release")

    def test_collect_install_config_noninteractive_uses_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/example/project.git"],
                cwd=root,
                check=True,
            )

            config = collect_install_config(
                root,
                True,
                environ={"SYMPHONZ_LINEAR_PROJECT": "env-project"},
            )

            self.assertEqual(config.linear_project_slug, "env-project")
            self.assertEqual(config.git_provider, "github")

    def test_collect_install_config_reports_missing_noninteractive_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/example/project.git"],
                cwd=root,
                check=True,
            )

            with self.assertRaisesRegex(RuntimeError, "--linear-project or SYMPHONZ_LINEAR_PROJECT"):
                collect_install_config(root, True, environ={})


class WorkflowInstallTests(unittest.TestCase):
    personal_values = [
        bytes.fromhex("7a68616e676a696168616f2d6167692d313836613135633839366163").decode(),
        bytes.fromhex("7a68616e676a696168616f2e6d65").decode(),
        bytes.fromhex("696c6f726468616c6f2f7a68616e676a696168616f5f616769").decode(),
    ]

    def make_config(self) -> InstallConfig:
        return InstallConfig(
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

    def test_render_workflow_replaces_project_values(self):
        template = Path("WORKFLOW.md").read_text()

        rendered = render_workflow(template, self.make_config())

        self.assertIn('api_key: $LINEAR_API_KEY', rendered)
        self.assertIn('project_slug: "project-slug"', rendered)
        self.assertIn('workspace:\n  root: .symphonz/workspace', rendered)
        self.assertIn('SYMPHONZ_REPO_URL:?SYMPHONZ_REPO_URL is required', rendered)
        self.assertNotIn('https://example.com/group/repo.git', rendered)
        self.assertIn("Review provider is configured by `SYMPHONZ_GIT_PROVIDER`", rendered)
        self.assertIn("- `Ready to Publish` -> implementation is complete", rendered)
        self.assertIn("- `Done`, `Closed`, `Cancelled`, `Canceled`, `Duplicate` -> terminal", rendered)
        self.assertNotIn("git checkout -B", rendered)

    def test_default_workflow_template_contains_no_personal_values(self):
        template = Path("WORKFLOW.md").read_text()

        for value in self.personal_values:
            self.assertNotIn(value, template)

    def test_install_project_creates_expected_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", "https://example.com/group/repo.git"], cwd=root, check=True)
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", "", ""])

            install_project(
                project_root=root,
                assume_yes=False,
                skip_linear_preflight=True,
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

    def test_install_project_always_uses_internal_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/project.git"], cwd=root, check=True)
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", ""])

            install_project(
                project_root=root,
                assume_yes=False,
                skip_linear_preflight=True,
                input_func=lambda prompt: next(answers),
            )

            config = read_config(root / ".symphonz" / "config.toml")
            self.assertEqual(config["runtime"]["mode"], "embedded")
            self.assertEqual(config["runtime"]["command"], "symphonz-internal")
            self.assertFalse((root / ".symphonz" / "runtime").exists())
            self.assertFalse((root / ".symphonz" / "bin" / "symphony").exists())


class RuntimeTests(unittest.TestCase):
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

    def test_legacy_global_config_is_ignored_and_uses_internal_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = InstallConfig(
                runtime_mode="global",
                runtime_command="symphony",
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

            command, _env = build_run_command(root)

            self.assertEqual(command[0:2], ["symphonz", "service"])

    def test_print_command_includes_shell_quoted_runtime_environment(self):
        from contextlib import redirect_stdout
        from io import StringIO

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = InstallConfig(
                runtime_mode="embedded",
                runtime_command="symphonz-internal",
                linear_api_key_env="LINEAR_API_KEY",
                linear_project_slug="project-slug",
                git_provider="github",
                repo_url="https://github.com/example/project with spaces.git",
                base_branch="main",
                mr_target="main",
                gitlab_base_url="",
                workspace_root=".symphonz/workspace",
                logs_root=".symphonz/logs",
            )
            write_config(root / ".symphonz" / "config.toml", config)
            output = StringIO()

            with redirect_stdout(output):
                exit_code = run_installed(print_command=True, project_root=root)

            self.assertEqual(exit_code, 0)
            printed = output.getvalue()
            self.assertIn("SYMPHONZ_REPO_URL='https://github.com/example/project with spaces.git'", printed)
            self.assertIn("SYMPHONZ_GIT_PROVIDER=github", printed)
            self.assertIn("symphonz service .symphonz/WORKFLOW.md", printed)


class CliSmokeTests(unittest.TestCase):
    def test_main_version_prints_package_version(self):
        from contextlib import redirect_stdout
        from io import StringIO

        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["version"])

        self.assertEqual(exit_code, 0)
        self.assertIn("symphonz 0.3.1", output.getvalue())

    def test_install_rejects_removed_runtime_option(self):
        with self.assertRaises(SystemExit) as raised:
            main(["install", "--runtime", "global"])
        self.assertEqual(raised.exception.code, 2)

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

    def test_install_with_answers_creates_project_files(self):
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
                    exit_code = main(["install", "--skip-linear-preflight"])
            finally:
                os.chdir(old_cwd)

            self.assertEqual(exit_code, 0)
            self.assertTrue((root / ".symphonz" / "config.toml").exists())
            self.assertTrue((root / ".symphonz" / "WORKFLOW.md").exists())

    def test_install_yes_accepts_complete_command_line_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", "https://example.com/group/repo.git"], cwd=root, check=True)
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                exit_code = main(
                    [
                        "install",
                        "--yes",
                        "--skip-linear-preflight",
                        "--linear-project",
                        "quality-project",
                        "--git-provider",
                        "github",
                        "--repo-url",
                        "https://github.com/example/quality.git",
                        "--base-branch",
                        "develop",
                        "--target-branch",
                        "release",
                    ]
                )
            finally:
                os.chdir(old_cwd)

            self.assertEqual(exit_code, 0)
            config = read_config(root / ".symphonz" / "config.toml")
            self.assertEqual(config["linear"]["project_slug"], "quality-project")
            self.assertEqual(config["git"]["provider"], "github")
            self.assertEqual(config["git"]["base_branch"], "develop")
            self.assertEqual(config["git"]["mr_target"], "release")


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
            self.assertTrue((home / ".symphonz" / "current" / "lib" / "symphonz" / "cli.py").exists())
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
            self.assertIn("symphonz 0.3.1", version.stdout)

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
            self.assertIn("symphonz 0.3.1", result.stdout)

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
            self.assertTrue((prefix / "current" / "lib" / "symphonz" / "cli.py").exists())
            self.assertTrue((prefix / "current").is_symlink())
            first_release = (prefix / "current").resolve()

            upgrade = subprocess.run(
                ["sh", "install.sh", "--prefix", str(prefix), "--source", str(Path.cwd())],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(upgrade.returncode, 0, upgrade.stderr)
            self.assertNotEqual((prefix / "current").resolve(), first_release)
            self.assertTrue((first_release / "lib" / "symphonz" / "cli.py").exists())

            version = subprocess.run(
                [str(prefix / "bin" / "symphonz"), "version"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertIn("symphonz 0.3.1", version.stdout)
            self.assertTrue((prefix / "current" / "lib" / "WORKFLOW.md").exists())
            self.assertTrue((prefix / "current" / "lib" / "symphonz" / "service" / "runner.py").exists())

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
                [
                    str(prefix / "bin" / "symphonz"),
                    "install",
                    "--skip-linear-preflight",
                ],
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

    def test_repository_exposes_standard_readme_and_staged_installer(self):
        self.assertTrue(Path("README.md").exists())
        script = Path("install.sh").read_text()
        self.assertIn("STAGING_DIR", script)


if __name__ == "__main__":
    unittest.main()
