from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

from basemap_cache import local_basemap_overlay
from composite_map import MAP_BASEMAP_DEFAULT, MAP_MAX_ANIMATION_FRAMES, _validate_basemap, write_composite_map_png
from geocode import GeoPoint
from gfs_core import CACHE_DIR, GfsProfileError, ProgressCallback

MAP_ANIMATION_PIXEL_SIZE = int(os.getenv("MAP_ANIMATION_PIXEL_SIZE", "1280"))
MAP_ANIMATION_FRAME_DURATION_MS = int(os.getenv("MAP_ANIMATION_FRAME_DURATION_MS", "650"))
MAP_ANIMATION_OUTPUT_FPS = int(os.getenv("MAP_ANIMATION_OUTPUT_FPS", "8"))
MAP_ANIMATION_CRF = int(os.getenv("MAP_ANIMATION_CRF", "20"))


def _emit(progress_callback: ProgressCallback | None, **payload) -> None:
    if progress_callback:
        progress_callback(payload)


def _ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise GfsProfileError("ffmpeg не найден. Для качественной Telegram-анимации карты установите пакет ffmpeg.")
    return path


def _quote_concat_path(path: Path) -> str:
    return shlex.quote(str(path.resolve()))


def _write_concat_list(frame_paths: list[Path], list_path: Path, frame_duration_s: float) -> None:
    if not frame_paths:
        raise GfsProfileError("Нет кадров для MP4-анимации")
    lines: list[str] = []
    for frame_path in frame_paths:
        lines.append(f"file {_quote_concat_path(frame_path)}")
        lines.append(f"duration {frame_duration_s:.3f}")
    lines.append(f"file {_quote_concat_path(frame_paths[-1])}")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ffmpeg_command(ffmpeg_bin: str, concat_list: Path, out_path: Path, *, fps: int, crf: int) -> list[str]:
    return [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-vf",
        f"fps={fps},scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
        "-an",
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
        "-movflags",
        "+faststart",
        str(out_path),
    ]


def _mp4_out_path(frames: list[dict], path: Path | None) -> Path:
    if path is not None:
        return path
    first = frames[0]
    return CACHE_DIR / f"map_{first['run'].date}_{first['run'].cycle}_anim_{int(time.time())}.mp4"


def _validate_mp4(path: Path) -> None:
    if not path.exists() or path.stat().st_size < 1024:
        path.unlink(missing_ok=True)
        raise GfsProfileError("MP4-анимация карты не создана или получилась слишком маленькой")


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def write_composite_map_mp4(
    frames: list[dict],
    path: Path | None = None,
    progress_callback: ProgressCallback | None = None,
    *,
    pixel_size: int = MAP_ANIMATION_PIXEL_SIZE,
    frame_duration_ms: int = MAP_ANIMATION_FRAME_DURATION_MS,
    fps: int = MAP_ANIMATION_OUTPUT_FPS,
    crf: int = MAP_ANIMATION_CRF,
) -> Path:
    """Write a high-resolution silent H.264/MP4 animation for Telegram sendAnimation.

    Telegram Bot API accepts animation as GIF or silent H.264/MPEG-4 AVC. MP4
    avoids GIF palette quantization and Telegram's aggressive GIF conversion,
    so map labels and vector overlays remain much sharper in the chat preview.
    """

    if not frames:
        raise GfsProfileError("Нет кадров для MP4-анимации")
    if len(frames) > MAP_MAX_ANIMATION_FRAMES:
        raise GfsProfileError(f"Для Telegram-анимации допускается не больше {MAP_MAX_ANIMATION_FRAMES} кадров")
    if pixel_size < 640 or pixel_size > 2048:
        raise GfsProfileError("MAP_ANIMATION_PIXEL_SIZE должен быть в диапазоне 640..2048")
    if frame_duration_ms < 100 or frame_duration_ms > 5000:
        raise GfsProfileError("MAP_ANIMATION_FRAME_DURATION_MS должен быть в диапазоне 100..5000")
    if fps < 1 or fps > 30:
        raise GfsProfileError("MAP_ANIMATION_OUTPUT_FPS должен быть в диапазоне 1..30")
    if crf < 16 or crf > 32:
        raise GfsProfileError("MAP_ANIMATION_CRF должен быть в диапазоне 16..32")

    ffmpeg_bin = _ffmpeg_bin()
    out_path = _mp4_out_path(frames, path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    basemap = _validate_basemap(str(frames[0].get("basemap", MAP_BASEMAP_DEFAULT)))
    point: GeoPoint = frames[0]["point"]
    basemap_overlay = local_basemap_overlay(point.lat, point.lon, float(frames[0]["radius_km"]), basemap)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        frame_paths: list[Path] = []
        for index, frame in enumerate(frames, start=1):
            _emit(progress_callback, stage="map_animation_frame", message=f"Строю MP4-кадр {index}/{len(frames)}", index=index, total=len(frames), lead_hour=frame["lead_hour"])
            png_path = tmp_dir / f"frame_{index:03d}.png"
            write_composite_map_png(frame, png_path, pixel_size=pixel_size, basemap_overlay=basemap_overlay)
            frame_paths.append(png_path)
        if frame_paths:
            width, height = _image_size(frame_paths[0])
            _emit(progress_callback, stage="map_animation_encode", message=f"Кодирую MP4 {width}×{height}", total=len(frame_paths))
        concat_list = tmp_dir / "frames.txt"
        _write_concat_list(frame_paths, concat_list, max(0.1, frame_duration_ms / 1000.0))
        command = _ffmpeg_command(ffmpeg_bin, concat_list, out_path, fps=fps, crf=crf)
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except subprocess.CalledProcessError as exc:
            out_path.unlink(missing_ok=True)
            stderr = (exc.stderr or "").strip()
            detail = f": {stderr[-400:]}" if stderr else ""
            raise GfsProfileError(f"Ошибка кодирования MP4-анимации ffmpeg{detail}") from exc
    _validate_mp4(out_path)
    _emit(progress_callback, stage="map_animation_done", message="MP4-анимация готова", file=str(out_path), size=out_path.stat().st_size)
    return out_path
