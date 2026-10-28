from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import client  # noqa: E402


class FakeResponse:
    def __init__(self, etag: str | None = None) -> None:
        self.headers = {"ETag": etag} if etag else {}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class UploadClientTests(unittest.TestCase):
    def test_upload_video_completes_multipart_and_queues_processing(self) -> None:
        initialized = {
            "upload_id": "upload-id",
            "recording_id": "recording-id",
            "upload": {
                "chunk_size_bytes": 5,
                "providers": [
                    {
                        "upload_parts": [
                            {"part_number": 1, "url": "https://storage/part-1"},
                            {"part_number": 2, "url": "https://storage/part-2"},
                            {"part_number": 3, "url": "https://storage/part-3"},
                        ],
                        "complete_url": "https://storage/complete",
                        "abort_url": "https://storage/abort",
                    }
                ],
            },
            "completion": {
                "url": "/api/public/v2/uploads/upload-id/complete",
                "token": "completion-token",
            },
        }
        queued = {
            "upload_id": "upload-id",
            "recording_id": "recording-id",
            "status": "processing",
        }
        storage_calls: list[tuple[str, str, bytes | None, str | None]] = []
        progress: list[tuple[int, int]] = []

        def storage_request(
            url: str,
            *,
            method: str,
            data: bytes | None = None,
            content_type: str | None = None,
        ) -> FakeResponse:
            storage_calls.append((url, method, data, content_type))
            etag = f'"etag-{url.rsplit("-", 1)[-1]}"' if method == "PUT" else None
            return FakeResponse(etag)

        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "meeting.mp4"
            video.write_bytes(b"hello world")
            with (
                patch.object(
                    client, "_request", side_effect=[initialized, queued]
                ) as request,
                patch.object(client, "_storage_request", side_effect=storage_request),
            ):
                result = client.upload_video(
                    video,
                    concurrency=1,
                    on_progress=lambda completed, total: progress.append(
                        (completed, total)
                    ),
                )

        self.assertEqual(result, queued)
        self.assertEqual(progress, [(1, 3), (2, 3), (3, 3)])
        self.assertEqual(
            request.call_args_list,
            [
                call(
                    "POST",
                    "/api/public/v2/uploads",
                    body={
                        "type": "video",
                        "title": "meeting.mp4",
                        "size_bytes": 11,
                    },
                ),
                call(
                    "POST",
                    "/api/public/v2/uploads/upload-id/complete",
                    body={"completion_token": "completion-token"},
                    retry_server_errors=True,
                ),
            ],
        )
        self.assertEqual(
            [entry[2] for entry in storage_calls[:3]], [b"hello", b" worl", b"d"]
        )
        complete = storage_calls[3]
        self.assertEqual(complete[0:2], ("https://storage/complete", "POST"))
        self.assertEqual(complete[3], "application/xml")
        self.assertIn(b"<PartNumber>3</PartNumber>", complete[2] or b"")
        self.assertIn(b'"etag-3"', complete[2] or b"")

    def test_upload_video_aborts_failed_provider_and_uses_next(self) -> None:
        initialized = {
            "upload": {
                "chunk_size_bytes": 20,
                "providers": [
                    {
                        "upload_parts": [
                            {"part_number": 1, "url": "https://first/part"}
                        ],
                        "complete_url": "https://first/complete",
                        "abort_url": "https://first/abort",
                    },
                    {
                        "upload_parts": [
                            {"part_number": 1, "url": "https://second/part"}
                        ],
                        "complete_url": "https://second/complete",
                        "abort_url": "https://second/abort",
                    },
                ],
            },
            "completion": {
                "url": "/api/public/v2/uploads/upload-id/complete",
                "token": "completion-token",
            },
        }
        queued = {"recording_id": "recording-id", "status": "processing"}
        storage_calls: list[tuple[str, str]] = []

        def storage_request(
            url: str,
            *,
            method: str,
            data: bytes | None = None,
            content_type: str | None = None,
        ) -> FakeResponse:
            storage_calls.append((url, method))
            if url == "https://first/complete":
                raise OSError("provider unavailable")
            if method == "PUT":
                return FakeResponse('"etag"')
            return FakeResponse()

        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "meeting.mp4"
            video.write_bytes(b"video")
            with (
                patch.object(client, "_request", side_effect=[initialized, queued]),
                patch.object(client, "_storage_request", side_effect=storage_request),
            ):
                result = client.upload_video(video, concurrency=1)

        self.assertEqual(result, queued)
        self.assertEqual(
            storage_calls,
            [
                ("https://first/part", "PUT"),
                ("https://first/complete", "POST"),
                ("https://first/abort", "DELETE"),
                ("https://second/part", "PUT"),
                ("https://second/complete", "POST"),
            ],
        )

    def test_upload_video_accepts_already_finalized_retry(self) -> None:
        initialized = {
            "upload_id": "upload-id",
            "recording_id": "recording-id",
            "upload": {
                "chunk_size_bytes": 20,
                "providers": [
                    {
                        "upload_parts": [
                            {"part_number": 1, "url": "https://storage/part"}
                        ],
                        "complete_url": "https://storage/complete",
                        "abort_url": "https://storage/abort",
                    }
                ],
            },
            "completion": {
                "url": "/api/public/v2/uploads/upload-id/complete",
                "token": "completion-token",
            },
        }

        def storage_request(
            url: str,
            *,
            method: str,
            data: bytes | None = None,
            content_type: str | None = None,
        ) -> FakeResponse:
            return FakeResponse('"etag"' if method == "PUT" else None)

        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "meeting.mp4"
            video.write_bytes(b"video")
            with (
                patch.object(
                    client,
                    "_request",
                    side_effect=[
                        initialized,
                        client.KommodoAPIError(
                            409,
                            {"error": "Upload already exists"},
                            "Upload already exists",
                        ),
                    ],
                ),
                patch.object(client, "_storage_request", side_effect=storage_request),
            ):
                result = client.upload_video(video, concurrency=1)

        self.assertEqual(
            result,
            {
                "upload_id": "upload-id",
                "recording_id": "recording-id",
                "status": "processing",
            },
        )

    def test_upload_image_puts_file_into_initialized_url(self) -> None:
        initialized = {
            "upload_id": "image-id",
            "upload": {"url": "https://storage/image"},
        }
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "diagram.png"
            image.write_bytes(b"png-data")
            with (
                patch.object(client, "_request", return_value=initialized) as request,
                patch.object(
                    client, "_storage_request", return_value=FakeResponse()
                ) as storage,
            ):
                result = client.upload_image(image, width=640, height=480)

        self.assertEqual(
            result,
            {"upload_id": "image-id", "type": "image", "status": "uploaded"},
        )
        request.assert_called_once_with(
            "POST",
            "/api/public/v2/uploads",
            body={
                "type": "image",
                "title": "diagram.png",
                "size_bytes": 8,
                "content_type": "image/png",
                "width": 640,
                "height": 480,
            },
        )
        storage.assert_called_once_with(
            "https://storage/image",
            method="PUT",
            data=b"png-data",
            content_type="image/png",
        )


if __name__ == "__main__":
    unittest.main()
