"""The compose file, .env.example and the services' Settings classes must agree (no missing/renamed variables)."""
import importlib
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def interpolate(value, env):
    if not isinstance(value, str):
        return value

    def sub(m):
        name, op, default = m.group(1), m.group(2), m.group(3)
        if name in env and env[name] != "":
            return env[name]
        if op and op.startswith(":?"):
            raise AssertionError(f"required variable {name} missing from .env.example")
        return default or ""

    return re.sub(r"\$\{([A-Z0-9_]+)(?:(:-|:\?)([^}]*))?\}", sub, value)


ENV = load_env(ROOT / ".env.example")
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
PY_SERVICES = {n: s for n, s in COMPOSE["services"].items() if "build" in s and isinstance(s["build"], dict) and s["build"].get("args", {}).get("PACKAGE")}


def test_every_python_service_is_in_compose():
    assert set(PY_SERVICES) == {"ingestion-service", "trace-service", "log-service", "metrics-service", "rca-service", "query-service",
                                "api-gateway", "order-service", "payment-service", "notification-service"}


@pytest.mark.parametrize("name", sorted(PY_SERVICES))
def test_settings_can_be_built_from_compose_environment(name):
    svc = PY_SERVICES[name]
    package = svc["build"]["args"]["PACKAGE"]
    env = {k.lower(): interpolate(str(v), ENV) for k, v in svc["environment"].items()}
    settings_cls = importlib.import_module(f"{package}.config").Settings
    settings = settings_cls(**env)
    assert settings.internal_api_token and len(settings.internal_api_token) >= 32
    if name == "api-gateway":
        settings.validate_secrets()


def test_database_urls_match_init_script():
    init = (ROOT / "db/init/01-create-databases.sh").read_text()
    seen = set()
    for name, svc in PY_SERVICES.items():
        url = interpolate(svc["environment"]["DATABASE_URL"], ENV)
        if url.startswith("none://"):
            assert name == "query-service"
            continue
        m = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@mysql:3306/([a-z_]+)\?charset=utf8mb4", url)
        assert m, url
        user, _, db = m.groups()
        assert f"create {db} " in init.replace("    ", " ") or re.search(rf"create\s+{db}\s+{user}\s", init), f"{db}/{user} not created by init script"
        seen.add(db)
    assert len(seen) == 9, "every database-owning service must have its own database"


def test_no_default_secrets_are_hardcoded_in_compose():
    text = (ROOT / "docker-compose.yml").read_text()
    for needle in ("password: ", "PASSWORD: change", "secret: "):
        assert needle not in text.replace("PASSWORD: ${", "")


def test_example_secrets_are_placeholders():
    for key, value in ENV.items():
        if any(w in key for w in ("PASSWORD", "SECRET", "TOKEN", "KEY")):
            assert "change-me" in value or "change-me" in value.lower() or value.endswith("change-me"), f"{key} must be an obvious placeholder"
