import unittest

from backend.config.config import Settings


class SettingsEnvTests(unittest.TestCase):
    def test_ignores_unknown_deploy_env_values(self):
        settings = Settings(
            CORMONITORING_BASE_URL="https://cor-monitoring.cor-int.com",
            UNUSED_DEPLOY_ENV="ignored",
        )

        self.assertFalse(hasattr(settings, "cormonitoring_base_url"))


if __name__ == "__main__":
    unittest.main()
