from __future__ import annotations


def handle_kill(context, command, out, services) -> int:
    summary = services.kill_project(context, command)
    services.write_lines(out, services.render_kill(summary))
    return 0


def handle_open(context, command, out, services) -> int:
    summary = services.open_project(context, command)
    services.write_lines(out, services.render_open(summary))
    return 0


def handle_logs(context, command, out, services) -> int:
    summary = services.agent_logs(context, command)
    services.write_lines(out, services.render_logs(summary))
    return 0


def handle_ps(context, command, out, services) -> int:
    payload = services.ps_summary(context, command)
    services.write_lines(out, services.render_ps(payload))
    return 0


def handle_sync_codex_home(context, command, out, services) -> int:
    summary = services.sync_project_codex_homes(context, command)
    status = 'synced' if summary.agents else 'noop'
    lines = [
        f'command_status: {status}',
        f'source_home: {summary.source_home}',
        f'codex_agents: {len(summary.agents)}',
    ]
    for result in summary.agents:
        synced = ','.join(result.synced) if result.synced else '(none)'
        auth_note = ' auth=skipped' if result.skipped_auth else ''
        lines.append(f'agent: {result.agent_name} synced={synced}{auth_note} path={result.path}')
    for skipped in getattr(summary, 'skipped', ()):
        path_note = f' path={skipped.path}' if skipped.path is not None else ''
        lines.append(f'skipped: {skipped.agent_name} reason={skipped.reason}{path_note}')
    services.write_lines(out, lines)
    return 0


def handle_doctor(context, command, out, services) -> int:
    if command.bundle:
        summary = services.export_diagnostic_bundle(context, command)
        services.write_lines(out, services.render_doctor_bundle(summary))
        return 0
    payload = services.doctor_summary(context)
    services.write_lines(out, services.render_doctor(payload))
    return 0


def handle_fault_list(context, command, out, services) -> int:
    summary = services.list_fault_rules(context)
    services.write_lines(out, services.render_fault_list(summary))
    return 0


def handle_fault_arm(context, command, out, services) -> int:
    summary = services.arm_fault_rule(context, command)
    services.write_lines(out, services.render_fault_arm(summary))
    return 0


def handle_fault_clear(context, command, out, services) -> int:
    summary = services.clear_fault_rule(context, command)
    services.write_lines(out, services.render_fault_clear(summary))
    return 0


__all__ = [
    'handle_doctor',
    'handle_fault_arm',
    'handle_fault_clear',
    'handle_fault_list',
    'handle_kill',
    'handle_logs',
    'handle_open',
    'handle_ps',
    'handle_sync_codex_home',
]
