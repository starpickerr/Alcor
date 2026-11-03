"""Live authorization orchestration, without another grant or policy store."""
from contextlib import contextmanager
from contextvars import ContextVar

_MODEL_AUTHORIZATION = ContextVar('stella_model_authorization', default=())


class SecurityService:
    @staticmethod
    def authorize_egress(url, body, *, sources, wait=True, cancel_event=None, method="POST", headers=None, category=None):
        # Sources are supplied by Core adapters, never accepted from tool/MCP
        # parameters. The same gateway owns the one-use approval and audit.
        from core.security.egress import authorize
        check_model_authorization()
        return authorize(url, body, sources=sources, wait=wait, cancel_event=cancel_event, method=method, headers=headers, category=category)

    @staticmethod
    def native_user_operation():
        from common.runtime_identity import current_identity
        from core.execution.context import current_execution_context
        from core.plugins.caller import current_caller_origin, current_invocation_purpose
        active = current_execution_context()
        if active:
            active.check_active()
        identity = current_identity()
        # Legacy Application ports use a Core-owned caller scope rather than
        # ExecutionContext. Only an explicit UI root qualifies; missing/system
        # origins and all Agent execution identities remain mode constrained.
        return (current_caller_origin() == 'ui' and current_invocation_purpose() != 'background' and
                (active is None or active.purpose == 'interactive') and
                not (identity.session_id or identity.task_id or identity.run_id or identity.execution_session_id))

    @staticmethod
    def native_background_operation(descriptor, arguments):
        from core.security.briefing_peers import native_background_operation
        return native_background_operation(descriptor, arguments)

    @staticmethod
    def policy_binding(*, origin=None, agent_id=None, session_id=None):
        """Snapshot authorization inputs without using/consuming an approval."""
        from common.runtime_identity import current_identity
        from core.execution.context import current_execution_context
        from agent.runtime.tool_invocation import _current_authorization
        identity = current_identity()
        active = current_execution_context()
        if not (identity.session_id or identity.task_id or session_id or
                origin in {'tool', 'job'} or active and active.origin in {'tool', 'job'}):
            return None
        return _current_authorization(None, identity.agent_id or agent_id,
                                      identity.session_id or session_id, None, False)[2]

    @staticmethod
    def authorize_resource(resource, operation, *, purpose=None, legacy_connection=None):
        """The trusted execution identity, rather than tool input, selects grants."""
        from common.runtime_identity import current_identity
        from core.security.resource_grants import ResourceGrantStore
        from core.plugins.caller import current_invocation_purpose, current_consumer_plugin
        identity = current_identity()
        user = identity.user_id or 'local-default'
        from agent.registry import get_agent_registry
        agent = identity.agent_id or get_agent_registry().default_agent_id
        if identity.task_id:
            subject = f'task:{agent}:{identity.task_id}'
            purpose = 'background'
        elif identity.session_id:
            subject = f'session:{agent}:{identity.session_id}'
        elif current_consumer_plugin():
            subject = 'plugin:' + current_consumer_plugin()
        else:
            subject = f'user:{user}'
        SecurityService.check_execution()
        from agent.permission.audit import record
        import uuid
        labels = dict(event='resource_access', tool='resource_grant', resource=resource['kind'],
                      operation=operation, operation_id=uuid.uuid4().hex)
        try:
            args = dict(user_id=user, subject=subject, operation=operation,
                        purpose=purpose or current_invocation_purpose() or 'interactive')
            store = ResourceGrantStore()
            try:
                grant = store.check(resource=resource, **args)
            except PermissionError:
                if not legacy_connection:
                    raise
                grant = store.check_legacy_container(connection=legacy_connection, **args)
        except PermissionError:
            record(**labels, decision='deny', reason='Resource scope is not authorized or was revoked.')
            raise
        record(**labels, decision='allow', reason='Live resource scope authorized.')
        return grant

    @staticmethod
    def authorize_connection(server, operation):
        from common.runtime_identity import current_identity
        from core.plugins.caller import current_consumer_plugin, current_delegation
        from core.execution.context import current_execution_context
        identity = current_identity()
        active = current_execution_context()
        if (identity.session_id or identity.task_id or current_consumer_plugin() or current_delegation() or
                active and active.origin in {'tool', 'job'}):
            return SecurityService.authorize_resource({'kind': 'connection', 'id': 'mcp:' + server},
                                                      'system_execute' if operation == 'unknown' else operation)

    @staticmethod
    def authorize_filesystem(path, *, operation):
        """Check a trusted adapter's concrete target using the existing gateway.

        This is an additional file boundary inside an already authorized
        operation, not a second Agent tool or permission store.
        """
        if operation not in {'read', 'write'}:
            raise ValueError('invalid filesystem operation')
        from common.runtime_identity import current_identity
        from agent.runtime.tool_invocation import _current_authorization
        from agent.permission.gateway import SecurityGateway
        identity = current_identity()
        SecurityService.check_execution()
        mode, kwargs, _ = _current_authorization(None, identity.agent_id, identity.session_id, None, False)
        if SecurityService.native_user_operation():
            policy = dict(kwargs['policy'])
            policy.update(preset='full-access', filesystem_mode='full-access', rules={}, migration_status='complete')
            policy.pop('error', None)
            kwargs['policy'], mode = policy, 'full-access'
        # The owning operation has already checked its tool allowlist/switch.
        # Only the actual filesystem authority is evaluated here.
        kwargs.pop('allowlist', None)
        decision = SecurityGateway.check(mode, operation, {'path': str(path)}, create_pending=False, **kwargs)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        SecurityService.check_execution()

    @staticmethod
    def legacy_connection_restrictions(connection, *, session_id=None, agent_id=None):
        """The migration adapter stops reading legacy config after authority moves."""
        names = [connection]
        if connection in {'mcp:email', 'mcp:elearning'}:
            names.append(connection.removeprefix('mcp:'))
        from core.security.migration import resource_authority_migrated
        if resource_authority_migrated():
            from common.runtime_identity import current_identity
            from agent.registry import get_agent_registry
            from core.security.resource_grants import ResourceGrantStore
            identity = current_identity()
            user = identity.user_id or 'local-default'
            agent = identity.agent_id or agent_id or get_agent_registry().default_agent_id
            subjects = ['user:' + user]
            if identity.task_id:
                subjects.append(f'task:{agent}:{identity.task_id}')
            elif identity.session_id or session_id:
                subjects.append(f'session:{agent}:{identity.session_id or session_id}')
            return ResourceGrantStore().connection_restrictions(user_id=user, subjects=subjects, connections=names)
        from config import conf
        policy = conf().get('security_policy')
        values = policy.get('connections', {}) if isinstance(policy, dict) else {}
        return [values[name] for name in names if name in values]

    @staticmethod
    def authorize_operation(capability_id, operation, arguments, *, source='plugin', **kwargs):
        from hashlib import sha256
        from core.security.capabilities import CapabilityDescriptor
        descriptor = CapabilityDescriptor(capability_id, source, operation)
        name = 'service_' + sha256(capability_id.encode()).hexdigest()[:20]
        return SecurityService.authorize_tool(name, arguments, capability_descriptor=descriptor, **kwargs)

    @staticmethod
    def check_execution():
        from core.execution.context import current_execution_context
        from core.workspace.service import WorkspaceService
        context = current_execution_context()
        if context:
            context.check_active()
            WorkspaceService.context()
        return context

    @staticmethod
    def authorize_tool(tool_name, arguments, **kwargs):
        try:
            from agent.runtime.tool_invocation import _permission_denial
        except ImportError:
            return 'Permission validation is unavailable; the operation did not run.'
        context = SecurityService.check_execution()
        from common.runtime_identity import current_identity
        from core.plugins.caller import current_invocation_purpose
        if current_identity().task_id or current_invocation_purpose() == 'background':
            kwargs.update(noninteractive=True, user_action=False, wait_for_approval=False)
        if context:
            for key in ('agent_id', 'session_id'):
                expected = getattr(context.identity, key)
                if kwargs.get(key) is not None and kwargs[key] != expected:
                    raise PermissionError('tool cannot replace execution identity')
            if context.origin != 'ui':
                kwargs['user_action'] = False
            if context.purpose == 'background':
                kwargs.update(noninteractive=True, user_action=False, wait_for_approval=False)
        reason = _permission_denial(tool_name, arguments, **kwargs)
        SecurityService.check_execution()
        return reason

    @staticmethod
    def authorize_model(session, parent, method):
        SecurityService.check_execution()
        session.registry.require_generation(session.plugin_id, session.generation)
        capability = {'complete': 'models.chat', 'embed': 'models.embedding',
                      'embedding_status': 'models.embedding'}.get(method)
        if capability is None:
            raise PermissionError('model method is not public')
        declared = session._method_permissions.get((parent.get('contract'), parent.get('method')), ())
        if capability not in declared:
            raise PermissionError('model capability not declared by invoking method')

    @staticmethod
    def authorize_data(session, parent, source_id):
        method = parent.get('method')
        if 'data.read' not in session._method_permissions.get((parent.get('contract'), method), ()):
            raise PermissionError('data capability not declared by invoking method')
        if not isinstance(source_id, str) or not source_id:
            raise ValueError('source_id is required')
        session.registry.require_generation(session.plugin_id, session.generation)
        SecurityService.check_execution()
        # Source ownership/account/resource authorization is enforced by the
        # Data Service; a plugin-specific capability grant is no longer issued.


def check_model_authorization():
    SecurityService.check_execution()
    for authorize in _MODEL_AUTHORIZATION.get():
        authorize()


@contextmanager
def model_authorization_scope(authorize):
    token = _MODEL_AUTHORIZATION.set((*_MODEL_AUTHORIZATION.get(), authorize))
    try:
        check_model_authorization()
        yield
    finally:
        _MODEL_AUTHORIZATION.reset(token)
