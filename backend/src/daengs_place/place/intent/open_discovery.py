"""사용자가 검색 대상 선택을 위임했을 때만 여는 제품 소유 탐색 정책."""

from typing import Self

from pydantic import Field, model_validator

from daengs_place.place.planning.contract import PlanningModel
from daengs_place.place.planning.purpose import PurposeId


class OpenDiscoveryBranchSpec(PlanningModel):
    branch_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9-]+$")
    display_label: str = Field(min_length=1, max_length=80)
    purpose_id: PurposeId
    support_note: str = Field(min_length=1, max_length=300)


class OpenDiscoveryPolicy(PlanningModel):
    policy_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.:-]+$")
    version: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_.:-]+$")
    branches: tuple[OpenDiscoveryBranchSpec, ...] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def branches_are_distinct(self) -> Self:
        if len({branch.branch_id for branch in self.branches}) != len(self.branches):
            raise ValueError("open discovery branch ids must be unique")
        if len({branch.purpose_id for branch in self.branches}) != len(self.branches):
            raise ValueError("open discovery branch purposes must be unique")
        return self


OPEN_DISCOVERY_POLICY = OpenDiscoveryPolicy(
    policy_id="place.open_discovery",
    version="v1",
    branches=(
        OpenDiscoveryBranchSpec(
            branch_id="eat-and-rest",
            display_label="먹고 쉬기",
            purpose_id=PurposeId.DINING,
            support_note=(
                "목적 선택을 위임받아 카페·식당을 넓게 펼쳤습니다. "
                "개인화된 최적 추천이나 반려견 동반 가능 여부를 보장하지 않습니다."
            ),
        ),
        OpenDiscoveryBranchSpec(
            branch_id="light-outing",
            display_label="가볍게 나가기",
            purpose_id=PurposeId.OUTING,
            support_note=(
                "목적 선택을 위임받아 여행·레저 장소를 넓게 펼쳤습니다. "
                "산책 적합성이나 현재 이용 가능 여부를 보장하지 않습니다."
            ),
        ),
        OpenDiscoveryBranchSpec(
            branch_id="browse-culture",
            display_label="구경하기",
            purpose_id=PurposeId.CULTURE,
            support_note=(
                "목적 선택을 위임받아 문화 장소를 넓게 펼쳤습니다. "
                "현재 행사나 반려견 입장 가능 여부를 보장하지 않습니다."
            ),
        ),
    ),
)

_BRANCH_BY_ID = {branch.branch_id: branch for branch in OPEN_DISCOVERY_POLICY.branches}


def open_discovery_branch(branch_id: str) -> OpenDiscoveryBranchSpec:
    try:
        return _BRANCH_BY_ID[branch_id]
    except KeyError as exc:
        raise ValueError(f"unknown open discovery branch: {branch_id}") from exc
