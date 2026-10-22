"""The runtime image must stay consistent with the code that depends on it."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from jobpilot.domain.cv import _SAFE_PACKAGES

ROOT = Path(__file__).resolve().parents[2]


class DockerAssetTests(unittest.TestCase):
    def test_tex_warmup_covers_every_allowlisted_package(self) -> None:
        """Compilation runs with --only-cached: a package missing from the warm-up document would fail offline."""
        used = {
            p.strip()
            for m in re.finditer(r"\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}", (ROOT / "docker/tex-warmup.tex").read_text())
            for p in m.group(1).split(",")
        }
        self.assertEqual(sorted(_SAFE_PACKAGES - used), [])

    def test_dockerfile_pins_tools_and_excludes_dev_dependencies(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertRegex(dockerfile, r"ARG CLAUDE_CODE_VERSION=\d+\.\d+\.\d+")
        self.assertRegex(dockerfile, r"ARG TECTONIC_VERSION=\d+\.\d+\.\d+")
        self.assertIn("sha256sum -c", dockerfile)
        self.assertNotRegex(dockerfile, r"(?m)^(COPY|RUN).*requirements-dev")
        runtime = (ROOT / "backend/requirements.lock").read_text()
        for dev in ("mypy==", "ruff==", "celery-types=="):
            self.assertNotIn(dev, runtime)


if __name__ == "__main__":
    unittest.main()
