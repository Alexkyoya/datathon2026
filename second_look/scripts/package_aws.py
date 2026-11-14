"""Build an inspectable Lambda ZIP and S3 input folder, without AWS access.

Run after ``python -m second_look demo`` from the Second Look project directory:
    python scripts/package_aws.py

The rule Lambda uses the AWS-provided boto3 SDK. pandas/numpy belong to the
offline data cleaner and are deliberately excluded from this artifact.
"""
import argparse
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

CONFIG_NAMES = ('assets.json', 'policy.json', 'reference_portfolios.json')
ZIP_NAME = 'lambda.zip'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_source(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Required source must be a regular file: {path}')
    return path.read_bytes()


def build_package(root, output=None):
    """Return a deterministic manifest and write only the allowlisted artifacts."""
    root = Path(root).resolve()
    output = Path(output).resolve() if output is not None else root/'dist/aws'
    client_path = root/'examples/client.json'
    client_bytes = read_source(client_path)
    client = json.loads(client_bytes)
    client_id = client['client_id']
    day = client['holdings_as_of']
    if not isinstance(client_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', client_id):
        raise ValueError('Stored client_id is not a safe S3 filename')
    if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError('Stored holdings_as_of must be YYYY-MM-DD')
    snapshot_path = root/f'data/runtime/snapshots/{day}.json'
    if not snapshot_path.is_file():
        raise ValueError(f'Missing snapshot {snapshot_path}. Run python -m second_look demo first.')
    snapshot_bytes = read_source(snapshot_path)
    if json.loads(snapshot_bytes)['decision_date'] != day:
        raise ValueError('Snapshot decision date does not match the stored holdings date')

    module = root/'second_look'
    module_paths = sorted(module.glob('*.py'))
    if not (module/'__init__.py').is_file() or not (module/'lambda_handler.py').is_file():
        raise ValueError('The inner second_look Python module is missing')
    archive_files = {f'second_look/{path.name}': read_source(path) for path in module_paths}
    s3_files = {f'clients/{client_id}.json': client_bytes,
                f'snapshots/{day}.json': snapshot_bytes}
    for name in CONFIG_NAMES:
        content = read_source(root/'config'/name)
        json.loads(content)
        archive_files[f'config/{name}'] = content
        s3_files[f'config/{name}'] = content

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(archive_files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content, compresslevel=9)
    zip_bytes = buffer.getvalue()
    manifest = {
        'format_version': 1,
        'handler': 'second_look.lambda_handler.handler',
        'aws_actions_performed': False,
        'lambda_zip': {'path': ZIP_NAME, 'sha256': sha256(zip_bytes), 'bytes': len(zip_bytes),
                       'members': {name: {'sha256': sha256(content), 'bytes': len(content)}
                                   for name, content in sorted(archive_files.items())}},
        's3_objects': {key: {'local_path': f's3/{key}', 'sha256': sha256(content), 'bytes': len(content)}
                       for key, content in sorted(s3_files.items())},
        'notes': [
            'Upload only to existing resources after inspecting the manifest.',
            'S3 keys are relative to the existing DATA_PREFIX; no bucket or prefix is created here.',
            'The ZIP contains source and fallback configuration, not dependencies, raw data, clients or snapshots.',
            'The AWS runtime supplies boto3. Offline cleaning requires requirements.txt separately.',
        ],
    }
    files = {ZIP_NAME: zip_bytes,
             **{f's3/{key}': content for key, content in s3_files.items()},
             'manifest.json': (json.dumps(manifest, indent=2, sort_keys=True)+'\n').encode()}
    # Keep source data intact even if a caller mistakenly chooses a source folder.
    if output == root or output in root.parents:
        raise ValueError('Output must be a dedicated artifact directory, not the project or its parent')
    if output.exists():
        for path in output.rglob('*'):
            if path.is_symlink():
                raise ValueError(f'Output contains a symlink; choose a new output directory: {path}')
            if path.is_file() and path.relative_to(output).as_posix() not in files:
                raise ValueError(f'Output contains unrelated or stale files; choose a new output directory: {path}')
    output.mkdir(parents=True, exist_ok=True)
    for name, content in sorted(files.items()):
        target = output/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, help='Dedicated artifact directory; default: PROJECT/dist/aws')
    args = parser.parse_args()
    try:
        manifest = build_package(args.root, args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'Cannot package Second Look: {exc}\n')
    output = args.output.resolve() if args.output else args.root.resolve()/'dist/aws'
    print(json.dumps({'output_directory': str(output), 'handler': manifest['handler'],
                      'lambda_zip': str(output/ZIP_NAME), 'manifest': str(output/'manifest.json'),
                      's3_object_count': len(manifest['s3_objects']),
                      'aws_actions_performed': False}, indent=2))


if __name__ == '__main__':
    main()
