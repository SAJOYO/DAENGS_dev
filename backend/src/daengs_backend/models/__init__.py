"""M — SQLAlchemy 모델 (DB 테이블 구조).

스키마의 원본은 여기가 아니라 `db/init/*.sql` 입니다.
Alembic 을 쓰지 않으므로 이 모델은 SQL 을 '따라가는' 쪽입니다.
SQL 을 고쳤으면 여기도 손으로 맞춰야 합니다.

바깥으로 나가는 응답 형태는 schemas/ 에 따로 있습니다. 섞지 마세요.

새 모델은 아래 import 에도 추가하세요. 매퍼가 한 번은 로드되어야
관계 문자열 참조와 `Base.metadata` 가 온전해집니다.
"""

from daengs_backend.models.admin_audit_log import (
    AUDIT_ACCOUNT_CREATED,
    AUDIT_ACCOUNT_PASSWORD_CHANGED,
    AUDIT_ACCOUNT_REACTIVATED,
    AUDIT_ACCOUNT_ROLE_CHANGED,
    AUDIT_ACCOUNT_SUSPENDED,
    AUDIT_ACTIONS,
    AUDIT_APP_USER_PII_REVEALED,
    AUDIT_APP_USER_REACTIVATED,
    AUDIT_APP_USER_SUSPENDED,
    AUDIT_LOGIN_DENIED_SUSPENDED,
    AUDIT_LOGIN_FAILED_PASSWORD,
    AUDIT_LOGIN_FAILED_UNKNOWN_ID,
    AUDIT_LOGIN_SUCCESS,
    AUDIT_TARGET_TYPES,
    AdminAuditLog,
)
from daengs_backend.models.admin_user import ADMIN_ROLES, ADMIN_STATUSES, AdminUser
from daengs_backend.models.app_user import APP_USER_STATUSES, AppUser
from daengs_backend.models.base import Base
from daengs_backend.models.chat import (
    CHAT_PROCESSING_STATUSES,
    CHAT_SUMMARY_STATUSES,
    ChatSession,
    ChatSummary,
    ChatTurn,
)
from daengs_backend.models.crawl_run import CRAWL_STATUSES, CRAWL_TRIGGERS, CrawlRun
from daengs_backend.models.gait_record import GAIT_STATUSES, GaitRecord
from daengs_backend.models.pet import (
    PET_BIRTH_DATE_KINDS,
    PET_SEXES,
    Pet,
)
from daengs_backend.models.refresh_token import RefreshToken
from daengs_backend.models.territory import (
    TERRITORY_ATTEMPT_STATUSES,
    TERRITORY_EVIDENCE_VERSION,
    TerritoryAttempt,
    VerifiedVisit,
)
from daengs_backend.models.walk import (
    WALK_ANALYSIS_STATES,
    Walk,
    WalkAnalysis,
    WalkCapsule,
    WalkCellophaneSheet,
    WalkPet,
    WalkPointChunk,
)

__all__ = [
    "ADMIN_ROLES",
    "ADMIN_STATUSES",
    "APP_USER_STATUSES",
    "AUDIT_ACCOUNT_CREATED",
    "AUDIT_ACCOUNT_PASSWORD_CHANGED",
    "AUDIT_ACCOUNT_REACTIVATED",
    "AUDIT_ACCOUNT_ROLE_CHANGED",
    "AUDIT_ACCOUNT_SUSPENDED",
    "AUDIT_ACTIONS",
    "AUDIT_APP_USER_PII_REVEALED",
    "AUDIT_APP_USER_REACTIVATED",
    "AUDIT_APP_USER_SUSPENDED",
    "AUDIT_LOGIN_DENIED_SUSPENDED",
    "AUDIT_LOGIN_FAILED_PASSWORD",
    "AUDIT_LOGIN_FAILED_UNKNOWN_ID",
    "AUDIT_LOGIN_SUCCESS",
    "AUDIT_TARGET_TYPES",
    "CHAT_PROCESSING_STATUSES",
    "CHAT_SUMMARY_STATUSES",
    "CRAWL_STATUSES",
    "CRAWL_TRIGGERS",
    "GAIT_STATUSES",
    "PET_BIRTH_DATE_KINDS",
    "PET_SEXES",
    "TERRITORY_ATTEMPT_STATUSES",
    "TERRITORY_EVIDENCE_VERSION",
    "WALK_ANALYSIS_STATES",
    "AdminAuditLog",
    "AdminUser",
    "AppUser",
    "Base",
    "ChatSession",
    "ChatSummary",
    "ChatTurn",
    "CrawlRun",
    "GaitRecord",
    "Pet",
    "RefreshToken",
    "TerritoryAttempt",
    "VerifiedVisit",
    "Walk",
    "WalkAnalysis",
    "WalkCapsule",
    "WalkCellophaneSheet",
    "WalkPet",
    "WalkPointChunk",
]
