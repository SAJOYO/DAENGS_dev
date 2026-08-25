"""모든 모델이 상속하는 선언 베이스.

테이블은 여기서 만들지 않습니다 (`Base.metadata.create_all` 을 부르지 마세요).
스키마 원본은 `db/init/*.sql` 이고, 모델은 그것을 따라갑니다.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
