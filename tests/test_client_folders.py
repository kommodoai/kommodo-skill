from __future__ import annotations

import sys
import io
import json
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import client


class FolderClientTests(unittest.TestCase):
    def test_folder_list_passes_every_public_filter(self) -> None:
        with patch.object(client, "_request", return_value={}) as request:
            client.list_folders(
                parent_id="parent",
                member_id="member",
                cursor="cursor",
                limit=50,
            )

        request.assert_called_once_with(
            "GET",
            "/api/public/v2/folders",
            params={
                "parent_id": "parent",
                "member_id": "member",
                "cursor": "cursor",
                "limit": 50,
            },
        )

    def test_sync_helpers_preserve_checkpoint_and_provenance_contract(self) -> None:
        payload = {"items": [], "scope_ref": "scope", "scope_complete": True,
                   "source_refs": ["overlap"], "removed_scopes": ["revoked"],
                   "next_cursor": "continuation", "complete": False}
        with patch.object(client, "_request", return_value=payload) as request:
            self.assertEqual(client.start_sync(), payload)
            self.assertEqual(client.get_sync_status(), payload)
            self.assertIs(client.get_sync_snapshot("signed", "next", 25), payload)
            self.assertIs(client.get_changes("checkpoint", 10), payload)
        self.assertEqual(request.call_args_list, [
            call("POST", "/api/public/v2/sync/checkpoints"),
            call("GET", "/api/public/v2/sync/status"),
            call("GET", "/api/public/v2/sync/snapshot", params={"checkpoint": "signed", "cursor": "next", "limit": 25}),
            call("GET", "/api/public/v2/changes", params={"cursor": "checkpoint", "limit": 10}),
        ])

    def test_webhook_create_does_not_retry_ambiguous_gateway_errors(self) -> None:
        with patch.object(client, "_request", return_value={}) as request:
            client.list_webhooks()
            client.create_webhook("https://receiver.example/hook")
            client.delete_webhook("opaque/id")
        self.assertEqual(request.call_args_list, [
            call("GET", "/api/public/v2/webhook-subscriptions"),
            call("POST", "/api/public/v2/webhook-subscriptions", body={"url": "https://receiver.example/hook"}, retry_gateway_errors=False),
            call("DELETE", "/api/public/v2/webhook-subscriptions/opaque%2Fid"),
        ])

    def test_quota_guidance_survives_without_retrying_before_the_allowed_time(self) -> None:
        body = {"error": "Work allowance exhausted", "guidance": "Use /changes for ongoing sync.",
                "documentation_url": "https://kommodo.ai/developer#sync"}
        def error(wait: int) -> urllib.error.HTTPError:
            return urllib.error.HTTPError("https://kommodo.ai/api/public/v2/recordings", 429, "limit",
                                          {"Retry-After": str(wait)}, io.BytesIO(json.dumps(body).encode()))
        for wait, expected_calls in [(120, 1), (5, 2)]:
            with patch.object(client, "_token", return_value="secret-token"), \
                 patch.object(client.urllib.request, "urlopen", side_effect=[error(wait), error(wait)]) as request, \
                 patch.object(client.time, "sleep") as sleep:
                with self.assertRaises(client.KommodoAPIError) as raised:
                    client.find_recordings()
            self.assertEqual(request.call_count, expected_calls)
            self.assertIn("/changes", str(raised.exception))
            self.assertIn(f"Retry after {wait}s", str(raised.exception))
            self.assertNotIn("secret-token", str(raised.exception))
            self.assertEqual(sleep.call_args_list, [call(5)] if wait == 5 else [])

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
