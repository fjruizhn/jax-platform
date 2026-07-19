import asyncio

# Shared between the WS endpoint (main.py) and the SSE endpoint (api/events.py):
# both mutate event_bus's single per-user subscription slot as part of a
# connect+subscribe / disconnect+maybe-unsubscribe sequence, so those
# sequences must be serialized against EACH OTHER, not just against
# same-channel siblings. Without this, an SSE tab's disconnect can race a
# WS tab's connect for the same user (or vice versa) and wipe out the
# subscription the other channel just installed. Lives here (not in
# main.py) because api/events.py is imported BY main.py at module load
# time, so main.py can't be imported back from api/events.py.
lifecycle_lock = asyncio.Lock()


class ChannelConnectionCounter:
    """Tracks live connection counts per user_id for a channel that (unlike
    ws_hub) has no need to keep the connection objects themselves — SSE only
    needs to know "is at least one connection for this user still open" so
    the shared disconnect logic can decide whether it's safe to tear down
    event_bus's subscription for that user. All mutation must happen while
    holding `lifecycle_lock`, matching ws_hub's connection bookkeeping.
    """

    def __init__(self):
        self._counts: dict[str, int] = {}

    def increment(self, user_id: str):
        self._counts[user_id] = self._counts.get(user_id, 0) + 1

    def decrement(self, user_id: str):
        if user_id not in self._counts:
            return
        self._counts[user_id] -= 1
        if self._counts[user_id] <= 0:
            del self._counts[user_id]

    def has_connections(self, user_id: str) -> bool:
        return self._counts.get(user_id, 0) > 0


sse_connections = ChannelConnectionCounter()
