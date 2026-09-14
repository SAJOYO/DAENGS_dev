"""Acknowledgement of one upload, never a receipt for the entire finalized walk."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class WalkChunkReceipt(BaseModel):
    seq_from: int = Field(ge=0)
    seq_to: int = Field(ge=0)
    point_count: int = Field(gt=0)
    status: Literal["stored", "replayed"]


class WalkUploadReceipt(BaseModel):
    contract_version: Literal["walk-upload-receipt-v1"] = "walk-upload-receipt-v1"
    walk_id: uuid.UUID
    client_session_id: uuid.UUID
    chunk: WalkChunkReceipt | None
