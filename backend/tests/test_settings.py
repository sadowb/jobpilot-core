from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from jobpilot.config import Settings


class BackendBindTests(unittest.TestCase):
    def test_binds_to_loopback_unless_a_container_overrides_it(self) -> None:
        self.assertEqual(Settings(_env_file=None, jobpilot_home=Path("/x")).backend_host, "127.0.0.1")
        with patch.dict("os.environ", {"BACKEND_HOST": "0.0.0.0"}):  # noqa: S104 - container bind, published on host loopback only
            self.assertEqual(Settings(_env_file=None, jobpilot_home=Path("/x")).backend_host, "0.0.0.0")  # noqa: S104


if __name__ == "__main__":
    unittest.main()
