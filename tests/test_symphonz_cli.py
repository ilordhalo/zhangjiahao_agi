from pathlib import Path
import base64
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
import stat
import subprocess
import tempfile
import unittest
import warnings
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
from symphonz.service.auth import verify_password
from symphonz.workflow import render_workflow


def _legacy_config_content() -> str:
    return (
        "[runtime]\nmode = \"embedded\"\ncommand = \"symphonz-internal\"\n\n"
        "[linear]\napi_key_env = \"LINEAR_API_KEY\"\nproject_slug = \"project\"\n\n"
        "[git]\nprovider = \"github\"\nremote = \"https://github.com/example/project.git\"\n"
        "base_branch = \"main\"\nmr_target = \"main\"\ngitlab_base_url = \"\"\n\n"
        "[workspace]\nroot = \".symphonz/workspace\"\n\n"
        "[logs]\nroot = \".symphonz/logs\"\n"
    )


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

    def test_gitignore_protected_rules_override_existing_negations_and_are_idempotent(self):
        protected = [
            ".symphonz/artifacts/",
            ".symphonz/logs/",
            ".symphonz/workspace/",
            ".symphonz/auth.toml",
        ]
        negations = [
            "!.symphonz/artifacts/**",
            "!.symphonz/logs/**",
            "!.symphonz/workspace/**",
            "!.symphonz/auth.toml",
        ]
        files = [
            ".symphonz/artifacts/report.json",
            ".symphonz/logs/service.log",
            ".symphonz/workspace/task/state.json",
            ".symphonz/auth.toml",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / ".gitignore").write_text("\n".join([*protected, *negations]) + "\n")
            for relative in files:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("private\n")

            ensure_gitignore(root)
            first = (root / ".gitignore").read_text()
            ensure_gitignore(root)

            self.assertEqual((root / ".gitignore").read_text(), first)
            self.assertEqual(first.splitlines()[-len(protected) :], protected)
            for relative in files:
                with self.subTest(relative=relative):
                    subprocess.run(
                        ["git", "check-ignore", "-q", "--", relative],
                        cwd=root,
                        check=True,
                    )

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

    def test_write_auth_config_post_replace_fsync_failure_restores_previous_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".symphonz" / "auth.toml"
            write_auth_config(path, "old-password")
            old_bytes = path.read_bytes()
            old_auth = read_dashboard_auth(root)
            real_fsync = os.fsync
            failed = False

            def fail_first_directory_fsync(descriptor):
                nonlocal failed
                if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not failed:
                    failed = True
                    raise OSError("post-replace directory fsync failed")
                return real_fsync(descriptor)

            with patch("symphonz.install.os.fsync", side_effect=fail_first_directory_fsync):
                with self.assertRaisesRegex(OSError, "post-replace directory fsync failed"):
                    write_auth_config(path, "new-password")

            restored = read_dashboard_auth(root)
            self.assertTrue(failed)
            self.assertEqual(path.read_bytes(), old_bytes)
            self.assertEqual(restored.session_secret, old_auth.session_secret)
            self.assertTrue(verify_password("old-password", restored.password_record))
            self.assertFalse(verify_password("new-password", restored.password_record))

    def test_write_auth_config_post_replace_fsync_failure_removes_new_auth_without_prior_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".symphonz" / "auth.toml"
            real_fsync = os.fsync
            failed = False

            def fail_first_directory_fsync(descriptor):
                nonlocal failed
                if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not failed:
                    failed = True
                    raise OSError("post-replace directory fsync failed")
                return real_fsync(descriptor)

            with patch("symphonz.install.os.fsync", side_effect=fail_first_directory_fsync):
                with self.assertRaisesRegex(OSError, "post-replace directory fsync failed"):
                    write_auth_config(path, "new-password")

            self.assertTrue(failed)
            self.assertFalse(path.exists())
            self.assertEqual(list(path.parent.glob(".auth.toml.*.tmp")), [])

    def test_write_auth_config_cleanup_failure_does_not_mask_replace_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".symphonz" / "auth.toml"
            path.parent.mkdir(parents=True)
            path.write_text("old-config")

            with (
                patch("symphonz.install.os.replace", side_effect=OSError("replace failed")),
                patch("symphonz.install.os.unlink", side_effect=OSError("temporary cleanup failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "replace failed.*temporary cleanup failed") as raised:
                    write_auth_config(path, "replacement")

            self.assertIsInstance(raised.exception.__cause__, OSError)
            self.assertIn("replace failed", str(raised.exception.__cause__))
            self.assertEqual(path.read_text(), "old-config")

    def test_write_auth_config_descriptor_close_failure_is_secondary_to_precommit_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".symphonz" / "auth.toml"
            real_close = os.close
            close_calls = 0

            def fail_first_close(descriptor):
                nonlocal close_calls
                close_calls += 1
                real_close(descriptor)
                if close_calls == 1:
                    raise OSError("auth descriptor close failed")

            with (
                patch("symphonz.install.os.fchmod", side_effect=OSError("auth preparation failed")),
                patch("symphonz.install.os.close", side_effect=fail_first_close),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "auth preparation failed.*auth descriptor close failed",
                ) as raised:
                    write_auth_config(path, "descriptor-test-password")

            self.assertIsInstance(raised.exception.__cause__, OSError)
            self.assertIn("auth preparation failed", str(raised.exception.__cause__))
            self.assertNotIn("descriptor-test-password", str(raised.exception))
            self.assertFalse(path.exists())

    def test_auth_restore_descriptor_close_failure_preserves_restore_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".symphonz" / "auth.toml"
            write_auth_config(path, "old-password")
            real_close = os.close
            real_fchmod = os.fchmod
            real_fsync = os.fsync
            close_calls = 0
            fchmod_calls = 0
            failed_directory_fsync = False

            def fail_first_close(descriptor):
                nonlocal close_calls
                close_calls += 1
                real_close(descriptor)
                if close_calls == 1:
                    raise OSError("restore descriptor close failed")

            def fail_restore_fchmod(descriptor, mode):
                nonlocal fchmod_calls
                fchmod_calls += 1
                if fchmod_calls == 2:
                    raise OSError("restore preparation failed")
                return real_fchmod(descriptor, mode)

            def fail_first_directory_fsync(descriptor):
                nonlocal failed_directory_fsync
                if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not failed_directory_fsync:
                    failed_directory_fsync = True
                    raise OSError("auth directory fsync failed")
                return real_fsync(descriptor)

            with (
                patch("symphonz.install.os.close", side_effect=fail_first_close),
                patch("symphonz.install.os.fchmod", side_effect=fail_restore_fchmod),
                patch("symphonz.install.os.fsync", side_effect=fail_first_directory_fsync),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "auth directory fsync failed.*auth rollback.*restore preparation failed"
                    ".*restore descriptor close failed",
                ) as raised:
                    write_auth_config(path, "new-password")

            self.assertIsInstance(raised.exception.__cause__, OSError)
            self.assertIn("auth directory fsync failed", str(raised.exception.__cause__))
            self.assertNotIn("new-password", str(raised.exception))

    def test_write_auth_config_surfaces_commit_and_rollback_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".symphonz" / "auth.toml"
            write_auth_config(path, "old-password")
            real_fsync = os.fsync
            real_replace = os.replace
            failed_fsync = False
            replace_calls = 0

            def fail_first_directory_fsync(descriptor):
                nonlocal failed_fsync
                if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not failed_fsync:
                    failed_fsync = True
                    raise OSError("post-replace directory fsync failed")
                return real_fsync(descriptor)

            def fail_rollback_replace(source, destination, **kwargs):
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls == 2:
                    raise OSError("auth rollback replace failed")
                return real_replace(source, destination, **kwargs)

            with (
                patch("symphonz.install.os.fsync", side_effect=fail_first_directory_fsync),
                patch("symphonz.install.os.replace", side_effect=fail_rollback_replace),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "post-replace directory fsync failed.*auth rollback.*auth rollback replace failed",
                ) as raised:
                    write_auth_config(path, "new-password")

            self.assertIsInstance(raised.exception.__cause__, OSError)
            self.assertIn("post-replace directory fsync failed", str(raised.exception.__cause__))

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

    def test_write_config_replace_failure_preserves_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("old-config\n")
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
            )

            with patch("symphonz.install.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    write_config(path, config)

            self.assertEqual(path.read_text(), "old-config\n")
            self.assertEqual(list(path.parent.glob(".config.toml.*.tmp")), [])

    def test_write_config_cleanup_failure_does_not_mask_replace_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("old-config\n")
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
            )

            with (
                patch("symphonz.install.os.replace", side_effect=OSError("replace failed")),
                patch("pathlib.Path.unlink", side_effect=OSError("temporary cleanup failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "replace failed.*temporary cleanup failed") as raised:
                    write_config(path, config)

            self.assertIsInstance(raised.exception.__cause__, OSError)
            self.assertIn("replace failed", str(raised.exception.__cause__))
            self.assertEqual(path.read_text(), "old-config\n")

    def test_write_config_descriptor_close_failure_is_secondary_to_precommit_primary(self):
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
            )
            real_close = os.close

            def close_then_fail(descriptor):
                real_close(descriptor)
                raise OSError("config descriptor close failed")

            with (
                patch("symphonz.install.os.fdopen", side_effect=OSError("config fdopen failed")),
                patch("symphonz.install.os.close", side_effect=close_then_fail),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "config fdopen failed.*config descriptor close failed",
                ) as raised:
                    write_config(path, config)

            self.assertIsInstance(raised.exception.__cause__, OSError)
            self.assertIn("config fdopen failed", str(raised.exception.__cause__))
            self.assertFalse(path.exists())
            self.assertEqual(list(path.parent.glob(".config.toml.*.tmp")), [])

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
            legacy = _legacy_config_content()
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

    def test_configure_dashboard_migrates_no_dashboard_config_with_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".symphonz" / "config.toml"
            config_path.parent.mkdir()
            original = "# retained project note\n" + _legacy_config_content()
            config_path.write_text(original)

            configure_dashboard(root, password="comment-migration-secret")

            updated = config_path.read_text()
            self.assertEqual(updated[: len(original)], original)
            self.assertEqual(read_config(config_path)["dashboard"]["port"], "4000")

    def test_configure_dashboard_migrates_no_dashboard_config_with_custom_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".symphonz" / "config.toml"
            config_path.parent.mkdir()
            original = _legacy_config_content() + '\n[custom]\nvalue = "preserve-me"\n'
            config_path.write_text(original)

            configure_dashboard(root, password="custom-migration-secret")

            updated = config_path.read_text()
            self.assertEqual(updated[: len(original)], original)
            self.assertEqual(read_config(config_path)["custom"]["value"], "preserve-me")

    def test_configure_dashboard_migrates_noncanonical_no_dashboard_formatting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".symphonz" / "config.toml"
            config_path.parent.mkdir()
            original = _legacy_config_content().replace(" = ", "=").rstrip("\n")
            config_path.write_text(original)

            configure_dashboard(root, password="format-migration-secret")

            updated = config_path.read_text()
            self.assertEqual(updated[: len(original)], original)
            self.assertEqual(read_config(config_path)["dashboard"]["session_days"], "30")

    def test_configure_dashboard_replaces_commented_section_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".symphonz" / "config.toml"
            config_path.parent.mkdir()
            config_path.write_text(
                '[dashboard] # old dashboard settings\n'
                'host = "127.0.0.1"\n'
                'port = "4000"\n'
                'public_base_url = "http://127.0.0.1:4000"\n'
                'username = "old"\n'
                'session_days = "30"\n\n'
                '[git] # preserve this table comment\n'
                'provider = "github"\n'
            )

            configure_dashboard(
                root,
                port=4300,
                username="operator",
                password="replacement-secret",
            )

            content = config_path.read_text()
            self.assertEqual(content.count("[dashboard]"), 1)
            self.assertNotIn("old dashboard settings", content)
            self.assertIn("[git] # preserve this table comment\n", content)
            self.assertEqual(read_config(config_path)["dashboard"]["port"], "4300")
            self.assertEqual(read_config(config_path)["dashboard"]["username"], "operator")

    def test_configure_dashboard_rejects_duplicate_commented_sections_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            symphonz = root / ".symphonz"
            symphonz.mkdir()
            config_path = symphonz / "config.toml"
            config_path.write_text(
                '[dashboard] # first\n'
                'host = "127.0.0.1"\nport = "4000"\n\n'
                '[dashboard]\n'
                'host = "127.0.0.2"\nport = "4100"\n'
            )
            auth_path = symphonz / "auth.toml"
            write_auth_config(auth_path, "existing-secret")
            gitignore = root / ".gitignore"
            gitignore.write_text("existing-entry\n")
            before = {
                "config": config_path.read_bytes(),
                "auth": auth_path.read_bytes(),
                "gitignore": gitignore.read_bytes(),
            }

            with self.assertRaisesRegex(RuntimeError, "multiple.*dashboard"):
                configure_dashboard(root, password="replacement-secret")

            self.assertEqual(config_path.read_bytes(), before["config"])
            self.assertEqual(auth_path.read_bytes(), before["auth"])
            self.assertEqual(gitignore.read_bytes(), before["gitignore"])

    def test_configure_dashboard_preserves_following_quoted_table_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".symphonz" / "config.toml"
            config_path.parent.mkdir()
            quoted_table = '["git"] # quoted table\nprovider = "github"\n'
            config_path.write_text(
                '[dashboard]\nhost = "127.0.0.1"\nport = "4000"\n'
                'public_base_url = "http://127.0.0.1:4000"\n'
                'username = "admin"\nsession_days = "30"\n\n'
                + quoted_table
            )

            configure_dashboard(root, port=4200, password="replacement-secret")

            self.assertTrue(config_path.read_text().endswith(quoted_table))
            self.assertEqual(read_config(config_path)["git"]["provider"], "github")

    def test_configure_dashboard_rejects_quoted_dashboard_alias_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".symphonz" / "config.toml"
            config_path.parent.mkdir()
            config_path.write_text(
                '[dashboard]\nhost = "127.0.0.1"\nport = "4000"\n\n'
                '["dashboard"] # same table name\nhost = "127.0.0.2"\nport = "4100"\n'
            )
            before = config_path.read_bytes()

            with self.assertRaisesRegex(RuntimeError, "multiple.*dashboard"):
                configure_dashboard(root, password="replacement-secret")

            self.assertEqual(config_path.read_bytes(), before)
            self.assertFalse((root / ".symphonz" / "auth.toml").exists())
            self.assertFalse((root / ".gitignore").exists())

    def test_configure_dashboard_rejects_noncanonical_dashboard_tables_before_mutation(self):
        variants = [
            '[dashboard.auth]\nprovider = "local"\n',
            '[[dashboard.auth]]\nprovider = "local"\n',
            '[["dash\\u0062oard"]]\nprovider = "local"\n',
        ]
        for dashboard_table in variants:
            with self.subTest(dashboard_table=dashboard_table), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                symphonz = root / ".symphonz"
                symphonz.mkdir()
                config_path = symphonz / "config.toml"
                content = '[linear]\nproject_slug = "legacy"\n\n' + dashboard_table
                config_path.write_text(content)

                with self.assertRaisesRegex(RuntimeError, "configure-dashboard"):
                    configure_dashboard(
                        root,
                        password="replacement-secret",
                        getpass_func=lambda prompt: self.fail("password prompt must not run"),
                    )

                self.assertEqual(config_path.read_text(), content)
                self.assertFalse((symphonz / "auth.toml").exists())
                self.assertFalse((root / ".gitignore").exists())

    def test_configure_dashboard_config_failure_preserves_existing_auth_and_config(self):
        from symphonz import install as install_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            symphonz = root / ".symphonz"
            symphonz.mkdir()
            config_path = symphonz / "config.toml"
            config_path.write_text(
                '[dashboard]\nhost = "127.0.0.1"\nport = "4000"\n'
                'public_base_url = "http://127.0.0.1:4000"\n'
                'username = "admin"\nsession_days = "30"\n'
            )
            auth_path = symphonz / "auth.toml"
            write_auth_config(auth_path, "existing-secret")
            before_config = config_path.read_bytes()
            before_auth = auth_path.read_bytes()
            real_atomic_write = install_module._atomic_write_text
            config_commits = 0

            def fail_config(path, content):
                nonlocal config_commits
                if Path(path) == config_path:
                    config_commits += 1
                    if config_commits == 1:
                        real_atomic_write(path, content)
                        raise OSError("config commit failed after replace")
                return real_atomic_write(path, content)

            with patch("symphonz.install._atomic_write_text", side_effect=fail_config):
                with self.assertRaisesRegex(OSError, "config commit failed after replace"):
                    configure_dashboard(root, port=4200, password="replacement-secret")

            self.assertEqual(config_path.read_bytes(), before_config)
            self.assertEqual(auth_path.read_bytes(), before_auth)

    def test_configure_dashboard_auth_failure_rolls_back_committed_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            symphonz = root / ".symphonz"
            symphonz.mkdir()
            config_path = symphonz / "config.toml"
            config_path.write_text(
                '[dashboard]\nhost = "127.0.0.1"\nport = "4000"\n'
                'public_base_url = "http://127.0.0.1:4000"\n'
                'username = "admin"\nsession_days = "30"\n'
            )
            auth_path = symphonz / "auth.toml"
            write_auth_config(auth_path, "existing-secret")
            (root / ".gitignore").write_text("keep-this\n")
            before_config = config_path.read_bytes()
            before_auth = auth_path.read_bytes()
            observed = {}

            def fail_auth(path, password):
                observed["port"] = read_config(config_path)["dashboard"]["port"]
                observed["gitignore"] = (root / ".gitignore").read_text()
                raise OSError("auth commit failed")

            with patch("symphonz.install.write_auth_config", side_effect=fail_auth):
                with self.assertRaisesRegex(OSError, "auth commit failed"):
                    configure_dashboard(root, port=4200, password="replacement-secret")

            self.assertEqual(observed["port"], "4200")
            self.assertIn(".symphonz/auth.toml", observed["gitignore"])
            self.assertEqual(config_path.read_bytes(), before_config)
            self.assertEqual(auth_path.read_bytes(), before_auth)

    def test_configure_dashboard_post_replace_auth_fsync_failure_restores_auth_and_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".symphonz" / "config.toml"
            write_config(
                config_path,
                InstallConfig(
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
                ),
            )
            auth_path = root / ".symphonz" / "auth.toml"
            write_auth_config(auth_path, "old-password")
            old_config = config_path.read_bytes()
            old_auth_bytes = auth_path.read_bytes()
            old_auth = read_dashboard_auth(root)
            real_fsync = os.fsync
            directory_fsyncs = 0

            def fail_auth_directory_fsync(descriptor):
                nonlocal directory_fsyncs
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    directory_fsyncs += 1
                    if directory_fsyncs == 3:
                        raise OSError("auth directory fsync failed")
                return real_fsync(descriptor)

            with patch("symphonz.install.os.fsync", side_effect=fail_auth_directory_fsync):
                with self.assertRaisesRegex(OSError, "auth directory fsync failed"):
                    configure_dashboard(root, port=4200, password="new-password")

            restored = read_dashboard_auth(root)
            self.assertGreaterEqual(directory_fsyncs, 5)
            self.assertEqual(config_path.read_bytes(), old_config)
            self.assertEqual(auth_path.read_bytes(), old_auth_bytes)
            self.assertEqual(restored.session_secret, old_auth.session_secret)
            self.assertTrue(verify_password("old-password", restored.password_record))
            self.assertFalse(verify_password("new-password", restored.password_record))

    def test_configure_dashboard_committed_close_failures_do_not_roll_back(self):
        for failed_close, marker in [
            (2, "config directory close failed"),
            (3, "auth directory close failed"),
        ]:
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config_path = root / ".symphonz" / "config.toml"
                write_config(
                    config_path,
                    InstallConfig(
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
                    ),
                )
                auth_path = root / ".symphonz" / "auth.toml"
                write_auth_config(auth_path, "old-password")
                real_close = os.close
                close_calls = 0

                def fail_selected_close(descriptor):
                    nonlocal close_calls
                    close_calls += 1
                    real_close(descriptor)
                    if close_calls == failed_close:
                        raise OSError(marker)

                with warnings.catch_warnings(record=True) as diagnostics:
                    warnings.simplefilter("always")
                    with patch("symphonz.install.os.close", side_effect=fail_selected_close):
                        configure_dashboard(root, port=4200, password="new-password")

                committed_auth = read_dashboard_auth(root)
                self.assertEqual(read_config(config_path)["dashboard"]["port"], "4200")
                self.assertTrue(verify_password("new-password", committed_auth.password_record))
                self.assertFalse(verify_password("old-password", committed_auth.password_record))
                diagnostic_text = "\n".join(str(item.message) for item in diagnostics)
                self.assertIn(marker, diagnostic_text)
                self.assertNotIn("new-password", diagnostic_text)

    def test_configure_dashboard_requires_a_secure_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".symphonz" / "config.toml"
            config_path.parent.mkdir()
            legacy = _legacy_config_content()
            config_path.write_text(legacy)

            with self.assertRaisesRegex(RuntimeError, "dashboard password is required"):
                configure_dashboard(
                    root,
                    environ={},
                    getpass_func=lambda prompt: "",
                )

            self.assertEqual(config_path.read_text(), legacy)
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

    def test_initial_install_commits_nonsecret_files_and_gitignore_before_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observed = {}

            def fail_auth(path, password):
                observed["gitignore"] = (root / ".gitignore").read_text()
                observed["config_exists"] = (root / ".symphonz" / "config.toml").is_file()
                observed["workflow_exists"] = (root / ".symphonz" / "WORKFLOW.md").is_file()
                raise OSError("auth commit failed")

            with (
                patch("symphonz.install.collect_install_config", return_value=self.make_config()),
                patch("symphonz.install._dashboard_password", return_value="dashboard-secret"),
                patch("symphonz.install.write_auth_config", side_effect=fail_auth),
            ):
                with self.assertRaisesRegex(OSError, "auth commit failed"):
                    install_project(root, assume_yes=True, skip_linear_preflight=True)

            self.assertIn(".symphonz/auth.toml", observed["gitignore"])
            self.assertTrue(observed["config_exists"])
            self.assertTrue(observed["workflow_exists"])
            self.assertFalse((root / ".symphonz" / "auth.toml").exists())


class RuntimeTests(unittest.TestCase):
    @staticmethod
    def _write_legacy_config(root: Path) -> None:
        path = root / ".symphonz" / "config.toml"
        path.parent.mkdir()
        path.write_text(_legacy_config_content())

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
            self._write_legacy_config(root)

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

    def test_semantic_dashboard_table_keys_never_enable_legacy_mode(self):
        headers = ['["dashboard"]', "['dashboard']", '["dash\\u0062oard"]']
        for header in headers:
            with self.subTest(header=header), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_config(
                    root / ".symphonz" / "config.toml",
                    InstallConfig(
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
                    ),
                )
                config_path = root / ".symphonz" / "config.toml"
                config_path.write_text(config_path.read_text().replace("[dashboard]", header))

                with patch("symphonz.service.runner.run_service", return_value=0) as service:
                    self.assertEqual(run_installed(project_root=root, port=4700), 0)

                self.assertFalse(service.call_args.kwargs["_legacy_unauthenticated_dashboard"])
                self.assertEqual(service.call_args.kwargs["dashboard_username"], "admin")

    def test_noncanonical_dashboard_tables_fail_closed_before_runtime(self):
        headers = [
            "[dashboard.auth]",
            ' [ "dashboard" . auth ] # implicit dashboard',
            "[[dashboard.auth]]",
            '[["dash\\u0062oard"]]',
        ]
        for header in headers:
            with self.subTest(header=header), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._write_legacy_config(root)
                config_path = root / ".symphonz" / "config.toml"
                config_path.write_text(config_path.read_text() + "\n" + header + "\nvalue = \"x\"\n")

                with self.assertRaisesRegex(RuntimeError, "configure-dashboard"):
                    build_run_command(root, port=4700)

    def test_modified_pre_dashboard_config_is_not_confidently_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_legacy_config(root)
            config_path = root / ".symphonz" / "config.toml"
            config_path.write_text(config_path.read_text() + '\n[custom]\nvalue = "x"\n')

            with self.assertRaisesRegex(RuntimeError, "configure-dashboard"):
                build_run_command(root, port=4700)

    def test_modified_legacy_fixed_values_never_enable_unauthenticated_mode(self):
        modifications = [
            ('mode = "embedded"', 'mode = "global"'),
            ('command = "symphonz-internal"', 'command = "custom-runner"'),
            ('root = ".symphonz/workspace"', 'root = "custom-workspace"'),
            ('root = ".symphonz/logs"', 'root = "custom-logs"'),
        ]
        for original, replacement in modifications:
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._write_legacy_config(root)
                config_path = root / ".symphonz" / "config.toml"
                config_path.write_text(config_path.read_text().replace(original, replacement))

                with patch("symphonz.service.runner.run_service") as service:
                    with self.assertRaisesRegex(RuntimeError, "configure-dashboard"):
                        run_installed(project_root=root, port=4700)

                service.assert_not_called()

    def test_legacy_explicit_port_runs_without_auth_on_loopback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_legacy_config(root)
            workflow = root / ".symphonz" / "WORKFLOW.md"
            workflow.write_text(
                "---\n"
                "tracker:\n  kind: linear\n  api_key: $LINEAR_API_KEY\n  project_slug: project\n"
                "polling:\n  interval_ms: 1000\n"
                "workspace:\n  root: .symphonz/workspace\n"
                "codex:\n  command: ignored\n"
                "---\n"
                "Work on {{ issue.identifier }}.\n"
            )

            class FakeLinear:
                def fetch_candidate_issues(self, active_states):
                    return []

            orchestrator = unittest.mock.Mock()
            orchestrator.tick.side_effect = KeyboardInterrupt
            dashboard = unittest.mock.Mock()
            dashboard.port = 4700
            output = StringIO()
            errors = StringIO()

            with (
                patch("symphonz.service.runner.build_linear_client", return_value=FakeLinear()),
                patch("symphonz.service.runner.CodexAppServer"),
                patch("symphonz.service.runner.Orchestrator", return_value=orchestrator),
                patch("symphonz.service.runner.DashboardServer", return_value=dashboard) as dashboard_server,
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                result = run_installed(project_root=root, port=4700)

            self.assertEqual(result, 0)
            self.assertFalse((root / ".symphonz" / "auth.toml").exists())
            self.assertEqual(dashboard_server.call_args.args[0:2], ("127.0.0.1", 4700))
            self.assertIn("legacy", errors.getvalue().lower())
            self.assertIn("unauthenticated", errors.getvalue().lower())

    def test_legacy_explicit_port_rejects_non_loopback_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_legacy_config(root)

            with self.assertRaisesRegex(ValueError, "loopback"):
                run_installed(project_root=root, host="0.0.0.0", port=4700)

    def test_internal_legacy_mode_rejects_configured_dashboard(self):
        from symphonz.service.runner import run_service

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(
                root / ".symphonz" / "config.toml",
                InstallConfig(
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
                ),
            )

            with self.assertRaisesRegex(ValueError, "without.*dashboard"):
                run_service(
                    project_root=root,
                    workflow_path=root / ".symphonz" / "WORKFLOW.md",
                    logs_root=root / ".symphonz" / "logs",
                    host="127.0.0.1",
                    port=4700,
                    _legacy_unauthenticated_dashboard=True,
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
