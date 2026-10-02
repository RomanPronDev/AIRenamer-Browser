import shutil
from pathlib import Path
import hashlib
import json
from uuid import uuid4
import os
import threading
import time
import zipfile
from types import SimpleNamespace

import pytest

import config
import utils


def make_temp_dir():
    path = Path.cwd() / f"test_tmp_{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_parse_existing_versions_reads_multiple_versions(monkeypatch):
    monkeypatch.setattr(config, "FILENAME_TEMPLATE", config.DEFAULT_FILENAME_TEMPLATE)
    monkeypatch.setattr(config, "SUBVERSION_ENABLED", True)
    tmp_path = make_temp_dir()
    try:
        (tmp_path / "SC01_SH010_VID_v001_00.mov").write_text("", encoding="utf-8")
        (tmp_path / "SC01_SH010_VID_v001_01.mov").write_text("", encoding="utf-8")
        (tmp_path / "SC01_SH010_VID_v002_00.mov").write_text("", encoding="utf-8")

        parsed = utils.parse_existing_versions(str(tmp_path))

        assert {"filename": "SC01_SH010_VID_v002_00.mov", "v": 2, "sub": 0} in parsed
        assert len(parsed) == 3
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_calculate_new_version_supports_targeted_subversion(monkeypatch):
    monkeypatch.setattr(config, "SUBVERSION_ENABLED", True)
    existing = [
        {"filename": "SC01_SH010_VID_v001_00.mov", "v": 1, "sub": 0},
        {"filename": "SC01_SH010_VID_v001_01.mov", "v": 1, "sub": 1},
        {"filename": "SC01_SH010_VID_v002_00.mov", "v": 2, "sub": 0},
    ]

    assert utils.calculate_new_version(existing, target_existing_filename="SC01_SH010_VID_v001_00.mov") == (1, 2)


def test_build_sequence_base_name_uses_next_version(monkeypatch):
    monkeypatch.setattr(config, "FILENAME_TEMPLATE", config.DEFAULT_FILENAME_TEMPLATE)
    monkeypatch.setattr(config, "SUBVERSION_ENABLED", True)
    tmp_path = make_temp_dir()
    try:
        (tmp_path / "SC01_SH010_VID_v001_00.mov").write_text("", encoding="utf-8")

        base_name = utils.build_sequence_base_name("SC01", "SH010", str(tmp_path))

        assert base_name == "SC01_SH010_VID_v002_00"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_next_available_version_advances_subversion_until_path_is_free(monkeypatch):
    monkeypatch.setattr(config, "SUBVERSION_ENABLED", True)
    versions = [{"filename": "shot.mov", "v": 2, "sub": 0}]

    assert utils.next_available_version(
        2,
        0,
        versions,
        lambda version, subversion: subversion < 3,
    ) == (2, 3)


def test_next_available_version_advances_main_version_when_subversions_disabled(monkeypatch):
    monkeypatch.setattr(config, "SUBVERSION_ENABLED", False)
    versions = [{"filename": "shot.mov", "v": 2, "sub": 0}]

    assert utils.next_available_version(
        2,
        0,
        versions,
        lambda version, subversion: version < 4,
    ) == (4, 0)


def test_format_versioned_base_name_uses_default_template(monkeypatch):
    monkeypatch.setattr(config, "FILENAME_TEMPLATE", config.DEFAULT_FILENAME_TEMPLATE)
    monkeypatch.setattr(config, "SUBVERSION_ENABLED", True)

    assert (
        utils.format_versioned_base_name("SQ010", "SH020", config.TYPE_IMG, 3, 2, "_16x09")
        == "SQ010_SH020_IMG_v003_02_16x09"
    )


def test_format_versioned_base_name_supports_minimal_template(monkeypatch):
    monkeypatch.setattr(config, "FILENAME_TEMPLATE", "{shot}_{type}_v{version}{format}")
    monkeypatch.setattr(config, "SUBVERSION_ENABLED", True)

    assert (
        utils.format_versioned_base_name("SQ010", "SH020", config.TYPE_IMG, 3, 0)
        == "SH020_IMG_v003"
    )

def test_format_versioned_base_name_uses_configured_type_suffix(monkeypatch):
    monkeypatch.setattr(config, "FILENAME_TEMPLATE", "{shot}_{type}_v{version}{format}")
    monkeypatch.setattr(config, "SUBVERSION_ENABLED", True)
    monkeypatch.setattr(config, "IMAGE_TYPE_SUFFIX", "KEY")
    monkeypatch.setattr(config, "VIDEO_TYPE_SUFFIX", "MOV")

    assert utils.format_versioned_base_name("SQ010", "SH020", config.TYPE_IMG, 3, 0) == "SH020_KEY_v003"
    assert utils.format_versioned_base_name("SQ010", "SH020", config.TYPE_VID, 4, 0) == "SH020_MOV_v004"


def test_format_versioned_base_name_omits_disabled_subversion_cleanly(monkeypatch):
    monkeypatch.setattr(config, "FILENAME_TEMPLATE", config.DEFAULT_FILENAME_TEMPLATE)
    monkeypatch.setattr(config, "SUBVERSION_ENABLED", False)

    assert (
        utils.format_versioned_base_name("SQ010", "SH020", config.TYPE_IMG, 3, 7)
        == "SQ010_SH020_IMG_v003"
    )


def test_parse_existing_versions_supports_main_and_subversion_styles(monkeypatch):
    tmp_path = make_temp_dir()
    try:
        monkeypatch.setattr(config, "FILENAME_TEMPLATE", "{shot}_{type}_v{version}{format}")
        monkeypatch.setattr(config, "SUBVERSION_ENABLED", True)
        (tmp_path / "SH020_IMG_v001.jpg").write_text("", encoding="utf-8")
        (tmp_path / "SH020_IMG_v002_03.jpg").write_text("", encoding="utf-8")

        parsed = utils.parse_existing_versions(str(tmp_path), "SQ010", "SH020", config.TYPE_IMG)

        assert {"filename": "SH020_IMG_v001.jpg", "v": 1, "sub": 0} in parsed
        assert {"filename": "SH020_IMG_v002_03.jpg", "v": 2, "sub": 3} in parsed
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_disabled_subversion_creates_next_main_version(monkeypatch):
    monkeypatch.setattr(config, "SUBVERSION_ENABLED", False)
    existing = [
        {"filename": "SH020_IMG_v001.jpg", "v": 1, "sub": 0},
        {"filename": "SH020_IMG_v001_03.jpg", "v": 1, "sub": 3},
    ]

    assert utils.calculate_new_version(existing, is_subversion=True, target_existing_filename="SH020_IMG_v001.jpg") == (2, 0)


def test_empty_sequence_token_cleans_filename_separators(monkeypatch):
    monkeypatch.setattr(config, "FILENAME_TEMPLATE", "{sequence}_{shot}_{type}_v{version}_{subversion}{format}")
    monkeypatch.setattr(config, "SUBVERSION_ENABLED", True)

    assert utils.format_versioned_base_name("", "SH020", config.TYPE_IMG, 1, 0) == "SH020_IMG_v001_00"


def test_converted_sequence_folder_marker_matches_video_base(tmp_path):
    video_file = tmp_path / "SC01_SH010_VID_v001_00.mov"
    video_file.write_text("video", encoding="utf-8")

    assert utils.has_converted_sequence_folder(str(video_file)) is False

    (tmp_path / "SC01_SH010_VID_v001_00").mkdir()
    assert utils.has_converted_sequence_folder(str(video_file)) is True


def test_convert_to_sequence_rejects_existing_target_folder(monkeypatch):
    tmp_path = make_temp_dir()
    try:
        sequence_dir = tmp_path / "SC01_SH010_VID_v002_00_sequence"
        sequence_dir.mkdir()

        monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "ffmpeg")

        with pytest.raises(FileExistsError):
            utils.convert_to_sequence("source.mov", str(tmp_path), sequence_dir.name)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_run_ffmpeg_process_avoids_pipe_backpressure(monkeypatch):
    popen_args = {}

    class FakeProcess:
        returncode = 0

        def poll(self):
            return self.returncode

        def communicate(self):
            popen_args["stderr"].write("captured error details")
            return None, None

    def fake_popen(cmd, stdout, stderr, text, creationflags):
        popen_args.update({
            "cmd": cmd,
            "stdout": stdout,
            "stderr": stderr,
            "text": text,
            "creationflags": creationflags,
        })
        return FakeProcess()

    monkeypatch.setattr(utils.subprocess, "Popen", fake_popen)

    returncode, stderr, cancelled = utils.run_ffmpeg_process(["ffmpeg", "-version"])

    assert returncode == 0
    assert stderr == "captured error details"
    assert cancelled is False
    assert popen_args["stdout"] == utils.subprocess.DEVNULL
    assert popen_args["stderr"] != utils.subprocess.PIPE


def test_convert_to_sequence_creates_new_folder_and_returns_undo_op(monkeypatch):
    tmp_path = make_temp_dir()
    try:
        source_video = tmp_path / "source.mov"
        source_video.write_text("video", encoding="utf-8")

        monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "ffmpeg")

        calls = []

        class FakeProcess:
            returncode = 0

            def __init__(self, cmd):
                self.cmd = cmd

            def poll(self):
                return self.returncode

            def communicate(self):
                output_pattern = Path(self.cmd[-1])
                output_pattern.parent.mkdir(exist_ok=True)
                output_pattern.write_text("frame", encoding="utf-8")
                return "", ""

        def fake_popen(cmd, stdout, stderr, text, creationflags):
            calls.append(cmd)
            return FakeProcess(cmd)

        monkeypatch.setattr(utils.subprocess, "Popen", fake_popen)

        seq_dir, sequence_name, undo_op = utils.convert_to_sequence(
            str(source_video),
            str(tmp_path),
            "SC01_SH010_VID_v002_00_sequence",
        )

        assert Path(seq_dir).is_dir()
        assert sequence_name == "SC01_SH010_VID_v002_00_sequence"
        assert undo_op == ("folder_create", seq_dir)
        assert calls
        assert calls[0][calls[0].index("-fps_mode") + 1] == "passthrough"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_convert_to_sequence_ignores_late_cancel_after_success(monkeypatch):
    tmp_path = make_temp_dir()
    try:
        source_video = tmp_path / "source.mov"
        source_video.write_text("video", encoding="utf-8")
        monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "ffmpeg")

        class FakeProcess:
            returncode = 0

            def __init__(self, cmd):
                self.cmd = cmd

            def poll(self):
                return self.returncode

            def communicate(self):
                output_pattern = Path(self.cmd[-1])
                output_pattern.write_text("frame", encoding="utf-8")
                return "", ""

        monkeypatch.setattr(
            utils.subprocess,
            "Popen",
            lambda cmd, stdout, stderr, text, creationflags: FakeProcess(cmd),
        )

        seq_dir, _, _ = utils.convert_to_sequence(
            str(source_video),
            str(tmp_path),
            "SC01_SH010_VID_v003_00",
            cancel_check=lambda: True,
        )

        assert Path(seq_dir).exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_sequence_base_name_and_converted_folder_keep_video_stem(tmp_path):
    video_file = tmp_path / "Shot_VID_v003.mov"
    video_file.write_text("", encoding="utf-8")

    assert utils.sequence_base_name_for_video(str(video_file)) == "Shot_VID_v003"
    assert utils.has_converted_sequence_folder(str(video_file)) is False

    (tmp_path / "Shot_VID_v003").mkdir()
    assert utils.has_converted_sequence_folder(str(video_file)) is True


def test_load_media_preview_rgb_scales_images(monkeypatch, tmp_path):
    from PIL import Image

    image_file = tmp_path / "preview.png"
    Image.new("RGB", (640, 320), "red").save(image_file)
    monkeypatch.setattr(utils, "ALLOWED_EXT_IMAGE", [".png"])

    width, height, rgb_bytes = utils.load_media_preview_rgb(str(image_file), (200, 200))

    assert (width, height) == (200, 100)
    assert len(rgb_bytes) == width * height * 3


def test_load_media_preview_rgb_reads_video_frame(monkeypatch, tmp_path):
    import io
    from PIL import Image

    video_file = tmp_path / "preview.mov"
    video_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(utils, "ALLOWED_EXT_VIDEO", [".mov"])
    monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "bundled-ffmpeg.exe")
    encoded_frame = io.BytesIO()
    Image.new("RGB", (40, 20), "black").save(encoded_frame, format="PNG")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=encoded_frame.getvalue(),
            stderr=b"",
        )

    monkeypatch.setattr(utils.subprocess, "run", fake_run)

    width, height, rgb_bytes = utils.load_media_preview_rgb(str(video_file), (100, 100))

    assert (width, height) == (40, 20)
    assert len(rgb_bytes) == width * height * 3
    assert calls[0][0][0] == "bundled-ffmpeg.exe"
    assert calls[0][0][calls[0][0].index("-c:v") + 1] == "png"
    assert calls[0][1]["text"] is False
    assert calls[0][1]["timeout"] == utils.FFMPEG_METADATA_TIMEOUT_SECONDS


def test_load_media_preview_rgb_falls_back_to_converted_sequence(monkeypatch, tmp_path):
    from PIL import Image

    video_file = tmp_path / "Shot_VID_v003.mov"
    video_file.write_text("unsupported codec", encoding="utf-8")
    sequence_dir = tmp_path / "Shot_VID_v003"
    sequence_dir.mkdir()
    Image.new("RGB", (80, 40), "blue").save(sequence_dir / "Shot_VID_v003_00001.png")

    monkeypatch.setattr(utils, "ALLOWED_EXT_VIDEO", [".mov"])
    monkeypatch.setattr(utils, "ALLOWED_EXT_IMAGE", [".png"])
    monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "bundled-ffmpeg.exe")
    monkeypatch.setattr(
        utils.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"unsupported codec",
        ),
    )

    width, height, rgb_bytes = utils.load_media_preview_rgb(str(video_file), (100, 100))

    assert (width, height) == (80, 40)
    assert len(rgb_bytes) == width * height * 3


def test_video_dimensions_and_fps_are_parsed_from_ffmpeg_showinfo(monkeypatch, tmp_path):
    video_file = tmp_path / "metadata.mov"
    video_file.write_text("video", encoding="utf-8")
    monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "bundled-ffmpeg.exe")
    calls = []
    diagnostics = (
        "[Parsed_showinfo_0] config in time_base: 1001/24000, "
        "frame_rate: 24000/1001\n"
        "[Parsed_showinfo_0] n: 0 pts: 0 fmt:yuv420p sar:1/1 s:1920x1080 i:P\n"
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr=diagnostics)

    monkeypatch.setattr(utils.subprocess, "run", fake_run)

    assert utils.read_video_dimensions(str(video_file)) == (1920, 1080)
    assert utils.read_video_fps(str(video_file)) == pytest.approx(24000 / 1001)
    assert len(calls) == 2
    assert all("showinfo" in command for command, _kwargs in calls)
    assert all(
        kwargs["env"] == utils.sanitized_subprocess_environment()
        for _command, kwargs in calls
    )


def test_video_metadata_falls_back_to_ffmpeg_stream_line(monkeypatch, tmp_path):
    video_file = tmp_path / "metadata.mov"
    video_file.write_text("video", encoding="utf-8")
    monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "bundled-ffmpeg.exe")
    monkeypatch.setattr(
        utils.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr=(
                "Stream #0:0: Video: prores, yuv422p10le, "
                "2048x858 [SAR 1:1 DAR 1024:429], 24 fps, 24 tbr\n"
            ),
        ),
    )

    assert utils.read_video_dimensions(str(video_file)) == (2048, 858)
    assert utils.read_video_fps(str(video_file)) == 24.0


def test_count_decoded_video_frames_uses_ffmpeg_progress(monkeypatch, tmp_path):
    video_file = tmp_path / "frames.mov"
    video_file.write_text("video", encoding="utf-8")
    monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "bundled-ffmpeg.exe")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout="frame=1\nprogress=continue\nframe=42\nprogress=end\n",
            stderr="",
        )

    monkeypatch.setattr(utils.subprocess, "run", fake_run)

    assert utils.count_decoded_video_frames(str(video_file)) == 42
    command, kwargs = calls[0]
    assert command[command.index("-fps_mode") + 1] == "passthrough"
    assert command[command.index("-progress") + 1] == "pipe:1"
    assert kwargs["stdout"] == utils.subprocess.PIPE
    assert "timeout" not in kwargs


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "message"),
    [
        (1, "", "decoder failed", "Could not read video frames"),
        (0, "progress=end\n", "", "no readable frames"),
    ],
)
def test_count_decoded_video_frames_rejects_failures(
    monkeypatch,
    tmp_path,
    returncode,
    stdout,
    stderr,
    message,
):
    video_file = tmp_path / "bad.mov"
    video_file.write_text("video", encoding="utf-8")
    monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "bundled-ffmpeg.exe")
    monkeypatch.setattr(
        utils.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        ),
    )

    with pytest.raises(RuntimeError, match=message):
        utils.count_decoded_video_frames(str(video_file))


def test_convert_to_sequence_cancel_removes_partial_folder(monkeypatch):
    tmp_path = make_temp_dir()
    try:
        source_video = tmp_path / "source.mov"
        source_video.write_text("video", encoding="utf-8")

        monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "ffmpeg")

        class FakeProcess:
            returncode = None

            def __init__(self):
                self.terminated = False

            def poll(self):
                return None if not self.terminated else -15

            def terminate(self):
                self.terminated = True
                self.returncode = -15

            def kill(self):
                self.terminated = True
                self.returncode = -9

            def wait(self, timeout=None):
                return self.returncode

            def communicate(self):
                return "", "cancelled"

        fake_process = FakeProcess()
        monkeypatch.setattr(utils.subprocess, "Popen", lambda *args, **kwargs: fake_process)

        target_name = "SC01_SH010_VID_v002_00_sequence"
        with pytest.raises(RuntimeError, match="cancelled"):
            utils.convert_to_sequence(
                str(source_video),
                str(tmp_path),
                target_name,
                cancel_check=lambda: True,
            )

        assert not (tmp_path / target_name).exists()
        assert fake_process.terminated
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_get_target_directory_uses_target_prefix(monkeypatch):
    tmp_path = make_temp_dir()
    try:
        monkeypatch.setattr(config, "TARGET_PREFIX", "publish")
        target_dir = utils.get_target_directory(str(tmp_path), config.TYPE_VID)

        assert Path(target_dir) == tmp_path / "publish" / config.DIR_VIDEO
        assert Path(target_dir).is_dir()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_build_retime_mp4_command_retimes_timestamps_without_audio():
    command = utils.build_retime_mp4_command("source.mp4", "target.mp4")

    assert "setpts=N/(24*TB)" in command
    assert "-fps_mode" in command
    assert "cfr" in command
    assert command[command.index("-r") + 1] == "24"
    assert command[command.index("-video_track_timescale") + 1] == "24000"
    assert "-an" in command


def test_retime_mp4_to_24fps_creates_next_video_version(monkeypatch):
    tmp_path = make_temp_dir()
    try:
        source_video = tmp_path / "camera.mp4"
        source_video.write_text("video", encoding="utf-8")
        (tmp_path / "SQ010_SH020_VID_v001_00.mp4").write_text("old", encoding="utf-8")
        monkeypatch.setattr(config, "FILENAME_TEMPLATE", config.DEFAULT_FILENAME_TEMPLATE)
        monkeypatch.setattr(config, "SUBVERSION_ENABLED", True)
        monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "ffmpeg")
        frame_counts = iter([42, 42])
        monkeypatch.setattr(utils, "count_decoded_video_frames", lambda _: next(frame_counts))
        monkeypatch.setattr(utils, "read_video_fps", lambda _: 24.0)

        class FakeProcess:
            returncode = 0

            def __init__(self, cmd):
                self.cmd = cmd

            def poll(self):
                return self.returncode

            def communicate(self):
                Path(self.cmd[-1]).write_text("retimed", encoding="utf-8")
                return "", ""

        monkeypatch.setattr(
            utils.subprocess,
            "Popen",
            lambda cmd, stdout, stderr, text, creationflags: FakeProcess(cmd),
        )

        new_path, new_name, undo_op = utils.retime_mp4_to_24fps(
            str(source_video),
            str(tmp_path),
            "SQ010",
            "SH020",
        )

        assert new_name == "SQ010_SH020_VID_v002_00.mp4"
        assert Path(new_path).read_text(encoding="utf-8") == "retimed"
        assert undo_op == ("copy", new_path)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_retime_mp4_to_24fps_removes_frame_count_mismatch(monkeypatch):
    tmp_path = make_temp_dir()
    try:
        source_video = tmp_path / "camera.mp4"
        source_video.write_text("video", encoding="utf-8")
        monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "ffmpeg")
        frame_counts = iter([12, 11])
        monkeypatch.setattr(utils, "count_decoded_video_frames", lambda _: next(frame_counts))
        monkeypatch.setattr(utils, "read_video_fps", lambda _: 24.0)

        class FakeProcess:
            returncode = 0

            def __init__(self, cmd):
                self.cmd = cmd

            def poll(self):
                return self.returncode

            def communicate(self):
                Path(self.cmd[-1]).write_text("wrong frames", encoding="utf-8")
                return "", ""

        monkeypatch.setattr(
            utils.subprocess,
            "Popen",
            lambda cmd, stdout, stderr, text, creationflags: FakeProcess(cmd),
        )

        with pytest.raises(RuntimeError, match="frame count mismatch"):
            utils.retime_mp4_to_24fps(str(source_video), str(tmp_path), "SQ010", "SH020")

        assert not list(tmp_path.glob(".*.retime.*.mp4"))
        assert not list(tmp_path.glob("SQ010_SH020_VID_*.mp4"))
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_retime_mp4_to_24fps_removes_fps_mismatch(monkeypatch):
    tmp_path = make_temp_dir()
    try:
        source_video = tmp_path / "camera.mp4"
        source_video.write_text("video", encoding="utf-8")
        monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "ffmpeg")
        frame_counts = iter([12, 12])
        monkeypatch.setattr(utils, "count_decoded_video_frames", lambda _: next(frame_counts))
        monkeypatch.setattr(utils, "read_video_fps", lambda _: 24.1)

        class FakeProcess:
            returncode = 0

            def __init__(self, cmd):
                self.cmd = cmd

            def poll(self):
                return self.returncode

            def communicate(self):
                Path(self.cmd[-1]).write_text("wrong fps", encoding="utf-8")
                return "", ""

        monkeypatch.setattr(
            utils.subprocess,
            "Popen",
            lambda cmd, stdout, stderr, text, creationflags: FakeProcess(cmd),
        )

        with pytest.raises(RuntimeError, match="output FPS mismatch"):
            utils.retime_mp4_to_24fps(str(source_video), str(tmp_path), "SQ010", "SH020")

        assert not list(tmp_path.glob(".*.retime.*.mp4"))
        assert not list(tmp_path.glob("SQ010_SH020_VID_*.mp4"))
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_retime_mp4_to_24fps_cancel_removes_partial_output(monkeypatch):
    tmp_path = make_temp_dir()
    try:
        source_video = tmp_path / "camera.mp4"
        source_video.write_text("video", encoding="utf-8")
        monkeypatch.setattr(utils, "resolve_ffmpeg", lambda: "ffmpeg")
        monkeypatch.setattr(utils, "count_decoded_video_frames", lambda _: 24)
        monkeypatch.setattr(utils, "read_video_fps", lambda _: 24.0)

        class FakeProcess:
            returncode = None

            def __init__(self, cmd):
                self.output_path = Path(cmd[-1])
                self.output_path.write_text("partial", encoding="utf-8")
                self.terminated = False

            def poll(self):
                return None if not self.terminated else -15

            def terminate(self):
                self.terminated = True
                self.returncode = -15

            def kill(self):
                self.terminated = True
                self.returncode = -9

            def wait(self, timeout=None):
                return self.returncode

            def communicate(self):
                return "", "cancelled"

        monkeypatch.setattr(
            utils.subprocess,
            "Popen",
            lambda cmd, stdout, stderr, text, creationflags: FakeProcess(cmd),
        )

        with pytest.raises(RuntimeError, match="cancelled"):
            utils.retime_mp4_to_24fps(
                str(source_video),
                str(tmp_path),
                "SQ010",
                "SH020",
                cancel_check=lambda: True,
            )

        assert not list(tmp_path.glob(".*.retime.*.mp4"))
        assert source_video.exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_additional_category_routes_and_names_with_own_suffix(monkeypatch, tmp_path):
    category = config.MediaCategory("upscale", "UPSCALE", config.MEDIA_TYPE_IMAGE, "UPS")
    monkeypatch.setattr(config, "TARGET_PREFIX", "publish")
    monkeypatch.setattr(config, "FILENAME_TEMPLATE", "{shot}_{type}_v{version}")

    target = utils.get_target_directory(str(tmp_path), category=category)
    name = utils.format_versioned_base_name(
        "SQ010", "SH020", config.TYPE_IMG, 1, category=category
    )

    assert Path(target) == tmp_path / "publish" / "UPSCALE"
    assert name == "SH020_UPS_v001"


def test_rename_target_files_uses_additional_category_suffix(monkeypatch, tmp_path):
    category = config.MediaCategory("upscale", "UPSCALE", config.MEDIA_TYPE_IMAGE, "UPS")
    target = tmp_path / "UPSCALE"
    target.mkdir()
    (target / "raw.png").write_text("pixels", encoding="utf-8")
    monkeypatch.setattr(config, "FILENAME_TEMPLATE", "{shot}_{type}_v{version}")
    monkeypatch.setattr(config, "SUBVERSION_ENABLED", False)

    messages, undo = utils.rename_target_files(
        str(target), "SQ010", "SH020", category=category
    )

    assert (target / "SH020_UPS_v001.png").exists()
    assert messages == ["Renamed: SH020_UPS_v001.png"]
    assert undo[0][0] == "rename"


def test_converted_drag_paths_support_legacy_fallback_mixed_selection_and_dedup(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "ALLOWED_EXT_VIDEO", [".mov"])
    video = tmp_path / "shot.mov"
    video.write_text("video", encoding="utf-8")
    legacy = tmp_path / "shot_sequence"
    legacy.mkdir()
    still = tmp_path / "still.png"
    still.write_text("image", encoding="utf-8")

    assert utils.find_converted_sequence_folder(str(video)) == str(legacy)
    assert utils.resolve_converted_drag_paths([str(video), str(legacy), str(still)]) == [
        str(legacy), str(still)
    ]
    legacy.rmdir()
    assert utils.resolve_converted_drag_path(str(video)) == str(video)


def test_sanitized_subprocess_environment_removes_foreign_runtime_paths(monkeypatch, tmp_path):
    frozen_root = tmp_path / "bundle"
    monkeypatch.setattr(utils.sys, "_MEIPASS", str(frozen_root), raising=False)
    environment = {
        "QT_PLUGIN_PATH": "foreign",
        "QT_QPA_PLATFORM_PLUGIN_PATH": "foreign-platforms",
        "QT_QPA_PLATFORM": "windows",
        "PYTHONHOME": "foreign-python",
        "PYTHONPATH": "foreign-modules",
        "_MEIPASS2": "old",
        "PATH": os.pathsep.join([str(frozen_root), str(frozen_root / "bin"), str(tmp_path / "safe")]),
    }

    cleaned = utils.sanitized_subprocess_environment(environment)

    for key in (
        "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM",
        "PYTHONHOME", "PYTHONPATH", "_MEIPASS2",
    ):
        assert key not in cleaned
    assert cleaned["PATH"] == str(tmp_path / "safe")


def test_validate_ffmpeg_checks_size_and_version(monkeypatch, tmp_path):
    executable = tmp_path / "ffmpeg.exe"
    executable.write_bytes(b"x" * 128)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="ffmpeg version test-build")

    monkeypatch.setattr(utils, "_run_compat", fake_run)

    assert utils.validate_ffmpeg_executable(str(executable), minimum_size=64) is True
    assert calls[0][0] == [str(executable), "-version"]
    assert "QT_PLUGIN_PATH" not in calls[0][1]["env"]
    assert utils.validate_ffmpeg_executable(str(executable), minimum_size=256) is False


def test_validate_pinned_ffmpeg_requires_exact_bytes_and_version(monkeypatch, tmp_path):
    executable = tmp_path / "ffmpeg.exe"
    payload = b"pinned"
    executable.write_bytes(payload)
    monkeypatch.setattr(utils, "EMBEDDED_FFMPEG_SIZE", len(payload))
    monkeypatch.setattr(
        utils,
        "EMBEDDED_FFMPEG_SHA256",
        hashlib.sha256(payload).hexdigest(),
    )
    monkeypatch.setattr(
        utils,
        "_run_ffmpeg_version",
        lambda _path: SimpleNamespace(
            returncode=0,
            stdout=f"ffmpeg version {utils.EMBEDDED_FFMPEG_VERSION_TOKEN}",
        ),
    )

    assert utils.validate_pinned_ffmpeg_executable(str(executable)) is True
    executable.write_bytes(b"changed")
    assert utils.validate_pinned_ffmpeg_executable(str(executable)) is False


def test_resolve_ffmpeg_reuses_valid_shared_executable_without_network(monkeypatch, tmp_path):
    target = tmp_path / "_tools" / "ffmpeg.exe"
    target.parent.mkdir()
    target.write_bytes(b"valid")
    utils.reset_ffmpeg_resolution_state()
    monkeypatch.setattr(utils, "validate_ffmpeg_executable", lambda path, **_: Path(path) == target)
    monkeypatch.setattr(
        utils, "_load_release_metadata",
        lambda *args, **kwargs: pytest.fail("network must not be used for an existing valid executable"),
    )

    assert utils.resolve_ffmpeg(target_path=str(target)) == str(target)


def _patch_fake_ffmpeg_release(monkeypatch, counter=None, delay=0.0):
    digest = "a" * 64
    metadata = {
        "assets": [{
            "name": utils.FFMPEG_ASSET_NAME,
            "browser_download_url": "https://example.invalid/ffmpeg.zip",
            "digest": f"sha256:{digest}",
        }]
    }
    monkeypatch.setattr(utils, "_load_release_metadata", lambda *args, **kwargs: metadata)

    def fake_download(url, destination, expected_sha256, **kwargs):
        if counter is not None:
            counter.append(url)
        if delay:
            time.sleep(delay)
        assert expected_sha256 == digest
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr("ffmpeg/bin/ffmpeg.exe", b"verified executable")

    monkeypatch.setattr(utils, "_download_verified", fake_download)
    monkeypatch.setattr(
        utils, "validate_ffmpeg_executable",
        lambda path, **_: Path(path).is_file() and Path(path).read_bytes() == b"verified executable",
    )


def test_resolve_ffmpeg_installs_verified_archive_atomically(monkeypatch, tmp_path):
    target = tmp_path / "_tools" / "ffmpeg.exe"
    utils.reset_ffmpeg_resolution_state()
    downloads = []
    _patch_fake_ffmpeg_release(monkeypatch, downloads)

    resolved = utils.resolve_ffmpeg(target_path=str(target), timeout=2)

    assert resolved == str(target)
    assert target.read_bytes() == b"verified executable"
    assert downloads == ["https://example.invalid/ffmpeg.zip"]
    assert not list(target.parent.glob(".ffmpeg-install-*"))
    assert not Path(f"{target}{utils.FFMPEG_LOCK_SUFFIX}").exists()


def test_resolve_ffmpeg_concurrent_callers_download_once(monkeypatch, tmp_path):
    target = tmp_path / "_tools" / "ffmpeg.exe"
    utils.reset_ffmpeg_resolution_state()
    downloads = []
    _patch_fake_ffmpeg_release(monkeypatch, downloads, delay=0.05)
    results = []
    errors = []

    def resolve():
        try:
            results.append(utils.resolve_ffmpeg(target_path=str(target), timeout=3))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=resolve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert results == [str(target), str(target)]
    assert len(downloads) == 1


def test_resolve_ffmpeg_memoizes_failed_attempt_for_session(monkeypatch, tmp_path):
    target = tmp_path / "_tools" / "ffmpeg.exe"
    utils.reset_ffmpeg_resolution_state()
    attempts = []
    monkeypatch.setattr(utils, "validate_ffmpeg_executable", lambda *args, **kwargs: False)

    def offline(*args, **kwargs):
        attempts.append(True)
        raise utils.FFmpegResolutionError("offline")

    monkeypatch.setattr(utils, "_load_release_metadata", offline)

    with pytest.raises(utils.FFmpegResolutionError, match="offline"):
        utils.resolve_ffmpeg(target_path=str(target), timeout=1)
    with pytest.raises(utils.FFmpegResolutionError, match="Previous FFmpeg setup attempt failed"):
        utils.resolve_ffmpeg(target_path=str(target), timeout=1)
    assert len(attempts) == 1


def _make_fake_embedded_ffmpeg(monkeypatch, root: Path, payload=b"embedded ffmpeg"):
    asset_dir = root / "embedded_tools" / "ffmpeg"
    asset_dir.mkdir(parents=True)
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(utils, "EMBEDDED_FFMPEG_SIZE", len(payload))
    monkeypatch.setattr(utils, "EMBEDDED_FFMPEG_SHA256", digest)
    (asset_dir / "ffmpeg.exe").write_bytes(payload)
    (asset_dir / "manifest.json").write_text(
        json.dumps(
            {
                "version": utils.EMBEDDED_FFMPEG_VERSION_TOKEN,
                "license": "GPL-3.0-or-later",
                "license_file": "LICENSE.GPLv3.txt",
                "notice_file": "NOTICE.txt",
                "size": len(payload),
                "sha256": digest,
                "embedded_relative_path": "embedded_tools/ffmpeg/ffmpeg.exe",
            }
        ),
        encoding="utf-8",
    )
    (asset_dir / "LICENSE.GPLv3.txt").write_text(
        "GNU GENERAL PUBLIC LICENSE Version 3", encoding="utf-8"
    )
    (asset_dir / "NOTICE.txt").write_text("FFmpeg notice", encoding="utf-8")
    (asset_dir / "provenance.txt").write_text("BtbN FFmpeg Builds", encoding="utf-8")
    return payload


def test_frozen_resolver_installs_verified_embedded_ffmpeg_without_network(
    monkeypatch, tmp_path
):
    embedded_root = tmp_path / "bundle"
    payload = _make_fake_embedded_ffmpeg(monkeypatch, embedded_root)
    target = tmp_path / "runtime" / "Tools" / "ffmpeg.exe"
    utils.reset_ffmpeg_resolution_state()
    monkeypatch.setattr(
        utils,
        "validate_pinned_ffmpeg_executable",
        lambda path, **_kwargs: Path(path).is_file()
        and Path(path).read_bytes() == payload,
    )
    monkeypatch.setattr(
        utils,
        "_load_release_metadata",
        lambda *_args, **_kwargs: pytest.fail("frozen resolver must never use network"),
    )

    resolved = utils.resolve_ffmpeg(
        target_path=str(target),
        embedded_root=str(embedded_root),
        timeout=2,
    )

    assert resolved == str(target)
    assert target.read_bytes() == payload
    assert (target.parent / "ffmpeg.manifest.json").is_file()
    assert "GNU GENERAL PUBLIC LICENSE" in (
        target.parent / "ffmpeg.LICENSE.GPLv3.txt"
    ).read_text(encoding="utf-8")
    assert (target.parent / "ffmpeg.NOTICE.txt").is_file()
    assert (target.parent / "ffmpeg.provenance.txt").is_file()
    assert not list(target.parent.glob(".ffmpeg-install-*"))


def test_frozen_resolver_never_falls_back_to_network_when_payload_is_missing(
    monkeypatch, tmp_path
):
    target = tmp_path / "runtime" / "Tools" / "ffmpeg.exe"
    embedded_root = tmp_path / "empty-bundle"
    embedded_root.mkdir()
    utils.reset_ffmpeg_resolution_state()
    attempts = []
    monkeypatch.setattr(
        utils,
        "validate_pinned_ffmpeg_executable",
        lambda *_args, **_kwargs: False,
    )

    def network(*_args, **_kwargs):
        attempts.append(True)
        raise AssertionError("network must not be called")

    monkeypatch.setattr(utils, "_load_release_metadata", network)

    with pytest.raises(utils.FFmpegResolutionError, match="missing embedded FFmpeg"):
        utils.resolve_ffmpeg(
            target_path=str(target),
            embedded_root=str(embedded_root),
            timeout=1,
        )
    assert attempts == []


def test_frozen_resolver_prefers_valid_local_tool_over_embedded_payload(
    monkeypatch, tmp_path
):
    target = tmp_path / "Tools" / "ffmpeg.exe"
    target.parent.mkdir()
    target.write_bytes(b"local")
    utils.reset_ffmpeg_resolution_state()
    monkeypatch.setattr(
        utils,
        "validate_pinned_ffmpeg_executable",
        lambda path, **_kwargs: Path(path) == target,
    )
    monkeypatch.setattr(
        utils,
        "_verified_embedded_ffmpeg",
        lambda *_args, **_kwargs: pytest.fail("valid local FFmpeg must win"),
    )

    assert utils.resolve_ffmpeg(
        target_path=str(target),
        embedded_root=str(tmp_path / "bundle"),
    ) == str(target)


def test_frozen_resolver_replaces_runnable_but_non_pinned_local_ffmpeg(
    monkeypatch, tmp_path
):
    embedded_root = tmp_path / "bundle"
    payload = _make_fake_embedded_ffmpeg(monkeypatch, embedded_root)
    target = tmp_path / "Tools" / "ffmpeg.exe"
    target.parent.mkdir()
    target.write_bytes(b"another runnable ffmpeg")
    utils.reset_ffmpeg_resolution_state()
    monkeypatch.setattr(
        utils,
        "validate_ffmpeg_executable",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        utils,
        "validate_pinned_ffmpeg_executable",
        lambda path, **_kwargs: Path(path).is_file()
        and Path(path).read_bytes() == payload,
    )

    resolved = utils.resolve_ffmpeg(
        target_path=str(target),
        embedded_root=str(embedded_root),
        timeout=2,
    )

    assert resolved == str(target)
    assert target.read_bytes() == payload


def test_embedded_ffmpeg_rejects_hash_damage(monkeypatch, tmp_path):
    embedded_root = tmp_path / "bundle"
    _make_fake_embedded_ffmpeg(monkeypatch, embedded_root)
    source = embedded_root / "embedded_tools" / "ffmpeg" / "ffmpeg.exe"
    source.write_bytes(b"tampered")

    with pytest.raises(
        utils.FFmpegResolutionError,
        match="size mismatch|SHA-256 mismatch",
    ):
        utils._verified_embedded_ffmpeg(str(embedded_root))


@pytest.mark.parametrize("template,filename,expected", [
    ("{shot}-{type}-{version}-r{subversion}{format}", "SH010-STILL-001-r02.png", (1,2)),
    ("{sequence}_{shot}_{type}_v{version}_{subversion}{format}", "SH010_IMG_v1000_101.jpg", (1000,101)),
    ("{shot}.{version}.{subversion}{format}", "SH010.002.03.png", (2,3)),
    ("{shot}_{version}{format}", "SH010_003.png", (3,0)),
    ("{shot}_{type}{version}_{subversion}{format}", "SH010_IMG005_01.png", (5,1)),
    ("{shot}-{version}-{version}-s{subversion}{format}", "SH010-006-006-s04.png", (6,4)),
])
def test_custom_template_version_parser(monkeypatch,template,filename,expected):
    monkeypatch.setattr(config,"FILENAME_TEMPLATE",template)
    assert utils.parse_version_from_filename(filename)==expected
