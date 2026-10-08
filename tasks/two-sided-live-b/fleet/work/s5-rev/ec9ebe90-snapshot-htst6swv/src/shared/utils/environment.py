"""Read environment settings with the repository dotenv fallback."""

import os

from shared.constants import paths


def env_value(name: str) -> str | None:
    """Read an env var, falling back to a KEY=value line in the repo `.env`."""
    value = os.getenv(name)
    if value:
        return value
    env_path = paths.BASE_DIR / ".env"
    if not env_path.exists():
        return None
    prefix = f"{name}="
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(prefix) and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None
