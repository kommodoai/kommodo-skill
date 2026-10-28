from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import client


class FolderClientTests(unittest.TestCase):
    def test_folder_writes_use_the_public_contract(self) -> None:
        with patch.object(client, "_request", return_value={}) as request:
            client.create_folder("Customer calls")
            client.create_folder("Q4", parent_id="parent")
            client.rename_folder("folder", "Archived")
            client.delete_folder("folder")

        self.assertEqual(
            request.call_args_list,
            [
                call("POST", "/api/public/v2/folders", body={"name": "Customer calls"}),
                call(
                    "POST",
                    "/api/public/v2/folders",
                    body={"name": "Q4", "parent_id": "parent"},
                ),
                call(
                    "PATCH",
                    "/api/public/v2/folders/folder",
                    body={"name": "Archived"},
                ),
                call("DELETE", "/api/public/v2/folders/folder"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
