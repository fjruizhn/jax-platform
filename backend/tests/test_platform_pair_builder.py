"""The unprivileged pair builder reads exact Git blobs and rejects unsafe trees."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import json
import subprocess
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


def test_rejects_symlink_git_entry_before_writing_any_source_bytes(tmp_path: Path) -> None:
    source, commit = _repository(tmp_path, symlink=True)
    target = tmp_path / "target"
    target.mkdir()

    tree, links = builder.extract(source, commit, target)
    assert tree
    assert links[0]["target"] == "backend/main.py"
    assert (target / "linked.py").is_symlink()


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
    (root / "build-attestation.json").write_text("{}\n", encoding="utf-8")
    for item in [root, *root.rglob("*")]:
        item.chmod(0o755 if item.is_dir() else 0o644)


def test_adoption_validator_accepts_plain_tree_and_rejects_hardlinks(tmp_path: Path) -> None:
    source = tmp_path / "build"
    _adoptable_tree(source)
    adoption.validate_builder_tree(source, os.getuid())
    executable = source / "jax-platform" / "backend" / ".venv" / "bin" / "python"
    os.link(executable, source / "duplicate")
    with pytest.raises(SystemExit, match="hardlink"):
        adoption.validate_builder_tree(source, os.getuid())


def test_adoption_validator_rejects_extended_attributes(tmp_path: Path) -> None:
    source = tmp_path / "build"
    _adoptable_tree(source)
    try:
        os.setxattr(source / "build-attestation.json", "user.pair-test", b"x", follow_symlinks=False)
    except OSError as error:
        pytest.skip(f"filesystem does not provide test xattrs: {error}")
    with pytest.raises(SystemExit, match="xattr or ACL"):
        adoption.validate_builder_tree(source, os.getuid())


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
