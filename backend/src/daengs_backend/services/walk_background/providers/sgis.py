"""SGIS exact-point administrative address and road-name snapshots."""

import asyncio
import copy
import hashlib
import math
import time
from collections import OrderedDict
from datetime import UTC, datetime

from daengs_backend.services.walk_background.http import PublicSourceError, get_json
from daengs_walk.route.road import road_name

BASE = "https://sgisapi.mods.go.kr/OpenAPI3"


class SgisSource:
    def __init__(self):
        self.token = None
        self.cache = OrderedDict()
        self.lock = asyncio.Lock()
        self.coordinates = OrderedDict()

    async def result(self, transport, path, params):
        body = await get_json(transport, BASE + path, params)
        if not isinstance(body, dict) or body.get("errCd") != 0:
            code = body.get("errCd") if isinstance(body, dict) else None
            if code == -401:
                self.token = None  # Next durable attempt authenticates again.
            raise PublicSourceError("sgis_rejected", code in {-401, -1})
        return body["result"]

    async def address(self, transport, key, secret, point, *, addr_type=20):
        if addr_type not in {10, 20}:
            raise ValueError("unsupported SGIS address type")
        credential = hashlib.sha256((key + ":" + secret).encode()).hexdigest()
        coordinate_key = (point["lat"], point["lng"])
        identity = (credential, *coordinate_key, addr_type)
        # Exact point cache only: rounding near a dong boundary could change its address.
        async with self.lock:
            now = time.time()
            if identity in self.cache and self.cache[identity][0] > now:
                self.cache.move_to_end(identity)
                return copy.deepcopy(self.cache[identity][1])
            if self.token is None or self.token[0] != credential or self.token[2] <= now + 30:
                auth = await self.result(
                    transport,
                    "/auth/authentication.json",
                    {
                        "consumer_key": key,
                        "consumer_secret": secret,
                    },
                )
                token, expires = auth["accessToken"], float(auth["accessTimeout"])
                if (
                    not isinstance(token, str)
                    or not token
                    or not math.isfinite(expires)
                    or expires <= now
                ):
                    raise PublicSourceError("invalid_authentication")
                self.token = (credential, token, expires)
            token = self.token[1]
            xy = self.coordinates.get(coordinate_key)
            if xy is None:
                xy = await self.result(
                    transport,
                    "/transformation/transcoord.json",
                    {
                        "accessToken": token,
                        "src": 4326,
                        "dst": 5179,
                        "posX": point["lng"],
                        "posY": point["lat"],
                    },
                )
                if not all(math.isfinite(float(xy[k])) for k in ("posX", "posY")):
                    raise PublicSourceError("invalid_coordinate_conversion")
                self.coordinates[coordinate_key] = xy
                while len(self.coordinates) > 128:
                    self.coordinates.popitem(last=False)
            rows = await self.result(
                transport,
                "/addr/rgeocode.json",
                {
                    "accessToken": token,
                    "x_coor": xy["posX"],
                    "y_coor": xy["posY"],
                    "addr_type": addr_type,
                },
            )
            if not isinstance(rows, list) or len(rows) > 1:
                raise PublicSourceError("ambiguous_address")
            address = None
            if rows:
                row = rows[0]
                fields = (
                    ("road_nm",)
                    if addr_type == 10
                    else ("sido_nm", "sgg_nm", "emdong_nm", "sido_cd", "sgg_cd", "emdong_cd")
                )
                if not isinstance(row, dict) or not all(
                    isinstance(row.get(k), str)
                    and 0 < len(row[k]) <= 100
                    and row[k].strip()
                    and row[k].lower() != "null"
                    for k in fields
                ):
                    raise PublicSourceError(
                        "incomplete_road" if addr_type == 10 else "incomplete_dong"
                    )
                address = {k: row[k] for k in fields}
                if addr_type == 10:
                    name = road_name(address["road_nm"])
                    if name is None:
                        raise PublicSourceError("invalid_road_name")
                    address = {"road_nm": name}
            value = (address, datetime.now(UTC).isoformat())
            self.cache[identity] = (now + 3600, value)
            self.cache.move_to_end(identity)
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
            return copy.deepcopy(value)


sgis = SgisSource()
