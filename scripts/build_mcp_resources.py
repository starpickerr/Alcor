"""Prepare fixed connector source snapshots and frozen provider identities."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.runtime.paths import bundle_digest, source_digest  # noqa: E402
from core.services.trusted_account_modules import _TRUSTED_MODULES  # noqa: E402


def prepare(dist, resources):
    catalog = json.loads((ROOT / 'resources/mcp-providers.json').read_text())
    for domain, modules in _TRUSTED_MODULES.items():
        repo = ROOT.parent / 'MCP' / ('email-mcp' if domain == 'email' else 'fudan-elearning-mcp')
        snapshot = {}
        row = catalog['providers'][domain]
        if source_digest(repo, snapshot=snapshot) != row['sourceSha256']:
            raise PermissionError('Provider source differs from the Core-pinned distribution')
        target = resources / 'trusted-connectors' / domain
        expected = {domain + '_mcp/' + name + '.py' for name in modules}
        if domain == 'email':
            expected.add('email_mcp/schema.sql')
        if target.exists() and {path.relative_to(target).as_posix() for path in target.rglob('*') if path.is_file()} - expected:
            raise PermissionError('Unexpected file in generated connector snapshot')
        for name in sorted(expected):
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(snapshot[name])
        row['connectorSha256'] = source_digest(target)
        row['bundleSha256'] = bundle_digest(dist / (domain + '-mcp'))
    (resources / 'mcp-providers.json').write_text(json.dumps(catalog, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist', type=Path, required=True)
    parser.add_argument('--resources', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.dist, args.resources)
