import asyncio

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


async def test_concurrent_evictions_do_not_double_close_the_same_conversation(monkeypatch):
    _reset()
    fake = _FakeMemory()

    async def slow_end_conversation(uuid_):
        await asyncio.sleep(0)  # fuerza un punto de yield real -> interleaving genuino
        fake.ended.append(uuid_)

    fake.end_conversation = slow_end_conversation
    monkeypatch.setattr(chat, "_memory", fake)
    monkeypatch.setattr(chat, "_memory_ready", True)
    monkeypatch.setattr(chat, "MAX_TRACKED_CONVERSATIONS", 3)
    try:
        for i in range(5):
            chat._conv_uuids[f"k{i}"] = f"conv-k{i}"

        await asyncio.gather(
            chat._evict_oldest_conversation_if_over_cap(),
            chat._evict_oldest_conversation_if_over_cap(),
        )

        assert len(chat._conv_uuids) == 3  # bajó al cap, no se quedó pegado en 4
        assert len(fake.ended) == 2
        assert set(fake.ended) == {"conv-k0", "conv-k1"}  # dos distintas, ninguna repetida
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
