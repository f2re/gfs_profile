from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from map_animation import _ffmpeg_command, _write_concat_list


class MapAnimationTest(unittest.TestCase):
    def test_ffmpeg_command_uses_h264_mp4_animation_settings(self) -> None:
        cmd = _ffmpeg_command("ffmpeg", Path("frames.txt"), Path("out.mp4"), fps=8, crf=20)
        self.assertIn("libx264", cmd)
        self.assertIn("+faststart", cmd)
        self.assertIn("fps=8,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", cmd)
        self.assertEqual(cmd[-1], "out.mp4")

    def test_concat_list_repeats_last_frame_for_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            frames = [tmp_dir / "a.png", tmp_dir / "b.png"]
            list_path = tmp_dir / "frames.txt"

            _write_concat_list(frames, list_path, 0.65)

            text = list_path.read_text(encoding="utf-8")
            self.assertIn("duration 0.650", text)
            self.assertEqual(text.count("file "), 3)
            self.assertTrue(text.strip().endswith("b.png'"))


if __name__ == "__main__":
    unittest.main()
