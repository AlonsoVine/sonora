from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


APP_NAME = "Sonora"
BITRATES = {128, 192, 256, 320}
ALLOWED_LICENSES = {
    ("creativecommons.org", "/publicdomain/zero/1.0"),
    ("creativecommons.org", "/publicdomain/mark/1.0"),
    ("creativecommons.org", "/licenses/by/1.0"),
    ("creativecommons.org", "/licenses/by/2.0"),
    ("creativecommons.org", "/licenses/by/3.0"),
    ("creativecommons.org", "/licenses/by/4.0"),
    ("creativecommons.org", "/licenses/by-sa/1.0"),
    ("creativecommons.org", "/licenses/by-sa/2.0"),
    ("creativecommons.org", "/licenses/by-sa/3.0"),
    ("creativecommons.org", "/licenses/by-sa/4.0"),
}
AUDIO_EXTENSIONS = {
    ".aac", ".aiff", ".aif", ".flac", ".m4a", ".mp3", ".oga", ".ogg",
    ".opus", ".wav", ".wma",
}
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sonora-job")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class SafeArchiveRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        target = urllib.parse.urlsplit(newurl)
        hostname = (target.hostname or "").lower()
        try:
            port = target.port
        except ValueError:
            port = -1
        if target.scheme != "https" or port not in {None, 443} or not (
            hostname == "archive.org" or hostname.endswith(".archive.org")
        ):
            raise urllib.error.HTTPError(
                newurl, code, "Redirect outside archive.org is not allowed", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


ARCHIVE_OPENER = urllib.request.build_opener(SafeArchiveRedirect)


def resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


def output_root() -> Path:
    configured = os.environ.get("MUSIC_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "musica"
    return Path(__file__).resolve().parent / "musica"


def ffmpeg_executable() -> str | None:
    configured = os.environ.get("FFMPEG_PATH")
    if configured:
        return configured
    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).resolve().parent / "ffmpeg.exe"
        if bundled.is_file():
            return str(bundled)
    return shutil.which("ffmpeg")


def safe_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned or cleaned in {".", ".."}:
        cleaned = fallback
    cleaned = cleaned[:100].rstrip(" .") or fallback
    if cleaned.split(".", 1)[0].upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }:
        cleaned = f"_{cleaned}"
    return cleaned


def parse_archive_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or hostname not in {"archive.org", "www.archive.org"}:
        raise ValueError(
            "Fuente no admitida. Usa un enlace HTTPS de un elemento de Internet Archive "
            "(archive.org/details/...)."
        )
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0] != "details":
        raise ValueError(
            "El enlace debe apuntar a un elemento de Internet Archive: "
            "https://archive.org/details/identificador"
        )
    identifier = urllib.parse.unquote(parts[1])
    if not IDENTIFIER_RE.fullmatch(identifier) or identifier in {".", ".."}:
        raise ValueError("El identificador del elemento no tiene un formato válido.")
    return identifier


def license_is_allowed(license_url: str) -> bool:
    parsed = urllib.parse.urlsplit(license_url.strip())
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.hostname or "").lower() == "creativecommons.org"
        and parsed.path.rstrip("/") in {path for _, path in ALLOWED_LICENSES}
    )


def fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Sonora/1.0 (authorized audio workflow)"}
    )
    with ARCHIVE_OPENER.open(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("Internet Archive devolvió metadatos con un formato inesperado.")
    return payload


def audio_files(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    files = metadata.get("files", [])
    if not isinstance(files, list):
        return []
    selected = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        normalized = name.replace("\\", "/")
        if any(part in {"", ".", ".."} for part in normalized.split("/")):
            continue
        if Path(normalized).suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        selected.append(entry)
    return selected


def update_job(job_id: str, **changes: Any) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(changes)


def update_track(job_id: str, index: int, **changes: Any) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job["tracks"][index].update(changes)


def archive_download_url(identifier: str, filename: str) -> str:
    encoded_name = "/".join(urllib.parse.quote(part, safe="") for part in filename.split("/"))
    return f"https://archive.org/download/{urllib.parse.quote(identifier, safe='')}/{encoded_name}"


def download_to_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Sonora/1.0 (authorized audio workflow)"}
    )
    with ARCHIVE_OPENER.open(request, timeout=60) as response:
        with destination.open("wb") as target:
            shutil.copyfileobj(response, target, length=1024 * 1024)


def run_track(job_id: str, index: int, track: dict[str, Any], bitrate: int,
              identifier: str, folder: Path, ffmpeg: str) -> None:
    output = folder / track["output_name"]
    if output.exists():
        if not output.is_file():
            update_track(
                job_id, index, status="failed",
                message=f"El destino existe y no es un archivo: {output.name}",
            )
            return
        update_track(job_id, index, status="skipped", message="El archivo ya existe; no se sobrescribió.")
        return

    update_track(job_id, index, status="downloading", message="Descargando desde Internet Archive.")
    source_name = track["name"].replace("\\", "/")
    source_suffix = Path(source_name).suffix or ".audio"
    source_temp: Path | None = None
    converted_temp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".sonora-source-", suffix=source_suffix, dir=folder, delete=False
        ) as file:
            source_temp = Path(file.name)
        with tempfile.NamedTemporaryFile(
            prefix=".sonora-converted-", suffix=".mp3", dir=folder, delete=False
        ) as file:
            converted_temp = Path(file.name)
        download_to_file(archive_download_url(identifier, source_name), source_temp)
        update_track(job_id, index, status="converting", message=f"Convirtiendo a MP3, {bitrate} kbps.")

        creator = track.get("creator", "")
        if isinstance(creator, list):
            creator = ", ".join(str(item) for item in creator)
        creator = creator or ""
        metadata_args = [
            "-metadata", f"title={track.get('title') or Path(source_name).stem}",
            "-metadata", f"artist={creator}",
            "-metadata", f"license={track['license']}",
            "-metadata", f"comment=Fuente: https://archive.org/details/{identifier} | Licencia: {track['license']}",
        ]
        result = subprocess.run(
            [
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i",
                str(source_temp), "-vn", "-codec:a", "libmp3lame",
                "-b:a", f"{bitrate}k", *metadata_args,
                "-id3v2_version", "3", "-f", "mp3", str(converted_temp),
            ],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip().splitlines()
            message = detail[-1] if detail else "ffmpeg no pudo convertir este archivo."
            raise RuntimeError(message[:500])
        try:
            output_descriptor = os.open(
                output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666
            )
        except FileExistsError:
            update_track(job_id, index, status="skipped", message="El archivo ya existe; no se sobrescribió.")
            return
        try:
            with os.fdopen(output_descriptor, "wb") as target, converted_temp.open("rb") as source:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        except OSError:
            output.unlink(missing_ok=True)
            raise
        update_track(
            job_id, index, status="processed", message="Procesada correctamente.",
            output=str(output),
        )
    except (OSError, urllib.error.URLError, subprocess.SubprocessError, RuntimeError) as error:
        update_track(job_id, index, status="failed", message=str(error)[:500])
    finally:
        for temporary in (source_temp, converted_temp):
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


def execute_job(job_id: str, identifier: str, bitrate: int) -> None:
    try:
        update_job(job_id, status="loading", message="Comprobando metadatos y permisos de uso.")
        payload = fetch_json(f"https://archive.org/metadata/{urllib.parse.quote(identifier, safe='')}")
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("No se encontraron metadatos para este elemento.")
        license_url = str(metadata.get("licenseurl") or "")
        if not license_is_allowed(license_url):
            raise ValueError(
                "No se descargó nada: el elemento no declara una licencia admitida "
                "(CC0, dominio público o CC BY/CC BY-SA). Verifica los permisos por separado."
            )
        entries = audio_files(payload)
        if not entries:
            raise ValueError("El elemento no contiene archivos con un formato de audio admitido.")
        ffmpeg = ffmpeg_executable()
        if not ffmpeg:
            raise RuntimeError(
                "No se encontró ffmpeg. Instálalo, coloca ffmpeg.exe junto al ejecutable "
                "o configura FFMPEG_PATH."
            )

        title = str(metadata.get("title") or identifier)
        folder_suffix = f" [{identifier}]"
        if len(folder_suffix) >= 100:
            folder_suffix = f" [{hashlib.sha256(identifier.encode('utf-8')).hexdigest()[:16]}]"
        folder_title = safe_component(title, identifier)
        folder_name = f"{folder_title[:100 - len(folder_suffix)].rstrip(' .')}{folder_suffix}"
        folder = output_root() / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        creator = metadata.get("creator", "")
        tracks: list[dict[str, Any]] = []
        used_names: set[str] = set()
        for index, entry in enumerate(entries, start=1):
            source_name = str(entry["name"]).replace("\\", "/")
            stem = safe_component(Path(source_name).stem, f"pista-{index:02d}")
            output_name = f"{stem}.mp3"
            if output_name.casefold() in used_names:
                output_name = f"{stem}-{index:02d}.mp3"
            used_names.add(output_name.casefold())
            entry_license = str(entry.get("licenseurl") or license_url)
            license_allowed = license_is_allowed(entry_license)
            tracks.append({
                "name": source_name,
                "title": str(entry.get("title") or Path(source_name).stem),
                "creator": creator,
                "output_name": output_name,
                "status": "queued" if license_allowed else "failed",
                "message": "En espera." if license_allowed else "No se descargó: la licencia de este archivo no está admitida.",
                "license": entry_license,
            })
        if not any(track["status"] == "queued" for track in tracks):
            update_job(
                job_id, title=title, folder=str(folder), status="failed",
                message="No se descargó ninguna pista: los archivos no tienen una licencia admitida.",
                tracks=tracks,
            )
            return
        update_job(
            job_id, title=title, folder=str(folder), status="processing",
            message=f"Procesando {len(tracks)} pista(s) autorizada(s).", tracks=tracks,
        )
        for index, track in enumerate(tracks):
            if track["status"] != "queued":
                continue
            run_track(job_id, index, track, bitrate, identifier, folder, ffmpeg)
        with JOBS_LOCK:
            statuses = [track["status"] for track in JOBS[job_id]["tracks"]]
            failures = statuses.count("failed")
            processed = statuses.count("processed")
            skipped = statuses.count("skipped")
            if failures == len(statuses):
                final_status = "failed"
            elif failures:
                final_status = "completed_with_errors"
            else:
                final_status = "completed"
            JOBS[job_id].update(
                status=final_status,
                message=f"Resultado: {processed} procesada(s), {skipped} omitida(s), {failures} fallida(s).",
            )
    except Exception as error:
        update_job(job_id, status="failed", message=str(error)[:700])


class SonoraHandler(BaseHTTPRequestHandler):
    server_version = "Sonora/1.0"

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"[sonora] {self.address_string()} - {format_string % args}")

    def send_json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/api/health":
            self.send_json(200, {"status": "ok", "name": APP_NAME})
            return
        if self.path.startswith("/api/jobs/"):
            job_id = self.path.removeprefix("/api/jobs/")
            with JOBS_LOCK:
                job = copy.deepcopy(JOBS.get(job_id))
            if job is None:
                self.send_json(404, {"error": "No se encontró ese trabajo."})
                return
            self.send_json(200, job)
            return
        static_files = {
            "/static/styles.css": ("static/styles.css", "text/css; charset=utf-8"),
            "/static/app.js": ("static/app.js", "text/javascript; charset=utf-8"),
        }
        if self.path in static_files:
            relative_path, content_type = static_files[self.path]
            try:
                content = resource_path(relative_path).read_bytes()
            except OSError as error:
                self.send_json(500, {"error": f"No se pudo cargar un recurso: {error}"})
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
            return
        if self.path not in {"/", "/index.html"}:
            self.send_error(404)
            return
        try:
            content = resource_path("static/index.html").read_bytes()
        except OSError as error:
            self.send_json(500, {"error": f"No se pudo cargar la interfaz: {error}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        if self.path != "/api/jobs":
            self.send_json(404, {"error": "Ruta no encontrada."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16_384:
                raise ValueError("La solicitud está vacía o supera el tamaño permitido.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("El cuerpo de la solicitud no es válido.")
            identifier = parse_archive_url(str(body.get("url", "")))
            bitrate = body.get("bitrate")
            if type(bitrate) is not int or bitrate not in BITRATES:
                raise ValueError("Elige una calidad válida: 128, 192, 256 o 320 kbps.")
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error)})
            return

        import uuid

        job_id = uuid.uuid4().hex
        with JOBS_LOCK:
            JOBS[job_id] = {
                "id": job_id,
                "identifier": identifier,
                "bitrate": bitrate,
                "status": "queued",
                "message": "Trabajo en cola.",
                "title": identifier,
                "folder": "",
                "tracks": [],
            }
        POOL.submit(execute_job, job_id, identifier, bitrate)
        self.send_json(202, {"id": job_id})


def main() -> None:
    root = output_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".sonora-write-check"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        raise SystemExit(
            f"No se puede escribir en la carpeta de música:\n{root}\n"
            f"Elige una ubicación con permisos de escritura o ejecuta la aplicación "
            f"desde una carpeta accesible.\nDetalle: {error}"
        ) from error

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer((host, port), SonoraHandler)
    address = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}"
    print(f"{APP_NAME} disponible en {address}")
    print(f"Carpeta de salida: {root}")
    if os.environ.get("SONORA_NO_BROWSER") != "1":
        threading.Timer(0.8, webbrowser.open, args=(address,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando Sonora...")
    finally:
        server.server_close()
        POOL.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
