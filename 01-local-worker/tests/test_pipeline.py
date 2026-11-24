from pathlib import Path

import pytest

from local_worker.config import apply_mode
from local_worker.errors import ConfigError, ContentError
from local_worker.pipeline import run as pipeline
from local_worker.pipeline.run import (
    build_parser,
    pages_project,
    run_pages_deploy,
    run_worker_deploy,
    worker_name,
)


def test_parser_accepts_all_stages() -> None:
    parser = build_parser()
    args = parser.parse_args(["all"])
    assert args.stage == "all"


def test_parser_accepts_deploy_pages() -> None:
    args = build_parser().parse_args(["deploy-pages"])
    assert args.stage == "deploy-pages"


def test_parser_accepts_deploy_worker() -> None:
    args = build_parser().parse_args(["deploy-worker"])
    assert args.stage == "deploy-worker"


def _settings(
    tmp_path: Path,
    project: str | None = "example-matrix",
    mode: str = "test",
) -> dict:
    cloudflare: dict = {"worker_name": "condom-iq-router", "kv_namespace_binding": "APP_KV"}
    if project:
        cloudflare["pages_project"] = project
    return {
        "app": {"mode": mode},
        "pipeline": {"output_dir": str(tmp_path / "build/public")},
        "build": {"site_dir": str(tmp_path / "site")},
        "cloudflare": cloudflare,
    }


def test_cloudflare_targets_follow_mode(monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_PAGES_PROJECT", raising=False)
    settings = {
        "app": {"mode": "test"},
        "cloudflare": {"pages_project": "condom-iq", "worker_name": "condom-iq-router"},
        "modes": {
            "test": {
                "cloudflare": {
                    "pages_project": "condom-iq-dev",
                    "worker_name": "condom-iq-router-dev",
                }
            },
            "production": {
                "cloudflare": {
                    "pages_project": "condom-iq",
                    "worker_name": "condom-iq-router",
                }
            },
        },
    }

    test_settings = apply_mode(settings)
    assert pages_project(test_settings) == "condom-iq-dev"
    assert worker_name(test_settings) == "condom-iq-router-dev"

    prod_settings = apply_mode(settings, mode="production")
    assert pages_project(prod_settings) == "condom-iq"
    assert worker_name(prod_settings) == "condom-iq-router"


def test_pages_deploy_builds_then_publishes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_PAGES_PROJECT", raising=False)
    worker_dir = tmp_path / "02-cloudflare/worker"
    worker_dir.mkdir(parents=True)
    events: list[object] = []
    commands: list[list[str]] = []

    monkeypatch.setattr(pipeline, "run_build", lambda root, settings: events.append("build"))

    def fake_run(command, **kwargs):
        commands.append(command)
        events.append(("deploy", kwargs.get("cwd")))

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    run_pages_deploy(tmp_path, _settings(tmp_path))

    assert events == ["build", ("deploy", worker_dir)]
    assert commands == [
        [
            "npx",
            "wrangler",
            "pages",
            "deploy",
            str(tmp_path / "build/public"),
            "--project-name",
            "example-matrix",
        ]
    ]


def test_pages_deploy_env_overrides_settings(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "02-cloudflare/worker").mkdir(parents=True)
    monkeypatch.setenv("CLOUDFLARE_PAGES_PROJECT", "env-project")
    commands: list[list[str]] = []
    monkeypatch.setattr(pipeline, "run_build", lambda root, settings: None)
    monkeypatch.setattr(
        pipeline.subprocess, "run", lambda command, **kwargs: commands.append(command)
    )

    run_pages_deploy(tmp_path, _settings(tmp_path, project="settings-project"))

    assert commands[0][-1] == "env-project"


def test_pages_deploy_requires_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_PAGES_PROJECT", raising=False)
    monkeypatch.setattr(
        pipeline, "run_build", lambda root, settings: pytest.fail("build must not run")
    )

    with pytest.raises(ConfigError, match="pages_project"):
        run_pages_deploy(tmp_path, _settings(tmp_path, project=None))


WRANGLER_TOML = '''
name = "cps-edge-router"
main = "src/index.ts"

[[kv_namespaces]]
binding = "APP_KV"
id = "toml-id"
'''


def _worker_dir(tmp_path: Path) -> Path:
    worker_dir = tmp_path / "02-cloudflare/worker"
    worker_dir.mkdir(parents=True)
    (worker_dir / "wrangler.toml").write_text(WRANGLER_TOML, encoding="utf-8")
    return worker_dir


def test_worker_deploy_uses_mode_namespace_and_name(tmp_path: Path, monkeypatch) -> None:
    worker_dir = _worker_dir(tmp_path)
    monkeypatch.delenv("CLOUDFLARE_KV_NAMESPACE_ID", raising=False)
    monkeypatch.setenv("CLOUDFLARE_KV_NAMESPACE_ID_PROD", "prod-id")
    monkeypatch.setenv("CLOUDFLARE_KV_NAMESPACE_ID_DEV", "dev-id")
    calls: list[tuple[list[str], Path]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs.get("cwd")))

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    run_worker_deploy(tmp_path, _settings(tmp_path))

    assert calls == [
        (
            [
                "npx",
                "wrangler",
                "deploy",
                "--config",
                "wrangler.deploy.toml",
                "--var",
                "ENVIRONMENT:test",
            ],
            worker_dir,
        )
    ]
    generated = (worker_dir / "wrangler.deploy.toml").read_text(encoding="utf-8")
    assert 'name = "condom-iq-router"' in generated
    assert 'main = "src/index.ts"' in generated
    assert 'id = "dev-id"' in generated


def test_worker_deploy_production_uses_prod_namespace(tmp_path: Path, monkeypatch) -> None:
    worker_dir = _worker_dir(tmp_path)
    monkeypatch.delenv("CLOUDFLARE_KV_NAMESPACE_ID", raising=False)
    monkeypatch.setenv("CLOUDFLARE_KV_NAMESPACE_ID_PROD", "prod-id")
    monkeypatch.setenv("CLOUDFLARE_KV_NAMESPACE_ID_DEV", "dev-id")
    monkeypatch.setattr(pipeline.subprocess, "run", lambda command, **kwargs: None)

    run_worker_deploy(tmp_path, _settings(tmp_path, mode="production"))

    generated = (worker_dir / "wrangler.deploy.toml").read_text(encoding="utf-8")
    assert 'id = "prod-id"' in generated


def test_worker_deploy_requires_wrangler_toml(tmp_path: Path) -> None:
    (tmp_path / "02-cloudflare/worker").mkdir(parents=True)

    with pytest.raises(ConfigError, match="wrangler.toml"):
        run_worker_deploy(tmp_path, _settings(tmp_path))


def test_worker_deploy_requires_worker_dir(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="worker directory"):
        run_worker_deploy(tmp_path, _settings(tmp_path))


def test_worker_deploy_reports_missing_npx(tmp_path: Path, monkeypatch) -> None:
    _worker_dir(tmp_path)

    def missing(command, **kwargs):
        raise FileNotFoundError("npx")

    monkeypatch.setattr(pipeline.subprocess, "run", missing)

    with pytest.raises(ContentError, match="npx not found"):
        run_worker_deploy(tmp_path, _settings(tmp_path))


def test_pages_deploy_reports_missing_npx(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "02-cloudflare/worker").mkdir(parents=True)
    monkeypatch.delenv("CLOUDFLARE_PAGES_PROJECT", raising=False)
    monkeypatch.setattr(pipeline, "run_build", lambda root, settings: None)

    def missing(command, **kwargs):
        raise FileNotFoundError("npx")

    monkeypatch.setattr(pipeline.subprocess, "run", missing)

    with pytest.raises(ContentError, match="npx not found"):
        run_pages_deploy(tmp_path, _settings(tmp_path))
