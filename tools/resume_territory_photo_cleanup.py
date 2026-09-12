"""Resume one blocked photo cleanup after the operator has checked its original object."""

import argparse
import asyncio
import json
import uuid

from daengs_backend.core.database import worker_session
from daengs_backend.services.territory import resume_photo_cleanup


async def resume(attempt_id, expected_generation):
    async with worker_session() as session:
        return await resume_photo_cleanup(
            session, attempt_id, expected_generation=expected_generation
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-id", type=uuid.UUID, required=True)
    parser.add_argument("--expected-generation", required=True)
    args = parser.parse_args()
    try:
        resumed = asyncio.run(resume(args.attempt_id, args.expected_generation))
    except Exception as exc:  # noqa: BLE001 -- no connection values or object identities in output
        print(json.dumps({"cleanup_resume_error": type(exc).__name__}))
        raise SystemExit(1) from None
    print(json.dumps({"cleanup_resumed": resumed}))
    raise SystemExit(0 if resumed else 1)
