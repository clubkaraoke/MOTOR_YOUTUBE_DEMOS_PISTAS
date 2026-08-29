import json
import math
import subprocess
from pathlib import Path
from typing import Callable


class MediaError(RuntimeError):
    pass


YOUTUBE_AUDIO_BITRATE = "384k"
# Panel previews use the same FFmpeg installation as production.


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise MediaError(f"No se encontró {command[0]} en PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc))[-4000:]
        raise MediaError(detail) from exc


def probe_media(path: str | Path) -> dict:
    result = _run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ])
    return json.loads(result.stdout)


def probe_duration(path: str | Path) -> float:
    data = probe_media(path)
    duration = data.get("format", {}).get("duration")
    if duration is None:
        durations = [s.get("duration") for s in data.get("streams", []) if s.get("duration")]
        duration = max(durations) if durations else None
    if duration is None:
        raise MediaError("ffprobe no pudo determinar la duración")
    return float(duration)


def validate_demo_video(path: str | Path, expected_duration: float, tolerance: float = 0.75) -> dict:
    data = probe_media(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    actual = float(data.get("format", {}).get("duration", 0))
    errors: list[str] = []
    if not video:
        errors.append("falta stream de video")
    elif (video.get("width"), video.get("height")) != (1280, 720):
        errors.append(f"resolución inesperada {video.get('width')}x{video.get('height')}")
    elif video.get("codec_name") not in {"h264", "avc1"}:
        errors.append(f"codec de video inesperado {video.get('codec_name')}")
    if not audio:
        errors.append("falta stream de audio")
    elif audio.get("codec_name") != "aac":
        errors.append(f"codec de audio inesperado {audio.get('codec_name')}")
    if abs(actual - expected_duration) > tolerance:
        errors.append(f"duración {actual:.3f}s fuera de tolerancia para {expected_duration:.3f}s")
    if errors:
        raise MediaError("; ".join(errors))
    return {"duration": actual, "video": video, "audio": audio}


def _encode_still_clip(image_path: Path, output_path: Path, duration_seconds: float) -> None:
    """Encode one still scene as a real 25 fps CFR clip for xfade."""
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", "25", "-loop", "1", "-i", str(image_path),
        "-t", f"{duration_seconds:.6f}", "-an",
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
        "-r", "25", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output_path),
    ])


def _animation_segment_count(duration: float, scene_seconds: float, animation_until_seconds: float) -> int:
    animated_end = min(float(duration), float(animation_until_seconds))
    return max(1, int(math.ceil(animated_end / float(scene_seconds))))


def _animated_video_filter(duration: float, scene_seconds: float, transition_seconds: float,
                           animation_until_seconds: float, transition_name: str) -> str:
    """Alternate independent CFR clip inputs, then hold the final QR frame."""
    scene = max(1.0, float(scene_seconds))
    fade = max(0.05, min(float(transition_seconds), scene / 2))
    until = max(scene * 2, float(animation_until_seconds))
    segment_count = _animation_segment_count(duration, scene, until)

    parts: list[str] = []
    # The temporary scene MP4s are already 25 fps CFR with identical geometry,
    # pixel format and timebase. Feed them directly to xfade. Adding fps/setpts
    # here makes some FFmpeg builds advertise an unknown 1/0 frame rate.
    chain = f"{2}:v"
    for index in range(1, segment_count):
        out = f"x{index}"
        right = f"{2 + index}:v"
        parts.append(
            f"[{chain}][{right}]xfade=transition={transition_name}:"
            f"duration={fade:.6f}:offset={scene * index:.6f}[{out}]"
        )
        chain = out

    animated_end = min(float(duration), until)
    if duration > until:
        hold = max(0.0, float(duration) - until)
        parts.append(
            f"[{chain}]trim=duration={until:.6f},setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:stop_duration={hold:.6f}[vout]"
        )
    else:
        parts.append(
            f"[{chain}]trim=duration={animated_end:.6f},setpts=PTS-STARTPTS[vout]"
        )
    return ";".join(parts)


def create_demo_video(
    original_audio: str | Path,
    background_image: str | Path,
    commercial_audio: str | Path,
    cut_seconds: float,
    output_path: str | Path,
    transition_seconds: float = 0.25,
    progress_callback: Callable[[int], None] | None = None,
    qr_background_image: str | Path | None = None,
    image_switch_seconds: float = 20.0,
    image_transition: str | None = None,
    animation_until_seconds: float = 40.0,
    animation_scene_seconds: float = 5.0,
    image_transition_seconds: float = 0.5,
) -> dict:
    """Render the intro image first and the QR image for the remainder of the MP4."""
    original_audio = Path(original_audio)
    background_image = Path(background_image)
    commercial_audio = Path(commercial_audio)
    qr_background_image = Path(qr_background_image) if qr_background_image else background_image
    output_path = Path(output_path)
    for source in (original_audio, background_image, qr_background_image, commercial_audio):
        if not source.is_file():
            raise MediaError(f"Archivo requerido inexistente: {source}")
    duration = probe_duration(original_audio)
    if not 0 < cut_seconds < duration:
        raise MediaError(f"El corte debe estar entre 0 y {duration:.3f}s")
    transition = max(0.0, min(float(transition_seconds), 1.0, cut_seconds, duration - cut_seconds))
    remaining = duration - cut_seconds
    image_switch = max(0.001, min(float(image_switch_seconds), duration))
    qr_image_duration = max(0.001, duration - image_switch)
    original_fade_start = max(0.0, cut_seconds - transition)
    audio_filters = (
        f"[0:a]atrim=start=0:end={cut_seconds:.6f},asetpts=PTS-STARTPTS,"
        f"afade=t=out:st={original_fade_start:.6f}:d={transition:.6f}[orig];"
        f"[1:a]atrim=start=0:end={remaining:.6f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={transition:.6f}[ad];"
        "[orig][ad]concat=n=2:v=0:a=1[aout]"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_intro_clip = output_path.with_name(f".{output_path.stem}.intro-cfr.mp4")
    temp_qr_clip = output_path.with_name(f".{output_path.stem}.qr-cfr.mp4")
    try:
        if image_transition:
            scene = max(1.0, float(animation_scene_seconds))
            fade = max(0.05, min(float(image_transition_seconds), scene / 2))
            until = max(float(animation_until_seconds), scene * 2)
            clip_duration = scene + fade
            _encode_still_clip(background_image, temp_intro_clip, clip_duration)
            _encode_still_clip(qr_background_image, temp_qr_clip, clip_duration)
            video_filters = _animated_video_filter(
                duration,
                scene,
                fade,
                until,
                image_transition,
            )
            segment_count = _animation_segment_count(duration, scene, until)
            video_inputs = []
            for index in range(segment_count):
                scene_path = temp_intro_clip if index % 2 == 0 else temp_qr_clip
                video_inputs.extend(["-i", str(scene_path)])
        else:
            video_filters = (
                f"[2:v]scale=1280:720:force_original_aspect_ratio=decrease,"
                f"pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p,"
                f"trim=duration={image_switch:.6f},setpts=PTS-STARTPTS[vintro];"
                f"[3:v]scale=1280:720:force_original_aspect_ratio=decrease,"
                f"pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p,"
                f"trim=duration={qr_image_duration:.6f},setpts=PTS-STARTPTS[vqr];"
                "[vintro][vqr]concat=n=2:v=1:a=0[vout]"
            )
            video_inputs = [
                "-loop", "1", "-i", str(background_image),
                "-loop", "1", "-i", str(qr_background_image),
            ]
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(original_audio), "-stream_loop", "-1", "-i", str(commercial_audio),
            *video_inputs,
            "-filter_complex", f"{audio_filters};{video_filters}",
            "-map", "[vout]", "-map", "[aout]", "-t", f"{duration:.6f}",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-r", "25",
            "-c:a", "aac", "-b:a", YOUTUBE_AUDIO_BITRATE, "-ar", "48000", "-movflags", "+faststart",
            "-shortest", str(output_path),
        ]
        if progress_callback:
            progress_callback(10)
        _run(command)
    finally:
        temp_intro_clip.unlink(missing_ok=True)
        temp_qr_clip.unlink(missing_ok=True)
    if progress_callback:
        progress_callback(90)
    result = validate_demo_video(output_path, duration)
    if progress_callback:
        progress_callback(100)
    return result
