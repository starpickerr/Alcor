import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from fresh_profile_fixture import fresh_test_home, initialize_fresh


class FreshFixtureTest(unittest.TestCase):
    def test_schema_once_and_closed_home(self):
        with fresh_test_home('alcor-fresh-test-') as home:
            dsh = Path(home) / 'dsh'; profile = dsh / 'profiles/desktop'; profile.mkdir(parents=True)
            marker = initialize_fresh(home, dsh, profile)
            self.assertEqual(marker, json.loads((profile / 'alcor/.migration.json').read_text()))
            self.assertEqual(marker, {'schema': 1, 'status': 'complete', 'source': 'fresh',
                'directories': dict.fromkeys(('business', 'appleSync', 'email', 'elearning'), 'absent'),
                'domains': dict.fromkeys(('organizer', 'secretary'), 'absent'),
                'credentials': dict.fromkeys(('elearning', 'email'), 'absent'), 'schedule': {'mapped': 0}})
            self.assertEqual((profile / 'alcor/.migration.json').stat().st_mode & 0o777, 0o600)
            root = Path(__file__).resolve().parents[2]
            check = """import { assertProfileMigrated } from './packages/alcor-organizer/src/profile-migration.js';
              assertProfileMigrated({ get: () => ({ dir: process.argv[1], home: process.argv[2], startedBundles: [] }) });"""
            subprocess.run(['node', '--input-type=module', '-e', check, str(profile), str(dsh)], cwd=root, check=True)
            with self.assertRaises(ValueError): initialize_fresh(home, dsh, profile)
        with self.assertRaises(ValueError): initialize_fresh(home, dsh, profile)

    def test_rejects_conflicts_and_does_not_overwrite(self):
        for relative in ['profiles/desktop/stella', 'profiles/desktop/alcor', 'storages/stella_organizer.json', 'storages/alcor_secretary.json', 'storages/schedule.json', '.credentials.yaml']:
            with self.subTest(relative=relative), fresh_test_home('alcor-conflict-test-') as home:
                dsh = Path(home) / 'dsh'; profile = dsh / 'profiles/desktop'; profile.mkdir(parents=True)
                target = dsh / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_text('sentinel')
                with self.assertRaises(ValueError): initialize_fresh(home, dsh, profile)
                self.assertEqual(target.read_text(), 'sentinel')

    def test_rejects_symlinks_escapes_legacy_bundle_and_forged_handle(self):
        with self.assertRaises(ValueError):
            with fresh_test_home('../outside-'):
                pass
        with fresh_test_home('alcor-links-test-') as home:
            root = Path(home); dsh = root / 'dsh'; profile = dsh / 'profiles/desktop'; profile.mkdir(parents=True)
            with self.assertRaises(ValueError): initialize_fresh(root, dsh, profile)
            with self.assertRaises(ValueError): initialize_fresh(home, dsh, Path('/private/tmp/profiles/desktop'))
            (profile / 'package.json').write_text(json.dumps({'dsh': {'profile': {'bundles': ['@stella/secretary']}}}))
            with self.assertRaises(ValueError): initialize_fresh(home, dsh, profile)
            (profile / 'package.json').unlink()
            (dsh / 'storages').symlink_to('/private/tmp')
            with self.assertRaises(OSError): initialize_fresh(home, dsh, profile)
            (dsh / 'storages').unlink()
            profile.rename(dsh / 'profiles/actual')
            profile.symlink_to(dsh / 'profiles/actual')
            with self.assertRaises(OSError): initialize_fresh(home, dsh, profile)
            self.assertFalse((dsh / 'profiles/actual/alcor').exists())


if __name__ == '__main__': unittest.main()
