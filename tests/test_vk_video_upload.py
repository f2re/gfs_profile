from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from messenger.errors import PlatformPermanentError
from messenger.vk.client import VkApiClient
from messenger.vk.gateway import VkGateway


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload; self.status_code = status_code; self.text = ""
    def json(self):
        return self.payload


class Session:
    def __init__(self):
        self.posts = []
    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if url == "https://upload.video/test":
            return Response({"owner_id": -123, "video_id": 77, "access_key": "key"})
        raise AssertionError(url)


class VkVideoClientTests(unittest.TestCase):
    def test_upload_video_uses_video_save_slot(self) -> None:
        session = Session()
        client = VkApiClient("token", session=session, retries=0)
        calls = []
        def call(method, **params):
            calls.append((method, params))
            if method == "video.save":
                return {"upload_url": "https://upload.video/test", "owner_id": -123, "video_id": 77}
            raise AssertionError(method)
        client.call = call
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"VK_GROUP_ID": "123"}, clear=False):
            path = Path(tmp) / "map.mp4"; path.write_bytes(b"video")
            attachment = client.upload_video("42", path)
        self.assertEqual(attachment, "video-123_77_key")
        self.assertEqual(calls[0][0], "video.save")
        self.assertEqual(calls[0][1]["group_id"], 123)
        self.assertEqual(session.posts[0][0], "https://upload.video/test")


class FakeGatewayClient:
    def __init__(self, fail_video=False):
        self.fail_video = fail_video; self.calls = []
    def upload_video(self, chat_id, path, title):
        self.calls.append("video")
        if self.fail_video:
            raise PlatformPermanentError("video forbidden")
        return "video-1_2"
    def upload_document(self, chat_id, path, title):
        self.calls.append("doc"); return "doc-1_3"
    def send_message(self, chat_id, message, *, attachment=None, keyboard=None):
        self.calls.append(("send", attachment)); return 99


class VkGatewayAnimationTests(unittest.IsolatedAsyncioTestCase):
    async def test_mp4_prefers_native_video(self) -> None:
        client = FakeGatewayClient()
        gateway = VkGateway(client)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.mp4"; path.write_bytes(b"v")
            await gateway.send_animation("42", path, caption="map")
        self.assertEqual(client.calls[0], "video")
        self.assertEqual(client.calls[1], ("send", "video-1_2"))

    async def test_mp4_falls_back_to_document(self) -> None:
        client = FakeGatewayClient(fail_video=True)
        gateway = VkGateway(client)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.mp4"; path.write_bytes(b"v")
            await gateway.send_animation("42", path, caption="map")
        self.assertEqual(client.calls[:2], ["video", "doc"])
        self.assertEqual(client.calls[2], ("send", "doc-1_3"))


if __name__ == "__main__":
    unittest.main()
