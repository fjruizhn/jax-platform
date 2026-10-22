"""The unprivileged pair builder reads exact Git blobs and rejects unsafe trees."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import json
import subprocess
import hashlib
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ops" / "migration" / "build-pair-jaxbuild.py"
SPEC = importlib.util.spec_from_file_location("pair_builder", SCRIPT)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
ADOPT = REPO / "ops" / "migration" / "adopt-pair-root.sh"
ADOPT_SPEC = importlib.util.spec_from_loader("pair_adoption", importlib.machinery.SourceFileLoader("pair_adoption", str(ADOPT)))
assert ADOPT_SPEC and ADOPT_SPEC.loader
adoption = importlib.util.module_from_spec(ADOPT_SPEC)
ADOPT_SPEC.loader.exec_module(adoption)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["/usr/bin/git", *args], cwd=repo, text=True).strip()


def _repository(tmp_path: Path, *, symlink: bool = False) -> tuple[Path, str]:
    repo = tmp_path / "source"
    subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "pair-test@example.invalid")
    _git(repo, "config", "user.name", "Pair test")
    (repo / "backend").mkdir()
    (repo / "backend" / "main.py").write_text("app = object()\n", encoding="utf-8")
    if symlink:
        (repo / "linked.py").symlink_to("backend/main.py")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_extracts_regular_blobs_from_exact_clean_git_tree(tmp_path: Path) -> None:
    source, commit = _repository(tmp_path)
    target = tmp_path / "target"
    target.mkdir()

    tree, links = builder.extract(source, commit, target)

    assert tree == _git(source, "rev-parse", f"{commit}^{{tree}}")
    assert links == []
    assert (target / "backend" / "main.py").read_text(encoding="utf-8") == "app = object()\n"
    assert (target / "backend" / "main.py").stat().st_mode & 0o777 == 0o644


def test_release_lineage_requires_a_real_git_ancestor(tmp_path: Path) -> None:
    source, first = _repository(tmp_path)
    (source / "backend" / "next.py").write_text("next\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "next")
    second = _git(source, "rev-parse", "HEAD")
    builder.require_ancestor(source, first, second)
    with pytest.raises(SystemExit, match="predecessor is not an ancestor"):
        builder.require_ancestor(source, second, first)


def test_rejects_symlink_git_entry_before_writing_any_source_bytes(tmp_path: Path) -> None:
    source, commit = _repository(tmp_path, symlink=True)
    target = tmp_path / "target"
    target.mkdir()

    tree, links = builder.extract(source, commit, target)
    assert tree
    assert links[0]["target"] == "backend/main.py"
    assert (target / "linked.py").is_symlink()


def test_canonical_jax_links_match_the_pair_artifact_namespace(tmp_path: Path) -> None:
    source, commit = _repository(tmp_path, symlink=True)
    _, links = builder.extract(source, commit, tmp_path / "target")

    canonical = builder.canonical_jax_links(links)

    assert canonical == [{**links[0], "path": "jax/linked.py", "target_path": "jax/backend/main.py"}]


def test_rejects_alternate_object_database_and_dirty_checkout(tmp_path: Path) -> None:
    source, commit = _repository(tmp_path)
    alternate = source / ".git" / "objects" / "info" / "alternates"
    alternate.parent.mkdir(exist_ok=True)
    alternate.write_text("/tmp/not-an-object-store\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="unsafe repository"):
        builder.repo_tree(source, commit)
    alternate.unlink()
    (source / "untracked").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="repository not fresh"):
        builder.repo_tree(source, commit)


def test_rejects_gitlink_and_blob_bytes_that_do_not_hash_to_claimed_oid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, commit = _repository(tmp_path)
    tree = _git(source, "write-tree")
    _git(source, "update-index", "--add", "--cacheinfo", f"160000,{commit},nested")
    gitlink_tree = _git(source, "write-tree")
    gitlink_commit = _git(source, "commit-tree", gitlink_tree, "-p", commit, "-m", "gitlink")
    _git(source, "update-ref", "HEAD", gitlink_commit)
    original = builder.run
    def clean_status(*args: str, cwd: Path) -> bytes:
        if args[:2] == ("status", "--porcelain"):
            return b""
        return original(*args, cwd=cwd)
    monkeypatch.setattr(builder, "run", clean_status)
    with pytest.raises(SystemExit, match="unsafe tree entry"):
        builder.extract(source, gitlink_commit, tmp_path / "gitlink-out")
    monkeypatch.setattr(builder, "run", original)
    source, commit = _repository(tmp_path / "regular")
    def corrupt(*args: str, cwd: Path) -> bytes:
        if args[:2] == ("cat-file", "blob"):
            return b"tampered"
        return original(*args, cwd=cwd)
    monkeypatch.setattr(builder, "run", corrupt)
    with pytest.raises(SystemExit, match="corrupt git blob"):
        builder.extract(source, commit, tmp_path / "corrupt-out")


def test_rejects_corrupt_nested_tree_before_writing_source_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, commit = _repository(tmp_path)
    nested_tree = _git(source, "rev-parse", f"{commit}:backend")
    original = builder.run

    def corrupt_nested_tree(*args: str, cwd: Path) -> bytes:
        if args == ("cat-file", "tree", nested_tree):
            return b"hostile nested tree"
        return original(*args, cwd=cwd)

    monkeypatch.setattr(builder, "run", corrupt_nested_tree)
    target = tmp_path / "out"
    with pytest.raises(SystemExit, match="corrupt git tree"):
        builder.extract(source, commit, target)
    assert not target.exists()


def test_mountinfo_octal_paths_are_decoded_before_comparison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "with space"; root.mkdir()
    encoded = os.fsencode(str(root)).replace(b" ", b"\\040")
    mountinfo = b"1 1 0:1 / " + encoded + b" rw - tmpfs tmpfs rw\n"
    monkeypatch.setattr(Path, "read_bytes", lambda path: mountinfo if str(path) == "/proc/self/mountinfo" else b"")
    assert adoption.mount_points_below(root) == {root}


def test_wheelhouse_digest_binds_filename_and_bytes_and_rejects_links(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "one.whl").write_bytes(b"one")
    first = builder.wheelhouse_digest(wheelhouse)
    (wheelhouse / "one.whl").write_bytes(b"two")
    assert builder.wheelhouse_digest(wheelhouse) != first
    (wheelhouse / "linked.whl").symlink_to("one.whl")
    with pytest.raises(SystemExit, match="unsafe entry"):
        builder.wheelhouse_digest(wheelhouse)


def _adoptable_tree(root: Path) -> None:
    (root / "jax-platform" / "backend" / ".venv" / "bin").mkdir(parents=True)
    (root / "jax-platform" / "backend" / ".venv" / "bin" / "python").write_bytes(b"python")
    attestation = {"schema": "axioma.pair-build/v2", "platform_commit": "a" * 40, "platform_tree": "b" * 40,
                   "platform_links": [], "jax_commit": "c" * 40, "jax_tree": "d" * 40, "jax_links": [],
                   "wheelhouse_sha256": "e" * 64, "lock_sha256": "f" * 64, "python": "/usr/bin/python3",
                   "python_sha256": "0" * 64, "python_version": "cpython-test", "freeze_sha256": "1" * 64}
    (root / "build-attestation.json").write_text(json.dumps(attestation), encoding="utf-8")
    for item in [root, *root.rglob("*")]:
        item.chmod(0o755 if item.is_dir() else 0o644)
    inventory = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "pair-manifest.json":
            inventory.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size,
                              "mode": f"{path.stat().st_mode & 0o7777:04o}",
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {"schema": "axioma.jax-platform-pair/v1", "jax_platform_revision": "a" * 40,
                "jax_revision": "c" * 40, "dependency_lock_sha256": "f" * 64,
                "wheelhouse_manifest_sha256": "e" * 64, "files": inventory, "jax_links": []}
    (root / "pair-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "pair-manifest.json").chmod(0o644)


def test_adoption_validator_accepts_plain_tree_and_rejects_hardlinks(tmp_path: Path) -> None:
    source = tmp_path / "build"
    _adoptable_tree(source)
    expected = hashlib.sha256((source / "pair-manifest.json").read_bytes()).hexdigest()
    adoption.validate_builder_tree(source, os.getuid(), expected)
    executable = source / "jax-platform" / "backend" / ".venv" / "bin" / "python"
    os.link(executable, source / "duplicate")
    with pytest.raises(SystemExit, match="hardlink"):
        adoption.validate_builder_tree(source, os.getuid(), expected)


def test_adoption_validator_rejects_extended_attributes(tmp_path: Path) -> None:
    source = tmp_path / "build"
    _adoptable_tree(source)
    try:
        os.setxattr(source / "build-attestation.json", "user.pair-test", b"x", follow_symlinks=False)
    except OSError as error:
        pytest.skip(f"filesystem does not provide test xattrs: {error}")
    with pytest.raises(SystemExit, match="xattr or ACL"):
        adoption.validate_builder_tree(source, os.getuid(), hashlib.sha256((source / "pair-manifest.json").read_bytes()).hexdigest())


def test_copy_and_seal_makes_fresh_inodes_despite_retained_writable_source_fd(tmp_path: Path) -> None:
    source = tmp_path / "build"; _adoptable_tree(source)
    expected = hashlib.sha256((source / "pair-manifest.json").read_bytes()).hexdigest()
    contract = adoption.validate_builder_tree(source, os.getuid(), expected)
    executable = source / "jax-platform" / "backend" / ".venv" / "bin" / "python"
    source_inode = executable.stat().st_ino
    held = executable.open("r+b")
    try:
        destination = tmp_path / "sealed"
        adoption.copy_and_seal(source, destination, contract, owner_uid=os.getuid(), owner_gid=os.getgid())
        held.seek(0); held.write(b"EVIL"); held.flush(); os.fsync(held.fileno())
    finally:
        held.close()
    copied = destination / "jax-platform" / "backend" / ".venv" / "bin" / "python"
    assert copied.read_bytes() == b"python"
    assert copied.stat().st_ino != source_inode
    assert copied.stat().st_nlink == 1


def test_copy_and_seal_rejects_intermediate_symlink_swap_without_publishing(tmp_path: Path) -> None:
    source = tmp_path / "build"; _adoptable_tree(source)
    expected = hashlib.sha256((source / "pair-manifest.json").read_bytes()).hexdigest()
    contract = adoption.validate_builder_tree(source, os.getuid(), expected)
    (source / "jax-platform").rename(source / "saved-platform")
    (source / "jax-platform").symlink_to(tmp_path / "outside")
    destination = tmp_path / "sealed"
    with pytest.raises(SystemExit, match="unsafe path component"):
        adoption.copy_and_seal(source, destination, contract, owner_uid=os.getuid(), owner_gid=os.getgid())
    assert not destination.exists()


def test_copy_and_seal_rechecks_builder_processes_before_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "build"; _adoptable_tree(source)
    expected = hashlib.sha256((source / "pair-manifest.json").read_bytes()).hexdigest()
    contract = adoption.validate_builder_tree(source, os.getuid(), expected)
    monkeypatch.setattr(adoption, "process_uids", lambda: {os.getuid()})
    destination = tmp_path / "sealed"
    with pytest.raises(SystemExit, match="gained a process"):
        adoption.copy_and_seal(source, destination, contract, owner_uid=os.getuid(), owner_gid=os.getgid(), builder_uid=os.getuid())
    assert not destination.exists()


def test_manifest_replacement_after_read_copies_the_external_digest_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "build"; _adoptable_tree(source)
    manifest_path = source / "pair-manifest.json"; first = manifest_path.read_bytes()
    replacement = tmp_path / "replacement-manifest.json"; replacement.write_bytes(first + b" \n")
    replacement.chmod(0o644)
    manifest_inode = manifest_path.stat().st_ino; original_read = adoption.os.read; swapped = False
    def replace_after_read(fd: int, count: int) -> bytes:
        nonlocal swapped
        value = original_read(fd, count)
        if not swapped and value and os.fstat(fd).st_ino == manifest_inode:
            os.replace(replacement, manifest_path); swapped = True
        return value
    monkeypatch.setattr(adoption.os, "read", replace_after_read)
    contract = adoption.validate_builder_tree(source, os.getuid(), hashlib.sha256(first).hexdigest())
    destination = tmp_path / "sealed"
    adoption.copy_and_seal(source, destination, contract, owner_uid=os.getuid(), owner_gid=os.getgid())
    assert swapped
    assert manifest_path.read_bytes() != first
    assert (destination / "pair-manifest.json").read_bytes() == first


def test_copy_and_seal_rejects_source_mutation_during_stream_and_never_publishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "build"; _adoptable_tree(source)
    large = source / "jax-platform" / "backend" / ".venv" / "bin" / "python"
    large.write_bytes(b"A" * (2 * 1024 * 1024))
    # Rebuild the authoritative inventory after making the fixture intentionally large.
    expected = hashlib.sha256((source / "pair-manifest.json").read_bytes()).hexdigest()
    manifest = json.loads((source / "pair-manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if item["path"].endswith("/.venv/bin/python"):
            item["size"] = large.stat().st_size; item["sha256"] = hashlib.sha256(large.read_bytes()).hexdigest()
    (source / "pair-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    expected = hashlib.sha256((source / "pair-manifest.json").read_bytes()).hexdigest()
    contract = adoption.validate_builder_tree(source, os.getuid(), expected)
    held = large.open("r+b"); original_read = adoption.os.read; changed = False
    source_inode = large.stat().st_ino
    def racing_read(fd: int, count: int) -> bytes:
        nonlocal changed
        value = original_read(fd, count)
        if not changed and value and os.fstat(fd).st_ino == source_inode:
            held.seek(1024 * 1024); held.write(b"EVIL"); held.flush(); changed = True
        return value
    monkeypatch.setattr(adoption.os, "read", racing_read)
    destination = tmp_path / "sealed"
    try:
        with pytest.raises(SystemExit, match="source changed during adoption"):
            adoption.copy_and_seal(source, destination, contract, owner_uid=os.getuid(), owner_gid=os.getgid())
    finally:
        held.close()
    assert changed and not destination.exists()


def test_adoption_requires_external_manifest_digest_and_all_attested_links(tmp_path: Path) -> None:
    source = tmp_path / "build"; _adoptable_tree(source)
    expected = hashlib.sha256((source / "pair-manifest.json").read_bytes()).hexdigest()
    with pytest.raises(SystemExit, match="unexpected manifest digest"):
        adoption.validate_builder_tree(source, os.getuid(), "0" * 64)
    manifest = json.loads((source / "pair-manifest.json").read_text(encoding="utf-8"))
    link = source / "jax" / "alias"; link.parent.mkdir(); link.symlink_to("target")
    (source / "jax" / "target").write_text("x", encoding="utf-8")
    manifest["jax_links"] = [{"path": "jax/alias", "mode": "120000", "blob_oid": "1" * 40,
                              "target": "target", "target_path": "jax/target", "target_mode": "100644", "target_oid": "2" * 40}]
    (source / "pair-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    expected = hashlib.sha256((source / "pair-manifest.json").read_bytes()).hexdigest()
    with pytest.raises(SystemExit, match="invalid build contract"):
        adoption.validate_builder_tree(source, os.getuid(), expected)


def test_real_jax_commit_extracts_all_internal_links_in_disposable_clone(tmp_path: Path) -> None:
    source = Path("/home/fruiz/jax")
    if not source.is_dir():
        pytest.skip("local JAX source unavailable")
    clone = tmp_path / "jax"
    subprocess.run(["/usr/bin/git", "clone", "-q", "--no-hardlinks", str(source), str(clone)], check=True)
    target = tmp_path / "release" / "jax"
    tree, links = builder.extract(clone, "8da1473939af2c8178b8b632e2d77b9120556cab", target)
    assert tree == "5a04d2ca83d6eda75d1b1b8b32d0b4f4942fb9fb"
    assert len(links) == 16
    canonical = builder.canonical_jax_links(links)
    assert hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == "b794e47404b5615509a6b7bf14bde1c68dd06b6c89185b4c9431b3ebdfd88d0f"
    assert all(link["path"].startswith("jax/") and link["target_path"].startswith("jax/") for link in canonical)
    assert all((target / link["path"]).is_symlink() for link in links)
    assert (target / "las_manos" / "jacobs").is_dir()


@pytest.mark.parametrize("target", ["/etc/passwd", "../../outside", "missing.py"])
def test_internal_link_rejects_absolute_escape_and_dangling_targets(tmp_path: Path, target: str) -> None:
    source, _ = _repository(tmp_path)
    (source / "bad").symlink_to(target)
    _git(source, "add", "."); _git(source, "commit", "-qm", "bad link")
    with pytest.raises(SystemExit, match="unsafe symlink target"):
        builder.extract(source, _git(source, "rev-parse", "HEAD"), tmp_path / "out")


@pytest.mark.parametrize("links", [("a", "backend/main.py", "b", "a"), ("a", "b", "b", "a"), ("a", "../platform.py", "", "")])
def test_internal_link_rejects_chain_cycle_and_cross_tree(tmp_path: Path, links: tuple[str, str, str, str]) -> None:
    source, _ = _repository(tmp_path)
    first, first_target, second, second_target = links
    (source / first).symlink_to(first_target)
    if second:
        (source / second).symlink_to(second_target)
    _git(source, "add", "."); _git(source, "commit", "-qm", "unsafe links")
    with pytest.raises(SystemExit, match="unsafe symlink target"):
        builder.extract(source, _git(source, "rev-parse", "HEAD"), tmp_path / "out")


def test_builder_requires_explicit_bootstrap_or_both_predecessors() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert '--bootstrap-predecessor' in source
    assert 'both Git predecessors are required' in source
    bootstrap = REPO / 'ops/migration/bootstrap-pair-predecessor.py'
    text = bootstrap.read_text(encoding='utf-8')
    assert 'bootstrap requires an empty canonical pair root' in text
    assert "--expected-manifest-sha256" in text
    assert 'subprocess.run' in text and 'adopt-pair-root.py' in text


def test_adopter_rejects_forged_supervisor_receipt_bindings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / 'output'; source.mkdir()
    manifest = b'{"sealed":true}\n'
    (source / 'pair-manifest.json').write_bytes(manifest)
    request = {'schema':'axioma.pair-build-request/v1','nonce':'a'*64,'platform_commit':'b'*40,'jax_commit':'c'*40}
    receipt = {'schema':'axioma.pair-build-receipt/v1','nonce':'a'*64,'platform_commit':'b'*40,'jax_commit':'c'*40,
               'output_dev':source.stat().st_dev,'output_ino':source.stat().st_ino,
               'manifest_sha256':hashlib.sha256(manifest).hexdigest()}
    class Stat:
        st_uid=0; st_gid=0; st_mode=0o100600
    original = adoption._read_regular_once
    def sealed(path: Path):
        if path.name == 'request': return json.dumps(request).encode(), Stat()
        if path.name == 'receipt': return json.dumps(receipt).encode(), Stat()
        return original(path)
    monkeypatch.setattr(adoption, '_read_regular_once', sealed)
    adoption.validate_supervisor_binding(source, tmp_path/'request', tmp_path/'receipt')
    receipt['output_ino'] += 1
    with pytest.raises(SystemExit, match='output identity'):
        adoption.validate_supervisor_binding(source, tmp_path/'request', tmp_path/'receipt')
    receipt['output_ino'] = source.stat().st_ino
    receipt['manifest_sha256'] = '0'*64
    with pytest.raises(SystemExit, match='manifest mismatch'):
        adoption.validate_supervisor_binding(source, tmp_path/'request', tmp_path/'receipt')
    receipt['manifest_sha256'] = hashlib.sha256(manifest).hexdigest()
    receipt['nonce'] = 'd'*64
    with pytest.raises(SystemExit, match='receipt mismatch'):
        adoption.validate_supervisor_binding(source, tmp_path/'request', tmp_path/'receipt')
