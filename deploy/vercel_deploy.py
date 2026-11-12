"""Минимальный клиент Vercel REST API: проекты, переменные, деплой файлами.

Нужен там, где недоступны npm/`vercel` CLI или не установлено GitHub-приложение
Vercel: проект собирается локально, файлы загружаются напрямую через API.

Токен берётся из переменной окружения VERCEL_TOKEN или из ~/.vercel-token
(создать: https://vercel.com/account/settings/tokens, scope — Full Account).

Запуск:
    python deploy/vercel_deploy.py whoami
    python deploy/vercel_deploy.py create-project <name> [framework]
    python deploy/vercel_deploy.py env <project> <env.json>
    python deploy/vercel_deploy.py deploy <project> <dir> [--all] [--target production]
    python deploy/vercel_deploy.py status <deployment-id>

Флаг --all нужен для статики (frontend/dist): без него каталоги верхнего уровня
вроде data/ пропускаются как служебные.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://api.vercel.com"


def _read_token() -> str:
    token = os.environ.get("VERCEL_TOKEN", "").strip()
    if token:
        return token
    path = Path(os.path.expanduser("~/.vercel-token"))
    if not path.exists():
        raise SystemExit(
            "Нет токена: задайте VERCEL_TOKEN или сохраните его в ~/.vercel-token"
        )
    return path.read_text(encoding="ascii").strip()


TOKEN = _read_token()

# Каталоги верхнего уровня, которые не нужны на хостинге или содержат секреты.
# Важно: только верхний уровень — app/data со справочниками обязателен.
SKIP_TOP_DIRS = {"data", "logs", "tests", "scripts", "sql", ".venv", "venv",
                 "node_modules", ".git"}
# Кэши можно выкидывать на любой глубине.
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SKIP_FILES = {
    ".env", ".dockerignore", "Dockerfile", "docker-compose.yml", "pytest.ini",
    "requirements-dev.txt", ".vercelignore", "radar.db", ".gitignore",
}
SKIP_SUFFIXES = (".session", ".db", ".db-wal", ".db-shm", ".pyc", ".log", ".bak")


def request(method: str, path: str, body: bytes | None = None,
            headers: dict[str, str] | None = None) -> tuple[int, dict | str]:
    url = path if path.startswith("http") else API + path
    hdrs = {"Authorization": f"Bearer {TOKEN}", "User-Agent": "drone-radar-deploy"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                raw = resp.read()
                ctype = resp.headers.get("content-type", "")
                if "json" in ctype:
                    return resp.status, json.loads(raw or b"{}")
                return resp.status, raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw or b"{}")
            except Exception:
                return exc.code, raw.decode("utf-8", "replace")
        except Exception as exc:  # сеть из РФ нестабильна — повторяем
            last = exc
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"{method} {path}: {last}")


def jrequest(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    status, data = request(method, path, body, {"Content-Type": "application/json"})
    if not isinstance(data, dict):
        data = {"raw": data}
    return status, data


def collect_files(root: Path, keep_top_dirs: bool = False) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        parts = set(rel.parts[:-1])
        if not keep_top_dirs and rel.parts[0] in SKIP_TOP_DIRS and len(rel.parts) > 1:
            continue
        if parts & SKIP_DIRS:
            continue
        name = rel.name
        if name in SKIP_FILES or name.startswith("radar.db"):
            continue
        if path.suffix in SKIP_SUFFIXES or ".bak-" in name:
            continue
        files.append((rel.as_posix(), path))
    return files


def upload_one(item: tuple[str, Path]) -> dict:
    rel, path = item
    data = path.read_bytes()
    digest = hashlib.sha1(data).hexdigest()
    status, resp = request(
        "POST", "/v2/files", data,
        {"Content-Type": "application/octet-stream", "x-now-digest": digest,
         "x-now-size": str(len(data))},
    )
    if status not in (200, 201):
        raise RuntimeError(f"upload {rel}: HTTP {status} {resp}")
    return {"file": rel, "sha": digest, "size": len(data)}


def cmd_whoami() -> None:
    status, data = jrequest("GET", "/v2/user")
    print(status, json.dumps(data, ensure_ascii=False)[:400])


def cmd_create_project(name: str, framework: str | None) -> None:
    payload: dict = {"name": name}
    if framework:
        payload["framework"] = framework
    status, data = jrequest("POST", "/v10/projects", payload)
    if status == 409:
        print("проект уже существует")
        status, data = jrequest("GET", f"/v9/projects/{urllib.parse.quote(name)}")
    print(status, json.dumps({k: data.get(k) for k in ("id", "name", "framework")}, ensure_ascii=False))
    if data.get("error"):
        print(json.dumps(data, ensure_ascii=False)[:600])


def cmd_env(project: str, env_file: str) -> None:
    """Загружает переменные из JSON-файла (ключ → значение), обновляя старые."""
    values = json.loads(Path(env_file).read_text(encoding="utf-8-sig"))
    targets = ["production", "preview", "development"]
    for key, value in values.items():
        payload = {"key": key, "value": str(value), "type": "encrypted", "target": targets}
        status, data = jrequest(
            "POST",
            f"/v10/projects/{urllib.parse.quote(project)}/env?upsert=true",
            payload,
        )
        print(f"{key}: {status}" + ("" if status < 400 else f" {json.dumps(data, ensure_ascii=False)[:300]}"))


def cmd_deploy(project: str, directory: str, target: str, keep_top_dirs: bool = False) -> None:
    root = Path(directory).resolve()
    files = collect_files(root, keep_top_dirs)
    total = sum(p.stat().st_size for _, p in files)
    print(f"файлов: {len(files)}, размер: {total / 1024:.0f} КБ")

    with ThreadPoolExecutor(max_workers=8) as pool:
        uploaded = list(pool.map(upload_one, files))

    payload = {
        "name": project,
        "project": project,
        "target": target,
        "files": uploaded,
    }
    status, data = jrequest("POST", "/v13/deployments?skipAutoDetectionConfirmation=1", payload)
    print(status, json.dumps({k: data.get(k) for k in ("id", "url", "readyState", "alias")}, ensure_ascii=False))
    if status >= 400:
        print(json.dumps(data, ensure_ascii=False)[:1500])
        return

    dep_id = data["id"]
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(10)
        st, info = jrequest("GET", f"/v13/deployments/{dep_id}")
        state = info.get("readyState")
        print("...", state, flush=True)
        if state in ("READY", "ERROR", "CANCELED"):
            print(json.dumps({"url": info.get("url"), "aliases": info.get("alias"),
                              "error": info.get("errorMessage")}, ensure_ascii=False))
            break


def cmd_status(dep_id: str) -> None:
    status, data = jrequest("GET", f"/v13/deployments/{dep_id}")
    print(status, json.dumps({k: data.get(k) for k in ("url", "readyState", "alias", "errorMessage")}, ensure_ascii=False))


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(1)
    cmd, rest = args[0], args[1:]
    if cmd == "whoami":
        cmd_whoami()
    elif cmd == "create-project":
        cmd_create_project(rest[0], rest[1] if len(rest) > 1 else None)
    elif cmd == "env":
        cmd_env(rest[0], rest[1])
    elif cmd == "deploy":
        target = "production"
        if "--target" in rest:
            target = rest[rest.index("--target") + 1]
        cmd_deploy(rest[0], rest[1], target, keep_top_dirs="--all" in rest)
    elif cmd == "status":
        cmd_status(rest[0])
    else:
        print(f"неизвестная команда: {cmd}")
        raise SystemExit(1)
