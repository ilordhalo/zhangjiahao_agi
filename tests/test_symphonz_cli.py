from pathlib import Path
import tempfile
import unittest

from symphonz.install import InstallConfig, read_config, write_config


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
