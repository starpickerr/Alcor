"""Core builds child process plans from the live parent ceiling, never wire ACLs."""
from pathlib import Path
import os
import shutil


def prepare_delegated(workspace, binaries):
    from common.runtime_identity import current_identity
    from agent.runtime.tool_invocation import _current_authorization
    from agent.permission.security_policy import rule
    from core.security.extension_sandbox import prepare_extension
    identity = current_identity()
    mode, context, _ = _current_authorization(None, identity.agent_id, identity.session_id, None, False)
    policy = context['policy']
    if mode == 'full-access':
        from agent.permission.process_sandbox import prepare
        env = {}
        plan = prepare('exec "$@"', str(workspace), [str(workspace)], policy, env)
        return {'prefix': [*plan.argv, 'stella-delegation'], 'env': env, 'mode': 'isolated'}
    reads = [context['cwd'], *context['scope'].write_roots, *(policy.get('extra_roots') or [])]
    executable_reads = [shutil.which(value) or value for value in binaries if value]
    env = {key: value for key, value in os.environ.items() if key in {
        'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'SYSTEMROOT', 'WINDIR'}}
    plan = prepare_extension(['/usr/bin/true'], Path(workspace), Path(workspace), env,
        writable=mode == 'workspace-write' and rule(policy, 'workspace_write') == 'allow',
        read_roots=reads, executable_reads=executable_reads, network=rule(policy, 'network') == 'allow')
    return {'prefix': plan.argv[:-1], 'env': plan.env, 'mode': 'isolated'}
