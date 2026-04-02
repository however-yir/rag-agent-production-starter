from __future__ import annotations

import os
import unittest

from app.core.settings import AppSettings


class SettingsTestCase(unittest.TestCase):
    def test_from_env_reads_flags(self) -> None:
        previous_value = os.environ.get("USE_MOCK_SERVICES")
        os.environ["USE_MOCK_SERVICES"] = "false"
        try:
            settings = AppSettings.from_env()
            self.assertFalse(settings.use_mock_services)
        finally:
            if previous_value is None:
                os.environ.pop("USE_MOCK_SERVICES", None)
            else:
                os.environ["USE_MOCK_SERVICES"] = previous_value


if __name__ == "__main__":
    unittest.main()
