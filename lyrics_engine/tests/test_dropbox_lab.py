import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.dropbox_lab import DropboxLabClient, DropboxLabError


class DropboxLabClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_unconfigured_status(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DROPBOX_APP_KEY": "",
                "DROPBOX_APP_SECRET": "",
                "DROPBOX_REDIRECT_URI": "",
            },
            clear=False,
        ):
            client = DropboxLabClient(self.base)
            self.assertFalse(client.configured)
            self.assertFalse(client.connected)
            with self.assertRaises(DropboxLabError):
                client.authorization_url()

    def test_connected_from_saved_refresh_token(self) -> None:
        (self.base / "dropbox_auth.json").write_text(
            json.dumps(
                {
                    "refresh_token": "refresh-test",
                    "display_name": "DJGABO",
                    "root_namespace_id": "123",
                }
            ),
            encoding="utf-8",
        )

        client = DropboxLabClient(self.base)
        self.assertTrue(client.connected)
        self.assertEqual(
            client.status()["display_name"],
            "DJGABO",
        )


if __name__ == "__main__":
    unittest.main()
