"""The retired plugin ACL endpoint cannot mint independent authorizations."""
import json
from unittest.mock import patch
import pytest
from channel.desktop import desktop_channel as desktop


def test_plugin_authorization_endpoint_is_retired():
    with patch.object(desktop, '_require_auth'), patch.object(desktop.web, 'header'):
        result = json.loads(desktop.PluginAuthorizationHandler().POST())
    assert result['status'] == 'error' and result['code'] == 'gone'


def test_retired_authorization_endpoint_still_authenticates():
    with patch.object(desktop, '_require_auth', side_effect=PermissionError('unauthenticated')):
        with pytest.raises(PermissionError):
            desktop.PluginAuthorizationHandler().POST()
