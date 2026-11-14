import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('package_aws', ROOT/'scripts/package_aws.py')
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'project'
        for directory in ('second_look', 'config', 'examples'):
            shutil.copytree(ROOT/directory, self.root/directory,
                            ignore=shutil.ignore_patterns('__pycache__'))
        self.client = json.loads((self.root/'examples/client.json').read_text())
        day = self.client['holdings_as_of']
        self.snapshot = self.root/f'data/runtime/snapshots/{day}.json'
        self.snapshot.parent.mkdir(parents=True)
        self.snapshot.write_text(json.dumps({'decision_date': day, 'quotes': {}}))
        # These extra inputs must never leak into the Lambda artifact.
        (self.root/'second_look/secrets.json').write_text('{"secret":"must stay out"}')
        (self.root/'second_look/__pycache__').mkdir()
        (self.root/'second_look/__pycache__/ignored.pyc').write_bytes(b'not-source')
        (self.root/'data/raw').mkdir()
        (self.root/'data/raw/ignored.csv').write_text('not packaged')
        self.output = Path(self.temp.name)/'artifacts'

    def test_zip_layout_is_importable_without_offline_dependencies(self):
        manifest = packaging.build_package(self.root, self.output)
        with zipfile.ZipFile(self.output/packaging.ZIP_NAME) as archive:
            names = archive.namelist()
            expected = {f'second_look/{p.name}' for p in (self.root/'second_look').glob('*.py')}
            expected |= {f'config/{name}' for name in packaging.CONFIG_NAMES}
            self.assertEqual(set(names), expected)
            self.assertEqual(names, sorted(names))
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))
            extracted = Path(self.temp.name)/'extracted'
            archive.extractall(extracted)
        code = (f'import sys; sys.path.insert(0, {str(extracted)!r}); '
                'from second_look.lambda_handler import handler; '
                'from second_look.spectrum import load_references; '
                'assert callable(handler); assert load_references()["version"]; '
                'assert "pandas" not in sys.modules; assert "numpy" not in sys.modules')
        result = subprocess.run([sys.executable, '-I', '-S', '-c', code],
                                capture_output=True, text=True, cwd=self.temp.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(manifest['lambda_zip']['sha256'],
                         packaging.sha256((self.output/packaging.ZIP_NAME).read_bytes()))

    def test_staged_s3_objects_and_hashes_match_original_bytes(self):
        manifest = packaging.build_package(self.root, self.output)
        day = self.client['holdings_as_of']
        expected = {f'config/{name}': self.root/'config'/name for name in packaging.CONFIG_NAMES}
        expected[f'clients/{self.client["client_id"]}.json'] = self.root/'examples/client.json'
        expected[f'snapshots/{day}.json'] = self.snapshot
        self.assertEqual(set(manifest['s3_objects']), set(expected))
        self.assertFalse(manifest['aws_actions_performed'])
        for key, source in expected.items():
            actual = (self.output/'s3'/key).read_bytes()
            self.assertEqual(actual, source.read_bytes())
            self.assertEqual(packaging.sha256(actual), manifest['s3_objects'][key]['sha256'])
        self.assertEqual(json.loads((self.output/'manifest.json').read_text()), manifest)

    def test_repeated_builds_are_byte_identical(self):
        first = packaging.build_package(self.root, self.output)
        before = {p.relative_to(self.output): p.read_bytes()
                  for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(packaging.build_package(self.root, self.output), first)
        another = Path(self.temp.name)/'another-output'
        self.assertEqual(packaging.build_package(self.root, another), first)
        self.assertEqual(before, {p.relative_to(another): p.read_bytes()
                                  for p in another.rglob('*') if p.is_file()})

    def test_missing_snapshot_fails_before_writing_output(self):
        self.snapshot.unlink()
        with self.assertRaisesRegex(ValueError, 'Run python -m second_look demo first'):
            packaging.build_package(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_unrelated_output_file_is_preserved(self):
        self.output.mkdir()
        unrelated = self.output/'teammate-notes.txt'
        unrelated.write_text('keep this')
        with self.assertRaisesRegex(ValueError, 'unrelated or stale'):
            packaging.build_package(self.root, self.output)
        self.assertEqual(unrelated.read_text(), 'keep this')
