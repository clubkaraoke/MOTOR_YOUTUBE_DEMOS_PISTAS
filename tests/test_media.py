import math
import struct
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.services.media import MediaError, _animated_video_filter, create_demo_video, probe_duration, validate_demo_video


def run(*args):
    subprocess.run([str(a) for a in args], check=True, capture_output=True)


def sine(path: Path, frequency: int, duration: float):
    run("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        f"sine=frequency={frequency}:sample_rate=48000:duration={duration}", "-c:a", "pcm_s16le", path)


def image(path: Path):
    run("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=0x181b22:s=1280x720", "-frames:v", "1", path)


def projection(path: Path, start: float, seconds: float, frequency: int) -> float:
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(start), "-t", str(seconds),
                          "-i", str(path), "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                         check=True, capture_output=True).stdout
    values = struct.unpack(f"<{len(raw)//4}f", raw)
    sin_sum = cos_sum = 0.0
    for i, value in enumerate(values):
        angle = 2 * math.pi * frequency * i / 48000
        sin_sum += value * math.sin(angle); cos_sum += value * math.cos(angle)
    return math.hypot(sin_sum, cos_sum) / max(1, len(values))


def test_acceptance_180s_cut_80s_loop_resolution_and_original_absent(tmp_path):
    original, commercial, bg, output = (tmp_path/n for n in ("original.wav","commercial.wav","bg.png","demo.mp4"))
    sine(original, 440, 180); sine(commercial, 880, 3); image(bg)
    create_demo_video(original, bg, commercial, 80, output, 0.25)
    info = validate_demo_video(output, 180)
    assert info["video"]["width"] == 1280 and info["video"]["height"] == 720
    assert abs(probe_duration(output) - 180) < .75
    assert projection(output, 1, 1, 440) > projection(output, 1, 1, 880) * 10
    assert projection(output, 100, 2, 880) > projection(output, 100, 2, 440) * 20


def test_commercial_longer_is_trimmed_and_invalid_cut_blocked(tmp_path):
    original, commercial, bg, output = (tmp_path/n for n in ("original.wav","commercial.wav","bg.png","demo.mp4"))
    sine(original, 440, 5); sine(commercial, 880, 20); image(bg)
    create_demo_video(original, bg, commercial, 2, output)
    assert abs(probe_duration(output) - 5) < .75
    with pytest.raises(MediaError):
        create_demo_video(original, bg, commercial, 5, tmp_path/"bad.mp4")


def test_intro_image_switches_to_qr_image_at_twenty_seconds(tmp_path):
    original, commercial = tmp_path/"original.wav", tmp_path/"commercial.wav"
    intro, qr, output = tmp_path/"intro.png", tmp_path/"qr.png", tmp_path/"demo.mp4"
    sine(original, 440, 25); sine(commercial, 880, 3)
    run("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=red:s=1280x720", "-frames:v", "1", intro)
    run("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=1280x720", "-frames:v", "1", qr)
    create_demo_video(original, intro, commercial, 10, output,
                      qr_background_image=qr, image_switch_seconds=20)
    before, after = tmp_path/"before.png", tmp_path/"after.png"
    run("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "10", "-i", output,
        "-frames:v", "1", before)
    run("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "22", "-i", output,
        "-frames:v", "1", after)
    assert Image.open(before).getpixel((640, 360))[0] > 240
    assert Image.open(after).getpixel((640, 360))[2] > 240



def test_ffmpeg_diagonal_animation_alternates_real_frames(tmp_path):
    original, commercial = tmp_path/"original.wav", tmp_path/"commercial.wav"
    intro, qr, output = tmp_path/"intro.png", tmp_path/"qr.png", tmp_path/"animated.mp4"
    sine(original, 440, 12); sine(commercial, 880, 3)
    run("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=red:s=1280x720", "-frames:v", "1", intro)
    run("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=1280x720", "-frames:v", "1", qr)

    create_demo_video(
        original, intro, commercial, 6, output,
        qr_background_image=qr,
        image_transition="diagtr",
        animation_until_seconds=40,
        animation_scene_seconds=5,
        image_transition_seconds=.5,
    )

    red_frame, blue_frame, red_again = (tmp_path/n for n in ("red.png", "blue.png", "red2.png"))
    run("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "2", "-i", output,
        "-frames:v", "1", red_frame)
    run("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "7", "-i", output,
        "-frames:v", "1", blue_frame)
    run("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "11", "-i", output,
        "-frames:v", "1", red_again)

    assert Image.open(red_frame).getpixel((640, 360))[0] > 220
    assert Image.open(blue_frame).getpixel((640, 360))[2] > 220
    assert Image.open(red_again).getpixel((640, 360))[0] > 220


def test_animation_switches_stop_at_35_and_qr_remains_after_40():
    graph = _animated_video_filter(180, 5, .5, 40, "diagtr")
    assert "offset=5.000000" in graph
    assert "offset=35.000000" in graph
    assert "offset=40.000000" not in graph
    assert "[2:v]fps=25,settb=AVTB" in graph
    assert "[9:v]fps=25,settb=AVTB" in graph
    assert "split=" not in graph
    assert "tpad=stop_mode=clone:stop_duration=140.000000" in graph


@pytest.mark.parametrize("transition_name", ["diagtr", "smoothleft", "circleopen", "dissolve"])
def test_all_channel_xfade_transitions_render_from_independent_cfr_clips(tmp_path, transition_name):
    original, commercial = tmp_path/"original.wav", tmp_path/"commercial.wav"
    intro, qr, output = tmp_path/"intro.png", tmp_path/"qr.png", tmp_path/f"{transition_name}.mp4"
    sine(original, 440, 6); sine(commercial, 880, 2)
    run("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=red:s=1280x720", "-frames:v", "1", intro)
    run("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=1280x720", "-frames:v", "1", qr)
    create_demo_video(
        original, intro, commercial, 3, output,
        qr_background_image=qr,
        image_transition=transition_name,
        animation_until_seconds=40,
        animation_scene_seconds=5,
        image_transition_seconds=.5,
    )
    info = validate_demo_video(output, 6)
    assert info["video"]["width"] == 1280
    assert info["video"]["height"] == 720
