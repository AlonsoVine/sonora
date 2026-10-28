import unittest
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import app


class PlaylistValidationTests(unittest.TestCase):
    def test_accepts_archive_item_url(self):
        self.assertEqual(
            app.parse_archive_url("https://archive.org/details/sample_collection?view=1"),
            "sample_collection",
        )

    def test_rejects_non_archive_and_non_item_urls(self):
        for url in (
            "http://archive.org/details/example",
            "https://youtube.com/playlist?list=abc",
            "https://archive.org/download/example/file.mp3",
            "https://archive.org/details/../private",
            "https://archive.org.evil.test/details/example",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                app.parse_archive_url(url)

    def test_only_explicitly_supported_licenses_pass(self):
        self.assertTrue(app.license_is_allowed("https://creativecommons.org/licenses/by/4.0/"))
        self.assertTrue(app.license_is_allowed("http://creativecommons.org/publicdomain/zero/1.0/"))
        self.assertFalse(app.license_is_allowed("https://creativecommons.org/licenses/by-nc/4.0/"))
        self.assertFalse(app.license_is_allowed(""))

    def test_redirect_handler_blocks_non_archive_hosts(self):
        handler = app.SafeArchiveRedirect()
        request = urllib.request.Request("https://archive.org/download/item/audio.mp3")
        for target in (
            "http://ia800.us.archive.org/audio.mp3",
            "https://example.com/audio.mp3",
            "https://ia800.us.archive.org:8443/audio.mp3",
        ):
            with self.subTest(target=target):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    handler.redirect_request(request, None, 302, "Found", {}, target)
                error.exception.close()

    def test_audio_filter_excludes_video_and_path_traversal(self):
        metadata = {
            "files": [
                {"name": "track.flac"},
                {"name": "video.mp4"},
                {"name": "../outside.mp3"},
                {"name": "subdir\\song.wav"},
            ]
        }
        self.assertEqual(
            [item["name"] for item in app.audio_files(metadata)],
            ["track.flac", "subdir\\song.wav"],
        )

    def test_windows_reserved_names_are_safe(self):
        self.assertEqual(app.safe_component("CON", "fallback"), "_CON")
        self.assertEqual(app.safe_component("../album", "fallback"), "_album")

    def test_ffmpeg_is_found_next_to_portable_executable(self):
        from pathlib import Path

        with patch.object(app.sys, "frozen", True, create=True), patch.object(
            app.sys, "executable", r"C:\Sonora\Sonora.exe"
        ), patch.object(app, "shutil") as shutil_mock:
            shutil_mock.which.return_value = None
            with patch.object(Path, "is_file", return_value=True):
                self.assertEqual(app.ffmpeg_executable(), r"C:\Sonora\ffmpeg.exe")
            shutil_mock.which.assert_not_called()

    def test_track_skips_existing_output_without_downloading(self):
        with patch.object(app, "download_to_file") as download, patch.object(
            app, "update_track"
        ) as update:
            from pathlib import Path
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as directory:
                output = Path(directory) / "song.mp3"
                output.touch()
                app.run_track(
                    "job", 0, {"output_name": "song.mp3", "name": "song.flac"},
                    192, "item", Path(directory), "ffmpeg",
                )
            download.assert_not_called()
            update.assert_called_once_with(
                "job", 0, status="skipped",
                message="El archivo ya existe; no se sobrescribió.",
            )

    def test_http_server_serves_interface_assets(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.SonoraHandler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            with urllib.request.urlopen(f"{base_url}/") as response:
                page = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("/static/styles.css", page)
            for asset in ("styles.css", "app.js"):
                with urllib.request.urlopen(f"{base_url}/static/{asset}") as response:
                    self.assertEqual(response.status, 200)
                    self.assertGreater(len(response.read()), 0)
            with urllib.request.urlopen(f"{base_url}/api/health") as response:
                self.assertEqual(response.status, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
