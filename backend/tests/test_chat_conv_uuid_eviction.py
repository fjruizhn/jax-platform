import api.chat as chat


class _FakeMemory:
    is_connected = True

    def __init__(self):
        self.ended = []
        self._counter = 0

    async def start_conversation(self, **kwargs):
        self._counter += 1
        return f"conv-{self._counter}"

    async def end_conversation(self, uuid_):
        self.ended.append(uuid_)


def _reset():
    chat._conv_uuids.clear()


async def test_evicts_the_least_recently_used_conversation_past_the_cap(monkeypatch):
    _reset()
    fake = _FakeMemory()
    monkeypatch.setattr(chat, "_memory", fake)
    monkeypatch.setattr(chat, "_memory_ready", True)
    monkeypatch.setattr(chat, "MAX_TRACKED_CONVERSATIONS", 3)
    try:
        await chat._get_conv_uuid(1, "t", None)
        await chat._get_conv_uuid(2, "t", None)
        await chat._get_conv_uuid(3, "t", None)
        assert list(chat._conv_uuids.keys()) == ["1:None", "2:None", "3:None"]

        # re-tocar user 1 -> pasa a ser el más reciente
        await chat._get_conv_uuid(1, "t", None)
        assert list(chat._conv_uuids.keys()) == ["2:None", "3:None", "1:None"]

        # un cuarto par (usuario, proyecto) empuja al menos-recientemente-usado (user 2) afuera
        await chat._get_conv_uuid(4, "t", None)
        assert "2:None" not in chat._conv_uuids
        assert list(chat._conv_uuids.keys()) == ["3:None", "1:None", "4:None"]

        # la conversación de user 2 se cerró en la DB antes de descartarla -- no queda abandonada
        assert fake.ended == ["conv-2"]
    finally:
        _reset()


async def test_a_cache_hit_does_not_start_a_new_conversation_or_evict_anything(monkeypatch):
    _reset()
    fake = _FakeMemory()
    monkeypatch.setattr(chat, "_memory", fake)
    monkeypatch.setattr(chat, "_memory_ready", True)
    try:
        first = await chat._get_conv_uuid(1, "t", None)
        second = await chat._get_conv_uuid(1, "t", None)

        assert first == second == "conv-1"
        assert fake._counter == 1
        assert fake.ended == []
    finally:
        _reset()
