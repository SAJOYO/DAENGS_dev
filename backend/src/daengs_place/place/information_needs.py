"""검색 의미와 presentation이 공유하는 Place 정보 요구 식별자."""

from enum import StrEnum


class InformationNeedId(StrEnum):
    ACTIVITY_PLAY = "activity.play"
    PRODUCTS_PURCHASABLE = "products.purchasable"
    OPERATIONS_PARKING = "operations.parking"
    OPERATIONS_OPEN_NOW = "operations.open_now"
    PET_SIZE = "pet.size"
    COST_TRAVEL_DISTANCE = "cost.travel_distance"
    COST_PET_FEE = "cost.pet_fee"
    COST_ADMISSION = "cost.admission"
    COST_PRODUCT_PRICE = "cost.product_price"
    AMBIENCE_QUIET = "ambience.quiet"


__all__ = ["InformationNeedId"]
