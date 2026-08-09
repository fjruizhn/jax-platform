import tomllib

from api.chat import _load_config


def test_load_config_reads_the_file_at_most_once(monkeypatch, tmp_path):
    _load_config.cache_clear()
    try:
        config_file = tmp_path / "config.toml"
        config_file.write_text('model_default = "qwen3-coder:30b"\n')
        monkeypatch.setattr("api.chat.CONFIG_PATH", str(config_file))

        calls = []
        original_load = tomllib.load

        def counting_load(f):
            calls.append(1)
            return original_load(f)

        monkeypatch.setattr(tomllib, "load", counting_load)

        first = _load_config()
        second = _load_config()
        third = _load_config()

        assert first == second == third == {"model_default": "qwen3-coder:30b"}
        assert len(calls) == 1
    finally:
        _load_config.cache_clear()
