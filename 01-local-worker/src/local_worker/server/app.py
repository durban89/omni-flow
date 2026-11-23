"""A tiny local web console for driving the pipeline by hand.

The panel is a development convenience only: it binds to loopback, has no
authentication and simply shells out to the existing CLI stages so a manual
run behaves exactly like `python -m local_worker.pipeline.run <stage>`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import uuid
from collections import Counter
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from local_worker.config import load_settings, resolve_llm_settings
from local_worker.content.review import ReviewService
from local_worker.errors import ConfigError, LocalWorkerError
from local_worker.pipeline.run import (
    DEFAULT_SETTINGS,
    PROJECT_ROOT,
    _data_dir,
    _resolve,
    _section,
    run_export,
)
from local_worker.server import offers as offer_store

STAGES = (
    "scrape",
    "content",
    "validate",
    "export",
    "build",
    "sync-affiliate",
    "sync-kv",
)
MAX_LOG_LINES = 4000
MAX_JOBS_KEPT = 30
UI_FILE = Path(__file__).with_name("index.html")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _truncate(value: Any, limit: int = 320) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else f"{text[:limit]}…"


class Job:
    """One subprocess run of a pipeline stage with its captured output."""

    def __init__(self, stage: str, mode: str | None) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.stage = stage
        self.mode = mode
        self.status = "running"
        self.started_at = _now()
        self.finished_at: str | None = None
        self.exit_code: int | None = None
        self.error: str | None = None
        self.command: list[str] = []
        self._lines: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        text = line.rstrip("\n")
        with self._lock:
            if len(self._lines) < MAX_LOG_LINES:
                self._lines.append({"ts": _now(), "text": text})

    def finish(self, exit_code: int | None, error: str | None = None) -> None:
        with self._lock:
            self.exit_code = exit_code
            self.error = error
            self.status = "succeeded" if exit_code == 0 and error is None else "failed"
            self.finished_at = _now()

    def log_slice(self, cursor: int) -> tuple[list[dict[str, str]], int]:
        with self._lock:
            cursor = max(cursor, 0)
            return self._lines[cursor:], len(self._lines)

    def to_dict(self, cursor: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "stage": self.stage,
            "mode": self.mode,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "error": self.error,
            "command": self.command,
        }
        if cursor is not None:
            lines, next_cursor = self.log_slice(cursor)
            payload["lines"] = lines
            payload["cursor"] = next_cursor
        return payload


class JobManager:
    """Serialises stage runs so two writes never race over the same files."""

    def __init__(self, project_root: Path, settings_path: Path) -> None:
        self.project_root = project_root
        self.settings_path = settings_path
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._running: str | None = None
        self._lock = threading.Lock()

    def running(self) -> Job | None:
        with self._lock:
            job = self._jobs.get(self._running) if self._running else None
            return job if job is not None and job.status == "running" else None

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [self._jobs[job_id] for job_id in reversed(self._order) if job_id in self._jobs]
        return [job.to_dict() for job in jobs[:10]]

    def start(self, stage: str, mode: str | None = None) -> Job:
        if stage not in STAGES:
            raise ValueError(f"unsupported stage: {stage}")

        with self._lock:
            current = self._jobs.get(self._running) if self._running else None
            if current is not None and current.status == "running":
                raise RuntimeError(f"stage '{current.stage}' is still running")

            job = Job(stage, mode)
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._running = job.id
            while len(self._order) > MAX_JOBS_KEPT:
                self._jobs.pop(self._order.pop(0), None)

        threading.Thread(target=self._execute, args=(job,), daemon=True).start()
        return job

    def _command(self, job: Job) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "local_worker.pipeline.run",
            job.stage,
            "--settings",
            str(self.settings_path),
        ]
        if job.mode:
            command.extend(["--mode", job.mode])
        return command

    def _execute(self, job: Job) -> None:
        source_dir = str(self.project_root / "01-local-worker" / "src")
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["LOCAL_WORKER_ROOT"] = str(self.project_root)
        env["PYTHONPATH"] = (
            source_dir
            if not env.get("PYTHONPATH")
            else source_dir + os.pathsep + env["PYTHONPATH"]
        )
        if job.mode:
            env["LOCAL_WORKER_MODE"] = job.mode

        job.command = self._command(job)
        try:
            # Arguments are fixed by the stage list, so no shell is involved.
            process = subprocess.Popen(
                job.command,
                cwd=self.project_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            job.append(f"[server] failed to start: {exc}")
            job.finish(None, str(exc))
            return

        assert process.stdout is not None
        for line in process.stdout:
            job.append(line)
        exit_code = process.wait()
        job.append(f"[server] stage '{job.stage}' exited with code {exit_code}")
        job.finish(exit_code)


def _settings_snapshot(settings_path: Path, mode: str | None) -> dict[str, Any]:
    settings = load_settings(settings_path, mode=mode)
    app = _section(settings, "app")
    llm = resolve_llm_settings(settings)
    return {
        "mode": app.get("mode"),
        "site_name": app.get("site_name"),
        "base_url": app.get("base_url"),
        "scrape_cap": _section(settings, "scraper").get("max_items_per_run"),
        "content_cap": _section(settings, "pipeline").get("max_items_per_run"),
        "model": llm.get("model"),
        "llm_enabled": llm.get("enable"),
    }


def _collection(root: Path, settings_path: Path) -> dict[str, Any]:
    settings = load_settings(settings_path)
    data_dir = _data_dir(root, settings)

    raw = _read_json(data_dir / "raw_items.json") or {}
    content = _read_json(data_dir / "content_items.json") or {}
    manifest = _read_json(data_dir / "manifest.json") or {}

    content_items = [entry for entry in content.get("items") or [] if isinstance(entry, dict)]
    raw_items = [entry for entry in raw.get("items") or [] if isinstance(entry, dict)]
    status_counts = Counter(str(item.get("status") or "unknown") for item in content_items)

    return {
        "data_dir": str(data_dir),
        "raw": {
            "path": str(data_dir / "raw_items.json"),
            "count": len(raw_items),
            "generated_at": raw.get("generated_at"),
            "source_ids": list(raw.get("source_ids") or []),
        },
        "content": {
            "path": str(data_dir / "content_items.json"),
            "count": len(content_items),
            "generated_at": content.get("generated_at"),
            "status_counts": dict(sorted(status_counts.items())),
        },
        "manifest": manifest,
    }


def _raw_items(root: Path, settings_path: Path) -> list[dict[str, Any]]:
    settings = load_settings(settings_path)
    payload = _read_json(_data_dir(root, settings) / "raw_items.json") or {}
    items = []
    for index, entry in enumerate(payload.get("items") or []):
        if not isinstance(entry, dict):
            continue
        items.append(
            {
                "index": index,
                "source_id": entry.get("source_id"),
                "title": entry.get("title"),
                "source_url": entry.get("source_url"),
                "publisher": entry.get("publisher"),
                "category": entry.get("category"),
                "tags": list(entry.get("tags") or []),
                "retrieved_at": entry.get("retrieved_at"),
                "summary": _truncate(entry.get("summary")),
                "body_chars": len(str(entry.get("body") or "")),
            }
        )
    return items


def _raw_item(root: Path, settings_path: Path, index: int) -> dict[str, Any] | None:
    settings = load_settings(settings_path)
    payload = _read_json(_data_dir(root, settings) / "raw_items.json") or {}
    entries = [entry for entry in payload.get("items") or [] if isinstance(entry, dict)]
    if not 0 <= index < len(entries):
        return None
    return entries[index]


def _content_items(root: Path, settings_path: Path) -> list[dict[str, Any]]:
    settings = load_settings(settings_path)
    payload = _read_json(_data_dir(root, settings) / "content_items.json") or {}
    items = []
    for index, entry in enumerate(payload.get("items") or []):
        if not isinstance(entry, dict):
            continue
        seo = entry.get("seo") if isinstance(entry.get("seo"), dict) else {}
        items.append(
            {
                "index": index,
                "id": entry.get("id"),
                "slug": entry.get("slug"),
                "title": entry.get("title"),
                "status": entry.get("status"),
                "reviewed": bool(entry.get("reviewed")),
                "word_count": entry.get("word_count"),
                "category": entry.get("category"),
                "tags": list(entry.get("tags") or []),
                "affiliate_slug": entry.get("affiliate_slug"),
                "published_at": entry.get("published_at"),
                "updated_at": entry.get("updated_at"),
                "noindex": seo.get("noindex"),
                "summary": _truncate(entry.get("summary")),
                "source_url": (
                    entry["sources"][0].get("url")
                    if isinstance(entry.get("sources"), list) and entry["sources"]
                    and isinstance(entry["sources"][0], dict)
                    else None
                ),
            }
        )
    return items


def _content_item(root: Path, settings_path: Path, index: int) -> dict[str, Any] | None:
    settings = load_settings(settings_path)
    payload = _read_json(_data_dir(root, settings) / "content_items.json") or {}
    entries = [entry for entry in payload.get("items") or [] if isinstance(entry, dict)]
    if not 0 <= index < len(entries):
        return None
    return entries[index]


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        project_root: Path,
        settings_path: Path,
        mode: str | None = None,
    ) -> None:
        self.project_root = project_root
        self.settings_path = settings_path
        self.mode_override = mode
        self.manager = JobManager(project_root, settings_path)
        super().__init__(address, ControlHandler)


class ControlHandler(BaseHTTPRequestHandler):
    server: ControlServer
    server_version = "LocalWorkerConsole/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the console quiet
        return

    # ---------------------------------------------------------------- helpers
    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _is_loopback(self) -> bool:
        host = self.client_address[0]
        return host in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}

    def _serve_ui(self) -> None:
        if not UI_FILE.exists():
            self._error(HTTPStatus.NOT_FOUND, "index.html is missing from the package")
            return
        body = UI_FILE.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _settings_path(self) -> Path:
        return self.server.settings_path

    def _default_mode(self) -> str | None:
        return self.server.mode_override

    # ------------------------------------------------------------------ routes
    def do_GET(self) -> None:
        if not self._is_loopback():
            self._error(HTTPStatus.FORBIDDEN, "local access only")
            return

        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        try:
            if route in {"/", "/index.html"}:
                self._serve_ui()
            elif route == "/api/overview":
                self._handle_overview()
            elif route == "/api/raw":
                self._send_json(
                    {"items": _raw_items(self.server.project_root, self._settings_path())}
                )
            elif route == "/api/raw/item":
                self._handle_item(_raw_item, query)
            elif route == "/api/content":
                self._send_json(
                    {"items": _content_items(self.server.project_root, self._settings_path())}
                )
            elif route == "/api/content/item":
                self._handle_item(_content_item, query)
            elif route == "/api/offers":
                self._send_json(offer_store.describe(self.server.project_root))
            elif route == "/api/jobs":
                self._send_json({"jobs": self.server.manager.history()})
            elif route.startswith("/api/jobs/"):
                self._handle_job(route.rsplit("/", 1)[-1], query)
            else:
                self._error(HTTPStatus.NOT_FOUND, f"unknown route: {route}")
        except (ConfigError, OSError, ValueError, KeyError, TypeError) as exc:
            # Surface setup problems in the panel instead of dropping the connection.
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def do_POST(self) -> None:
        if not self._is_loopback():
            self._error(HTTPStatus.FORBIDDEN, "local access only")
            return

        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        routes = {
            "/api/run": self._handle_run,
            "/api/content/approve": self._handle_content_approve,
            "/api/offers": self._handle_offer_save,
            "/api/offers/delete": self._handle_offer_delete,
        }
        handler = routes.get(route)
        if handler is None:
            self._error(HTTPStatus.NOT_FOUND, f"unknown route: {route}")
            return

        payload = self._read_json_body()
        if payload is None:
            return
        handler(payload)

    def _read_json_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid JSON body")
            return None
        if not isinstance(payload, dict):
            self._error(HTTPStatus.BAD_REQUEST, "expected a JSON object")
            return None
        return payload

    def _handle_run(self, payload: dict[str, Any]) -> None:
        stage = str(payload.get("stage") or "").strip()
        mode = str(payload.get("mode") or "").strip() or self._default_mode()
        if mode not in {None, "test", "production"}:
            self._error(HTTPStatus.BAD_REQUEST, f"unsupported mode: {mode}")
            return

        try:
            job = self.server.manager.start(stage, mode=mode)
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except RuntimeError as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
            return

        self._send_json({"job": job.to_dict(cursor=0)}, status=HTTPStatus.ACCEPTED)

    def _handle_content_approve(self, payload: dict[str, Any]) -> None:
        target = str(payload.get("slug") or payload.get("id") or "").strip()
        if not target:
            self._error(HTTPStatus.BAD_REQUEST, "slug is required")
            return

        running = self.server.manager.running()
        if running is not None:
            self._error(HTTPStatus.CONFLICT, f"stage '{running.stage}' is still running")
            return

        try:
            settings = load_settings(self._settings_path())
            item = ReviewService(
                _data_dir(self.server.project_root, settings)
            ).approve(
                target,
                reviewer=str(payload.get("reviewer") or "console"),
                notes=str(payload.get("notes") or ""),
            )
        except (LocalWorkerError, OSError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, f"{type(exc).__name__}: {exc}")
            return

        result: dict[str, Any] = {
            "item": {
                "slug": item.slug,
                "status": item.status,
                "reviewed": item.reviewed,
                "published_at": item.published_at.isoformat() if item.published_at else None,
            },
            "exported": False,
        }
        # Mirror the CLI: refresh the Hugo posts so the approved page is live.
        build_conf = _section(settings, "build")
        site_dir = _resolve(
            self.server.project_root, str(build_conf.get("site_dir") or "03-distribution/site")
        )
        try:
            if site_dir.exists():
                run_export(self.server.project_root, settings)
                result["exported"] = True
        except (LocalWorkerError, OSError) as exc:
            result["warning"] = f"export failed: {type(exc).__name__}: {exc}"
        self._send_json(result)

    def _handle_offer_save(self, payload: dict[str, Any]) -> None:
        original = payload.get("original_slug")
        try:
            result = offer_store.save_offer(
                self.server.project_root,
                payload.get("offer"),
                original_slug=str(original).strip() if original else None,
            )
        except LookupError as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))
            return
        except (ConfigError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(result)

    def _handle_offer_delete(self, payload: dict[str, Any]) -> None:
        try:
            result = offer_store.delete_offer(
                self.server.project_root, str(payload.get("slug") or "")
            )
        except LookupError as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))
            return
        except (ConfigError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(result)

    # ---------------------------------------------------------------- handlers
    def _handle_overview(self) -> None:
        settings_path = self._settings_path()
        try:
            settings = _settings_snapshot(settings_path, self._default_mode())
        except (ConfigError, OSError, ValueError) as exc:
            settings = {"error": f"{type(exc).__name__}: {exc}"}

        try:
            collection = _collection(self.server.project_root, settings_path)
        except (ConfigError, OSError, ValueError) as exc:
            collection = {"error": f"{type(exc).__name__}: {exc}"}

        running = self.server.manager.running()
        self._send_json(
            {
                "settings": settings,
                "collection": collection,
                "running": running.to_dict() if running else None,
                "stages": list(STAGES),
                "jobs": self.server.manager.history(),
            }
        )

    def _handle_item(self, loader: Any, query: dict[str, list[str]]) -> None:
        raw_index = (query.get("index") or ["-1"])[0]
        try:
            index = int(raw_index)
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "index must be an integer")
            return

        item = loader(self.server.project_root, self._settings_path(), index)
        if item is None:
            self._error(HTTPStatus.NOT_FOUND, f"no item at index {index}")
            return
        self._send_json({"item": item})

    def _handle_job(self, job_id: str, query: dict[str, list[str]]) -> None:
        job = self.server.manager.get(job_id)
        if job is None:
            self._error(HTTPStatus.NOT_FOUND, f"unknown job: {job_id}")
            return
        try:
            cursor = int((query.get("cursor") or ["0"])[0])
        except ValueError:
            cursor = 0
        self._send_json({"job": job.to_dict(cursor=cursor)})


def create_server(
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    project_root: Path = PROJECT_ROOT,
    settings_path: Path = DEFAULT_SETTINGS,
    mode: str | None = None,
) -> ControlServer:
    return ControlServer((host, port), project_root, settings_path, mode)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    project_root: Path = PROJECT_ROOT,
    settings_path: Path = DEFAULT_SETTINGS,
    mode: str | None = None,
) -> None:
    server = create_server(
        host,
        port,
        project_root=project_root,
        settings_path=settings_path,
        mode=mode,
    )
    print(f"local-worker console: http://{host}:{server.server_port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="local-worker console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--mode", choices=["test", "production"])
    args = parser.parse_args(argv)
    run_server(
        args.host,
        args.port,
        settings_path=args.settings,
        mode=args.mode,
    )


if __name__ == "__main__":
    main()
