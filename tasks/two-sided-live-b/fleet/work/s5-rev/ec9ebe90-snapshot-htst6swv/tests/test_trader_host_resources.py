"""Resource gating: Dota-live, LoL-paper, and empty assignment open different things."""

# pyright: reportPrivateUsage=false, reportUnknownLambdaType=false, reportUnknownArgumentType=false

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from trader.game_profile import GAME_PROFILES, GameProfile
from trader.host_resources import run_wallet_daemon
from trader.trading_mode import ExecutionMode
from trader.wallet_host import WalletHost
from trader.wallet_store import WalletStateStore

DOTA = GAME_PROFILES["dota"]
LOL = GAME_PROFILES["lol"]


class ResourceLog:
    """Records which host-resource calls ran."""

    def __init__(self) -> None:
        self.require_live_wallet = 0
        self.use_http1_clob_transport = 0
        self.steam_from_environment = 0
        self.http_client = 0
        self.engine_paper: list[bool] = []
        self.acquire_file_lock = 0
        self.materialize_templates: list[object] = []
        self.load_archive_root = 0
        self.archive_envs: list[str] = []
        self.loaded_games: list[str] = []


def _patch_engine_io_fakes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, log: ResourceLog
) -> None:
    """Stub live wallet, Steam, HTTP, and Engine constructors."""

    def fake_require() -> None:
        log.require_live_wallet += 1

    class FakeSteam:
        @classmethod
        def from_environment(cls, client: object) -> SimpleNamespace:
            log.steam_from_environment += 1
            return SimpleNamespace(client=client)

    def fake_http() -> SimpleNamespace:
        log.http_client += 1
        return SimpleNamespace(close=lambda: None)

    class Eng:
        def __init__(self, cfg: object, paper: bool = False) -> None:
            assert log.use_http1_clob_transport == 1
            log.engine_paper.append(paper)
            self.cfg = cfg
            self.state = WalletStateStore(tmp_path / "wallet.db")
            self.gateway = SimpleNamespace(funder="")
            self.paper = paper

        def _next_wake_s(self, _cid: str, base_tick: float) -> float:
            return base_tick

    monkeypatch.setattr("trader.host_resources.require_live_wallet", fake_require)
    monkeypatch.setattr("trader.host_resources.SteamClient", FakeSteam)
    monkeypatch.setattr("trader.host_resources.http_client", fake_http)
    monkeypatch.setattr("trader.host_resources.Engine", Eng)


def _patch_host_runtime_fakes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, log: ResourceLog
) -> None:
    """Stub flock, config dir, archive, model, and WalletHost lifecycle."""

    def fake_lock(path: Path) -> SimpleNamespace:
        log.acquire_file_lock += 1
        return SimpleNamespace(close=lambda: None, path=path)

    def fake_materialize(db_path: Path, journal: Path, template: object) -> SimpleNamespace:
        del db_path, journal
        log.materialize_templates.append(template)
        return SimpleNamespace(config_dir=tmp_path, cleanup=lambda: None)

    def fake_archive(profile: GameProfile) -> Path:
        log.load_archive_root += 1
        log.archive_envs.append(profile.archive_root_env)
        return tmp_path

    def fake_load_model(
        model_dir: Path, expected_features: object, expected_lag_seconds: int
    ) -> object:
        del expected_features, expected_lag_seconds
        log.loaded_games.append(model_dir.name)
        return object()

    def fake_init(self: WalletHost, *args: object) -> None:
        del args
        self._closed = False

    def fake_close(self: WalletHost) -> None:
        self._closed = True

    async def fake_run(self: WalletHost) -> None:
        del self

    monkeypatch.setattr("trader.host_resources.acquire_file_lock", fake_lock)
    monkeypatch.setattr("trader.host_resources.materialize_wallet_config_dir", fake_materialize)
    monkeypatch.setattr("trader.host_resources.load_archive_root", fake_archive)
    monkeypatch.setattr("trader.host_resources.load_model", fake_load_model)
    monkeypatch.setattr(WalletHost, "__init__", fake_init)
    monkeypatch.setattr(WalletHost, "close", fake_close)
    monkeypatch.setattr(WalletHost, "run", fake_run)


def _install_resource_fakes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, log: ResourceLog
) -> None:
    """Stub IO so run_wallet_daemon records calls without opening credentials."""

    def fake_transport() -> None:
        log.use_http1_clob_transport += 1

    cfg = SimpleNamespace(
        engine=SimpleNamespace(journal=True),
        wallet=SimpleNamespace(signature_type=1, chain_id=1),
    )
    _patch_engine_io_fakes(monkeypatch, tmp_path, log)
    _patch_host_runtime_fakes(monkeypatch, tmp_path, log)
    monkeypatch.setattr("trader.host_resources.reject_legacy_env", lambda: None)
    monkeypatch.setattr("trader.host_resources.use_http1_clob_transport", fake_transport)
    monkeypatch.setattr("trader.host_resources.patch_engine_classes", lambda: object())
    monkeypatch.setattr("trader.host_resources.restore_engine_classes", lambda restore: None)
    monkeypatch.setattr("trader.host_resources.Config", SimpleNamespace(load=lambda *_a, **_k: cfg))
    monkeypatch.setattr("trader.host_resources._bind_quotes_adapter", lambda *_a, **_k: object())
    monkeypatch.setattr("trader.host_resources.pin_engine_identity", lambda engine: None)


def _assign(monkeypatch: pytest.MonkeyPatch, games: tuple[GameProfile, ...]) -> None:
    """Stub assigned_games to the tuple under test."""

    def fake_assigned(mode: ExecutionMode) -> tuple[GameProfile, ...]:
        del mode
        return games

    monkeypatch.setattr("trader.host_resources.assigned_games", fake_assigned)


def test_dota_live_opens_wallet_and_steam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live Dota takes the CLOB wallet, Steam, and Dota risk clips."""
    log = ResourceLog()
    _install_resource_fakes(monkeypatch, tmp_path, log)
    _assign(monkeypatch, (DOTA,))
    asyncio.run(run_wallet_daemon("commit", "live"))
    assert log.require_live_wallet == 1
    assert log.use_http1_clob_transport == 1
    assert log.http_client == 1
    assert log.steam_from_environment == 1
    assert log.engine_paper == [False]
    assert log.acquire_file_lock == 1
    assert len(log.materialize_templates) == 1
    assert log.load_archive_root == 1
    assert log.archive_envs == ["DOTA_ARCHIVE_ROOT"]
    assert log.loaded_games == ["production", "production-noxp"]


def test_lol_paper_skips_steam_and_live_wallet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LoL paper still flocks the paper wallet; no Steam."""
    log = ResourceLog()
    _install_resource_fakes(monkeypatch, tmp_path, log)
    _assign(monkeypatch, (LOL,))
    asyncio.run(run_wallet_daemon("commit", "paper"))
    assert log.require_live_wallet == 0
    assert log.http_client == 0
    assert log.steam_from_environment == 0
    assert log.engine_paper == [True]
    assert log.acquire_file_lock == 1
    assert len(log.materialize_templates) == 1
    assert log.load_archive_root == 1
    assert log.archive_envs == ["LOL_ARCHIVE_ROOT"]
    assert log.loaded_games == ["production"]


def test_lol_live_opens_wallet_and_skips_steam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LoL-only live takes the CLOB wallet; Steam stays closed."""
    log = ResourceLog()
    _install_resource_fakes(monkeypatch, tmp_path, log)
    _assign(monkeypatch, (LOL,))
    asyncio.run(run_wallet_daemon("commit", "live"))
    assert log.require_live_wallet == 1
    assert log.http_client == 0
    assert log.steam_from_environment == 0
    assert log.engine_paper == [False]
    assert log.loaded_games == ["production"]


def test_empty_assignment_idles_without_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No games: log mode and empty assignment, stay up, open nothing."""
    log = ResourceLog()
    _install_resource_fakes(monkeypatch, tmp_path, log)
    _assign(monkeypatch, ())
    caplog.set_level(logging.INFO)

    async def probe() -> None:
        task = asyncio.create_task(run_wallet_daemon("commit", "live"))
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(probe())
    assert "mode=live" in caplog.text
    assert "assigned=()" in caplog.text
    assert log.require_live_wallet == 0
    assert log.http_client == 0
    assert log.steam_from_environment == 0
    assert log.engine_paper == []
    assert log.acquire_file_lock == 0
    assert log.materialize_templates == []
    assert log.load_archive_root == 0
    assert log.archive_envs == []
    assert log.loaded_games == []
