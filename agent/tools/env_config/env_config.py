"""Compatibility tombstone for the retired Agent credential tool."""
from agent.tools.base_tool import BaseTool, ToolResult


class EnvConfig(BaseTool):
    name = 'env_config'
    description = 'Credential management is available only in the owning feature settings.'
    params = {'type': 'object', 'properties': {}}

    def __init__(self, config=None):
        pass

    def execute(self, args):
        return ToolResult.fail('Configure credentials in the owning feature settings. Agent credential access is unavailable.')
