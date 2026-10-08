from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from dashboard import diagnostics
from dashboard.catalog import MapView
from dashboard.diagnostics import SellObservation, SellWatchState, observe_sell
from dashboard.health import HealthFacts
from dashboard.home import fmt_age
from dashboard.hub_types import HubSnapshot, SessionFacts

WALLET_STALE_S = 15.0
_DIAG_CAP = 24

Severity = Literal["fatal", "error", "warn", "info"]

_SEVERITY_RANK: dict[Severity, int] = {"fatal": 0, "error": 1, "warn": 2, "info": 3}


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    label: str
    source: str
    ts: float | None
    details: tuple[str, ...]


@dataclass(frozen=True)
class DiagnosticsView:
    line: str
    headline: Diagnostic | None
    findings: tuple[Diagnostic, ...]
    observed: str | None
    total: int
    health_state: Literal["ok", "warn", "error", "unknown"]
    health_text: str


@dataclass(frozen=True)
class HealthAssessment:
    state: Literal["ok", "warn", "error", "unknown"]
    text: str
    details: tuple[str, ...]
    diagnostic: Diagnostic | None
    observed: str | None


def _finding(
    severity: Severity,
    label: str,
    source: str,
    ts: float | None,
    *details: str,
) -> Diagnostic:
    return Diagnostic(severity=severity, label=label, source=source, ts=ts, details=tuple(details))


def assess_health(facts: HealthFacts, now_s: float) -> HealthAssessment:
    details: list[str] = []
    if facts.container_name is not None:
        details.append(f"контейнер {facts.container_name}")
    if facts.container_id is not None:
        details.append(f"id {facts.container_id[:12]}")
    if facts.started_at is not None:
        details.append(f"старт {fmt_age(now_s - facts.started_at)} назад")
    details.extend(facts.evidence)
    det = tuple(details)
    if not facts.ok:
        return HealthAssessment(
            state="unknown",
            text="состояние трейдера неизвестно",
            details=det,
            diagnostic=_finding(
                "warn",
                f"состояние контейнера неизвестно: {facts.error or '?'}",
                "docker",
                facts.attempt_at,
            ),
            observed=None,
        )
    if not facts.consistent:
        return HealthAssessment(
            state="unknown",
            text="контейнер сменился во время чтения",
            details=det,
            diagnostic=_finding(
                "warn", "контейнер сменился во время чтения", "docker", facts.read_at
            ),
            observed=None,
        )
    if facts.container_id is None:
        return HealthAssessment(
            state="error",
            text="контейнер не запущен",
            details=det,
            diagnostic=_finding("error", "контейнер не запущен", "docker compose", facts.read_at),
            observed="контейнер не запущен",
        )
    if facts.container_state != "running":
        text = f"контейнер {facts.container_state or 'неизвестно'}"
        return HealthAssessment(
            state="error",
            text=text,
            details=det,
            diagnostic=_finding("error", text, "docker inspect", facts.read_at),
            observed=text,
        )
    if facts.halted:
        label = facts.halt_label or "risk_halt"
        return HealthAssessment(
            state="error",
            text=f"HALT {label}",
            details=det,
            diagnostic=_finding(
                "error",
                f"halt: {label}",
                "docker logs",
                facts.halt_at or facts.read_at,
                *facts.evidence[:2],
            ),
            observed="контейнер работает",
        )
    if facts.halted is None:
        return HealthAssessment(
            state="warn",
            text="работает · halt-статус неизвестен (лог обрезан)",
            details=det,
            diagnostic=_finding(
                "warn",
                "текущий halt-статус неизвестен (лог обрезан/неполный)",
                "docker logs",
                facts.read_at,
            ),
            observed="контейнер работает",
        )
    return HealthAssessment(
        state="ok",
        text="работает · риск чист",
        details=det,
        diagnostic=None,
        observed="контейнер работает · halt чист",
    )


class SellWatcher:
    def __init__(self) -> None:
        self._states: dict[tuple[str, str, str, str], SellWatchState] = {}
        self._generation: int | None = None

    def observe(
        self,
        sessions: Sequence[SessionFacts],
        *,
        wallet_read_at: float | None,
        generation: int,
        now_s: float,
    ) -> Mapping[str, diagnostics.SellWedgeVerdict]:
        input_stale = wallet_read_at is None or (now_s - wallet_read_at) > WALLET_STALE_S
        if generation != self._generation or input_stale:
            self._states = {}
            self._generation = generation
        current: dict[tuple[str, str, str, str], SellWatchState] = {}
        verdicts: dict[str, diagnostics.SellWedgeVerdict] = {}
        if input_stale:
            return verdicts
        for sess in sessions:
            for sell in sess.sells:
                if sell.token_id is None:
                    continue
                obs = sell.observation(sess.session_id)
                state, verdict = observe_sell(
                    self._states.get(_sell_key(obs), SellWatchState(None, None)),
                    obs,
                    now_s,
                )
                if state.key is not None:
                    current[state.key] = state
                if verdict.order_id is not None:
                    verdicts[f"{sess.session_id}:{sell.order_id}"] = verdict
        self._states = current
        return verdicts


def _sell_key(obs: SellObservation) -> tuple[str, str, str, str]:
    return (obs.session_key, obs.token_id, obs.order_id, obs.status)


def _exit_findings(
    sessions: Sequence[SessionFacts],
    wedge: Mapping[str, diagnostics.SellWedgeVerdict],
    min_order_by_cid: Mapping[str, float | None],
    now_s: float,
) -> Iterable[Diagnostic]:
    for sess in sessions:
        held = max((q for q in sess.held if q is not None), default=0.0)
        if held <= 0.0 and not sess.sells:
            continue
        sells = tuple(sell.observation(sess.session_id) for sell in sess.sells)
        wedged = next(
            (v for k, v in wedge.items() if k.startswith(f"{sess.session_id}:") and v.wedged),
            None,
        )
        assessment = diagnostics.explain_exit(
            held_qty=held,
            min_order_size=min_order_by_cid.get(sess.condition_id),
            sells=sells,
            sell_only=sess.sell_only,
            recovery_pending=sess.recovery_pending,
            unconfirmed=sess.unconfirmed,
            pending_ownership=sess.pending_ownership,
            wedge=wedged,
        )
        source = "live.db core_sessions"
        ids = (
            f"session {sess.session_id}",
            f"condition {sess.condition_id}",
            f"rev {sess.revision} · обновлено {fmt_age(now_s - sess.updated_at)} назад",
        )
        if assessment.kind == "wedge":
            yield _finding(
                "warn",
                f"возможное зависание SELL: {assessment.detail}",
                source,
                sess.updated_at,
                *ids,
                assessment.label,
            )
        elif assessment.kind == "unknown":
            yield _finding(
                "info",
                f"нет SELL: причина не установлена ({sess.session_id})",
                source,
                sess.updated_at,
                *ids,
            )


def _source_findings(snap: HubSnapshot, health_diagnostic: Diagnostic | None) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    if snap.error is not None:
        findings.append(
            _finding("fatal", f"источник дашборда упал: {snap.error}", "live hub", None)
        )
    if not snap.wallet.ok:
        findings.append(
            _finding(
                "error",
                f"чтение кошелька: {snap.wallet.error or 'нет данных'}",
                "wallet live.db",
                snap.wallet.read_at,
            )
        )
    if snap.balance.funder_mismatch:
        findings.append(
            _finding(
                "error",
                "funder кошелька не совпадает с аккаунтом",
                "wallet_identity/config",
                snap.wallet.read_at,
            )
        )
    elif snap.balance.last_error is not None:
        findings.append(
            _finding(
                "error",
                f"collateral: {snap.balance.last_error}",
                "balance allowance",
                snap.balance.last_attempt_at,
            )
        )
    if health_diagnostic is not None:
        findings.append(health_diagnostic)
    if snap.subscriptions.error is not None:
        findings.append(
            _finding(
                "warn",
                f"каталог/подписки: {snap.subscriptions.error}",
                "catalog lane",
                snap.subscriptions.catalog_built_at,
            )
        )
    if not snap.nontrading.ok:
        findings.append(
            _finding(
                "warn",
                f"не-торговые рынки: {snap.nontrading.error or 'нет чтения'}",
                "sidecar scan",
                snap.nontrading.attempt_at,
            )
        )
    if not snap.service_logs.ok and snap.service_logs.error is not None:
        findings.append(
            _finding(
                "warn",
                f"логи сервиса: {snap.service_logs.error}",
                "docker logs",
                snap.service_logs.attempt_at,
            )
        )
    if snap.day.error is not None:
        findings.append(
            _finding(
                "info",
                f"дневной PnL: последнее чтение — {snap.day.error}",
                "data-api /activity",
                snap.day.attempt_at,
            )
        )
    return findings


def _session_findings(
    sessions: Sequence[SessionFacts],
    wedge: Mapping[str, diagnostics.SellWedgeVerdict],
    min_order_by_cid: Mapping[str, float | None],
    now_s: float,
) -> list[Diagnostic]:
    findings = list(_exit_findings(sessions, wedge, min_order_by_cid, now_s))
    for sess in sessions:
        if sess.unconfirmed or sess.pending_ownership:
            findings.append(
                _finding(
                    "warn",
                    f"неподтверждённые сделки: unconfirmed={sess.unconfirmed} pending={sess.pending_ownership}",
                    "live.db core_sessions",
                    sess.updated_at,
                    f"session {sess.session_id}",
                )
            )
        if sess.checkpoint_error is not None:
            findings.append(
                _finding(
                    "warn",
                    f"checkpoint не декодируется: {sess.checkpoint_error}",
                    "live.db core_sessions",
                    sess.updated_at,
                    f"session {sess.session_id}",
                )
            )
    return findings


_NEUTRAL_BLOCKS = frozenset({"no_edge", "position_open", "position_cap", "min_delta"})


def _map_finding(view: MapView) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    if view.status == "stale":
        findings.append(
            _finding(
                "warn",
                f"карта {view.entry.match_id}: запись остановилась",
                "session journal",
                view.entry.last_write,
                f"возраст {fmt_age(view.write_age_s)}",
            )
        )
        return findings
    if view.status != "live" or view.decision is None:
        return findings
    decision = view.decision
    if decision.reason in ("model_error", "trading_error", "sidecar_fault"):
        findings.append(
            _finding(
                "warn",
                f"карта {view.entry.match_id}: {diagnostics.reason_label(decision.reason)}",
                str(decision.path),
                None,
                f"решение {fmt_age(view.decision_age_s)} назад",
            )
        )
    elif decision.entry_block in _NEUTRAL_BLOCKS:
        params = view.entry.params
        findings.append(
            _finding(
                "info",
                f"карта {view.entry.match_id}: "
                + diagnostics.entry_block_label(
                    decision.entry_block,
                    model_evaluated=decision.model_evaluated,
                    raw_delta=decision.raw_delta,
                    min_abs_delta=(params.min_abs_delta if params is not None else None),
                ),
                "decision tail",
                None,
            )
        )
    return findings


def _map_findings(snap: HubSnapshot) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    for view in snap.maps:
        findings.extend(_map_finding(view))
    return findings


def build_diagnostics(
    snap: HubSnapshot,
    watcher: SellWatcher,
    now_s: float,
) -> DiagnosticsView:
    closed_cids = frozenset(
        view.entry.condition_id
        for view in (*snap.maps, *snap.legacy_maps)
        if view.status in ("final", "terminal") and view.entry.condition_id is not None
    )
    sessions = tuple(
        session for session in snap.wallet.sessions if session.condition_id not in closed_cids
    )
    wedge = watcher.observe(
        sessions,
        wallet_read_at=snap.wallet.read_at,
        generation=snap.generation,
        now_s=now_s,
    )
    assessment = assess_health(snap.health, now_s)
    min_order_by_cid = {
        view.entry.condition_id: (
            view.entry.params.min_order_size if view.entry.params is not None else None
        )
        for view in (*snap.maps, *snap.legacy_maps)
        if view.entry.condition_id is not None
    }
    findings = (
        _source_findings(snap, assessment.diagnostic)
        + _session_findings(sessions, wedge, min_order_by_cid, now_s)
        + _map_findings(snap)
    )
    return _diagnostics_view(findings, assessment)


def build_match_diagnostics(
    snap: HubSnapshot,
    view: MapView,
    session: SessionFacts | None,
    wedge: Mapping[str, diagnostics.SellWedgeVerdict],
    summary_notes: Sequence[str],
    now_s: float,
) -> DiagnosticsView:
    assessment = assess_health(snap.health, now_s)
    min_order = view.entry.params.min_order_size if view.entry.params is not None else None
    sessions = () if session is None else (session,)
    min_order_by_cid = {} if session is None else {session.condition_id: min_order}
    findings = (
        _source_findings(snap, assessment.diagnostic)
        + _session_findings(sessions, wedge, min_order_by_cid, now_s)
        + _map_finding(view)
    )
    for note in summary_notes:
        findings.append(_finding("info", note, "game summary", None))
    return _diagnostics_view(findings, assessment)


def status_text(view: DiagnosticsView) -> str:
    serious = [finding for finding in view.findings if finding.severity != "info"]
    if view.health_state == "ok" and not serious:
        return ""
    if serious:
        return serious[0].label
    return view.health_text


def _diagnostics_view(findings: list[Diagnostic], assessment: HealthAssessment) -> DiagnosticsView:
    findings.sort(key=lambda f: (_SEVERITY_RANK[f.severity], -(f.ts or 0.0)))
    total = len(findings)
    capped = tuple(findings[:_DIAG_CAP])
    headline = capped[0] if capped else None
    observed = assessment.observed
    if headline is not None:
        line = f"{headline.label} · {headline.source}"
    elif observed is not None:
        line = f"проблем не видно · {observed}"
    else:
        line = "текущее состояние неизвестно"
    return DiagnosticsView(
        line=line,
        headline=headline,
        findings=capped,
        observed=observed,
        total=total,
        health_state=assessment.state,
        health_text=assessment.text,
    )
