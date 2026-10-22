import unittest
from unittest import mock

from jobpilot import posting_fetch

FRAME = "https://job-boards.greenhouse.io/embed/job_app?for=acme&validityToken=x%3D%3D&token=6225647004"


class EmbeddedGreenhouseTests(unittest.TestCase):
    def test_reads_board_and_job_from_the_embed_url(self) -> None:
        with mock.patch.object(posting_fetch, "_greenhouse", return_value="form") as fetch:
            self.assertEqual(posting_fetch.fetch_embedded_greenhouse(["https://x.test/a", FRAME]), "form")
        fetch.assert_called_once_with("acme", "6225647004")

    def test_ignores_foreign_hosts_and_bad_ids(self) -> None:
        with mock.patch.object(posting_fetch, "_greenhouse") as fetch:
            self.assertIsNone(
                posting_fetch.fetch_embedded_greenhouse(
                    ["https://evil.test/embed/job_app?for=acme&token=1", FRAME.replace("6225647004", "abc")]
                )
            )
        fetch.assert_not_called()

    def test_network_failure_is_not_fatal(self) -> None:
        with mock.patch.object(posting_fetch, "_greenhouse", side_effect=OSError("down")):
            self.assertIsNone(posting_fetch.fetch_embedded_greenhouse([FRAME]))


if __name__ == "__main__":
    unittest.main()
