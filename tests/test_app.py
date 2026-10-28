import json
import unittest
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
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

    def test_unsupported_source_error_explains_youtube_is_not_supported(self):
        with self.assertRaisesRegex(ValueError, "no admite YouTube ni listas de reproducción"):
            app.parse_archive_url("https://www.youtube.com/watch?v=example&list=example")

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

    def test_history_is_persisted_and_running_jobs_are_marked_interrupted(self):
        job = {
            "id": "test-job",
            "identifier": "sample_collection",
            "bitrate": 192,
            "status": "processing",
            "message": "Convirtiendo.",
            "title": "Sample",
            "folder": "musica",
            "tracks": [{"name": "song.flac", "status": "converting", "message": "Convirtiendo."}],
        }
        with TemporaryDirectory() as directory, patch.object(
            app, "output_root", return_value=Path(directory)
        ):
            with app.JOBS_LOCK:
                app.JOBS.clear()
                app.JOBS[job["id"]] = job
                app._persist_jobs_locked()
                app.JOBS.clear()

            app.load_jobs_history()

            self.assertEqual(app.JOBS["test-job"]["status"], "failed")
            self.assertIn("interrumpió al cerrar Sonora", app.JOBS["test-job"]["message"])
            self.assertEqual(app.JOBS["test-job"]["tracks"][0]["status"], "failed")
            self.assertTrue(app.history_path().is_file())
            self.assertIsNone(app.HISTORY_WARNING)
            with app.JOBS_LOCK:
                app.JOBS.clear()

    def test_corrupt_history_is_reported(self):
        with TemporaryDirectory() as directory, patch.object(
            app, "output_root", return_value=Path(directory)
        ):
            app.history_path().write_text("{invalid", encoding="utf-8")

            app.load_jobs_history()

            self.assertIsNotNone(app.HISTORY_WARNING)
            self.assertIn("No se pudo leer el historial", app.HISTORY_WARNING)
            with app.JOBS_LOCK:
                app.JOBS.clear()
                app.HISTORY_WARNING = None

    def test_history_retains_only_the_latest_50_completed_jobs(self):
        with TemporaryDirectory() as directory, patch.object(
            app, "output_root", return_value=Path(directory)
        ):
            with app.JOBS_LOCK:
                app.JOBS.clear()
                for index in range(51):
                    job_id = f"job-{index:02d}"
                    app.JOBS[job_id] = {
                        "id": job_id,
                        "identifier": job_id,
                        "bitrate": 192,
                        "status": "completed",
                        "tracks": [],
                    }
                app._persist_jobs_locked()
                records = json.loads(app.history_path().read_text(encoding="utf-8"))
                app.JOBS.clear()

            self.assertEqual(len(records), 50)
            self.assertEqual(records[0]["id"], "job-01")
            self.assertEqual(records[-1]["id"], "job-50")

    def test_remote_errors_explain_what_to_try(self):
        not_found = urllib.error.HTTPError(
            "https://archive.org/metadata/missing", 404, "Not Found", {}, None
        )
        try:
            self.assertIn("Comprueba el enlace", app.user_facing_error(not_found))
        finally:
            not_found.close()
        self.assertIn(
            "Comprueba tu conexión",
            app.user_facing_error(urllib.error.URLError("offline")),
        )

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
                self.assertIn("no admite YouTube ni listas de reproducción", page)
                self.assertIn("URL DEL ELEMENTO DE INTERNET ARCHIVE", page)
            for asset in ("styles.css", "app.js"):
                with urllib.request.urlopen(f"{base_url}/static/{asset}") as response:
                    self.assertEqual(response.status, 200)
                    self.assertGreater(len(response.read()), 0)
            with urllib.request.urlopen(f"{base_url}/api/health") as response:
                self.assertEqual(response.status, 200)
            with urllib.request.urlopen(f"{base_url}/api/jobs") as response:
                history = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertIn("jobs", history)
                self.assertIn("historyWarning", history)
                self.assertTrue(history["canOpenFolder"])
            request = urllib.request.Request(
                f"{base_url}/api/open-folder", method="POST"
            )
            with patch.object(app, "open_output_folder", return_value="C:\\Musica") as open_folder:
                with urllib.request.urlopen(request) as response:
                    opened = json.loads(response.read())
                    self.assertEqual(response.status, 200)
                    self.assertEqual(opened["folder"], "C:\\Musica")
                open_folder.assert_called_once_with()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
