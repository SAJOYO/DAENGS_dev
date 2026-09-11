"""One activity publisher per Redis DB; no game activation or season creation."""

import json
import logging
import os
import time
from pathlib import Path

from celery.beat import Scheduler
from redis import Redis
from redis.exceptions import LockError, RedisError

LOG = logging.getLogger(__name__)
LOCK_KEY = "daengs:activity:beat:leader:v1"
HEARTBEAT = Path(os.environ.get("ACTIVITY_BEAT_HEARTBEAT", "/tmp/activity-beat.json"))


class ActivityScheduler(Scheduler):
    """Keep run times in memory: process_pending catches up from durable DB revisions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.redis = Redis.from_url(
            self.app.conf.broker_url, socket_connect_timeout=5, socket_timeout=5
        )
        self.lease = self.redis.lock(LOCK_KEY, timeout=60, thread_local=False)

    def _leader(self):
        try:
            if self.lease.owned():
                self.lease.reacquire()
                return True
            return self.lease.acquire(blocking=False)
        except (RedisError, LockError):
            # A partition must not turn this scheduler into an independent publisher.
            LOG.warning("activity beat lease unavailable")
            return False

    def _heartbeat(self, state):
        temporary = HEARTBEAT.with_suffix(".tmp")
        temporary.write_text(json.dumps({"state": state, "at": time.time()}))
        temporary.replace(HEARTBEAT)

    def tick(self, *args, **kwargs):
        from daengs_backend.config import settings

        if not settings.activity_game_enabled:
            self._heartbeat("disabled")
            return 5
        if not self._leader():
            self._heartbeat("standby")
            return 5
        delay = super().tick(*args, **kwargs)
        self._heartbeat("leader")
        return min(delay, 5)

    def apply_entry(self, entry, producer=None):
        # Recheck after obtaining the broker producer: acquiring it can take time.
        if self._leader():
            return super().apply_entry(entry, producer=producer)
        return None

    def close(self):
        try:
            if self.lease.owned():
                self.lease.release()
        except (RedisError, LockError):
            pass  # TTL releases a crashed/disconnected owner; never delete another token.
        finally:
            self.redis.close()
            super().close()
