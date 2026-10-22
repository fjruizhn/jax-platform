"""Credential parser keeps service input separate from inherited environment."""
import importlib.util
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
