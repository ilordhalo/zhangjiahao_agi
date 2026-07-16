from pathlib import Path
import base64
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from symphonz.cli import main
from symphonz.install import (
    InstallConfig,
    collect_install_config,
    configure_dashboard,
    detect_git_defaults,
    ensure_gitignore,
    install_project,
    linear_preflight,
    read_dashboard_auth,
    read_config,
    write_auth_config,
    write_config,
)
from symphonz.runtime import build_run_command, run_installed
from symphonz.workflow import render_workflow


class ConfigTests(unittest.TestCase):
    def test_auth_config_is_private_hashed_and_gitignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            password = "dashboard-password"
            path = root / ".symphonz" / "auth.toml"

            write_auth_config(path, password)
            ensure_gitignore(root)

            content = path.read_text()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertRegex(content, r'algorithm = "(?:scrypt-v1|pbkdf2-sha256-v1)"')
            self.assertNotIn(password, content)
            auth = read_dashboard_auth(root)
            self.assertTrue(auth.password_record.password_hash)
            self.assertTrue(auth.session_secret)
            self.assertIn(".symphonz/auth.toml", (root / ".gitignore").read_text())

    def test_read_dashboard_auth_rejects_corrupt_fields(self):
        corrupt_values = {
            "unsupported algorithm": ("algorithm", "pbkdf2"),
            "invalid salt base64": ("salt", "!invalid"),
            "invalid session secret base64": ("session_secret", "!invalid"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".symphonz" / "auth.toml"
            write_auth_config(path, "correct")
            original = path.read_text()

            for label, (field, value) in corrupt_values.items():
                with self.subTest(label=label):
                    lines = [f'{field} = "{value}"' if line.startswith(f"{field} = ") else line for line in original.splitlines()]
                    path.write_text("\n".join(lines) + "\n")
                    os.chmod(path, 0o600)
                    with self.assertRaisesRegex(RuntimeError, "configure-dashboard"):
                        read_dashboard_auth(root)

            encoded_values = {
                "short salt": ("salt", base64.b64encode(b"s" * 15).decode("ascii")),
                "short password hash": ("password_hash", base64.b64encode(b"h" * 31).decode("ascii")),
                "short session secret": ("session_secret", base64.b64encode(b"k" * 31).decode("ascii")),
            }
            for label, (field, value) in encoded_values.items():
                with self.subTest(label=label):
                    lines = [f'{field} = "{value}"' if line.startswith(f"{field} = ") else line for line in original.splitlines()]
                    path.write_text("\n".join(lines) + "\n")
                    os.chmod(path, 0o600)
                    with self.assertRaisesRegex(RuntimeError, "configure-dashboard"):
                        read_dashboard_auth(root)

    def test_read_dashboard_auth_rejects_invalid_utf8_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".symphonz" / "auth.toml"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"[auth]\nalgorithm = \"\xff\"\n")
            os.chmod(path, 0o600)

            with self.assertRaisesRegex(RuntimeError, "configure-dashboard"):
                read_dashboard_auth(root)

    def test_read_dashboard_auth_rejects_scrypt_when_runtime_has_no_scrypt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".symphonz" / "auth.toml"
            path.parent.mkdir(parents=True)
            path.write_text(
                "\n".join(
                    [
                        "[auth]",
                        'algorithm = "scrypt-v1"',
                        f'salt = "{base64.b64encode(b"s" * 16).decode("ascii")}"',
                        f'password_hash = "{base64.b64encode(b"h" * 32).decode("ascii")}"',
                        f'session_secret = "{base64.b64encode(b"k" * 32).decode("ascii")}"',
                        "",
                    ]
                )
            )
            os.chmod(path, 0o600)

            with patch("symphonz.service.auth.hashlib.scrypt", None, create=True):
                with self.assertRaisesRegex(RuntimeError, "scrypt.*unavailable"):
                    read_dashboard_auth(root)

    def test_read_dashboard_auth_rejects_non_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".symphonz" / "auth.toml"
            write_auth_config(path, "correct")
            os.chmod(path, 0o640)

            with self.assertRaisesRegex(RuntimeError, "0600"):
                read_dashboard_auth(root)

    def test_auth_config_read_and_write_reject_destination_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".symphonz" / "auth.toml"
            write_auth_config(path, "correct")
            target = root / "target.toml"
            path.replace(target)
            path.symlink_to(target)
            original = target.read_text()

            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                read_dashboard_auth(root)
            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                write_auth_config(path, "replacement")

            self.assertEqual(target.read_text(), original)

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_auth_config_read_and_write_reject_symlinked_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            (root / ".symphonz").symlink_to(outside, target_is_directory=True)
            path = root / ".symphonz" / "auth.toml"

            with self.assertRaisesRegex(RuntimeError, "parent directory.*symbolic link"):
                write_auth_config(path, "replacement")
            self.assertFalse((outside / "auth.toml").exists())

            (outside / "auth.toml").write_text("not-safe")
            os.chmod(outside / "auth.toml", 0o600)
            with self.assertRaisesRegex(RuntimeError, "parent directory.*symbolic link"):
                read_dashboard_auth(root)

    def test_auth_config_read_and_write_reject_non_regular_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".symphonz" / "auth.toml"
            path.mkdir(parents=True)

            with self.assertRaisesRegex(RuntimeError, "regular file"):
                read_dashboard_auth(root)
            with self.assertRaisesRegex(RuntimeError, "regular file"):
                write_auth_config(path, "replacement")

    def test_write_auth_config_atomically_replaces_with_private_synced_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".symphonz" / "auth.toml"
            path.parent.mkdir(parents=True)
            path.write_text("old-config")
            os.chmod(path, 0o644)
            real_replace = os.replace
            observed = {}

            def inspect_replace(source, destination, **kwargs):
                source_dir_fd = kwargs.get("src_dir_fd")
                destination_dir_fd = kwargs.get("dst_dir_fd")
                if source_dir_fd is None:
                    source_path = Path(source)
                    observed["same_directory"] = source_path.parent == path.parent
                    observed["temporary_mode"] = source_path.stat().st_mode & 0o777
                    observed["destination_before_replace"] = Path(destination).read_text()
                else:
                    observed["same_directory"] = source_dir_fd == destination_dir_fd
                    observed["temporary_mode"] = os.stat(source, dir_fd=source_dir_fd).st_mode & 0o777
                    destination_descriptor = os.open(destination, os.O_RDONLY, dir_fd=destination_dir_fd)
                    try:
                        observed["destination_before_replace"] = os.read(destination_descriptor, 100).decode()
                    finally:
                        os.close(destination_descriptor)
                real_replace(source, destination, **kwargs)

            with (
                patch("symphonz.install.os.replace", side_effect=inspect_replace) as replace,
                patch("symphonz.install.os.fsync", wraps=os.fsync) as fsync,
            ):
                write_auth_config(path, "replacement")

            replace.assert_called_once()
            self.assertGreaterEqual(fsync.call_count, 2)
            self.assertEqual(observed["same_directory"], True)
            self.assertEqual(observed["temporary_mode"], 0o600)
            self.assertEqual(observed["destination_before_replace"], "old-config")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(hasattr(os, "O_DIRECTORY") and hasattr(os, "symlink"), "dir_fd APIs unavailable")
    def test_write_auth_config_stays_in_pinned_parent_when_parent_path_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".symphonz" / "auth.toml"
            write_auth_config(path, "original")
            original = path.read_text()
            pinned_parent = root / ".symphonz-pinned"
            outside = root / "outside"
            outside.mkdir()
            real_replace = os.replace
            swapped = False

            def swap_parent_then_replace(source, destination, **kwargs):
                nonlocal swapped
                if not swapped:
                    os.rename(path.parent, pinned_parent)
                    path.parent.symlink_to(outside, target_is_directory=True)
                    swapped = True
                return real_replace(source, destination, **kwargs)

            with patch("symphonz.install.os.replace", side_effect=swap_parent_then_replace):
                try:
                    write_auth_config(path, "replacement")
                except OSError as error:
                    self.fail(f"pinned parent replacement failed: {error}")

            self.assertTrue(swapped)
            self.assertNotEqual((pinned_parent / "auth.toml").read_text(), original)
            self.assertFalse((outside / "auth.toml").exists())

    def test_write_auth_config_replace_failure_preserves_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".symphonz" / "auth.toml"
            path.parent.mkdir(parents=True)
            path.write_text("old-config")

            with patch("symphonz.install.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    write_auth_config(path, "replacement")

            self.assertEqual(path.read_text(), "old-config")
            self.assertEqual(list(path.parent.glob(".auth.toml.*.tmp")), [])

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

    def test_write_config_serializes_dashboard_values_as_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            config = InstallConfig(
                runtime_mode="embedded",
                runtime_command="symphonz-internal",
                linear_api_key_env="LINEAR_API_KEY",
                linear_project_slug="project",
                git_provider="github",
                repo_url="https://github.com/example/project.git",
                base_branch="main",
                mr_target="main",
                gitlab_base_url="",
                workspace_root=".symphonz/workspace",
                logs_root=".symphonz/logs",
                dashboard_host="0.0.0.0",
                dashboard_port=4100,
                dashboard_public_base_url="http://192.0.2.10:4100",
                dashboard_username="operator",
                dashboard_session_days=14,
            )

            write_config(path, config)

            parsed = read_config(path)
            self.assertEqual(
                parsed["dashboard"],
                {
                    "host": "0.0.0.0",
                    "port": "4100",
                    "public_base_url": "http://192.0.2.10:4100",
                    "username": "operator",
                    "session_days": "14",
                },
            )

    def test_configure_dashboard_preserves_other_config_bytes_and_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            symphonz = root / ".symphonz"
            symphonz.mkdir()
            config_path = symphonz / "config.toml"
            original_prefix = (
                "# preserve this comment\n"
                "[linear]\n"
                "api_key_env = \"LINEAR_API_KEY\"\n"
                "project_slug = \"team\"\n\n"
                "[dashboard]\n"
                "host = \"127.0.0.1\"\n"
                "port = \"4000\"\n"
                "public_base_url = \"http://127.0.0.1:4000\"\n"
                "username = \"old\"\n"
                "session_days = \"30\"\n\n"
            )
            original_suffix = (
                "[git]\n"
                "provider = \"gitlab\"\n"
                "remote = \"ssh://git.example.test/team/repo.git\"\n"
                "# preserve suffix comment\n"
            )
            config_path.write_text(original_prefix + original_suffix)
            workflow = symphonz / "WORKFLOW.md"
            workflow.write_text("project-specific workflow\n")

            with (
                patch("symphonz.install.run_git", side_effect=AssertionError("Git must not run")),
                patch("symphonz.install.linear_preflight", side_effect=AssertionError("Linear must not run")),
                patch("symphonz.workflow.write_workflow", side_effect=AssertionError("workflow must not change")),
            ):
                configure_dashboard(
                    root,
                    host="0.0.0.0",
                    port=4200,
                    public_base_url="http://192.0.2.20:4200",
                    username="admin",
                    password="migration-secret",
                    session_days=9,
                )

            content = config_path.read_text()
            self.assertTrue(content.startswith("# preserve this comment\n[linear]\n"))
            self.assertTrue(content.endswith(original_suffix))
            self.assertEqual(workflow.read_text(), "project-specific workflow\n")
            self.assertNotIn("migration-secret", content)
            self.assertEqual(read_config(config_path)["git"]["remote"], "ssh://git.example.test/team/repo.git")
            self.assertEqual(read_config(config_path)["dashboard"]["port"], "4200")
            self.assertEqual((symphonz / "auth.toml").stat().st_mode & 0o777, 0o600)

    def test_configure_dashboard_adds_section_and_uses_environment_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".symphonz" / "config.toml"
            config_path.parent.mkdir()
            legacy = '[linear]\nproject_slug = "legacy"\n\n[git]\nprovider = "github"\n'
            config_path.write_text(legacy)

            configure_dashboard(
                root,
                host="127.0.0.2",
                port=4300,
                public_base_url="http://127.0.0.2:4300",
                username="operator",
                session_days=5,
                environ={"SYMPHONZ_DASHBOARD_PASSWORD": "environment-secret"},
                getpass_func=lambda prompt: self.fail("getpass should not be called"),
            )

            content = config_path.read_text()
            self.assertTrue(content.startswith(legacy))
            self.assertEqual(read_config(config_path)["dashboard"]["host"], "127.0.0.2")
            self.assertNotIn("environment-secret", content)
            self.assertIn(".symphonz/artifacts/", (root / ".gitignore").read_text())

    def test_configure_dashboard_requires_a_secure_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".symphonz" / "config.toml"
            config_path.parent.mkdir()
            config_path.write_text('[linear]\nproject_slug = "legacy"\n')

            with self.assertRaisesRegex(RuntimeError, "dashboard password is required"):
                configure_dashboard(
                    root,
                    environ={},
                    getpass_func=lambda prompt: "",
                )

            self.assertEqual(config_path.read_text(), '[linear]\nproject_slug = "legacy"\n')
            self.assertFalse((root / ".symphonz" / "auth.toml").exists())


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
            answers = iter(
                [
                    "LINEAR_API_KEY",
                    "project-slug",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "0.0.0.0",
                    "4100",
                    "http://192.0.2.30:4100",
                    "operator",
                    "12",
                ]
            )

            config = collect_install_config(root, False, input_func=lambda prompt: next(answers))

            self.assertEqual(config.runtime_mode, "embedded")
            self.assertEqual(config.runtime_command, "symphonz-internal")
            self.assertEqual(config.linear_project_slug, "project-slug")
            self.assertEqual(config.git_provider, "gitlab")
            self.assertEqual(config.repo_url, "https://example.com/group/repo.git")
            self.assertEqual(config.base_branch, "main")
            self.assertEqual(config.mr_target, "main")
            self.assertEqual(config.dashboard_host, "0.0.0.0")
            self.assertEqual(config.dashboard_port, 4100)
            self.assertEqual(config.dashboard_public_base_url, "http://192.0.2.30:4100")
            self.assertEqual(config.dashboard_username, "operator")
            self.assertEqual(config.dashboard_session_days, 12)

    def test_collect_install_config_accepts_github_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/example/project.git"],
                cwd=root,
                check=True,
            )
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", "", "", "", "", "", ""])

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
                environ={
                    "SYMPHONZ_LINEAR_PROJECT": "env-project",
                    "SYMPHONZ_DASHBOARD_HOST": "0.0.0.0",
                    "SYMPHONZ_DASHBOARD_PORT": "4400",
                    "SYMPHONZ_DASHBOARD_PUBLIC_BASE_URL": "http://192.0.2.40:4400",
                    "SYMPHONZ_DASHBOARD_USERNAME": "env-admin",
                    "SYMPHONZ_DASHBOARD_SESSION_DAYS": "21",
                },
            )

            self.assertEqual(config.linear_project_slug, "env-project")
            self.assertEqual(config.git_provider, "github")
            self.assertEqual(config.dashboard_host, "0.0.0.0")
            self.assertEqual(config.dashboard_port, 4400)
            self.assertEqual(config.dashboard_public_base_url, "http://192.0.2.40:4400")
            self.assertEqual(config.dashboard_username, "env-admin")
            self.assertEqual(config.dashboard_session_days, 21)

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

    def test_collect_install_config_rejects_explicit_zero_dashboard_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/example/project.git"],
                cwd=root,
                check=True,
            )

            for field in ("dashboard_port", "dashboard_session_days"):
                with self.subTest(field=field):
                    with self.assertRaisesRegex(RuntimeError, "positive integer"):
                        collect_install_config(
                            root,
                            True,
                            linear_project_slug="project",
                            **{field: 0},
                        )


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
        review_index = rendered.index("Create or update a review request")
        report_index = rendered.index("Call `symphonz_report`")
        human_review_index = rendered.index("Move the issue to `Human Review`", report_index)
        self.assertLess(review_index, report_index)
        self.assertLess(report_index, human_review_index)
        self.assertIn("A missing or failed report publication is a publication blocker", rendered)
        self.assertIn("report URL and review request URL", rendered)
        self.assertIn("### Implementation Report", rendered)

    def test_default_workflow_template_contains_no_personal_values(self):
        template = Path("WORKFLOW.md").read_text()

        for value in self.personal_values:
            self.assertNotIn(value, template)

    def test_install_project_creates_expected_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", "https://example.com/group/repo.git"], cwd=root, check=True)
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", "", "", "", "", "", "", ""])

            install_project(
                project_root=root,
                assume_yes=False,
                skip_linear_preflight=True,
                input_func=lambda prompt: next(answers),
                getpass_func=lambda prompt: "dashboard-secret",
            )

            self.assertTrue((root / ".symphonz" / "WORKFLOW.md").exists())
            self.assertTrue((root / ".symphonz" / "config.toml").exists())
            self.assertTrue((root / ".symphonz" / "workspace").is_dir())
            self.assertTrue((root / ".symphonz" / "logs").is_dir())
            self.assertTrue((root / ".symphonz" / "artifacts").is_dir())
            self.assertTrue((root / ".symphonz" / "auth.toml").is_file())
            self.assertEqual((root / ".symphonz" / "auth.toml").stat().st_mode & 0o777, 0o600)
            self.assertFalse((root / ".symphonz" / "runtime").exists())
            gitignore = (root / ".gitignore").read_text()
            for ignored in (
                ".symphonz/artifacts/",
                ".symphonz/logs/",
                ".symphonz/workspace/",
                ".symphonz/auth.toml",
            ):
                self.assertIn(ignored, gitignore)

            config = read_config(root / ".symphonz" / "config.toml")
            self.assertEqual(config["dashboard"]["host"], "127.0.0.1")
            self.assertEqual(config["dashboard"]["port"], "4000")
            self.assertNotIn("dashboard-secret", (root / ".symphonz" / "config.toml").read_text())

            rendered = (root / ".symphonz" / "WORKFLOW.md").read_text()
            for value in self.personal_values:
                self.assertNotIn(value, rendered)

    def test_install_project_always_uses_internal_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/project.git"], cwd=root, check=True)
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", "", "", "", "", "", ""])

            install_project(
                project_root=root,
                assume_yes=False,
                skip_linear_preflight=True,
                input_func=lambda prompt: next(answers),
                getpass_func=lambda prompt: "dashboard-secret",
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

            self.assertEqual(
                command,
                [
                    "symphonz",
                    "service",
                    ".symphonz/WORKFLOW.md",
                    "--logs-root",
                    ".symphonz/logs",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "4000",
                    "--public-base-url",
                    "http://127.0.0.1:4000",
                    "--dashboard-username",
                    "admin",
                    "--session-days",
                    "30",
                ],
            )
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
                [
                    "symphonz",
                    "service",
                    ".symphonz/WORKFLOW.md",
                    "--logs-root",
                    ".symphonz/logs",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "4100",
                    "--public-base-url",
                    "http://127.0.0.1:4000",
                    "--dashboard-username",
                    "admin",
                    "--session-days",
                    "30",
                ],
            )

    def test_build_run_command_uses_configured_dashboard_and_temporary_overrides(self):
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
                dashboard_host="0.0.0.0",
                dashboard_port=4000,
                dashboard_public_base_url="http://192.0.2.50:4000",
                dashboard_username="operator",
                dashboard_session_days=8,
            )
            write_config(root / ".symphonz" / "config.toml", config)

            command, _env = build_run_command(root, host="127.0.0.2", port=4500)

            self.assertEqual(command[command.index("--host") + 1], "127.0.0.2")
            self.assertEqual(command[command.index("--port") + 1], "4500")
            self.assertEqual(command[command.index("--public-base-url") + 1], "http://192.0.2.50:4000")
            self.assertEqual(command[command.index("--dashboard-username") + 1], "operator")
            self.assertEqual(command[command.index("--session-days") + 1], "8")

    def test_run_installed_propagates_configured_dashboard_and_overrides(self):
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
                dashboard_host="0.0.0.0",
                dashboard_port=4000,
                dashboard_public_base_url="http://192.0.2.60:4000",
                dashboard_username="operator",
                dashboard_session_days=6,
            )
            write_config(root / ".symphonz" / "config.toml", config)

            with patch("symphonz.service.runner.run_service", return_value=17) as service:
                exit_code = run_installed(project_root=root, host="127.0.0.3", port=4600)

            self.assertEqual(exit_code, 17)
            self.assertEqual(service.call_args.kwargs["host"], "127.0.0.3")
            self.assertEqual(service.call_args.kwargs["port"], 4600)
            self.assertEqual(service.call_args.kwargs["public_base_url"], "http://192.0.2.60:4000")
            self.assertEqual(service.call_args.kwargs["dashboard_username"], "operator")
            self.assertEqual(service.call_args.kwargs["session_days"], 6)

    def test_legacy_config_stays_loopback_only_and_keeps_explicit_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".symphonz" / "config.toml"
            path.parent.mkdir()
            path.write_text(
                "[runtime]\nmode = \"embedded\"\ncommand = \"symphonz-internal\"\n\n"
                "[linear]\napi_key_env = \"LINEAR_API_KEY\"\nproject_slug = \"project\"\n\n"
                "[git]\nprovider = \"github\"\nremote = \"https://github.com/example/project.git\"\n"
                "base_branch = \"main\"\nmr_target = \"main\"\ngitlab_base_url = \"\"\n\n"
                "[workspace]\nroot = \".symphonz/workspace\"\n\n"
                "[logs]\nroot = \".symphonz/logs\"\n"
            )

            command, _env = build_run_command(root, port=4700)

            self.assertEqual(
                command,
                [
                    "symphonz",
                    "service",
                    ".symphonz/WORKFLOW.md",
                    "--logs-root",
                    ".symphonz/logs",
                    "--port",
                    "4700",
                ],
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
        self.assertEqual(output.getvalue(), "symphonz 0.4.0\n")

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
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", "", "", "", "", "", "", ""])

            try:
                os.chdir(root)
                from unittest.mock import patch

                with (
                    patch("builtins.input", lambda prompt: next(answers)),
                    patch("getpass.getpass", return_value="dashboard-secret") as password_prompt,
                ):
                    exit_code = main(["install", "--skip-linear-preflight"])
            finally:
                os.chdir(old_cwd)

            self.assertEqual(exit_code, 0)
            self.assertTrue((root / ".symphonz" / "config.toml").exists())
            self.assertTrue((root / ".symphonz" / "WORKFLOW.md").exists())
            password_prompt.assert_called_once()
            self.assertNotIn("dashboard-secret", (root / ".symphonz" / "config.toml").read_text())

    def test_install_yes_accepts_complete_command_line_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", "https://example.com/group/repo.git"], cwd=root, check=True)
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(
                    os.environ,
                    {
                        "SYMPHONZ_DASHBOARD_HOST": "0.0.0.0",
                        "SYMPHONZ_DASHBOARD_PORT": "4800",
                        "SYMPHONZ_DASHBOARD_PUBLIC_BASE_URL": "http://192.0.2.80:4800",
                        "SYMPHONZ_DASHBOARD_USERNAME": "ci-admin",
                        "SYMPHONZ_DASHBOARD_PASSWORD": "ci-secret",
                        "SYMPHONZ_DASHBOARD_SESSION_DAYS": "15",
                    },
                    clear=False,
                ):
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
            self.assertEqual(config["dashboard"]["host"], "0.0.0.0")
            self.assertEqual(config["dashboard"]["port"], "4800")
            self.assertNotIn("ci-secret", (root / ".symphonz" / "config.toml").read_text())

    def test_install_yes_requires_dashboard_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/example/project.git"],
                cwd=root,
                check=True,
            )
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {"SYMPHONZ_LINEAR_PROJECT": "project"}, clear=True):
                    with self.assertRaisesRegex(RuntimeError, "SYMPHONZ_DASHBOARD_PASSWORD"):
                        main(["install", "--yes", "--skip-linear-preflight"])
            finally:
                os.chdir(old_cwd)

            self.assertFalse((root / ".symphonz" / "config.toml").exists())

    def test_configure_dashboard_cli_dispatches_all_values(self):
        with patch("symphonz.install.configure_dashboard", return_value=Path(".symphonz")) as configure:
            exit_code = main(
                [
                    "configure-dashboard",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "4900",
                    "--public-base-url",
                    "http://192.0.2.90:4900",
                    "--username",
                    "operator",
                    "--session-days",
                    "11",
                ]
            )

        self.assertEqual(exit_code, 0)
        configure.assert_called_once_with(
            host="0.0.0.0",
            port=4900,
            public_base_url="http://192.0.2.90:4900",
            username="operator",
            session_days=11,
        )

    def test_run_cli_dispatches_temporary_host_and_port(self):
        with patch("symphonz.runtime.run_installed", return_value=0) as run:
            exit_code = main(["run", "--host", "127.0.0.2", "--port", "4950"])

        self.assertEqual(exit_code, 0)
        run.assert_called_once_with(print_command=False, host="127.0.0.2", port=4950)

    def test_service_cli_dispatches_dashboard_internals(self):
        with patch("symphonz.service.runner.run_service", return_value=0) as service:
            exit_code = main(
                [
                    "service",
                    ".symphonz/WORKFLOW.md",
                    "--logs-root",
                    ".symphonz/logs",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "4000",
                    "--public-base-url",
                    "http://192.0.2.100:4000",
                    "--dashboard-username",
                    "admin",
                    "--session-days",
                    "7",
                    "--once",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(service.call_args.kwargs["host"], "0.0.0.0")
        self.assertEqual(service.call_args.kwargs["port"], 4000)
        self.assertEqual(service.call_args.kwargs["public_base_url"], "http://192.0.2.100:4000")
        self.assertEqual(service.call_args.kwargs["dashboard_username"], "admin")
        self.assertEqual(service.call_args.kwargs["session_days"], 7)


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
            self.assertIn("symphonz 0.4.0", version.stdout)

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
            self.assertIn("symphonz 0.4.0", result.stdout)

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
            self.assertIn("symphonz 0.4.0", version.stdout)
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

            env = os.environ.copy()
            env.update(
                {
                    "SYMPHONZ_LINEAR_PROJECT": "project-slug",
                    "SYMPHONZ_DASHBOARD_PASSWORD": "installed-cli-secret",
                }
            )
            result = subprocess.run(
                [
                    str(prefix / "bin" / "symphonz"),
                    "install",
                    "--yes",
                    "--skip-linear-preflight",
                ],
                cwd=project,
                env=env,
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
            self.assertEqual(config["dashboard"]["host"], "127.0.0.1")
            self.assertEqual((project / ".symphonz" / "auth.toml").stat().st_mode & 0o777, 0o600)

    def test_repository_exposes_standard_readme_and_staged_installer(self):
        self.assertTrue(Path("README.md").exists())
        script = Path("install.sh").read_text()
        self.assertIn("STAGING_DIR", script)

        readme = Path("README.md").read_text()
        self.assertIn("symphonz configure-dashboard", readme)
        self.assertIn("SYMPHONZ_DASHBOARD_PASSWORD", readme)
        self.assertIn("--host", readme)
        self.assertIn("--port", readme)
        self.assertIn("HTTP", readme)
        self.assertIn("report", readme.lower())


if __name__ == "__main__":
    unittest.main()
