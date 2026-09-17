"""One-time server-local migration. Never prints credential values or calls APIs."""

import argparse
import os
import re
import tempfile
from pathlib import Path

MARKER = "# walk-root-env-migrated-v1"
LIFE_KEYS = {"DATA_GO_KR_KEY", "KAKAO_REST_KEY", "KMA_HUB_KEY"}


def read_env(path):
    text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    values = {}
    for line in text.splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if match:
            values[match[1]] = match[2].strip()
    return text, values


def migrate(root):
    target = root / ".env"
    if not target.is_file():
        raise ValueError("Project root .env is required")
    original = target.read_bytes()
    text, current = read_env(target)
    if MARKER in text.splitlines():
        return 0
    _, source = read_env(root / "backend/.env")
    legacy_path = os.environ.get("WALK_PUBLIC_ENV_FILE") or current.get("WALK_PUBLIC_ENV_FILE")
    if legacy_path:
        legacy_path = legacy_path.strip().strip('"').strip("'")
        if "$" in legacy_path or " #" in legacy_path:
            raise ValueError("Resolve the legacy settings path before migrating")
        path = Path(legacy_path)
        if not path.is_absolute():
            path = root / path
        if not path.is_file():
            raise ValueError("Configured legacy settings file is missing; migrate manually first")
    else:
        path = root / "backend/.env.walk-public.local"
    _, legacy = read_env(path)
    source.update(legacy)
    # Preserve the app default used before explicit Compose mapping was added.
    source.setdefault("DAENGS_WALK_DIARY_SPACE_ENABLED", "true")
    additions = {}
    for key, value in source.items():
        if (key.startswith("DAENGS_WALK_") or key in LIFE_KEYS) and key not in current:
            if value.startswith(("'", '"')) and not value.endswith(value[0]):
                raise ValueError("Multiline selected settings need manual migration")
            additions[key] = value
    for new, old in (
        ("DAENGS_DATA_GO_KR_SERVICE_KEY", "PLACE_DATA_GO_KR_SERVICE_KEY"),
        ("DAENGS_KTO_SERVICE_KEY", "PLACE_KTO_SERVICE_KEY"),
    ):
        if new not in current and old in current:
            additions[new] = current[old]
    backup = root / ".env.walk-root-backup.local"
    if backup.exists():
        raise ValueError("Migration backup already exists; inspect before retrying")
    with backup.open("xb") as handle:
        os.chmod(backup, 0o600)
        handle.write(original)
    content = (
        text.rstrip()
        + "\n"
        + "".join(f"{key}={value}\n" for key, value in sorted(additions.items()))
        + MARKER
        + "\n"
    )
    fd, temporary = tempfile.mkstemp(prefix=".env.migrate-", suffix=".local", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        if target.read_bytes() != original:
            raise ValueError("Root .env changed during migration; refusing replacement")
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return len(additions)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        count = migrate(args.root.resolve())
    except (OSError, ValueError):
        # Exceptions can contain paths or file contents. Keep server logs value-free.
        raise SystemExit("Root env migration stopped; inspect local settings and backup") from None
    print(f"Root env migration complete: {count} missing settings added (values hidden)")
