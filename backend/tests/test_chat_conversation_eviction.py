import api.chat as chat


def _reset():
    chat._conversations.clear()


def test_update_history_caps_turns_per_user():
    _reset()
    try:
        for i in range(chat.MAX_TURNS + 5):
            chat._update_history("user-1", f"msg{i}", f"reply{i}")

        history = chat._conversations["user-1"]
        assert len(history) == chat.MAX_TURNS * 2
        # se conservan los turnos más recientes, no los primeros
        assert history[-1]["content"] == f"reply{chat.MAX_TURNS + 4}"
    finally:
        _reset()


def test_update_history_evicts_the_least_recently_active_user_past_the_cap(monkeypatch):
    _reset()
    monkeypatch.setattr(chat, "MAX_TRACKED_USERS", 3)
    try:
        chat._update_history("user-a", "hi", "hola")
        chat._update_history("user-b", "hi", "hola")
        chat._update_history("user-c", "hi", "hola")
        assert list(chat._conversations.keys()) == ["user-a", "user-b", "user-c"]

        # user-a vuelve a hablar -> pasa a ser el más reciente
        chat._update_history("user-a", "again", "again reply")
        assert list(chat._conversations.keys()) == ["user-b", "user-c", "user-a"]

        # un cuarto usuario nuevo empuja al menos-recientemente-activo (user-b) afuera
        chat._update_history("user-d", "hi", "hola")
        assert "user-b" not in chat._conversations
        assert list(chat._conversations.keys()) == ["user-c", "user-a", "user-d"]
        assert len(chat._conversations) == 3
    finally:
        _reset()
