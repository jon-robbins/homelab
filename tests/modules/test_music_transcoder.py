import os
import shutil
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "config" / "music-transcoder" / "transcode.sh"


def test_transcode_script_exists_and_is_executable():
    assert SCRIPT.is_file(), f"Expected {SCRIPT} to exist"
    assert os.access(SCRIPT, os.X_OK)
    assert SCRIPT.read_text().startswith("#!/bin/bash")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required for transcoding test")
def test_transcode_converts_flac_and_removes_original(tmp_path):
    music_dir = tmp_path / "music"
    album_dir = music_dir / "Artist With Spaces" / "Album (2024) [FLAC]"
    album_dir.mkdir(parents=True)
    flac_file = album_dir / "01 - Test Track.flac"

    # Generate a 1-second silent flac using ffmpeg
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", str(flac_file)],
        check=True,
        capture_output=True,
    )
    assert flac_file.is_file()

    # Run transcode.sh in ONCE mode
    env = os.environ.copy()
    env["MUSIC_DIR"] = str(music_dir)
    env["ONCE"] = "1"
    env["LIDARR_URL"] = "http://127.0.0.1:9999"  # Dummy URL, shouldn't fail if unreachable

    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    mp3_file = album_dir / "01 - Test Track.mp3"
    assert mp3_file.is_file(), "Expected 01 - Test Track.mp3 to be created"
    assert not flac_file.is_file(), "Expected 01 - Test Track.flac to be deleted"
    assert mp3_file.stat().st_size > 1000


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required for transcoding test")
def test_transcode_cleans_up_flac_if_valid_mp3_exists(tmp_path):
    music_dir = tmp_path / "music"
    album_dir = music_dir / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    flac_file = album_dir / "track01.flac"
    mp3_file = album_dir / "track01.mp3"

    # Generate both FLAC and valid MP3 (> 1024 bytes)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", str(flac_file)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", str(mp3_file)],
        check=True,
        capture_output=True,
    )
    assert flac_file.is_file()
    assert mp3_file.is_file()
    assert mp3_file.stat().st_size > 1024

    env = os.environ.copy()
    env["MUSIC_DIR"] = str(music_dir)
    env["ONCE"] = "1"
    env["LIDARR_URL"] = "http://127.0.0.1:9999"

    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    assert mp3_file.is_file()
    assert not flac_file.is_file(), "Expected orphan flac to be removed"


def test_transcode_nonexistent_music_dir(tmp_path):
    missing_dir = tmp_path / "does_not_exist"
    env = os.environ.copy()
    env["MUSIC_DIR"] = str(missing_dir)
    env["ONCE"] = "1"

    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert "does not exist, skipping" in result.stdout


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required for transcoding test")
def test_transcode_notifies_lidarr_when_api_key_provided(tmp_path):
    import http.server
    import json
    import threading

    music_dir = tmp_path / "music"
    album_dir = music_dir / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    flac_file = album_dir / "track01.flac"

    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", str(flac_file)],
        check=True,
        capture_output=True,
    )

    received_requests = []

    class MockLidarrHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received_requests.append({
                "path": self.path,
                "api_key": self.headers.get("X-Api-Key"),
                "body": json.loads(body.decode("utf-8")),
            })
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), MockLidarrHandler)
    server_port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request)
    thread.daemon = True
    thread.start()

    env = os.environ.copy()
    env["MUSIC_DIR"] = str(music_dir)
    env["ONCE"] = "1"
    env["LIDARR_URL"] = f"http://127.0.0.1:{server_port}/lidarr"
    env["LIDARR_API_KEY"] = "dummy-lidarr-token"

    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    thread.join(timeout=2)
    server.server_close()

    assert len(received_requests) == 1
    req = received_requests[0]
    assert req["path"] == "/lidarr/api/v1/command"
    assert req["api_key"] == "dummy-lidarr-token"
    assert req["body"]["name"] == "RescanFolders"
    assert req["body"]["folders"] == [str(album_dir)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required for transcoding test")
def test_transcode_reads_api_key_from_secret_file(tmp_path):
    import http.server
    import json
    import threading

    music_dir = tmp_path / "music"
    album_dir = music_dir / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    flac_file = album_dir / "track01.flac"

    secret_file = tmp_path / "LIDARR_API_KEY"
    secret_file.write_text("secret-file-token\n")

    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", str(flac_file)],
        check=True,
        capture_output=True,
    )

    received_requests = []

    class MockLidarrHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received_requests.append({
                "path": self.path,
                "api_key": self.headers.get("X-Api-Key"),
                "body": json.loads(body.decode("utf-8")),
            })
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), MockLidarrHandler)
    server_port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request)
    thread.daemon = True
    thread.start()

    env = os.environ.copy()
    env["MUSIC_DIR"] = str(music_dir)
    env["ONCE"] = "1"
    env["LIDARR_URL"] = f"http://127.0.0.1:{server_port}/lidarr"
    env["API_KEY_FILE"] = str(secret_file)
    env.pop("LIDARR_API_KEY", None)

    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    thread.join(timeout=2)
    server.server_close()

    assert len(received_requests) == 1
    req = received_requests[0]
    assert req["api_key"] == "secret-file-token"


def test_transcode_preserves_flac_on_ffmpeg_failure(tmp_path):
    music_dir = tmp_path / "music"
    album_dir = music_dir / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    corrupt_file = album_dir / "bad_track.flac"
    corrupt_file.write_bytes(b"not an audio stream")

    env = os.environ.copy()
    env["MUSIC_DIR"] = str(music_dir)
    env["ONCE"] = "1"

    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert "ERROR: ffmpeg failed on" in result.stdout

    # Ensure source file is untouched and no corrupted mp3 exists
    assert corrupt_file.is_file(), "Original flac must not be deleted on ffmpeg failure"
    assert not (album_dir / "bad_track.mp3").exists()
    assert not (album_dir / "bad_track.mp3.tmp").exists()


