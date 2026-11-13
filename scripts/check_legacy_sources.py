"""Verify the immutable companion sources used by the retained Legacy suite."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.plugins.package_ops import _sha256_tree  # noqa: E402
from core.runtime.paths import source_digest  # noqa: E402


def verify_checkout(path, revision):
    actual = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != revision:
        raise ValueError(f'{path}: expected commit {revision}, found {actual}')


def verify_sources(workspace, sources):
    for name, source in sources.items():
        path = workspace / source['path']
        verify_checkout(path, source['revision'])
        if name == 'plugins':
            catalog = json.loads((ROOT / 'resources/default-plugins.json').read_text())
            for row in catalog['plugins']:
                plugin = row['pluginId'].removeprefix('stella.')
                if _sha256_tree(path / 'plugins' / plugin) != row['sourceSha256']:
                    raise ValueError(f'{name}/{plugin}: source digest mismatch')
        else:
            catalog = json.loads((ROOT / 'resources/mcp-providers.json').read_text())
            if source_digest(path) != catalog['providers'][name]['sourceSha256']:
                raise ValueError(f'{name}: source digest mismatch')
        print(f'PASS {name}: {source["revision"]}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()
    sources = json.loads((ROOT / 'resources/legacy-sources.lock.json').read_text())
    if args.github_output:
        with args.github_output.open('a') as output:
            for name, source in sources.items():
                for field in ('repository', 'revision'):
                    output.write(f'{name}_{field}={source[field]}\n')
    else:
        verify_sources(ROOT.parent, sources)


if __name__ == '__main__':
    main()
