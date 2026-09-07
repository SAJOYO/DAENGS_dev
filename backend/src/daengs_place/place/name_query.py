"""명시적 장소명 검색. 의미 확장이나 와일드카드가 아닌 표기 이름의 부분 일치."""

from typing import Annotated

from pydantic import StringConstraints

PlaceNameQuery = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
