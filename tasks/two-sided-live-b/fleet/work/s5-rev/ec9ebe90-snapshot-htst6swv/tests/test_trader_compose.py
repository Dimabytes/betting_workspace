"""Compose and Makefile isolate live vs paper traders (file-only, no recreate)."""

# Compose JSON has no project schema; we only assert key names and bind paths.
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO / "compose.yaml"
MAKEFILE_PATH = REPO / "Makefile"
SERVICE_KEY = re.compile(r"^  ([a-z0-9-]+):", re.MULTILINE)
SHARED_ENV_KEYS = (
    "DOTA_TRADING_MODE",
    "LOL_TRADING_MODE",
    "DOTA_ARCHIVE_ROOT",
    "LOL_ARCHIVE_ROOT",
)


def _compose_text() -> str:
    """Read compose.yaml from the repo root."""
    return COMPOSE_PATH.read_text()


def _service_blocks(text: str) -> dict[str, str]:
    """Map each two-space service key under services: to its YAML block."""
    marker = "services:"
    start = text.index(marker) + len(marker)
    body = text[start:]
    matches = list(SERVICE_KEY.finditer(body))
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        blocks[match.group(1)] = body[match.start() : end]
    return blocks


def _command_tokens(block: str) -> list[str]:
    """Quoted tokens in the service command array."""
    start = block.index("command:")
    end = block.index("]", start)
    return re.findall(r'"([^"]*)"', block[start : end + 1])


def _mode_after_flag(tokens: list[str]) -> str:
    """Token immediately after --mode in a command array."""
    index = tokens.index("--mode")
    return tokens[index + 1]


def _load_compose_config() -> dict[str, object]:
    """Read-only docker compose config as JSON, without interpolating secrets."""
    completed = subprocess.run(
        ["docker", "compose", "config", "--no-interpolate", "--format", "json"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    loaded: object = json.loads(completed.stdout)
    assert isinstance(loaded, dict)
    return loaded


def _environment_keys(service: object) -> set[str]:
    """Environment key names from a compose config service."""
    assert isinstance(service, dict)
    raw: object = service.get("environment") or {}
    if isinstance(raw, dict):
        return {str(key) for key in raw}
    assert isinstance(raw, list)
    keys: set[str] = set()
    for item in raw:
        assert isinstance(item, str)
        keys.add(item.split("=", 1)[0])
    return keys


def _state_bind(service: object) -> tuple[str, str]:
    """Host source and container target of the live_paper state bind."""
    assert isinstance(service, dict)
    volumes: object = service.get("volumes") or []
    assert isinstance(volumes, list)
    matches: list[tuple[str, str]] = []
    for volume in volumes:
        source = ""
        target = ""
        if isinstance(volume, str):
            parts = volume.split(":")
            source = parts[0]
            target = parts[1]
        elif isinstance(volume, dict):
            source_obj: object = volume.get("source")
            target_obj: object = volume.get("target")
            assert isinstance(source_obj, str)
            assert isinstance(target_obj, str)
            source = source_obj
            target = target_obj
        if target == "/app/data/trader":
            matches.append((source, target))
    assert len(matches) == 1
    return matches[0]


def test_compose_has_live_and_paper_services() -> None:
    """YAML names the two services live and paper."""
    text = _compose_text()
    assert "  live:" in text
    assert "  paper:" in text
    assert "  trader-live:" not in text
    assert "  trader-paper:" not in text


def test_compose_drops_env_file_legacy_and_archive_root() -> None:
    """No env_file leak, no LIVE_TRADING, no unsuffixed ARCHIVE_ROOT key."""
    text = _compose_text()
    assert "env_file" not in text
    assert "LIVE_TRADING" not in text
    assert re.search(r"(?m)^\s+ARCHIVE_ROOT:", text) is None


def test_compose_commands_set_mode_per_service() -> None:
    """Live command is --mode live; paper command is --mode paper."""
    blocks = _service_blocks(_compose_text())
    live_tokens = _command_tokens(blocks["live"])
    paper_tokens = _command_tokens(blocks["paper"])
    assert "--mode" in live_tokens
    assert _mode_after_flag(live_tokens) == "live"
    assert "--mode" in paper_tokens
    assert _mode_after_flag(paper_tokens) == "paper"


def test_compose_mounts_both_archives_and_isolated_state() -> None:
    """Both services bind both archives; state dirs differ; unsuffixed bind is gone."""
    text = _compose_text()
    blocks = _service_blocks(text)
    for block in (blocks["live"], blocks["paper"]):
        assert "/archive/dota:ro" in block
        assert "/archive/lol:ro" in block
        assert "./src" in block
        assert "./config" in block
        assert "./data/new_model" in block
        assert "./data/lol/models:/app/data/lol/models:ro" in block
        for key in SHARED_ENV_KEYS:
            assert key in block
    assert "./data/trader_live:/app/data/trader" in blocks["live"]
    assert "./data/trader_paper:/app/data/trader" in blocks["paper"]
    assert "./data/live_paper:/app/data/trader" not in text
    assert "KALSHI_" not in text
    assert "kalshi" not in text


def test_compose_paper_block_has_no_live_wallet_keys() -> None:
    """Paper YAML must omit PK and BROWSER_ADDRESS; live YAML must list both."""
    blocks = _service_blocks(_compose_text())
    live = blocks["live"]
    paper = blocks["paper"]
    assert "PK:" in live
    assert "BROWSER_ADDRESS:" in live
    assert "${PK}" in live
    assert "${BROWSER_ADDRESS}" in live
    assert "PK" not in paper
    assert "BROWSER_ADDRESS" not in paper


def test_docker_compose_config_services_isolated_state() -> None:
    """Read-only compose config: isolated trader state, compress has no secrets."""
    cfg = _load_compose_config()
    services_obj: object = cfg.get("services")
    assert isinstance(services_obj, dict)
    names = set(services_obj)
    assert names == {"live", "paper", "compress"}, sorted(names)
    live = services_obj["live"]
    paper = services_obj["paper"]
    compress = services_obj["compress"]
    live_source, live_target = _state_bind(live)
    paper_source, paper_target = _state_bind(paper)
    assert live_target == "/app/data/trader"
    assert paper_target == "/app/data/trader"
    assert live_source.endswith("trader_live")
    assert paper_source.endswith("trader_paper")
    live_keys = _environment_keys(live)
    paper_keys = _environment_keys(paper)
    compress_keys = _environment_keys(compress)
    assert "PK" in live_keys
    assert "BROWSER_ADDRESS" in live_keys
    for keys in (paper_keys, compress_keys):
        assert "PK" not in keys, sorted(keys)
        assert "BROWSER_ADDRESS" not in keys, sorted(keys)
        assert "LIVE_TRADING" not in keys, sorted(keys)
    compress_volumes: object = compress.get("volumes") or []
    assert isinstance(compress_volumes, list)
    compress_targets = {
        volume.get("target") for volume in compress_volumes if isinstance(volume, dict)
    }
    assert compress_targets >= {"/app/data/trader_live", "/app/data/trader_paper"}
    for service in (live, paper):
        volumes: object = service.get("volumes") or []
        assert isinstance(volumes, list)
        lol_models = [
            volume
            for volume in volumes
            if isinstance(volume, dict) and volume.get("target") == "/app/data/lol/models"
        ]
        assert len(lol_models) == 1
        assert lol_models[0].get("read_only") is True
        dota_models = [
            volume
            for volume in volumes
            if isinstance(volume, dict) and volume.get("target") == "/app/data/new_model"
        ]
        assert len(dota_models) == 1
        assert dota_models[0].get("read_only") is True


def test_docker_compose_config_services_stdout() -> None:
    """config --services lists exactly live, paper and compress."""
    completed = subprocess.run(
        ["docker", "compose", "config", "--services"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    assert set(completed.stdout.split()) == {"live", "paper", "compress"}


def test_makefile_trader_target_forms_mode() -> None:
    """make trader defaults MODE=paper and runs trader.orchestrator."""
    text = MAKEFILE_PATH.read_text()
    assert "MODE ?= paper" in text
    recipe_start = text.index("trader:")
    recipe_end = text.index("\nmarket-data:")
    recipe = text[recipe_start:recipe_end]
    assert "-m trader.orchestrator daemon --mode $(MODE)" in recipe
    phony_start = text.index(".PHONY:")
    phony_lines: list[str] = []
    for line in text[phony_start:].splitlines():
        phony_lines.append(line)
        if not line.rstrip().endswith("\\"):
            break
    phony = "\n".join(phony_lines)
    assert "trader" in phony
