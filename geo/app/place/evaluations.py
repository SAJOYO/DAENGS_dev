"""요청 조건과 Place 사실을 대조하는 결정론적 평가.

평가는 장소 자체의 사실이 아니다. 같은 시설도 데려가는 개에 따라 답이 달라지므로
`PlaceResult.facts`에 섞지 않고 검색 hit에 붙인다. None은 불가가 아니라 미상이다.
"""

from typing import Literal

from pydantic import BaseModel

from app.geo.pet import size_class_accepts
from app.place.contracts import PetAccessFacts
from app.profile.contract import SizeClass

DogAccessState = Literal["compatible", "incompatible", "unknown"]
DogAccessReason = Literal[
    "size_allowed",
    "size_exceeded",
    "weight_allowed",
    "weight_exceeded",
    "weight_boundary_unknown",
    "dog_disallowed",
    "missing_dog_size",
    "missing_dog_weight",
    "missing_restriction",
]


class DogAccessEvaluation(BaseModel):
    state: DogAccessState
    reason: DogAccessReason


def evaluate_dog_access(
    pet_access: PetAccessFacts | None,
    dog_size: SizeClass | None,
    dog_weight_kg: float | None = None,
) -> DogAccessEvaluation:
    """원천에서 파생한 입장 축만으로 이 크기의 개와 시설을 대조한다."""
    if pet_access is None:
        return DogAccessEvaluation(state="unknown", reason="missing_restriction")
    if pet_access.allowed is False or pet_access.dog_ok is False:
        return DogAccessEvaluation(state="incompatible", reason="dog_disallowed")

    facility_limit = pet_access.size_class
    if pet_access.max_kg is not None:
        if dog_weight_kg is not None:
            if dog_weight_kg > pet_access.max_kg:
                return DogAccessEvaluation(state="incompatible", reason="weight_exceeded")
            if dog_weight_kg < pet_access.max_kg:
                return DogAccessEvaluation(state="compatible", reason="weight_allowed")
            # 파서가 `미만`과 `이하`를 같은 max_kg로 보존한다. 경계값은 지어내지 않는다.
            return DogAccessEvaluation(state="unknown", reason="weight_boundary_unknown")

        class_answer = size_class_accepts(facility_limit, dog_size)
        if class_answer is False:
            return DogAccessEvaluation(state="incompatible", reason="size_exceeded")
        return DogAccessEvaluation(state="unknown", reason="missing_dog_weight")

    if facility_limit is None:
        return DogAccessEvaluation(state="unknown", reason="missing_restriction")
    if dog_size is None:
        return DogAccessEvaluation(state="unknown", reason="missing_dog_size")

    class_answer = size_class_accepts(facility_limit, dog_size)
    if class_answer is None:
        return DogAccessEvaluation(state="unknown", reason="missing_restriction")
    if class_answer:
        return DogAccessEvaluation(state="compatible", reason="size_allowed")
    return DogAccessEvaluation(state="incompatible", reason="size_exceeded")
