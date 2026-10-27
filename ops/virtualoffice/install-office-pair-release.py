#!/usr/bin/python3
"""Install the first Office code pair with its own immutable selector.

Upgrades are deliberately a separate migration operation.  This installer is
idempotent only for the same pair, so a JAX deployment cannot move Office.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile


ROOT = Path('/opt/axioma-office-pair')
SOURCE_ROOT = Path('/opt/jax-platform-pair/releases')
SELECTION = Path('/etc/axioma-office/platform-pair-selection.json')
HELPER = ROOT / 'libexec/pair_artifact_readiness.py'
HEX40 = re.compile(r'[0-9a-f]{40}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_tree(path: Path) -> None:
    for directory, _, filenames in os.walk(path):
        for name in filenames:
            file_path = Path(directory) / name
            fd = os.open(file_path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    for directory, _, _ in os.walk(path, topdown=False):
        sync_dir(Path(directory))


def read_selection(path: Path = SELECTION) -> dict:
    node = path.lstat()
    if not stat.S_ISREG(node.st_mode) or node.st_uid != 0 or node.st_gid != 0 or stat.S_IMODE(node.st_mode) != 0o644:
        raise RuntimeError('Office selection has unsafe ownership or mode')
    data = json.loads(path.read_text(encoding='utf-8'))
    if set(data) != {'release', 'jax_platform_revision', 'jax_revision', 'manifest_sha256'}:
        raise RuntimeError('Office selection shape is invalid')
    return data


def inactive(unit: str) -> None:
    result = subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=False)
    if result.returncode == 0:
        raise RuntimeError(f'Office unit must be inactive: {unit}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-release', required=True, type=Path)
    parser.add_argument('--expected-platform-sha', required=True)
    parser.add_argument('--expected-jax-sha', required=True)
    parser.add_argument('--expected-manifest-sha256', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('root required')
    if not HEX40.fullmatch(args.expected_platform_sha) or not HEX40.fullmatch(args.expected_jax_sha) or not HEX64.fullmatch(args.expected_manifest_sha256):
        raise SystemExit('full reviewed digests required')
    source = args.source_release.resolve(strict=True)
    if source.parent != SOURCE_ROOT.resolve(strict=True) or args.source_release.is_symlink():
        raise RuntimeError('source is not an immutable pair release')
    if not HELPER.is_file() or HELPER.is_symlink():
        raise RuntimeError('Office pair attestor is absent')
    spec = importlib.util.spec_from_file_location('office_pair_attestor', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify(source, expected_jxp=args.expected_platform_sha, expected_jax=args.expected_jax_sha)
    if digest(source / 'pair-manifest.json') != args.expected_manifest_sha256:
        raise RuntimeError('source pair manifest digest differs')
    target = ROOT / 'releases' / f'{args.expected_platform_sha}-{args.expected_jax_sha}'
    expected = {
        'release': str(target), 'jax_platform_revision': args.expected_platform_sha,
        'jax_revision': args.expected_jax_sha, 'manifest_sha256': args.expected_manifest_sha256,
    }
    def existing_selection_matches() -> bool:
        current_link = ROOT / 'current'
        if not SELECTION.exists() and not SELECTION.is_symlink():
            return False
        if read_selection() != expected:
            raise RuntimeError('Office pair upgrade requires its own migration procedure')
        if not current_link.exists() and not current_link.is_symlink():
            return False
        if not current_link.is_symlink() or current_link.resolve(strict=True) != target:
            raise RuntimeError('Office pair upgrade requires its own migration procedure')
        module.verify(target, expected_jxp=args.expected_platform_sha, expected_jax=args.expected_jax_sha)
        if digest(target / 'pair-manifest.json') != args.expected_manifest_sha256:
            raise RuntimeError('installed pair manifest differs')
        return True

    if existing_selection_matches():
        return
    for unit in ('axioma-office-backend.service', 'axioma-office-migrate.service'):
        inactive(unit)
    lock_path = ROOT / '.runtime.lock'
    with lock_path.open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if existing_selection_matches():
            return
        for unit in ('axioma-office-backend.service', 'axioma-office-migrate.service'):
            inactive(unit)
        if not target.exists():
            stage = Path(tempfile.mkdtemp(prefix='.office-pair.', dir=ROOT / 'releases'))
            try:
                shutil.rmtree(stage)
                shutil.copytree(source, stage, symlinks=True)
                module.verify(stage, expected_jxp=args.expected_platform_sha, expected_jax=args.expected_jax_sha)
                if digest(stage / 'pair-manifest.json') != args.expected_manifest_sha256:
                    raise RuntimeError('copied pair manifest differs')
                sync_tree(stage)
                os.replace(stage, target)
                sync_dir(target.parent)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        module.verify(target, expected_jxp=args.expected_platform_sha, expected_jax=args.expected_jax_sha)
        if digest(target / 'pair-manifest.json') != args.expected_manifest_sha256:
            raise RuntimeError('installed pair manifest differs')
        current_link = ROOT / 'current'
        if current_link.exists() or current_link.is_symlink():
            if not current_link.is_symlink() or current_link.resolve(strict=True) != target:
                raise RuntimeError('Office selector conflicts with reviewed release')
        if SELECTION.exists() or SELECTION.is_symlink():
            if read_selection() != expected:
                raise RuntimeError('Office selection conflicts with reviewed release')
        tmp_link = ROOT / '.current.new'
        tmp_selection = SELECTION.with_name('.platform-pair-selection.new')
        if tmp_link.exists() or tmp_link.is_symlink():
            if not tmp_link.is_symlink() or os.readlink(tmp_link) != f'releases/{target.name}':
                raise RuntimeError('Office selector staging state is ambiguous')
            tmp_link.unlink()
            sync_dir(ROOT)
        if tmp_selection.exists() or tmp_selection.is_symlink():
            if read_selection(tmp_selection) != expected:
                raise RuntimeError('Office selection staging state is ambiguous')
            tmp_selection.unlink()
            sync_dir(SELECTION.parent)
        published_link = False
        published_selection = False
        try:
            if not current_link.is_symlink():
                tmp_link.symlink_to(Path('releases') / target.name)
                sync_dir(ROOT)
                os.replace(tmp_link, current_link)
                sync_dir(ROOT)
                published_link = True
            if not SELECTION.exists():
                with tmp_selection.open('x', encoding='utf-8') as stream:
                    os.fchmod(stream.fileno(), 0o644)
                    json.dump(expected, stream, separators=(',', ':'))
                    stream.write('\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                sync_dir(SELECTION.parent)
                os.replace(tmp_selection, SELECTION)
                sync_dir(SELECTION.parent)
                published_selection = True
            if read_selection() != expected or (ROOT / 'current').resolve(strict=True) != target:
                raise RuntimeError('Office selector publication failed')
        except BaseException:
            if published_selection:
                SELECTION.unlink()
            if published_link:
                (ROOT / 'current').unlink()
            raise
        finally:
            if tmp_link.is_symlink():
                tmp_link.unlink()
            if tmp_selection.exists():
                tmp_selection.unlink()
    print('Office pair installed with independent selection')


if __name__ == '__main__':
    main()
