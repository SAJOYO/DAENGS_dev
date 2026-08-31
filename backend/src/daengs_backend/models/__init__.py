"""M — SQLAlchemy 모델 (DB 테이블 구조).

스키마의 원본은 여기가 아니라 `db/init/*.sql` 입니다.
Alembic 을 쓰지 않으므로 이 모델은 SQL 을 '따라가는' 쪽입니다.
SQL 을 고쳤으면 여기도 손으로 맞춰야 합니다.

바깥으로 나가는 응답 형태는 schemas/ 에 따로 있습니다. 섞지 마세요.

새 모델은 아래 import 에도 추가하세요. 매퍼가 한 번은 로드되어야
관계 문자열 참조와 `Base.metadata` 가 온전해집니다.
"""

from daengs_backend.models.admin_user import ADMIN_ROLES, ADMIN_STATUSES, AdminUser
from daengs_backend.models.app_user import APP_USER_STATUSES, AppUser
from daengs_backend.models.base import Base
from daengs_backend.models.crawl_run import CRAWL_STATUSES, CRAWL_TRIGGERS, CrawlRun
from daengs_backend.models.pet import (
    PET_BIRTH_DATE_KINDS,
    PET_SEXES,
    Pet,
)
from daengs_backend.models.refresh_token import RefreshToken

__all__ = [
    "ADMIN_ROLES",
    "CRAWL_STATUSES",
    "CRAWL_TRIGGERS",
    "CrawlRun",
    "ADMIN_STATUSES",
    "APP_USER_STATUSES",
    "AdminUser",
    "AppUser",
    "Base",
    "PET_BIRTH_DATE_KINDS",
    "PET_SEXES",
    "Pet",
    "RefreshToken",
]
