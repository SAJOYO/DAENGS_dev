"""Opt-in subprocess adapter: real tasks/DB/broker, deterministic photo provider."""

import asyncio
import os
import re
import uuid
from urllib.parse import urlparse

from redis import Redis
from sqlalchemy.engine import make_url

from daengs_backend.config import settings
from daengs_backend.core import database as connections
from daengs_backend.core.storage import StoredObject
from daengs_backend.services import territory, territory_vision
from daengs_backend.tasks.territory import app

address = make_url(os.environ["TERRITORY_TEST_DATABASE_URL"])
schema = os.environ["TERRITORY_RUNTIME_TEST_SCHEMA"]
broker = os.environ["TERRITORY_RUNTIME_TEST_REDIS"]
prefix = os.environ["TERRITORY_RUNTIME_TEST_PREFIX"]
target = urlparse(broker)
assert address.host in {"127.0.0.1", "localhost"} and address.database == "claims_test"
assert target.hostname in {"127.0.0.1", "localhost"} and target.port not in {None, 6379}
assert re.fullmatch(r"territory_test_[0-9a-f]{32}", schema)
assert re.fullmatch(r"territory_runtime_[0-9a-f]{32}:", prefix)
settings.db_host = address.host
settings.db_port = address.port or 5432
settings.db_user = address.username
settings.db_name = address.database
settings.activity_game_enabled = False
app.conf.update(
    broker_url=broker,
    result_backend=broker,
    broker_transport_options={**app.conf.broker_transport_options, "global_keyprefix": prefix},
    result_backend_transport_options={"global_keyprefix": prefix},
)
redis = Redis.from_url(broker, decode_responses=True)
original_engine = connections.create_async_engine


def schema_engine(*args, **kwargs):
    # Keep production worker_session's per-loop engine/dispose lifecycle; isolate its schema.
    return original_engine(
        address,
        **kwargs,
        connect_args={"server_settings": {"search_path": schema, "lock_timeout": "5000"}},
    )


connections.create_async_engine = schema_engine


class Storage:
    def stat(self, key):
        return StoredObject("runtime-generation", 36, "image/jpeg")

    def read_bytes(self, key, *, generation, max_bytes):
        assert generation == "runtime-generation" and max_bytes == 36
        return str(uuid.UUID(key)).encode()

    def redact(self, key, *, generation):
        assert generation == "runtime-generation"


class Classifier:
    provider_name = "runtime-test"
    model_version = "deterministic-v1"

    async def classify(self, *, photo, content_type):
        attempt_id = str(uuid.UUID(photo.decode()))
        redis.incr(prefix + "calls:" + attempt_id)
        while redis.get(prefix + "block:" + attempt_id):
            await asyncio.sleep(0.05)
        return territory_vision.TerritoryVisionResult("verified", "dog_visible")


territory.get_storage = territory_vision.get_storage = Storage
territory_vision.GeminiTerritoryVision = Classifier
