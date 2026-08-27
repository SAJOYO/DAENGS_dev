"""프로필 원천. Fake = 가상 페르소나, HTTP = 팀 API (미구현).

**개 8마리는 "지도 위에서 걸리는 것"으로 갈랐다.** 견종·인기순이 아니라 경로 난점 축이다 —
계단 / 지하보도 / 큰길 횡단 / 소요시간 상한 / 기온 / 아예 못 걷는 상태. 한 마리가 한 난점을 맡는다.
docs/contracts/dog-profile.md

**견주 5명은 "병원의 역할을 아는 정도"로 갈랐다.** 주인은 1차·2차·응급·과목이 뭔지 모르는 채로 온다.
docs/contracts/owner-profile.md
"""

from datetime import date
from typing import Protocol

from app.profile.contract import BreedMix, DogProfile, OwnerProfile


class ProfileSource(Protocol):
    async def get(self, dog_id: str) -> DogProfile | None: ...


class OwnerSource(Protocol):
    async def get(self, owner_id: str) -> OwnerProfile | None: ...


# --------------------------------------------------------------------- 개 8마리
# 난점 요약 (자세한 건 docs/contracts/dog-profile.md 표)
#   kong     다른 개 마주침      → 골목 선호, 큰길 횡단 경고
#   dubu     여름 기온 + 단두종  → 28℃ 금지, 차 없음
#   halmae   계단 = 불가         → 15분 상한, 신호 한 번에 못 건넘
#   bau      지하보도 스트레스   → 대중교통·택시 거부, 사실상 도보뿐
#   bbogeul  슬개골              → 계단·점프 금지, 본인은 가겠다고 함
#   choco    심장               → 경사·계단에서 호흡, 자주 쉼        (※ heart 미배선)
#   samwol   접종 미완           → 땅에 못 내려놓음, 도보가 성립 안 함 (※ unvaccinated 미배선)
#   janggun  대형 + 노령         → 못 안고·계단 불가·대중교통 불가 = 차 없으면 선택지 0
PERSONAS: dict[str, DogProfile] = {
    "kong": DogProfile(
        dog_id="kong", name="콩이",
        breed=[BreedMix(breed="border collie", ratio=0.6), BreedMix(breed="mix", ratio=0.4)],
        birth_date=date(2024, 5, 1), sex="M", neutered=True, weight_kg=18, size_class="medium",
        activity_level="high", temperament=["curious", "reactive_to_dogs"], has_car=True,
    ),
    "dubu": DogProfile(
        dog_id="dubu", name="두부",
        breed=[BreedMix(breed="pug")],
        birth_date=date(2021, 3, 15), sex="F", neutered=True, weight_kg=9, size_class="small",
        brachycephalic=True, health_flags=["obesity"], activity_level="mid", has_car=False,
    ),
    "halmae": DogProfile(
        dog_id="halmae", name="할매",
        breed=[BreedMix(breed="maltese")],
        birth_date=date(2013, 7, 1), sex="F", neutered=False, weight_kg=3.2, size_class="small",
        health_flags=["senior", "joint"], activity_level="low", temperament=["timid"], has_car=False,
    ),
    # --- 신규 5마리 -------------------------------------------------------
    "bau": DogProfile(
        # 대형견의 난점은 체력이 아니라 **통로와 탑승**이다. 지하보도 울림, 대중교통 동반 거절,
        # 택시 승차 거부. 걷는 건 제일 잘하는데 선택지가 도보뿐이다.
        dog_id="bau", name="바우",
        breed=[BreedMix(breed="golden retriever")],
        birth_date=date(2022, 6, 10), sex="M", neutered=True, weight_kg=32, size_class="large",
        activity_level="mid", temperament=["food_driven"], has_car=True,
    ),
    "bbogeul": DogProfile(
        # 소형견 슬개골. 할매와 달리 **어리고 활발하다** — 본인은 계단도 뛰어오르려 한다.
        # 그래서 '느려서 못 감'이 아니라 '가겠다는 걸 말려야 함'이 난점이다.
        dog_id="bbogeul", name="뽀글",
        breed=[BreedMix(breed="pomeranian")],
        birth_date=date(2024, 3, 20), sex="F", neutered=True, weight_kg=2.1, size_class="small",
        health_flags=["joint"], activity_level="high", temperament=["curious"], has_car=False,
    ),
    "choco": DogProfile(
        # 카발리에 = 승모판 질환 호발종. 단두종이 아니라서 **심장 하나만** 변수로 남는다
        # (시츄로 하면 brachy와 섞여 심장 신호가 묻힌다).
        dog_id="choco", name="초코",
        breed=[BreedMix(breed="cavalier king charles spaniel")],
        birth_date=date(2017, 4, 5), sex="M", neutered=True, weight_kg=7.5, size_class="small",
        health_flags=["heart"], activity_level="low", temperament=["timid"], has_car=False,
    ),
    "samwol": DogProfile(
        # 접종 미완 퍼피. **도보 경로 자체가 성립하지 않는다** — 땅에 내려놓으면 안 된다.
        # 다른 7마리는 '어떤 길로 갈까'가 문제인데 얘만 '걸어갈 수 있나'가 문제다.
        dog_id="samwol", name="삼월",
        breed=[BreedMix(breed="mix")],
        birth_date=date(2026, 5, 10), sex="F", neutered=False, weight_kg=1.8, size_class="small",
        health_flags=["unvaccinated"], activity_level="high", temperament=["curious", "timid"],
        has_car=False,
    ),
    "janggun": DogProfile(
        # 최악의 조합: 대형 + 노령 + 관절. 안을 수도 없고(34kg) 계단도 못 오르고 대중교통도 안 된다.
        # 차가 없으면 **갈 수 있는 병원이 0곳**이 되는 케이스 — 서비스가 값을 하는지 여기서 갈린다.
        dog_id="janggun", name="장군",
        breed=[BreedMix(breed="german shepherd")],
        birth_date=date(2015, 2, 14), sex="M", neutered=True, weight_kg=34, size_class="large",
        health_flags=["senior", "joint"], activity_level="low", has_car=False,
    ),
}


# ------------------------------------------------------------------- 견주 5명
# 축은 **병원의 역할을 아는 정도**다. 주인은 1차/2차·응급/야간·과목 구분을 모르는 채로 온다.
OWNERS: dict[str, OwnerProfile] = {
    "jihyun": OwnerProfile(
        # 뭘 모르는지도 모름. 동물병원이 다 같은 줄 안다. 증상을 문장으로 친다.
        owner_id="jihyun", name="지현", dog_ids=["samwol"],
        has_car=False, can_carry_kg=10, transit_ok=None, vet_literacy="none",
    ),
    "myeongsu": OwnerProfile(
        # 새벽 응급. '24시'와 '응급'이 다른 걸 모른다. 차 없어 택시뿐.
        owner_id="myeongsu", name="명수", dog_ids=["halmae"],
        has_car=False, can_carry_kg=8, transit_ok=False, vet_literacy="basic",
    ),
    "eunyoung": OwnerProfile(
        # 만성질환 베테랑. 2차·전문의·과목을 안다. 모르는 건 '거기까지 우리 개가 갈 수 있나'뿐.
        owner_id="eunyoung", name="은영", dog_ids=["janggun", "choco"],
        has_car=True, can_carry_kg=12, transit_ok=False, vet_literacy="experienced",
    ),
    "taewoo": OwnerProfile(
        # 차 없는 도보 생활권. 큰 병원이 멀다는 것만 안다. 도보 몇 분이 전부.
        owner_id="taewoo", name="태우", dog_ids=["dubu", "bbogeul"],
        has_car=False, can_carry_kg=10, transit_ok=True, vet_literacy="basic",
    ),
    "seojun": OwnerProfile(
        # 대형·활동견 보호자. 차는 있는데 바우(32kg)는 못 안는다.
        owner_id="seojun", name="서준", dog_ids=["kong", "bau"],
        has_car=True, can_carry_kg=20, transit_ok=False, vet_literacy="basic",
    ),
}

OWNER_OF: dict[str, str] = {d: o.owner_id for o in OWNERS.values() for d in o.dog_ids}


class FakeProfileSource:
    async def get(self, dog_id: str) -> DogProfile | None:
        return PERSONAS.get(dog_id)


class FakeOwnerSource:
    async def get(self, owner_id: str) -> OwnerProfile | None:
        return OWNERS.get(owner_id)


def profile_source() -> ProfileSource:
    return FakeProfileSource()


def owner_source() -> OwnerSource:
    return FakeOwnerSource()


async def owner_of(dog_id: str) -> OwnerProfile | None:
    """이 개의 보호자. 팀 API가 붙으면 여기만 갈아끼운다."""
    oid = OWNER_OF.get(dog_id)
    return await owner_source().get(oid) if oid else None
