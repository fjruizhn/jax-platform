"""Credential parser keeps service input separate from inherited environment."""
import importlib.util
import subprocess
import sys
from pathlib import Path
import pytest

MODULE=Path(__file__).resolve().parents[2]/'ops/migration/jax-platform-pair-exec.py'
SPEC=importlib.util.spec_from_file_location('pair_exec',MODULE); assert SPEC and SPEC.loader
pair_exec=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(pair_exec)

def test_credential_parser_accepts_values_with_equals_and_spaces_once(tmp_path):
    path=tmp_path/'runtime.env'; path.write_text('JAX_DB_PASSWORD=a=b with spaces\nJAX_TRUSTED_PROXIES=127.0.0.1\n')
    assert pair_exec.credential(path)['JAX_DB_PASSWORD'] == 'a=b with spaces'

@pytest.mark.parametrize('text', ['UNKNOWN=x\n','JAX_DB_HOST=x\nJAX_DB_HOST=y\n','not_name=x\n','JAX_DB_HOST\n'])
def test_credential_parser_rejects_unknown_duplicate_or_malformed_input(tmp_path,text):
    path=tmp_path/'runtime.env'; path.write_text(text)
    with pytest.raises(SystemExit): pair_exec.credential(path)

def test_isolated_child_uses_real_venv_site_packages(tmp_path):
    wheelhouse=Path('/home/fruiz/encargos-codex/office-worker-wheelhouse-20261022')
    if not wheelhouse.is_dir(): pytest.skip('pinned wheelhouse unavailable')
    venv=tmp_path/'venv'
    subprocess.run([sys.executable,'-m','venv',str(venv)],check=True)
    python=venv/'bin/python'
    subprocess.run([str(python),'-m','pip','install','--no-index',f'--find-links={wheelhouse}','uvicorn==0.51.0'],check=True)
    assert subprocess.run([str(python),'-I','-c','import uvicorn'],check=False).returncode == 0
    assert subprocess.run([str(python),'-I','-S','-c','import uvicorn'],check=False).returncode != 0

def test_isolated_child_resolves_validated_backend_modules_without_running_migration(tmp_path):
    venv=tmp_path/'venv'; backend=tmp_path/'backend'; (backend/'db').mkdir(parents=True)
    (backend/'main.py').write_text('app=object()\n')
    (backend/'db'/'__init__.py').write_text('')
    (backend/'db'/'migrate.py').write_text('MARKER=True\n')
    subprocess.run([sys.executable,'-m','venv',str(venv)],check=True)
    python=venv/'bin/python'
    code="import importlib,sys;sys.path.insert(0,sys.argv[1]);import main,db.migrate;assert main.app and db.migrate.MARKER"
    assert subprocess.run([str(python),'-I','-c',code,str(backend)],check=False).returncode == 0

def test_launcher_refuses_pre_ddl_activation_journal_before_release_execution():
    source = MODULE.read_text(encoding='utf-8')
    assert "activation-journal.json" in source
    assert "journal.get('state') != 'DDL_STARTED'" in source


def test_launcher_allows_broker_configuration_and_a_root_only_engine_activation_mode():
    source = MODULE.read_text(encoding='utf-8')
    assert 'OFFICE_BROKER_ALLOWED_BASE_URLS' in source
    assert "'office-engine'" in source
    assert "migrate.env" in source
    assert "Path(cred) != Path('/etc/jax/migrate.env')" in source
    assert 'stat.S_IMODE(credential_stat.st_mode) != 0o600' in source
    assert "virtual_office.engine_activation_cli" in source


def test_office_engine_cli_imports_from_verified_backend_under_isolated_python():
    backend = Path(__file__).resolve().parents[1]
    argv = pair_exec.office_engine_argv(sys.executable, backend, ['--help'])
    result = subprocess.run(argv, cwd='/', capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert 'activate-office-engine' in result.stdout
