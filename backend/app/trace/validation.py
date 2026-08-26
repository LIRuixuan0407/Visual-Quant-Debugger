from collections.abc import Iterable

from app.trace.models import BacktestTrace, Diagnostic, TimelineEvent


def collect_look_ahead_diagnostics_from_events(
    events: Iterable[TimelineEvent],
) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    for event in events:
        for dependency in event.data_dependencies:
            if dependency.available_at <= dependency.used_at:
                continue
            diagnostics.append(
                Diagnostic(
                    diagnostic_id=f"diagnostic-{len(diagnostics) + 1:06d}",
                    severity="WARNING",
                    code="LOOK_AHEAD_WARNING",
                    message=(
                        f"{dependency.source}.{dependency.field} was available at "
                        f"{dependency.available_at.isoformat()}, after it was used at "
                        f"{dependency.used_at.isoformat()}"
                    ),
                    event_id=event.event_id,
                    dependency_id=dependency.dependency_id,
                )
            )
    return tuple(diagnostics)


def collect_look_ahead_diagnostics(trace: BacktestTrace) -> tuple[Diagnostic, ...]:
    return collect_look_ahead_diagnostics_from_events(trace.timeline)
