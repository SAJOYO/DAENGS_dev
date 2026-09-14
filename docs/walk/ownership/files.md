# 파일별 소유권 목록

기준 `0b53cb0e + #533`. [범위·판단·부채 목록](README.md)이 정본 설명이다. 이 표는 `inventory.json`의 읽기용 표현이다.

소유 영역은 최종 폴더 이름이 아니다. `split` 파일은 주 소유 영역과 분리할 책임을 함께 기록한다. `integration`은 파일 전체 이관 대상이 아니다.

## recording: 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션

| 파일 | 현재 계층 | 처리 | 근거 / 후속 부채 |
| --- | --- | --- | --- |
| [backend/src/daengs_backend/models/walk.py](../../../backend/src/daengs_backend/models/walk.py) | models | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/models/walk_motion.py](../../../backend/src/daengs_backend/models/walk_motion.py) | models | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/models/walk_precision.py](../../../backend/src/daengs_backend/models/walk_precision.py) | models | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/repositories/walk.py](../../../backend/src/daengs_backend/repositories/walk.py) | repositories | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/repositories/walk_motion.py](../../../backend/src/daengs_backend/repositories/walk_motion.py) | repositories | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/repositories/walk_precision.py](../../../backend/src/daengs_backend/repositories/walk_precision.py) | repositories | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/repositories/walk_upload.py](../../../backend/src/daengs_backend/repositories/walk_upload.py) | repositories | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/routers/walk.py](../../../backend/src/daengs_backend/routers/walk.py) | routers | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/routers/walk_motion.py](../../../backend/src/daengs_backend/routers/walk_motion.py) | routers | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/routers/walk_precision.py](../../../backend/src/daengs_backend/routers/walk_precision.py) | routers | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/schemas/walk.py](../../../backend/src/daengs_backend/schemas/walk.py) | schemas | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/schemas/walk_motion.py](../../../backend/src/daengs_backend/schemas/walk_motion.py) | schemas | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/schemas/walk_precision.py](../../../backend/src/daengs_backend/schemas/walk_precision.py) | schemas | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/schemas/walk_upload_receipt.py](../../../backend/src/daengs_backend/schemas/walk_upload_receipt.py) | schemas | retain | 산책 원본·업로드·불변 보조 입력·봉인 트랜잭션. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/services/walk.py](../../../backend/src/daengs_backend/services/walk.py) | services | retain | 기존 공개 이름의 지연 호환. 실제 구현/공통 예외는 walk_session 소유; 공개 대상은 session-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_chunk.py](../../../backend/src/daengs_backend/services/walk_chunk.py) | services | retain | 기존 공개 이름의 지연 호환. 실제 구현/공통 예외는 walk_session 소유; 공개 대상은 session-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_finalize.py](../../../backend/src/daengs_backend/services/walk_finalize.py) | services | retain | 기존 공개 이름의 지연 호환. 실제 구현/공통 예외는 walk_session 소유; 공개 대상은 session-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_motion.py](../../../backend/src/daengs_backend/services/walk_motion.py) | services | retain | 기존 공개 이름의 지연 호환. 실제 구현/공통 예외는 walk_session 소유; 공개 대상은 session-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_motion_contract.py](../../../backend/src/daengs_backend/services/walk_motion_contract.py) | services | retain | 기존 공개 이름의 지연 호환. 실제 구현/공통 예외는 walk_session 소유; 공개 대상은 session-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_precision.py](../../../backend/src/daengs_backend/services/walk_precision.py) | services | retain | 기존 공개 이름의 지연 호환. 실제 구현/공통 예외는 walk_session 소유; 공개 대상은 session-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_precision_contract.py](../../../backend/src/daengs_backend/services/walk_precision_contract.py) | services | retain | 기존 공개 이름의 지연 호환. 실제 구현/공통 예외는 walk_session 소유; 공개 대상은 session-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_recording.py](../../../backend/src/daengs_backend/services/walk_recording.py) | services | retain | 기존 공개 이름의 지연 호환. 실제 구현/공통 예외는 walk_session 소유; 공개 대상은 session-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_session/__init__.py](../../../backend/src/daengs_backend/services/walk_session/__init__.py) | services | retain | 원본 청크/지문·기록 보완·업로드/수신 확인·불변 백업·봉인 중 명시된 책임. 공통 오류는 봉인 실행을 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_session/chunk.py](../../../backend/src/daengs_backend/services/walk_session/chunk.py) | services | retain | 원본 청크/지문·기록 보완·업로드/수신 확인·불변 백업·봉인 중 명시된 책임. 공통 오류는 봉인 실행을 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_session/errors.py](../../../backend/src/daengs_backend/services/walk_session/errors.py) | services | retain | 원본 청크/지문·기록 보완·업로드/수신 확인·불변 백업·봉인 중 명시된 책임. 공통 오류는 봉인 실행을 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_session/finalize.py](../../../backend/src/daengs_backend/services/walk_session/finalize.py) | services | retain | 원본 청크/지문·기록 보완·업로드/수신 확인·불변 백업·봉인 중 명시된 책임. 공통 오류는 봉인 실행을 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_session/lifecycle.py](../../../backend/src/daengs_backend/services/walk_session/lifecycle.py) | services | retain | 원본 청크/지문·기록 보완·업로드/수신 확인·불변 백업·봉인 중 명시된 책임. 공통 오류는 봉인 실행을 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_session/motion.py](../../../backend/src/daengs_backend/services/walk_session/motion.py) | services | retain | 원본 청크/지문·기록 보완·업로드/수신 확인·불변 백업·봉인 중 명시된 책임. 공통 오류는 봉인 실행을 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_session/motion_contract.py](../../../backend/src/daengs_backend/services/walk_session/motion_contract.py) | services | retain | 원본 청크/지문·기록 보완·업로드/수신 확인·불변 백업·봉인 중 명시된 책임. 공통 오류는 봉인 실행을 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_session/precision.py](../../../backend/src/daengs_backend/services/walk_session/precision.py) | services | retain | 원본 청크/지문·기록 보완·업로드/수신 확인·불변 백업·봉인 중 명시된 책임. 공통 오류는 봉인 실행을 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_session/precision_contract.py](../../../backend/src/daengs_backend/services/walk_session/precision_contract.py) | services | retain | 원본 청크/지문·기록 보완·업로드/수신 확인·불변 백업·봉인 중 명시된 책임. 공통 오류는 봉인 실행을 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_session/recording.py](../../../backend/src/daengs_backend/services/walk_session/recording.py) | services | retain | 원본 청크/지문·기록 보완·업로드/수신 확인·불변 백업·봉인 중 명시된 책임. 공통 오류는 봉인 실행을 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_session/upload_receipt.py](../../../backend/src/daengs_backend/services/walk_session/upload_receipt.py) | services | retain | 원본 청크/지문·기록 보완·업로드/수신 확인·불변 백업·봉인 중 명시된 책임. 공통 오류는 봉인 실행을 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_upload_receipt.py](../../../backend/src/daengs_backend/services/walk_upload_receipt.py) | services | retain | 기존 공개 이름의 지연 호환. 실제 구현/공통 예외는 walk_session 소유; 공개 대상은 session-package.json 참조.  |

## measurement: 증거 계산·동선 투영·측정 저장 및 조회

| 파일 | 현재 계층 | 처리 | 근거 / 후속 부채 |
| --- | --- | --- | --- |
| [backend/src/daengs_backend/models/walk_measurement.py](../../../backend/src/daengs_backend/models/walk_measurement.py) | models | retain | 증거 계산·동선 투영·측정 저장 및 조회. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/repositories/walk_measurement.py](../../../backend/src/daengs_backend/repositories/walk_measurement.py) | repositories | retain | 증거 계산·동선 투영·측정 저장 및 조회. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/routers/walk_trajectory.py](../../../backend/src/daengs_backend/routers/walk_trajectory.py) | routers | retain | 증거 계산·동선 투영·측정 저장 및 조회. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/schemas/walk_measurement.py](../../../backend/src/daengs_backend/schemas/walk_measurement.py) | schemas | retain | 불변 측정 요약과 경로 페이지 저장/전송 계약. 원본 시각 계약은 공통 투영에서 소비.  |
| [backend/src/daengs_backend/schemas/walk_style.py](../../../backend/src/daengs_backend/schemas/walk_style.py) | schemas | retain | 증거 계산·동선 투영·측정 저장 및 조회. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/schemas/walk_trajectory.py](../../../backend/src/daengs_backend/schemas/walk_trajectory.py) | schemas | retain | 증거 계산·동선 투영·측정 저장 및 조회. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/services/walk_analysis.py](../../../backend/src/daengs_backend/services/walk_analysis.py) | services | retain | 구현 없는 지연 공개 호환. 실제 소유 대상과 기존 산출물 호환은 remaining-packages.json에 명시.  |
| [backend/src/daengs_backend/services/walk_measurement.py](../../../backend/src/daengs_backend/services/walk_measurement.py) | services | retain | 구현 없는 지연 공개 호환. 실제 소유 대상과 기존 산출물 호환은 remaining-packages.json에 명시.  |
| [backend/src/daengs_backend/services/walk_measurement_projection.py](../../../backend/src/daengs_backend/services/walk_measurement_projection.py) | services | retain | 구현 없는 지연 공개 호환. 실제 소유 대상과 기존 산출물 호환은 remaining-packages.json에 명시.  |
| [backend/src/daengs_backend/services/walk_metrics/__init__.py](../../../backend/src/daengs_backend/services/walk_metrics/__init__.py) | services | retain | 소유 패키지 진입. 하위 실행 모듈을 암묵 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_metrics/analysis.py](../../../backend/src/daengs_backend/services/walk_metrics/analysis.py) | services | retain | 기존 구현을 소유 패키지로 이동. 계산·직렬화·저장·조회 정책은 기존 함수 본문을 유지하며 공개 경로는 remaining-packages.json에서 호환.  |
| [backend/src/daengs_backend/services/walk_metrics/measurement.py](../../../backend/src/daengs_backend/services/walk_metrics/measurement.py) | services | retain | 기존 구현을 소유 패키지로 이동. 계산·직렬화·저장·조회 정책은 기존 함수 본문을 유지하며 공개 경로는 remaining-packages.json에서 호환.  |
| [backend/src/daengs_backend/services/walk_metrics/measurement_projection.py](../../../backend/src/daengs_backend/services/walk_metrics/measurement_projection.py) | services | retain | 기존 구현을 소유 패키지로 이동. 계산·직렬화·저장·조회 정책은 기존 함수 본문을 유지하며 공개 경로는 remaining-packages.json에서 호환.  |
| [backend/src/daengs_backend/services/walk_metrics/motion_calculation.py](../../../backend/src/daengs_backend/services/walk_metrics/motion_calculation.py) | services | retain | 기존 구현을 소유 패키지로 이동. 계산·직렬화·저장·조회 정책은 기존 함수 본문을 유지하며 공개 경로는 remaining-packages.json에서 호환.  |
| [backend/src/daengs_backend/services/walk_metrics/motion_engine.py](../../../backend/src/daengs_backend/services/walk_metrics/motion_engine.py) | services | retain | 기존 구현을 소유 패키지로 이동. 계산·직렬화·저장·조회 정책은 기존 함수 본문을 유지하며 공개 경로는 remaining-packages.json에서 호환.  |
| [backend/src/daengs_backend/services/walk_metrics/trajectory.py](../../../backend/src/daengs_backend/services/walk_metrics/trajectory.py) | services | retain | 기존 구현을 소유 패키지로 이동. 계산·직렬화·저장·조회 정책은 기존 함수 본문을 유지하며 공개 경로는 remaining-packages.json에서 호환.  |
| [backend/src/daengs_backend/services/walk_metrics/trajectory_shadow.py](../../../backend/src/daengs_backend/services/walk_metrics/trajectory_shadow.py) | services | retain | 기존 구현을 소유 패키지로 이동. 계산·직렬화·저장·조회 정책은 기존 함수 본문을 유지하며 공개 경로는 remaining-packages.json에서 호환.  |
| [backend/src/daengs_backend/services/walk_motion_calculation.py](../../../backend/src/daengs_backend/services/walk_motion_calculation.py) | services | retain | 구현 없는 지연 공개 호환. 실제 소유 대상과 기존 산출물 호환은 remaining-packages.json에 명시.  |
| [backend/src/daengs_backend/services/walk_motion_engine.py](../../../backend/src/daengs_backend/services/walk_motion_engine.py) | services | retain | 구현 없는 지연 공개 호환. 실제 소유 대상과 기존 산출물 호환은 remaining-packages.json에 명시.  |
| [backend/src/daengs_backend/services/walk_style.py](../../../backend/src/daengs_backend/services/walk_style.py) | services | repackage | 증거 계산·동선 투영·측정 저장 및 조회. 후속 소유 영역별 서비스 패키지 재배치 대상.  |
| [backend/src/daengs_backend/services/walk_trajectory.py](../../../backend/src/daengs_backend/services/walk_trajectory.py) | services | retain | 구현 없는 지연 공개 호환. 실제 소유 대상과 기존 산출물 호환은 remaining-packages.json에 명시.  |
| [backend/src/daengs_backend/services/walk_trajectory_shadow.py](../../../backend/src/daengs_backend/services/walk_trajectory_shadow.py) | services | retain | 구현 없는 지연 공개 호환. 실제 소유 대상과 기존 산출물 호환은 remaining-packages.json에 명시.  |
| [backend/src/daengs_walk/evidence.py](../../../backend/src/daengs_walk/evidence.py) | domain | repackage | 증거 계산·동선 투영·측정 저장 및 조회. 후속 배치는 소유권 기준이며 계산/버전은 보존.  |
| [backend/src/daengs_walk/facts.py](../../../backend/src/daengs_walk/facts.py) | domain | repackage | 증거 계산·동선 투영·측정 저장 및 조회. 후속 배치는 소유권 기준이며 계산/버전은 보존.  |
| [backend/src/daengs_walk/measurement.py](../../../backend/src/daengs_walk/measurement.py) | domain | repackage | 증거 계산·동선 투영·측정 저장 및 조회. 후속 배치는 소유권 기준이며 계산/버전은 보존.  |
| [backend/src/daengs_walk/observation.py](../../../backend/src/daengs_walk/observation.py) | domain | repackage | 증거 계산·동선 투영·측정 저장 및 조회. 후속 배치는 소유권 기준이며 계산/버전은 보존.  |
| [backend/src/daengs_walk/route/__init__.py](../../../backend/src/daengs_walk/route/__init__.py) | domain | retain | 연속 경로/거리/관측 계산. 현재/과거 소비자가 기준값을 소유하며 일기·storyboard 역참조 없음.  |
| [backend/src/daengs_walk/route/geometry.py](../../../backend/src/daengs_walk/route/geometry.py) | domain | retain | 연속 경로/거리/관측 계산. 현재/과거 소비자가 기준값을 소유하며 일기·storyboard 역참조 없음.  |
| [backend/src/daengs_walk/route/nodes.py](../../../backend/src/daengs_walk/route/nodes.py) | domain | retain | 연속 경로/거리/관측 계산. 현재/과거 소비자가 기준값을 소유하며 일기·storyboard 역참조 없음.  |
| [backend/src/daengs_walk/route/pace.py](../../../backend/src/daengs_walk/route/pace.py) | domain | retain | 연속 경로/거리/관측 계산. 현재/과거 소비자가 기준값을 소유하며 일기·storyboard 역참조 없음.  |
| [backend/src/daengs_walk/trajectory.py](../../../backend/src/daengs_walk/trajectory.py) | domain | repackage | 증거 계산·동선 투영·측정 저장 및 조회. 후속 배치는 소유권 기준이며 계산/버전은 보존.  |
| [backend/src/daengs_walk/trajectory_projection.py](../../../backend/src/daengs_walk/trajectory_projection.py) | domain | repackage | 최종 구간 소유권 기반 경로·경계와 원본 시각 공통 계약. 조회 전용 스키마에 의존하지 않음.  |
| [backend/src/daengs_walk/trajectory_selection.py](../../../backend/src/daengs_walk/trajectory_selection.py) | domain | repackage | 증거 계산·동선 투영·측정 저장 및 조회. 후속 배치는 소유권 기준이며 계산/버전은 보존.  |
| [backend/src/daengs_walk/trajectory_view.py](../../../backend/src/daengs_walk/trajectory_view.py) | domain | repackage | 증거 계산·동선 투영·측정 저장 및 조회. 후속 배치는 소유권 기준이며 계산/버전은 보존.  |

## sealed_artifacts: 한 산책의 캡슐·셀로판 산출물 생성/복원

| 파일 | 현재 계층 | 처리 | 근거 / 후속 부채 |
| --- | --- | --- | --- |
| [backend/src/daengs_backend/services/walk_artifacts/__init__.py](../../../backend/src/daengs_backend/services/walk_artifacts/__init__.py) | services | retain | 한 산책의 봉인 산출물 저장 어댑터 패키지. 트랜잭션은 원본 기록 서비스 소유.  |
| [backend/src/daengs_backend/services/walk_artifacts/api.py](../../../backend/src/daengs_backend/services/walk_artifacts/api.py) | services | retain | 측정 분석과 셀로판 identity 검증 후 ORM graph 조립. 계산·잠금·commit 소유하지 않음.  |
| [backend/src/daengs_backend/services/walk_artifacts/capsule.py](../../../backend/src/daengs_backend/services/walk_artifacts/capsule.py) | services | retain | 측정 분석 identity에 묶인 1:1 캡슐 저장/복원 어댑터.  |
| [backend/src/daengs_backend/services/walk_artifacts/cellophane.py](../../../backend/src/daengs_backend/services/walk_artifacts/cellophane.py) | services | retain | 셀로판 v1 codec·fingerprint·ORM 저장/복원 어댑터. 측정 분석 조립과 공간 집계 미포함.  |
| [backend/src/daengs_backend/services/walk_capsule.py](../../../backend/src/daengs_backend/services/walk_capsule.py) | services | repackage | 기존 캡슐 함수 import 호환. 구현/자료형 객체는 walk_artifacts.capsule 소유.  |
| [backend/src/daengs_walk/capsule.py](../../../backend/src/daengs_walk/capsule.py) | domain | repackage | 한 산책의 캡슐·셀로판 산출물 생성/복원. 후속 배치는 소유권 기준이며 계산/버전은 보존.  |
| [backend/src/daengs_walk/cellophane.py](../../../backend/src/daengs_walk/cellophane.py) | domain | repackage | 한 산책의 캡슐·셀로판 산출물 생성/복원. 후속 배치는 소유권 기준이며 계산/버전은 보존.  |

## spatial_view: 봉인 산출물의 개별 조회·여러 산책 공간 집계

| 파일 | 현재 계층 | 처리 | 근거 / 후속 부채 |
| --- | --- | --- | --- |
| [backend/src/daengs_backend/repositories/walk_spatial_diary.py](../../../backend/src/daengs_backend/repositories/walk_spatial_diary.py) | repositories | retain | 봉인 산출물의 개별 조회·여러 산책 공간 집계. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/routers/walk_spatial_diary.py](../../../backend/src/daengs_backend/routers/walk_spatial_diary.py) | routers | retain | 봉인 산출물의 개별 조회·여러 산책 공간 집계. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/schemas/walk_spatial_diary.py](../../../backend/src/daengs_backend/schemas/walk_spatial_diary.py) | schemas | retain | 봉인 산출물의 개별 조회·여러 산책 공간 집계. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/services/walk_spatial_diary.py](../../../backend/src/daengs_backend/services/walk_spatial_diary.py) | services | retain | 구현 없는 지연 공개 호환. 실제 소유 대상과 기존 산출물 호환은 remaining-packages.json에 명시.  |
| [backend/src/daengs_backend/services/walk_views/__init__.py](../../../backend/src/daengs_backend/services/walk_views/__init__.py) | services | retain | 소유 패키지 진입. 하위 실행 모듈을 암묵 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_views/spatial_diary.py](../../../backend/src/daengs_backend/services/walk_views/spatial_diary.py) | services | retain | 기존 구현을 소유 패키지로 이동. 계산·직렬화·저장·조회 정책은 기존 함수 본문을 유지하며 공개 경로는 remaining-packages.json에서 호환.  |
| [backend/src/daengs_walk/spatial_diary.py](../../../backend/src/daengs_walk/spatial_diary.py) | domain | repackage | 봉인 산출물의 개별 조회·여러 산책 공간 집계. 후속 배치는 소유권 기준이며 계산/버전은 보존.  |

## records: 사용자 메모·행동 핀의 원본/수정/삭제/버전 계약

| 파일 | 현재 계층 | 처리 | 근거 / 후속 부채 |
| --- | --- | --- | --- |
| [backend/src/daengs_backend/cli/walk_context_backfill.py](../../../backend/src/daengs_backend/cli/walk_context_backfill.py) | cli | retain | 기록별 배경 outbox/재수집의 저장·조회·운영 계약. 기록 트랜잭션이 소유하며 MVC/CLI 계층은 제자리에 유지.  |
| [backend/src/daengs_backend/models/walk_entry.py](../../../backend/src/daengs_backend/models/walk_entry.py) | models | retain | 사용자 메모·행동 핀의 원본/수정/삭제/버전 계약. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/models/walk_entry_context.py](../../../backend/src/daengs_backend/models/walk_entry_context.py) | models | retain | 기록별 배경 outbox/재수집의 저장·조회·운영 계약. 기록 트랜잭션이 소유하며 MVC/CLI 계층은 제자리에 유지.  |
| [backend/src/daengs_backend/models/walk_entry_v2.py](../../../backend/src/daengs_backend/models/walk_entry_v2.py) | models | retain | 사용자 메모·행동 핀의 원본/수정/삭제/버전 계약. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/repositories/walk_context_backfill.py](../../../backend/src/daengs_backend/repositories/walk_context_backfill.py) | repositories | retain | 기록별 배경 outbox/재수집의 저장·조회·운영 계약. 기록 트랜잭션이 소유하며 MVC/CLI 계층은 제자리에 유지.  |
| [backend/src/daengs_backend/repositories/walk_entry.py](../../../backend/src/daengs_backend/repositories/walk_entry.py) | repositories | retain | 사용자 메모·행동 핀의 원본/수정/삭제/버전 계약. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/repositories/walk_entry_context.py](../../../backend/src/daengs_backend/repositories/walk_entry_context.py) | repositories | retain | 기록별 배경 outbox/재수집의 저장·조회·운영 계약. 기록 트랜잭션이 소유하며 MVC/CLI 계층은 제자리에 유지.  |
| [backend/src/daengs_backend/repositories/walk_entry_v2.py](../../../backend/src/daengs_backend/repositories/walk_entry_v2.py) | repositories | retain | 사용자 메모·행동 핀의 원본/수정/삭제/버전 계약. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/routers/walk_entry.py](../../../backend/src/daengs_backend/routers/walk_entry.py) | routers | retain | 사용자 메모·행동 핀의 원본/수정/삭제/버전 계약. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/routers/walk_entry_errors.py](../../../backend/src/daengs_backend/routers/walk_entry_errors.py) | routers | retain | 사용자 메모·행동 핀의 원본/수정/삭제/버전 계약. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/routers/walk_entry_v2.py](../../../backend/src/daengs_backend/routers/walk_entry_v2.py) | routers | retain | 사용자 메모·행동 핀의 원본/수정/삭제/버전 계약. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/schemas/walk_entry.py](../../../backend/src/daengs_backend/schemas/walk_entry.py) | schemas | retain | 사용자 메모·행동 핀의 원본/수정/삭제/버전 계약. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/schemas/walk_entry_context.py](../../../backend/src/daengs_backend/schemas/walk_entry_context.py) | schemas | retain | 기록별 배경 outbox/재수집의 저장·조회·운영 계약. 기록 트랜잭션이 소유하며 MVC/CLI 계층은 제자리에 유지.  |
| [backend/src/daengs_backend/schemas/walk_entry_v2.py](../../../backend/src/daengs_backend/schemas/walk_entry_v2.py) | schemas | retain | 사용자 메모·행동 핀의 원본/수정/삭제/버전 계약. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/services/walk_context_backfill.py](../../../backend/src/daengs_backend/services/walk_context_backfill.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 구현은 walk_records/walk_photos 소유; 공개 이름/대상은 records-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_entry.py](../../../backend/src/daengs_backend/services/walk_entry.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 구현은 walk_records/walk_photos 소유; 공개 이름/대상은 records-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_entry_context.py](../../../backend/src/daengs_backend/services/walk_entry_context.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 구현은 walk_records/walk_photos 소유; 공개 이름/대상은 records-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_entry_errors.py](../../../backend/src/daengs_backend/services/walk_entry_errors.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 구현은 walk_records/walk_photos 소유; 공개 이름/대상은 records-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_entry_pin.py](../../../backend/src/daengs_backend/services/walk_entry_pin.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 구현은 walk_records/walk_photos 소유; 공개 이름/대상은 records-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_entry_policy.py](../../../backend/src/daengs_backend/services/walk_entry_policy.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 구현은 walk_records/walk_photos 소유; 공개 이름/대상은 records-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_entry_profile.py](../../../backend/src/daengs_backend/services/walk_entry_profile.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 구현은 walk_records/walk_photos 소유; 공개 이름/대상은 records-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_entry_v2.py](../../../backend/src/daengs_backend/services/walk_entry_v2.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 구현은 walk_records/walk_photos 소유; 공개 이름/대상은 records-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_records/__init__.py](../../../backend/src/daengs_backend/services/walk_records/__init__.py) | services | retain | 기록 v1/v2·핀·공통 정책·프로필·오류·outbox·재수집 중 명시된 소유 모듈. 공급자와 사진·일기 해석은 별도 영역.  |
| [backend/src/daengs_backend/services/walk_records/backfill.py](../../../backend/src/daengs_backend/services/walk_records/backfill.py) | services | retain | 기록 v1/v2·핀·공통 정책·프로필·오류·outbox·재수집 중 명시된 소유 모듈. 공급자와 사진·일기 해석은 별도 영역.  |
| [backend/src/daengs_backend/services/walk_records/context.py](../../../backend/src/daengs_backend/services/walk_records/context.py) | services | retain | 기록 v1/v2·핀·공통 정책·프로필·오류·outbox·재수집 중 명시된 소유 모듈. 공급자와 사진·일기 해석은 별도 영역.  |
| [backend/src/daengs_backend/services/walk_records/errors.py](../../../backend/src/daengs_backend/services/walk_records/errors.py) | services | retain | 기록 v1/v2·핀·공통 정책·프로필·오류·outbox·재수집 중 명시된 소유 모듈. 공급자와 사진·일기 해석은 별도 영역.  |
| [backend/src/daengs_backend/services/walk_records/pins.py](../../../backend/src/daengs_backend/services/walk_records/pins.py) | services | retain | 기록 v1/v2·핀·공통 정책·프로필·오류·outbox·재수집 중 명시된 소유 모듈. 공급자와 사진·일기 해석은 별도 영역.  |
| [backend/src/daengs_backend/services/walk_records/policy.py](../../../backend/src/daengs_backend/services/walk_records/policy.py) | services | retain | 기록 v1/v2·핀·공통 정책·프로필·오류·outbox·재수집 중 명시된 소유 모듈. 공급자와 사진·일기 해석은 별도 영역.  |
| [backend/src/daengs_backend/services/walk_records/profile.py](../../../backend/src/daengs_backend/services/walk_records/profile.py) | services | retain | 기록 v1/v2·핀·공통 정책·프로필·오류·outbox·재수집 중 명시된 소유 모듈. 공급자와 사진·일기 해석은 별도 영역.  |
| [backend/src/daengs_backend/services/walk_records/v1.py](../../../backend/src/daengs_backend/services/walk_records/v1.py) | services | retain | 기록 v1/v2·핀·공통 정책·프로필·오류·outbox·재수집 중 명시된 소유 모듈. 공급자와 사진·일기 해석은 별도 영역.  |
| [backend/src/daengs_backend/services/walk_records/v2.py](../../../backend/src/daengs_backend/services/walk_records/v2.py) | services | retain | 기록 v1/v2·핀·공통 정책·프로필·오류·outbox·재수집 중 명시된 소유 모듈. 공급자와 사진·일기 해석은 별도 영역.  |

## photos: 사진 메타데이터 동기화·재시도·삭제 기록

| 파일 | 현재 계층 | 처리 | 근거 / 후속 부채 |
| --- | --- | --- | --- |
| [backend/src/daengs_backend/models/walk_photo.py](../../../backend/src/daengs_backend/models/walk_photo.py) | models | retain | 사진 메타데이터 동기화·재시도·삭제 기록. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/repositories/walk_photo.py](../../../backend/src/daengs_backend/repositories/walk_photo.py) | repositories | retain | 사진 메타데이터 동기화·재시도·삭제 기록. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/routers/walk_photo.py](../../../backend/src/daengs_backend/routers/walk_photo.py) | routers | retain | 사진 메타데이터 동기화·재시도·삭제 기록. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/schemas/walk_photo.py](../../../backend/src/daengs_backend/schemas/walk_photo.py) | schemas | retain | 사진 메타데이터 동기화·재시도·삭제 기록. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/services/walk_photo.py](../../../backend/src/daengs_backend/services/walk_photo.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 구현은 walk_records/walk_photos 소유; 공개 이름/대상은 records-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_photos/__init__.py](../../../backend/src/daengs_backend/services/walk_photos/__init__.py) | services | retain | 사진 manifest 동기화·CAS·삭제 tombstone. 기록·배경·일기를 로딩하지 않음.  |
| [backend/src/daengs_backend/services/walk_photos/api.py](../../../backend/src/daengs_backend/services/walk_photos/api.py) | services | retain | 사진 manifest 동기화·CAS·삭제 tombstone. 기록·배경·일기를 로딩하지 않음.  |

## background: 관측·카탈로그 보존/공급·갱신·기록별 수집 작업

| 파일 | 현재 계층 | 처리 | 근거 / 후속 부채 |
| --- | --- | --- | --- |
| [backend/src/daengs_backend/cli/walk_area_catalog.py](../../../backend/src/daengs_backend/cli/walk_area_catalog.py) | cli | retain | 관측·카탈로그 보존/공급·갱신·기록별 수집 작업. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/cli/walk_park_catalog.py](../../../backend/src/daengs_backend/cli/walk_park_catalog.py) | cli | retain | 관측·카탈로그 보존/공급·갱신·기록별 수집 작업. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/repositories/walk_catalog_demand.py](../../../backend/src/daengs_backend/repositories/walk_catalog_demand.py) | repositories | retain | 관측·카탈로그 보존/공급·갱신·기록별 수집 작업. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/services/walk_area_catalog.py](../../../backend/src/daengs_backend/services/walk_area_catalog.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 실제 구현은 walk_background 소유; 명시된 이름/대상은 background-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_area_context.py](../../../backend/src/daengs_backend/services/walk_area_context.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 실제 구현은 walk_background 소유; 명시된 이름/대상은 background-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_background/__init__.py](../../../backend/src/daengs_backend/services/walk_background/__init__.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/catalogs/__init__.py](../../../backend/src/daengs_backend/services/walk_background/catalogs/__init__.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/catalogs/area.py](../../../backend/src/daengs_backend/services/walk_background/catalogs/area.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/catalogs/commerce.py](../../../backend/src/daengs_backend/services/walk_background/catalogs/commerce.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/catalogs/park.py](../../../backend/src/daengs_backend/services/walk_background/catalogs/park.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/catalogs/refresh.py](../../../backend/src/daengs_backend/services/walk_background/catalogs/refresh.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/catalogs/regions.py](../../../backend/src/daengs_backend/services/walk_background/catalogs/regions.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/catalogs/retention.py](../../../backend/src/daengs_backend/services/walk_background/catalogs/retention.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/catalogs/river.py](../../../backend/src/daengs_backend/services/walk_background/catalogs/river.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/collection.py](../../../backend/src/daengs_backend/services/walk_background/collection.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/contracts.py](../../../backend/src/daengs_backend/services/walk_background/contracts.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/http.py](../../../backend/src/daengs_backend/services/walk_background/http.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/providers/__init__.py](../../../backend/src/daengs_backend/services/walk_background/providers/__init__.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/providers/area.py](../../../backend/src/daengs_backend/services/walk_background/providers/area.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/providers/facility.py](../../../backend/src/daengs_backend/services/walk_background/providers/facility.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/providers/public.py](../../../backend/src/daengs_backend/services/walk_background/providers/public.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/providers/sgis.py](../../../backend/src/daengs_backend/services/walk_background/providers/sgis.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_background/providers/weather.py](../../../backend/src/daengs_backend/services/walk_background/providers/weather.py) | services | retain | 배경 공급 패키지의 계약·수집·공급자·카탈로그 소유 모듈. 기록 outbox와 일기 해석은 포함하지 않음.  |
| [backend/src/daengs_backend/services/walk_catalog_refresh.py](../../../backend/src/daengs_backend/services/walk_catalog_refresh.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 실제 구현은 walk_background 소유; 명시된 이름/대상은 background-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_catalog_regions.py](../../../backend/src/daengs_backend/services/walk_catalog_regions.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 실제 구현은 walk_background 소유; 명시된 이름/대상은 background-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_commerce_catalog.py](../../../backend/src/daengs_backend/services/walk_commerce_catalog.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 실제 구현은 walk_background 소유; 명시된 이름/대상은 background-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_entry_context_source.py](../../../backend/src/daengs_backend/services/walk_entry_context_source.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 실제 구현은 walk_background 소유; 명시된 이름/대상은 background-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_park_catalog.py](../../../backend/src/daengs_backend/services/walk_park_catalog.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 실제 구현은 walk_background 소유; 명시된 이름/대상은 background-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_public_context.py](../../../backend/src/daengs_backend/services/walk_public_context.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 실제 구현은 walk_background 소유; 명시된 이름/대상은 background-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_public_http.py](../../../backend/src/daengs_backend/services/walk_public_http.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 실제 구현은 walk_background 소유; 명시된 이름/대상은 background-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_river_catalog.py](../../../backend/src/daengs_backend/services/walk_river_catalog.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 실제 구현은 walk_background 소유; 명시된 이름/대상은 background-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_sgis.py](../../../backend/src/daengs_backend/services/walk_sgis.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 실제 구현은 walk_background 소유; 명시된 이름/대상은 background-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_space_catalog_input.py](../../../backend/src/daengs_backend/services/walk_space_catalog_input.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 실제 구현은 walk_background 소유; 명시된 이름/대상은 background-package.json 참조.  |
| [backend/src/daengs_backend/services/walk_weather_context.py](../../../backend/src/daengs_backend/services/walk_weather_context.py) | services | retain | 기존 공개 이름의 지연 import 호환만 유지. 실제 구현은 walk_background 소유; 명시된 이름/대상은 background-package.json 참조.  |
| [backend/src/daengs_walk/weather.py](../../../backend/src/daengs_walk/weather.py) | domain | retain | 역사적 격자 기온 관측과 시각/위치 출처 검증. 장면 채택 정책 없음.  |

## diary: 현재 일기 재료 해석·선정·작성·발행

| 파일 | 현재 계층 | 처리 | 근거 / 후속 부채 |
| --- | --- | --- | --- |
| [backend/src/daengs_backend/orchestration/diary.py](../../../backend/src/daengs_backend/orchestration/diary.py) | orchestration | retain | 일기 작업 그래프. 공통 JobExecutor를 사용하며 별도 실행기 생성 금지.  |
| [backend/src/daengs_backend/routers/walk_diary_slots.py](../../../backend/src/daengs_backend/routers/walk_diary_slots.py) | routers | retain | 현재 일기 재료 해석·선정·작성·발행. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/schemas/walk_diary.py](../../../backend/src/daengs_backend/schemas/walk_diary.py) | schemas | retain | 현재 일기/board 응답과 저장 bundle 검증.  |
| [backend/src/daengs_backend/schemas/walk_diary_slots.py](../../../backend/src/daengs_backend/schemas/walk_diary_slots.py) | schemas | retain | 현재 일기 재료 해석·선정·작성·발행. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/services/walk_diary/__init__.py](../../../backend/src/daengs_backend/services/walk_diary/__init__.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/api.py](../../../backend/src/daengs_backend/services/walk_diary/api.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/collection/__init__.py](../../../backend/src/daengs_backend/services/walk_diary/collection/__init__.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/collection/application.py](../../../backend/src/daengs_backend/services/walk_diary/collection/application.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/collection/catalog.py](../../../backend/src/daengs_backend/services/walk_diary/collection/catalog.py) | services | retain | 보존된 카탈로그의 hash/coverage 확인 후 일기 AreaInput으로 변환하는 수집 어댑터.  |
| [backend/src/daengs_backend/services/walk_diary/collection/comparison.py](../../../backend/src/daengs_backend/services/walk_diary/collection/comparison.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/collection/progress.py](../../../backend/src/daengs_backend/services/walk_diary/collection/progress.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/collection/service.py](../../../backend/src/daengs_backend/services/walk_diary/collection/service.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/collection/snapshot.py](../../../backend/src/daengs_backend/services/walk_diary/collection/snapshot.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/contracts.py](../../../backend/src/daengs_backend/services/walk_diary/contracts.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/deadline.py](../../../backend/src/daengs_backend/services/walk_diary/deadline.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/guard.py](../../../backend/src/daengs_backend/services/walk_diary/guard.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/lifecycle/__init__.py](../../../backend/src/daengs_backend/services/walk_diary/lifecycle/__init__.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/lifecycle/generation.py](../../../backend/src/daengs_backend/services/walk_diary/lifecycle/generation.py) | services | retain | 예약 전 확정한 작성/결과/완료 계약으로 생성. 결과 타입에 따른 전략 전환 없음.  |
| [backend/src/daengs_backend/services/walk_diary/lifecycle/negotiation.py](../../../backend/src/daengs_backend/services/walk_diary/lifecycle/negotiation.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/lifecycle/publication.py](../../../backend/src/daengs_backend/services/walk_diary/lifecycle/publication.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/lifecycle/reservation.py](../../../backend/src/daengs_backend/services/walk_diary/lifecycle/reservation.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/lifecycle/snapshot.py](../../../backend/src/daengs_backend/services/walk_diary/lifecycle/snapshot.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/lifecycle/strategy.py](../../../backend/src/daengs_backend/services/walk_diary/lifecycle/strategy.py) | services | retain | 협상된 형식과 명시 override로 작성·결과·완료를 실행 전에 고정하는 호환 어댑터.  |
| [backend/src/daengs_backend/services/walk_diary/model_input.py](../../../backend/src/daengs_backend/services/walk_diary/model_input.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/model_materials.py](../../../backend/src/daengs_backend/services/walk_diary/model_materials.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/preparation/__init__.py](../../../backend/src/daengs_backend/services/walk_diary/preparation/__init__.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/preparation/board.py](../../../backend/src/daengs_backend/services/walk_diary/preparation/board.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/preparation/diary.py](../../../backend/src/daengs_backend/services/walk_diary/preparation/diary.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/preparation/input.py](../../../backend/src/daengs_backend/services/walk_diary/preparation/input.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/preparation/observations.py](../../../backend/src/daengs_backend/services/walk_diary/preparation/observations.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/preparation/route_policy.py](../../../backend/src/daengs_backend/services/walk_diary/preparation/route_policy.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/preview.py](../../../backend/src/daengs_backend/services/walk_diary/preview.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/runtime.py](../../../backend/src/daengs_backend/services/walk_diary/runtime.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/storage/__init__.py](../../../backend/src/daengs_backend/services/walk_diary/storage/__init__.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/storage/board.py](../../../backend/src/daengs_backend/services/walk_diary/storage/board.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/storage/bundle.py](../../../backend/src/daengs_backend/services/walk_diary/storage/bundle.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/storage/card_receipt.py](../../../backend/src/daengs_backend/services/walk_diary/storage/card_receipt.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/storage/provenance.py](../../../backend/src/daengs_backend/services/walk_diary/storage/provenance.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/writer_contract.py](../../../backend/src/daengs_backend/services/walk_diary/writer_contract.py) | services | retain | 과거 슬롯 작성에 대한 명시적인 내부 override 계약.  |
| [backend/src/daengs_backend/services/walk_diary/writing/__init__.py](../../../backend/src/daengs_backend/services/walk_diary/writing/__init__.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/writing/assembly.py](../../../backend/src/daengs_backend/services/walk_diary/writing/assembly.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/writing/jobs.py](../../../backend/src/daengs_backend/services/walk_diary/writing/jobs.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/writing/policy.py](../../../backend/src/daengs_backend/services/walk_diary/writing/policy.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/writing/prompts.py](../../../backend/src/daengs_backend/services/walk_diary/writing/prompts.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_backend/services/walk_diary/writing/provider.py](../../../backend/src/daengs_backend/services/walk_diary/writing/provider.py) | services | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/__init__.py](../../../backend/src/daengs_walk/diary/__init__.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/board/__init__.py](../../../backend/src/daengs_walk/diary/board/__init__.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/board/activity.py](../../../backend/src/daengs_walk/diary/board/activity.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/board/assembly.py](../../../backend/src/daengs_walk/diary/board/assembly.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/board/backgrounds.py](../../../backend/src/daengs_walk/diary/board/backgrounds.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/board/models.py](../../../backend/src/daengs_walk/diary/board/models.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/board/output.py](../../../backend/src/daengs_walk/diary/board/output.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/board/preview.py](../../../backend/src/daengs_walk/diary/board/preview.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/board/scene_input.py](../../../backend/src/daengs_walk/diary/board/scene_input.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/board/writing_context.py](../../../backend/src/daengs_walk/diary/board/writing_context.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/cli/__init__.py](../../../backend/src/daengs_walk/diary/cli/__init__.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/cli/route_normalize.py](../../../backend/src/daengs_walk/diary/cli/route_normalize.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/cli/space_normalize.py](../../../backend/src/daengs_walk/diary/cli/space_normalize.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/cli/space_replay.py](../../../backend/src/daengs_walk/diary/cli/space_replay.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/contracts/__init__.py](../../../backend/src/daengs_walk/diary/contracts/__init__.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/contracts/canonical.py](../../../backend/src/daengs_walk/diary/contracts/canonical.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/contracts/input.py](../../../backend/src/daengs_walk/diary/contracts/input.py) | domain | retain | 일기 입력/검증 계약. 공용 값의 기존 import 이름은 같은 객체를 재노출.  |
| [backend/src/daengs_walk/diary/contracts/narrative.py](../../../backend/src/daengs_walk/diary/contracts/narrative.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/contracts/output.py](../../../backend/src/daengs_walk/diary/contracts/output.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/contracts/slot_receipt.py](../../../backend/src/daengs_walk/diary/contracts/slot_receipt.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/contracts/slots.py](../../../backend/src/daengs_walk/diary/contracts/slots.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/route/__init__.py](../../../backend/src/daengs_walk/diary/route/__init__.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/route/binding.py](../../../backend/src/daengs_walk/diary/route/binding.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/route/geometry.py](../../../backend/src/daengs_walk/diary/route/geometry.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/route/movement.py](../../../backend/src/daengs_walk/diary/route/movement.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/route/movement_policy.py](../../../backend/src/daengs_walk/diary/route/movement_policy.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/route/observations.py](../../../backend/src/daengs_walk/diary/route/observations.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/route/patterns.py](../../../backend/src/daengs_walk/diary/route/patterns.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/selection/__init__.py](../../../backend/src/daengs_walk/diary/selection/__init__.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/selection/board.py](../../../backend/src/daengs_walk/diary/selection/board.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/selection/stamps.py](../../../backend/src/daengs_walk/diary/selection/stamps.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/slots/__init__.py](../../../backend/src/daengs_walk/diary/slots/__init__.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/slots/admission.py](../../../backend/src/daengs_walk/diary/slots/admission.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/slots/claims.py](../../../backend/src/daengs_walk/diary/slots/claims.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/slots/memory.py](../../../backend/src/daengs_walk/diary/slots/memory.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/slots/movement.py](../../../backend/src/daengs_walk/diary/slots/movement.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/slots/route.py](../../../backend/src/daengs_walk/diary/slots/route.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/slots/service.py](../../../backend/src/daengs_walk/diary/slots/service.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/slots/sources.py](../../../backend/src/daengs_walk/diary/slots/sources.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/slots/space.py](../../../backend/src/daengs_walk/diary/slots/space.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/slots/spatial.py](../../../backend/src/daengs_walk/diary/slots/spatial.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/slots/temperature.py](../../../backend/src/daengs_walk/diary/slots/temperature.py) | domain | retain | 공급 관측을 소비하는 일기 장면 채택 정책. 기존 관측 타입 이름은 같은 객체를 재노출.  |
| [backend/src/daengs_walk/diary/space/__init__.py](../../../backend/src/daengs_walk/diary/space/__init__.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/space/area.py](../../../backend/src/daengs_walk/diary/space/area.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/space/cases.py](../../../backend/src/daengs_walk/diary/space/cases.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/space/coverage.py](../../../backend/src/daengs_walk/diary/space/coverage.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/space/geometry.py](../../../backend/src/daengs_walk/diary/space/geometry.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/space/kakao.py](../../../backend/src/daengs_walk/diary/space/kakao.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/space/materials.py](../../../backend/src/daengs_walk/diary/space/materials.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/space/policy.py](../../../backend/src/daengs_walk/diary/space/policy.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/space/projection.py](../../../backend/src/daengs_walk/diary/space/projection.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |
| [backend/src/daengs_walk/diary/space/public.py](../../../backend/src/daengs_walk/diary/space/public.py) | domain | retain | 일기 전용 계약·정책·조립 책임. 외부 기능의 역참조는 별도 부채 목록에서 제거.  |

## legacy: 지원 중인 과거 스토리보드/bundle/slot 계약

| 파일 | 현재 계층 | 처리 | 근거 / 후속 부채 |
| --- | --- | --- | --- |
| [backend/src/daengs_backend/routers/walk_storyboard.py](../../../backend/src/daengs_backend/routers/walk_storyboard.py) | routers | retain | 지원 중인 과거 스토리보드/bundle/slot 계약. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_backend/schemas/walk_legacy.py](../../../backend/src/daengs_backend/schemas/walk_legacy.py) | schemas | retain | 지원 중인 과거 후보 응답 계약.  |
| [backend/src/daengs_backend/services/walk_diary/legacy/__init__.py](../../../backend/src/daengs_backend/services/walk_diary/legacy/__init__.py) | services | retain | 지원 중인 과거 작성 계약 유지.  |
| [backend/src/daengs_backend/services/walk_diary/legacy/board_bundle.py](../../../backend/src/daengs_backend/services/walk_diary/legacy/board_bundle.py) | services | retain | 지원 중인 과거 작성 계약 유지.  |
| [backend/src/daengs_backend/services/walk_diary/legacy/board_slots.py](../../../backend/src/daengs_backend/services/walk_diary/legacy/board_slots.py) | services | retain | 지원 중인 과거 작성 계약 유지.  |
| [backend/src/daengs_backend/services/walk_diary/legacy/bundle.py](../../../backend/src/daengs_backend/services/walk_diary/legacy/bundle.py) | services | retain | 지원 중인 과거 작성 계약 유지.  |
| [backend/src/daengs_backend/services/walk_diary/legacy/slots.py](../../../backend/src/daengs_backend/services/walk_diary/legacy/slots.py) | services | retain | 지원 중인 과거 작성 계약 유지.  |
| [backend/src/daengs_backend/services/walk_legacy/__init__.py](../../../backend/src/daengs_backend/services/walk_legacy/__init__.py) | services | retain | 지원 중인 과거 생성 구현의 소유 영역.  |
| [backend/src/daengs_backend/services/walk_legacy/context.py](../../../backend/src/daengs_backend/services/walk_legacy/context.py) | services | retain | 기존 구현을 소유 패키지로 이동. 계산·직렬화·저장·조회 정책은 기존 함수 본문을 유지하며 공개 경로는 remaining-packages.json에서 호환.  |
| [backend/src/daengs_backend/services/walk_legacy/storyboard.py](../../../backend/src/daengs_backend/services/walk_legacy/storyboard.py) | services | retain | 과거 후보 생성/조회 및 과거 기록 투영. 상태 전이는 공통 구현 소비.  |
| [backend/src/daengs_backend/services/walk_legacy/titles.py](../../../backend/src/daengs_backend/services/walk_legacy/titles.py) | services | retain | 기존 구현을 소유 패키지로 이동. 계산·직렬화·저장·조회 정책은 기존 함수 본문을 유지하며 공개 경로는 remaining-packages.json에서 호환.  |
| [backend/src/daengs_backend/services/walk_storyboard_context.py](../../../backend/src/daengs_backend/services/walk_storyboard_context.py) | services | retain | 구현 없는 지연 공개 호환. 실제 소유 대상과 기존 산출물 호환은 remaining-packages.json에 명시.  |
| [backend/src/daengs_backend/services/walk_storyboard_titles.py](../../../backend/src/daengs_backend/services/walk_storyboard_titles.py) | services | retain | 구현 없는 지연 공개 호환. 실제 소유 대상과 기존 산출물 호환은 remaining-packages.json에 명시.  |
| [backend/src/daengs_walk/diary/legacy/__init__.py](../../../backend/src/daengs_walk/diary/legacy/__init__.py) | domain | retain | 지원 중인 과거 작성 계약 유지.  |
| [backend/src/daengs_walk/diary/legacy/writing.py](../../../backend/src/daengs_walk/diary/legacy/writing.py) | domain | retain | 지원 중인 과거 작성 계약 유지.  |
| [backend/src/daengs_walk/storyboard.py](../../../backend/src/daengs_walk/storyboard.py) | domain | repackage | 지원 중인 과거 스토리보드/bundle/slot 계약. 후속 배치는 소유권 기준이며 계산/버전은 보존.  |
| [backend/src/daengs_walk/storyboard_input.py](../../../backend/src/daengs_walk/storyboard_input.py) | domain | retain | 과거 기록 투영/scene_inputs 소유. 연속 경로는 공통 route.nodes를 소비하며 기존 route_nodes 이름 유지.  |
| [backend/src/daengs_walk/storyboard_selection.py](../../../backend/src/daengs_walk/storyboard_selection.py) | domain | retain | 과거 우선순위 선정 및 속도 정책 소유. 공통 route 계산을 소비하며 기존 함수 시그니처 보존.  |

## generation_state: 현재·과거 생성 시도의 공통 상태 전이

| 파일 | 현재 계층 | 처리 | 근거 / 후속 부채 |
| --- | --- | --- | --- |
| [backend/src/daengs_backend/models/walk_storyboard.py](../../../backend/src/daengs_backend/models/walk_storyboard.py) | models | retain | 현재/과거 형식이 공유하는 저장 행과 생성 접근. ORM/DAO 계층과 스키마는 보존; 과거 전용으로 분류하지 않음.  |
| [backend/src/daengs_backend/repositories/walk_storyboard.py](../../../backend/src/daengs_backend/repositories/walk_storyboard.py) | repositories | retain | 현재/과거 형식이 공유하는 저장 행과 생성 접근. ORM/DAO 계층과 스키마는 보존; 과거 전용으로 분류하지 않음.  |
| [backend/src/daengs_backend/services/walk_generation/state.py](../../../backend/src/daengs_backend/services/walk_generation/state.py) | services | retain | 현재/과거가 같은 저장 행의 lease·세대·revision을 보호하는 유일한 상태 전이.  |
| [backend/src/daengs_backend/services/walk_storyboard_state.py](../../../backend/src/daengs_backend/services/walk_storyboard_state.py) | services | repackage | 기존 이름의 호환 import. 공통 상태 함수/예외와 동일 객체이며 별도 상태 전이 없음.  |

## shared_contracts: 소비자에 종속되지 않는 값·좌표·정규 직렬화 계약

| 파일 | 현재 계층 | 처리 | 근거 / 후속 부채 |
| --- | --- | --- | --- |
| [backend/src/daengs_walk/contracts.py](../../../backend/src/daengs_walk/contracts.py) | domain | retain | 소비자에 종속되지 않는 값·좌표·정규 직렬화 계약. 후속 배치는 소유권 기준이며 계산/버전은 보존.  |
| [backend/src/daengs_walk/hex_grid.py](../../../backend/src/daengs_walk/hex_grid.py) | domain | retain | 소비자에 종속되지 않는 값·좌표·정규 직렬화 계약. 후속 배치는 소유권 기준이며 계산/버전은 보존.  |
| [backend/src/daengs_walk/value_contracts.py](../../../backend/src/daengs_walk/value_contracts.py) | domain | retain | 동일 snapshot 값/정규 JSON hash 계약. 측정 좌표와 GPS fingerprint는 독립 계약 유지.  |

## integration: 다른 제품/공통 실행/인증/설정/배포의 연결 접점

| 파일 | 현재 계층 | 처리 | 근거 / 후속 부채 |
| --- | --- | --- | --- |
| [.github/workflows/db-migrate.yml](../../../.github/workflows/db-migrate.yml) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [.github/workflows/deploy.yml](../../../.github/workflows/deploy.yml) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [.github/workflows/walk-diary-runtime.yml](../../../.github/workflows/walk-diary-runtime.yml) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [backend/src/daengs_backend/config.py](../../../backend/src/daengs_backend/config.py) | support | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/core/database.py](../../../backend/src/daengs_backend/core/database.py) | support | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/core/deps.py](../../../backend/src/daengs_backend/core/deps.py) | support | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/main.py](../../../backend/src/daengs_backend/main.py) | support | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/models/__init__.py](../../../backend/src/daengs_backend/models/__init__.py) | models | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/models/base.py](../../../backend/src/daengs_backend/models/base.py) | models | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/orchestration/adapters/__init__.py](../../../backend/src/daengs_backend/orchestration/adapters/__init__.py) | orchestration | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/orchestration/adapters/life.py](../../../backend/src/daengs_backend/orchestration/adapters/life.py) | orchestration | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/orchestration/adapters/walk.py](../../../backend/src/daengs_backend/orchestration/adapters/walk.py) | orchestration | integration | 활동/어시스턴트가 산책 기능을 소비하는 연결점. 해당 제품 소유 유지.  |
| [backend/src/daengs_backend/orchestration/contracts.py](../../../backend/src/daengs_backend/orchestration/contracts.py) | orchestration | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/orchestration/execution.py](../../../backend/src/daengs_backend/orchestration/execution.py) | orchestration | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/orchestration/runtime.py](../../../backend/src/daengs_backend/orchestration/runtime.py) | orchestration | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/repositories/__init__.py](../../../backend/src/daengs_backend/repositories/__init__.py) | repositories | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/repositories/activity.py](../../../backend/src/daengs_backend/repositories/activity.py) | repositories | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/repositories/pet.py](../../../backend/src/daengs_backend/repositories/pet.py) | repositories | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/routers/assistant.py](../../../backend/src/daengs_backend/routers/assistant.py) | routers | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/schemas/walk_generation.py](../../../backend/src/daengs_backend/schemas/walk_generation.py) | schemas | retain | 현재/과거가 공유하는 HTTP 형식 협상 요청 계약.  |
| [backend/src/daengs_backend/schemas/walk_storyboard.py](../../../backend/src/daengs_backend/schemas/walk_storyboard.py) | schemas | retain | 기존 schema import 호환. 현재/과거 응답 및 공통 요청은 각 소유 모듈에서 재노출.  |
| [backend/src/daengs_backend/services/__init__.py](../../../backend/src/daengs_backend/services/__init__.py) | services | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/services/activity.py](../../../backend/src/daengs_backend/services/activity.py) | services | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/services/activity_core/common.py](../../../backend/src/daengs_backend/services/activity_core/common.py) | services | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/services/activity_core/sessions.py](../../../backend/src/daengs_backend/services/activity_core/sessions.py) | services | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/services/activity_core/walk.py](../../../backend/src/daengs_backend/services/activity_core/walk.py) | services | integration | 활동/어시스턴트가 산책 기능을 소비하는 연결점. 해당 제품 소유 유지.  |
| [backend/src/daengs_backend/services/activity_game.py](../../../backend/src/daengs_backend/services/activity_game.py) | services | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/services/activity_walk_projection.py](../../../backend/src/daengs_backend/services/activity_walk_projection.py) | services | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/services/app_auth.py](../../../backend/src/daengs_backend/services/app_auth.py) | services | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/services/care_event.py](../../../backend/src/daengs_backend/services/care_event.py) | services | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/services/pet.py](../../../backend/src/daengs_backend/services/pet.py) | services | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/services/pet_identity.py](../../../backend/src/daengs_backend/services/pet_identity.py) | services | integration | 산책과 직접 연결된 기존 공통/다른 제품 모듈. 이 파일 전체를 이관하지 않고 해당 import/call 접점만 관리.  |
| [backend/src/daengs_backend/services/walk_activity_context.py](../../../backend/src/daengs_backend/services/walk_activity_context.py) | services | repackage | 다른 제품/공통 실행/인증/설정/배포의 연결 접점. 후속 소유 영역별 서비스 패키지 재배치 대상.  |
| [backend/src/daengs_backend/services/walk_generation/__init__.py](../../../backend/src/daengs_backend/services/walk_generation/__init__.py) | services | retain | 형식 협상과 공통 생성 상태의 소유 영역; 별도 런타임 없음.  |
| [backend/src/daengs_backend/services/walk_generation/api.py](../../../backend/src/daengs_backend/services/walk_generation/api.py) | services | retain | 저장 형식 협상 후 현재/과거 실행 진입을 선택; 기존 URL과 요청 보존.  |
| [backend/src/daengs_backend/services/walk_storyboard.py](../../../backend/src/daengs_backend/services/walk_storyboard.py) | services | retain | 기존 URL 호출자의 호환 import. 실행/협상 구현은 walk_generation.api 소유.  |
| [backend/src/daengs_backend/tasks/walk_entry_context.py](../../../backend/src/daengs_backend/tasks/walk_entry_context.py) | tasks | retain | 기록 context 처리와 배경 카탈로그 갱신의 Celery 작업 접점. 태스크·queue 이름/프로세스 경계 유지.  |
| [backend/src/daengs_walk/__init__.py](../../../backend/src/daengs_walk/__init__.py) | domain | repackage | 11개 기존 공개 이름을 소유 모듈의 같은 객체로 지연 제공. 전체 기능 일괄 로딩 없음. 제품 호출자는 소유 모듈 직접 소비.  |
| [db/init/06_walks.sql](../../../db/init/06_walks.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/08_territory_visits.sql](../../../db/init/08_territory_visits.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/19_walk_entries.sql](../../../db/init/19_walk_entries.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/20_walk_storyboards.sql](../../../db/init/20_walk_storyboards.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/21_activity_game.sql](../../../db/init/21_activity_game.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/23_care_events.sql](../../../db/init/23_care_events.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/24_walk_entry_contexts.sql](../../../db/init/24_walk_entry_contexts.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/25_walk_entry_pins.sql](../../../db/init/25_walk_entry_pins.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/26_walk_photo_manifests.sql](../../../db/init/26_walk_photo_manifests.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/27_walk_public_context.sql](../../../db/init/27_walk_public_context.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/28_walk_commerce_context.sql](../../../db/init/28_walk_commerce_context.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/32_walk_context_recollection.sql](../../../db/init/32_walk_context_recollection.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/35_walk_motion_backup.sql](../../../db/init/35_walk_motion_backup.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/36_walk_precision_backup.sql](../../../db/init/36_walk_precision_backup.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/init/37_walk_measurements.sql](../../../db/init/37_walk_measurements.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-08-31_walks.sql](../../../db/migrations/2026-08-31_walks.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-01_walk_pets.sql](../../../db/migrations/2026-09-01_walk_pets.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-02_walk_analyses.sql](../../../db/migrations/2026-09-02_walk_analyses.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-02_walk_point_chunks.sql](../../../db/migrations/2026-09-02_walk_point_chunks.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-03_walk_capsules.sql](../../../db/migrations/2026-09-03_walk_capsules.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-05_walk_entries.sql](../../../db/migrations/2026-09-05_walk_entries.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-05_walk_storyboards.sql](../../../db/migrations/2026-09-05_walk_storyboards.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-06_activity_game.sql](../../../db/migrations/2026-09-06_activity_game.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-08_care_events.sql](../../../db/migrations/2026-09-08_care_events.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-08_walk_entry_contexts.sql](../../../db/migrations/2026-09-08_walk_entry_contexts.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-09_walk_entry_pins.sql](../../../db/migrations/2026-09-09_walk_entry_pins.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-09_walk_photo_manifests.sql](../../../db/migrations/2026-09-09_walk_photo_manifests.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-09_walk_public_context.sql](../../../db/migrations/2026-09-09_walk_public_context.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-09_walk_public_context_commerce.sql](../../../db/migrations/2026-09-09_walk_public_context_commerce.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-10_walk_context_recollection.sql](../../../db/migrations/2026-09-10_walk_context_recollection.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-11_walk_motion_backup.sql](../../../db/migrations/2026-09-11_walk_motion_backup.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-11_walk_precision_backup.sql](../../../db/migrations/2026-09-11_walk_precision_backup.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/2026-09-13_walk_measurements.sql](../../../db/migrations/2026-09-13_walk_measurements.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-08-31_walks.sql](../../../db/migrations/verify_2026-08-31_walks.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-01_walk_pets.sql](../../../db/migrations/verify_2026-09-01_walk_pets.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-02_walk_analyses.sql](../../../db/migrations/verify_2026-09-02_walk_analyses.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-02_walk_point_chunks.sql](../../../db/migrations/verify_2026-09-02_walk_point_chunks.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-03_walk_capsules.sql](../../../db/migrations/verify_2026-09-03_walk_capsules.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-05_walk_entries.sql](../../../db/migrations/verify_2026-09-05_walk_entries.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-05_walk_storyboards.sql](../../../db/migrations/verify_2026-09-05_walk_storyboards.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-06_activity_game.sql](../../../db/migrations/verify_2026-09-06_activity_game.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-08_care_events.sql](../../../db/migrations/verify_2026-09-08_care_events.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-08_walk_entry_contexts.sql](../../../db/migrations/verify_2026-09-08_walk_entry_contexts.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-09_vet_visits.sql](../../../db/migrations/verify_2026-09-09_vet_visits.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-09_walk_entry_pins.sql](../../../db/migrations/verify_2026-09-09_walk_entry_pins.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-09_walk_photo_manifests.sql](../../../db/migrations/verify_2026-09-09_walk_photo_manifests.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-09_walk_public_context.sql](../../../db/migrations/verify_2026-09-09_walk_public_context.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-09_walk_public_context_commerce.sql](../../../db/migrations/verify_2026-09-09_walk_public_context_commerce.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-10_walk_context_recollection.sql](../../../db/migrations/verify_2026-09-10_walk_context_recollection.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-11_walk_motion_backup.sql](../../../db/migrations/verify_2026-09-11_walk_motion_backup.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-11_walk_precision_backup.sql](../../../db/migrations/verify_2026-09-11_walk_precision_backup.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [db/migrations/verify_2026-09-13_walk_measurements.sql](../../../db/migrations/verify_2026-09-13_walk_measurements.sql) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [docker-compose.yml](../../../docker-compose.yml) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [docs/ci/walk-entry-context-tests.yml](../../../docs/ci/walk-entry-context-tests.yml) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [docs/ci/walk-entry-v2-tests.yml](../../../docs/ci/walk-entry-v2-tests.yml) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [frontend/app/components/assistant-inspect.tsx](../../../frontend/app/components/assistant-inspect.tsx) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [frontend/app/components/inspect-tabs.tsx](../../../frontend/app/components/inspect-tabs.tsx) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [frontend/app/components/walk-inspect.tsx](../../../frontend/app/components/walk-inspect.tsx) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |
| [nginx/default.conf](../../../nginx/default.conf) | support | integration | 저장/HTTP/UI/운영 접점만 범위에 포함. 파일 전체의 기능을 산책이나 일기로 이관하지 않음.  |

## verification: 여러 영역을 검증하는 테스트·표본·평가·운영 점검 도구

| 파일 | 현재 계층 | 처리 | 근거 / 후속 부채 |
| --- | --- | --- | --- |
| [backend/evals/diary_route_scenario/LLM_INPUT.md](../../../backend/evals/diary_route_scenario/LLM_INPUT.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/PUBLIC_DATA.md](../../../backend/evals/diary_route_scenario/PUBLIC_DATA.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/README.md](../../../backend/evals/diary_route_scenario/README.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/activity-offline-03/preview.html](../../../backend/evals/diary_route_scenario/activity-offline-03/preview.html) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/activity-offline-03/result.json.gz](../../../backend/evals/diary_route_scenario/activity-offline-03/result.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/activity-offline-03/slots.json.gz](../../../backend/evals/diary_route_scenario/activity-offline-03/slots.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/activity-offline-03/stored.json.gz](../../../backend/evals/diary_route_scenario/activity-offline-03/stored.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/activity-offline-03/summary.json](../../../backend/evals/diary_route_scenario/activity-offline-03/summary.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/backgrounds.json.gz](../../../backend/evals/diary_route_scenario/public-01/backgrounds.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/base.json](../../../backend/evals/diary_route_scenario/public-01/base.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/catalog-preparation.json](../../../backend/evals/diary_route_scenario/public-01/catalog-preparation.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/diary.md](../../../backend/evals/diary_route_scenario/public-01/diary.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/input.json](../../../backend/evals/diary_route_scenario/public-01/input.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/map.json](../../../backend/evals/diary_route_scenario/public-01/map.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/plan.json](../../../backend/evals/diary_route_scenario/public-01/plan.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/preview.html](../../../backend/evals/diary_route_scenario/public-01/preview.html) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/001.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/001.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/002.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/002.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/003.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/003.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/004.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/004.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/005.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/005.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/006.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/006.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/007.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/007.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/008.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/008.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/009.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/009.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/010.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/010.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/011.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/011.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/012.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/012.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/013.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/013.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/014.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/014.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/015.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/015.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/016.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/016.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/017.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/017.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/018.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/018.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/019.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/019.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/020.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/020.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/021.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/021.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/022.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/022.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/023.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/023.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/024.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/024.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/025.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/025.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/026.json](../../../backend/evals/diary_route_scenario/public-01/public-http/026.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/027.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/027.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/028.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/028.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/029.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/029.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/030.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/030.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/031.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/031.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/032.json](../../../backend/evals/diary_route_scenario/public-01/public-http/032.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/033.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/033.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/034.json](../../../backend/evals/diary_route_scenario/public-01/public-http/034.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/035.json](../../../backend/evals/diary_route_scenario/public-01/public-http/035.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/036.json](../../../backend/evals/diary_route_scenario/public-01/public-http/036.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/037.json](../../../backend/evals/diary_route_scenario/public-01/public-http/037.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/038.json.gz](../../../backend/evals/diary_route_scenario/public-01/public-http/038.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/039.json](../../../backend/evals/diary_route_scenario/public-01/public-http/039.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/040.json](../../../backend/evals/diary_route_scenario/public-01/public-http/040.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/041.json](../../../backend/evals/diary_route_scenario/public-01/public-http/041.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/042.json](../../../backend/evals/diary_route_scenario/public-01/public-http/042.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/public-http/043.json](../../../backend/evals/diary_route_scenario/public-01/public-http/043.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/result.json.gz](../../../backend/evals/diary_route_scenario/public-01/result.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/run.json](../../../backend/evals/diary_route_scenario/public-01/run.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/slots.json.gz](../../../backend/evals/diary_route_scenario/public-01/slots.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/source-lineage.json](../../../backend/evals/diary_route_scenario/public-01/source-lineage.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-01/weather-raw/d6f5ce3fa0881b4852158fc8d7a5a627ec90187cbc1bad7d011daabcff848a19.json](../../../backend/evals/diary_route_scenario/public-01/weather-raw/d6f5ce3fa0881b4852158fc8d7a5a627ec90187cbc1bad7d011daabcff848a19.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02-titles/diary.md](../../../backend/evals/diary_route_scenario/public-02-titles/diary.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02-titles/preview.html](../../../backend/evals/diary_route_scenario/public-02-titles/preview.html) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02-titles/raw-response.json](../../../backend/evals/diary_route_scenario/public-02-titles/raw-response.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02-titles/request.json](../../../backend/evals/diary_route_scenario/public-02-titles/request.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02-titles/scene-titles.json](../../../backend/evals/diary_route_scenario/public-02-titles/scene-titles.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/backgrounds.json.gz](../../../backend/evals/diary_route_scenario/public-02/backgrounds.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/base.json](../../../backend/evals/diary_route_scenario/public-02/base.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/catalog-preparation.json](../../../backend/evals/diary_route_scenario/public-02/catalog-preparation.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/diary.md](../../../backend/evals/diary_route_scenario/public-02/diary.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/input.json](../../../backend/evals/diary_route_scenario/public-02/input.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/map.json](../../../backend/evals/diary_route_scenario/public-02/map.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/plan.json](../../../backend/evals/diary_route_scenario/public-02/plan.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/preview.html](../../../backend/evals/diary_route_scenario/public-02/preview.html) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/001.json](../../../backend/evals/diary_route_scenario/public-02/public-http/001.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/002.json.gz](../../../backend/evals/diary_route_scenario/public-02/public-http/002.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/003.json](../../../backend/evals/diary_route_scenario/public-02/public-http/003.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/004.json](../../../backend/evals/diary_route_scenario/public-02/public-http/004.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/005.json](../../../backend/evals/diary_route_scenario/public-02/public-http/005.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/006.json.gz](../../../backend/evals/diary_route_scenario/public-02/public-http/006.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/007.json](../../../backend/evals/diary_route_scenario/public-02/public-http/007.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/008.json](../../../backend/evals/diary_route_scenario/public-02/public-http/008.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/009.json](../../../backend/evals/diary_route_scenario/public-02/public-http/009.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/010.json.gz](../../../backend/evals/diary_route_scenario/public-02/public-http/010.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/011.json](../../../backend/evals/diary_route_scenario/public-02/public-http/011.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/012.json](../../../backend/evals/diary_route_scenario/public-02/public-http/012.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/013.json](../../../backend/evals/diary_route_scenario/public-02/public-http/013.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/014.json](../../../backend/evals/diary_route_scenario/public-02/public-http/014.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/015.json](../../../backend/evals/diary_route_scenario/public-02/public-http/015.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/016.json.gz](../../../backend/evals/diary_route_scenario/public-02/public-http/016.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/017.json](../../../backend/evals/diary_route_scenario/public-02/public-http/017.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/018.json](../../../backend/evals/diary_route_scenario/public-02/public-http/018.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/019.json.gz](../../../backend/evals/diary_route_scenario/public-02/public-http/019.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/020.json](../../../backend/evals/diary_route_scenario/public-02/public-http/020.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/021.json](../../../backend/evals/diary_route_scenario/public-02/public-http/021.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/public-http/022.json](../../../backend/evals/diary_route_scenario/public-02/public-http/022.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/result.json.gz](../../../backend/evals/diary_route_scenario/public-02/result.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/run.json](../../../backend/evals/diary_route_scenario/public-02/run.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/slots.json.gz](../../../backend/evals/diary_route_scenario/public-02/slots.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/source-lineage.json](../../../backend/evals/diary_route_scenario/public-02/source-lineage.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-02/weather-raw/d6f5ce3fa0881b4852158fc8d7a5a627ec90187cbc1bad7d011daabcff848a19.json](../../../backend/evals/diary_route_scenario/public-02/weather-raw/d6f5ce3fa0881b4852158fc8d7a5a627ec90187cbc1bad7d011daabcff848a19.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized-titles/diary.md](../../../backend/evals/diary_route_scenario/public-03-normalized-titles/diary.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized-titles/preview.html](../../../backend/evals/diary_route_scenario/public-03-normalized-titles/preview.html) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized-titles/raw-response.json](../../../backend/evals/diary_route_scenario/public-03-normalized-titles/raw-response.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized-titles/request.json](../../../backend/evals/diary_route_scenario/public-03-normalized-titles/request.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized-titles/scene-titles.json](../../../backend/evals/diary_route_scenario/public-03-normalized-titles/scene-titles.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized/backgrounds.json.gz](../../../backend/evals/diary_route_scenario/public-03-normalized/backgrounds.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized/base.json](../../../backend/evals/diary_route_scenario/public-03-normalized/base.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized/input-comparison.json](../../../backend/evals/diary_route_scenario/public-03-normalized/input-comparison.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized/input.json](../../../backend/evals/diary_route_scenario/public-03-normalized/input.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized/map.json](../../../backend/evals/diary_route_scenario/public-03-normalized/map.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized/plan.json](../../../backend/evals/diary_route_scenario/public-03-normalized/plan.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized/preview.html](../../../backend/evals/diary_route_scenario/public-03-normalized/preview.html) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized/result.json.gz](../../../backend/evals/diary_route_scenario/public-03-normalized/result.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized/run.json](../../../backend/evals/diary_route_scenario/public-03-normalized/run.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized/slots.json.gz](../../../backend/evals/diary_route_scenario/public-03-normalized/slots.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized/source-lineage.json](../../../backend/evals/diary_route_scenario/public-03-normalized/source-lineage.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/public-03-normalized/source-replay.json](../../../backend/evals/diary_route_scenario/public-03-normalized/source-replay.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/scene-titles-01/diary.md](../../../backend/evals/diary_route_scenario/scene-titles-01/diary.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/scene-titles-01/preview.html](../../../backend/evals/diary_route_scenario/scene-titles-01/preview.html) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/scene-titles-01/raw-response.json](../../../backend/evals/diary_route_scenario/scene-titles-01/raw-response.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/scene-titles-01/request.json](../../../backend/evals/diary_route_scenario/scene-titles-01/request.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/scene-titles-01/scene-titles.json](../../../backend/evals/diary_route_scenario/scene-titles-01/scene-titles.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/tmap-route.json](../../../backend/evals/diary_route_scenario/tmap-route.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/whole-title-01/diary.md](../../../backend/evals/diary_route_scenario/whole-title-01/diary.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/whole-title-01/preview.html](../../../backend/evals/diary_route_scenario/whole-title-01/preview.html) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/whole-title-01/raw-response.json](../../../backend/evals/diary_route_scenario/whole-title-01/raw-response.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/whole-title-01/request.json](../../../backend/evals/diary_route_scenario/whole-title-01/request.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/whole-title-01/whole-title.json](../../../backend/evals/diary_route_scenario/whole-title-01/whole-title.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-01/backgrounds.json.gz](../../../backend/evals/diary_route_scenario/yangjae-01/backgrounds.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-01/base.json](../../../backend/evals/diary_route_scenario/yangjae-01/base.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-01/diary.md](../../../backend/evals/diary_route_scenario/yangjae-01/diary.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-01/input.json](../../../backend/evals/diary_route_scenario/yangjae-01/input.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-01/map.json](../../../backend/evals/diary_route_scenario/yangjae-01/map.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-01/plan.json](../../../backend/evals/diary_route_scenario/yangjae-01/plan.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-01/preview.html](../../../backend/evals/diary_route_scenario/yangjae-01/preview.html) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-01/result.json](../../../backend/evals/diary_route_scenario/yangjae-01/result.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-01/run.json](../../../backend/evals/diary_route_scenario/yangjae-01/run.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-01/slots.json](../../../backend/evals/diary_route_scenario/yangjae-01/slots.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-02/backgrounds.json.gz](../../../backend/evals/diary_route_scenario/yangjae-02/backgrounds.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-02/base.json](../../../backend/evals/diary_route_scenario/yangjae-02/base.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-02/diary.md](../../../backend/evals/diary_route_scenario/yangjae-02/diary.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-02/input.json](../../../backend/evals/diary_route_scenario/yangjae-02/input.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-02/map.json](../../../backend/evals/diary_route_scenario/yangjae-02/map.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-02/plan.json](../../../backend/evals/diary_route_scenario/yangjae-02/plan.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-02/preview.html](../../../backend/evals/diary_route_scenario/yangjae-02/preview.html) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-02/result.json.gz](../../../backend/evals/diary_route_scenario/yangjae-02/result.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-02/run.json](../../../backend/evals/diary_route_scenario/yangjae-02/run.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_route_scenario/yangjae-02/slots.json.gz](../../../backend/evals/diary_route_scenario/yangjae-02/slots.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_slots/live-temperature-v1.json](../../../backend/evals/diary_slots/live-temperature-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_slots/review-fixes-v2.json](../../../backend/evals/diary_slots/review-fixes-v2.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/diary_slots/service-writer-v1.json](../../../backend/evals/diary_slots/service-writer-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk-diary/board-v1.json](../../../backend/evals/walk-diary/board-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk-diary/observation-card-v1.json](../../../backend/evals/walk-diary/observation-card-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk-diary/route-patterns-v1/geo-geometry-v1.json](../../../backend/evals/walk-diary/route-patterns-v1/geo-geometry-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk-diary/route-patterns-v1/phone-summary.json](../../../backend/evals/walk-diary/route-patterns-v1/phone-summary.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk-diary/space-board-v1.json](../../../backend/evals/walk-diary/space-board-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk-diary/space-policy-v1/input.json](../../../backend/evals/walk-diary/space-policy-v1/input.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk-diary/space-policy-v1/real-summary.json](../../../backend/evals/walk-diary/space-policy-v1/real-summary.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk-diary/space-policy-v1/summary.json](../../../backend/evals/walk-diary/space-policy-v1/summary.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk-diary/writing-boundary-records-v1.json.gz](../../../backend/evals/walk-diary/writing-boundary-records-v1.json.gz) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk-diary/writing-boundary-v1.json](../../../backend/evals/walk-diary/writing-boundary-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/README.md](../../../backend/evals/walk_diary_stamps/README.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/inputs/missing-route.json](../../../backend/evals/walk_diary_stamps/inputs/missing-route.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/inputs/mixed.json](../../../backend/evals/walk_diary_stamps/inputs/mixed.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/inputs/observations-only.json](../../../backend/evals/walk_diary_stamps/inputs/observations-only.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/inputs/records-enough.json](../../../backend/evals/walk_diary_stamps/inputs/records-enough.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/missing-route/bundle.json](../../../backend/evals/walk_diary_stamps/results/missing-route/bundle.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/missing-route/input.json](../../../backend/evals/walk_diary_stamps/results/missing-route/input.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/missing-route/prepared.json](../../../backend/evals/walk_diary_stamps/results/missing-route/prepared.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/missing-route/preview.md](../../../backend/evals/walk_diary_stamps/results/missing-route/preview.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/mixed/bundle.json](../../../backend/evals/walk_diary_stamps/results/mixed/bundle.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/mixed/input.json](../../../backend/evals/walk_diary_stamps/results/mixed/input.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/mixed/prepared.json](../../../backend/evals/walk_diary_stamps/results/mixed/prepared.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/mixed/preview.md](../../../backend/evals/walk_diary_stamps/results/mixed/preview.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/observations-only/bundle.json](../../../backend/evals/walk_diary_stamps/results/observations-only/bundle.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/observations-only/input.json](../../../backend/evals/walk_diary_stamps/results/observations-only/input.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/observations-only/prepared.json](../../../backend/evals/walk_diary_stamps/results/observations-only/prepared.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/observations-only/preview.md](../../../backend/evals/walk_diary_stamps/results/observations-only/preview.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/records-enough/bundle.json](../../../backend/evals/walk_diary_stamps/results/records-enough/bundle.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/records-enough/input.json](../../../backend/evals/walk_diary_stamps/results/records-enough/input.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/records-enough/prepared.json](../../../backend/evals/walk_diary_stamps/results/records-enough/prepared.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/results/records-enough/preview.md](../../../backend/evals/walk_diary_stamps/results/records-enough/preview.md) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/evals/walk_diary_stamps/selection-parity.json](../../../backend/evals/walk_diary_stamps/selection-parity.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/src/daengs_backend/cli/walk_runtime_check.py](../../../backend/src/daengs_backend/cli/walk_runtime_check.py) | cli | retain | 여러 영역을 검증하는 테스트·표본·평가·운영 점검 도구. MVC/작업자 계층 유지; 내부 계약/호출만 소유 영역과 일치시킴.  |
| [backend/src/daengs_evals/diary_slots_demo.py](../../../backend/src/daengs_evals/diary_slots_demo.py) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [backend/src/daengs_evals/walk_base_board.py](../../../backend/src/daengs_evals/walk_base_board.py) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [backend/tests/activity/test_activity.py](../../../backend/tests/activity/test_activity.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/activity/test_activity_db.py](../../../backend/tests/activity/test_activity_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/activity/test_walk_summary_queries_db.py](../../../backend/tests/activity/test_walk_summary_queries_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/fakes.py](../../../backend/tests/fakes.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/test_app_auth.py](../../../backend/tests/test_app_auth.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/test_assistant_care_log.py](../../../backend/tests/test_assistant_care_log.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/test_assistant_walk_activity.py](../../../backend/tests/test_assistant_walk_activity.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/test_care_events.py](../../../backend/tests/test_care_events.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/test_orchestration_adapters.py](../../../backend/tests/test_orchestration_adapters.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/test_pet_members.py](../../../backend/tests/test_pet_members.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/test_pet_membership_postgres.py](../../../backend/tests/test_pet_membership_postgres.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/api/conftest.py](../../../backend/tests/walk/api/conftest.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/api/test_motion_calculation.py](../../../backend/tests/walk/api/test_motion_calculation.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/api/test_session_package.py](../../../backend/tests/walk/api/test_session_package.py) | support | retain | 원본/백업의 봉인·일기 없는 독립 import, 순수 계약 의존 방향 및 9개 기존 경로의 공개 객체 동일성 검증.  |
| [backend/tests/walk/api/test_trajectory_calculation.py](../../../backend/tests/walk/api/test_trajectory_calculation.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/api/test_walk_api.py](../../../backend/tests/walk/api/test_walk_api.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/api/test_walk_auth.py](../../../backend/tests/walk/api/test_walk_auth.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/api/test_walk_chunk.py](../../../backend/tests/walk/api/test_walk_chunk.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/api/test_walk_finalize_db.py](../../../backend/tests/walk/api/test_walk_finalize_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/api/test_walk_motion_contract.py](../../../backend/tests/walk/api/test_walk_motion_contract.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/api/test_walk_recording.py](../../../backend/tests/walk/api/test_walk_recording.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/api/test_walk_repository.py](../../../backend/tests/walk/api/test_walk_repository.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/api/test_walk_upload_db.py](../../../backend/tests/walk/api/test_walk_upload_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/api/test_walk_upload_receipt_db.py](../../../backend/tests/walk/api/test_walk_upload_receipt_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/api/test_walks.py](../../../backend/tests/walk/api/test_walks.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/conftest.py](../../../backend/tests/walk/conftest.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/conftest.py](../../../backend/tests/walk/context/conftest.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_background_package.py](../../../backend/tests/walk/context/test_background_package.py) | support | retain | 배경 역방향 의존 차단, 공급자 단독 import, 13개 기존 경로의 동일 객체 호환 검증.  |
| [backend/tests/walk/context/test_provider_contract_boundaries.py](../../../backend/tests/walk/context/test_provider_contract_boundaries.py) | support | retain | 공급 경계 회귀 및 변경 전 390ddd0c의 JSON/schema/hash 고정 표본.  |
| [backend/tests/walk/context/test_provider_contract_compatibility.py](../../../backend/tests/walk/context/test_provider_contract_compatibility.py) | support | retain | 공급 경계 회귀 및 변경 전 390ddd0c의 JSON/schema/hash 고정 표본.  |
| [backend/tests/walk/context/test_walk_area_context.py](../../../backend/tests/walk/context/test_walk_area_context.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_backfill_commands.py](../../../backend/tests/walk/context/test_walk_backfill_commands.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_catalog_demand_db.py](../../../backend/tests/walk/context/test_walk_catalog_demand_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_catalog_redis.py](../../../backend/tests/walk/context/test_walk_catalog_redis.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_catalog_refresh.py](../../../backend/tests/walk/context/test_walk_catalog_refresh.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_context_backfill_db.py](../../../backend/tests/walk/context/test_walk_context_backfill_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_entry_context.py](../../../backend/tests/walk/context/test_walk_entry_context.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_entry_context_db.py](../../../backend/tests/walk/context/test_walk_entry_context_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_entry_pin_context.py](../../../backend/tests/walk/context/test_walk_entry_pin_context.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_public_context.py](../../../backend/tests/walk/context/test_walk_public_context.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_runtime_check.py](../../../backend/tests/walk/context/test_walk_runtime_check.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_runtime_commands.py](../../../backend/tests/walk/context/test_walk_runtime_commands.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_runtime_db.py](../../../backend/tests/walk/context/test_walk_runtime_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_runtime_smoke.py](../../../backend/tests/walk/context/test_walk_runtime_smoke.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/context/test_walk_weather_context.py](../../../backend/tests/walk/context/test_walk_weather_context.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/conftest.py](../../../backend/tests/walk/diary/conftest.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_activity.py](../../../backend/tests/walk/diary/test_diary_activity.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_base_assembly.py](../../../backend/tests/walk/diary/test_diary_base_assembly.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_base_selection.py](../../../backend/tests/walk/diary/test_diary_base_selection.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_board_api.py](../../../backend/tests/walk/diary/test_diary_board_api.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_board_contract.py](../../../backend/tests/walk/diary/test_diary_board_contract.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_board_db.py](../../../backend/tests/walk/diary/test_diary_board_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_board_receipt.py](../../../backend/tests/walk/diary/test_diary_board_receipt.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_board_slot_writing.py](../../../backend/tests/walk/diary/test_diary_board_slot_writing.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_board_slots.py](../../../backend/tests/walk/diary/test_diary_board_slots.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_card_writing.py](../../../backend/tests/walk/diary/test_diary_card_writing.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_collection_db.py](../../../backend/tests/walk/diary/test_diary_collection_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_collection_progress.py](../../../backend/tests/walk/diary/test_diary_collection_progress.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_context_entry.py](../../../backend/tests/walk/diary/test_diary_context_entry.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_contract.py](../../../backend/tests/walk/diary/test_diary_contract.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_domain_package.py](../../../backend/tests/walk/diary/test_diary_domain_package.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_generation.py](../../../backend/tests/walk/diary/test_diary_generation.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_generation_db.py](../../../backend/tests/walk/diary/test_diary_generation_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_llm.py](../../../backend/tests/walk/diary/test_diary_llm.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_observation_content.py](../../../backend/tests/walk/diary/test_diary_observation_content.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_observations.py](../../../backend/tests/walk/diary/test_diary_observations.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_public_background.py](../../../backend/tests/walk/diary/test_diary_public_background.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_publication.py](../../../backend/tests/walk/diary/test_diary_publication.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_retention.py](../../../backend/tests/walk/diary/test_diary_retention.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_retention_db.py](../../../backend/tests/walk/diary/test_diary_retention_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_route_integration.py](../../../backend/tests/walk/diary/test_diary_route_integration.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_route_patterns.py](../../../backend/tests/walk/diary/test_diary_route_patterns.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_scene_comparison.py](../../../backend/tests/walk/diary/test_diary_scene_comparison.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_scene_context.py](../../../backend/tests/walk/diary/test_diary_scene_context.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_scene_writing.py](../../../backend/tests/walk/diary/test_diary_scene_writing.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_service_package.py](../../../backend/tests/walk/diary/test_diary_service_package.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_slot_claims.py](../../../backend/tests/walk/diary/test_diary_slot_claims.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_slots.py](../../../backend/tests/walk/diary/test_diary_slots.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_slots_api.py](../../../backend/tests/walk/diary/test_diary_slots_api.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_space_integration.py](../../../backend/tests/walk/diary/test_diary_space_integration.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_space_materials.py](../../../backend/tests/walk/diary/test_diary_space_materials.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_space_memory.py](../../../backend/tests/walk/diary/test_diary_space_memory.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_stamps.py](../../../backend/tests/walk/diary/test_diary_stamps.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_temperature.py](../../../backend/tests/walk/diary/test_diary_temperature.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_temperature_db.py](../../../backend/tests/walk/diary/test_diary_temperature_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_title_cache.py](../../../backend/tests/walk/diary/test_diary_title_cache.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_writer_entrypoints.py](../../../backend/tests/walk/diary/test_diary_writer_entrypoints.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_writing.py](../../../backend/tests/walk/diary/test_diary_writing.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_diary_writing_boundaries.py](../../../backend/tests/walk/diary/test_diary_writing_boundaries.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_generation_contract_boundary.py](../../../backend/tests/walk/diary/test_generation_contract_boundary.py) | support | retain | 생성 계약 경계 회귀 및 변경 전 4d7407f6의 HTTP schema 고정 표본.  |
| [backend/tests/walk/diary/test_route_calculation_boundary.py](../../../backend/tests/walk/diary/test_route_calculation_boundary.py) | support | retain | 경로 경계 회귀 및 변경 전 d9b1b2da의 관측/장면/이동/과거 선정 고정 표본.  |
| [backend/tests/walk/diary/test_spatial_diary.py](../../../backend/tests/walk/diary/test_spatial_diary.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/diary/test_spatial_diary_query.py](../../../backend/tests/walk/diary/test_spatial_diary_query.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/entries/conftest.py](../../../backend/tests/walk/entries/conftest.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/entries/test_records_package.py](../../../backend/tests/walk/entries/test_records_package.py) | support | retain | 기록/사진 import 방향·독립 로딩·9개 기존 경로의 공개 객체 동일성 및 기본 수집기 계약 검증.  |
| [backend/tests/walk/entries/test_walk_entries.py](../../../backend/tests/walk/entries/test_walk_entries.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/entries/test_walk_entry_context_atomicity_db.py](../../../backend/tests/walk/entries/test_walk_entry_context_atomicity_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/entries/test_walk_entry_http.py](../../../backend/tests/walk/entries/test_walk_entry_http.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/entries/test_walk_entry_v2.py](../../../backend/tests/walk/entries/test_walk_entry_v2.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/entries/test_walk_entry_v2_db.py](../../../backend/tests/walk/entries/test_walk_entry_v2_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/environment/test_walk_observation.py](../../../backend/tests/walk/environment/test_walk_observation.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/environment/test_walk_weather_adapter.py](../../../backend/tests/walk/environment/test_walk_weather_adapter.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/MotionReplayFixtureExportTest.kt](../../../backend/tests/walk/fixtures/MotionReplayFixtureExportTest.kt) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/finalize-promotion-v1.json](../../../backend/tests/walk/fixtures/finalize-promotion-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/generation-schema-v1.json](../../../backend/tests/walk/fixtures/generation-schema-v1.json) | support | retain | 생성 계약 경계 회귀 및 변경 전 4d7407f6의 HTTP schema 고정 표본.  |
| [backend/tests/walk/fixtures/gps-motion-backup-v1.json](../../../backend/tests/walk/fixtures/gps-motion-backup-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/gps-motion-precision-v1.json](../../../backend/tests/walk/fixtures/gps-motion-precision-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/gps-motion-replay-v1.json](../../../backend/tests/walk/fixtures/gps-motion-replay-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/gps-recording-v1.json](../../../backend/tests/walk/fixtures/gps-recording-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/hex-grid-golden.json](../../../backend/tests/walk/fixtures/hex-grid-golden.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/measurement-projection-v1.json](../../../backend/tests/walk/fixtures/measurement-projection-v1.json) | support | retain | 측정 투영 경계 회귀 및 변경 전 018a9ce7의 조회/저장 바이트·ID 고정 표본.  |
| [backend/tests/walk/fixtures/provider-contracts-v1.json](../../../backend/tests/walk/fixtures/provider-contracts-v1.json) | support | retain | 공급 경계 회귀 및 변경 전 390ddd0c의 JSON/schema/hash 고정 표본.  |
| [backend/tests/walk/fixtures/route-boundary-v1.json](../../../backend/tests/walk/fixtures/route-boundary-v1.json) | support | retain | 경로 경계 회귀 및 변경 전 d9b1b2da의 관측/장면/이동/과거 선정 고정 표본.  |
| [backend/tests/walk/fixtures/sealed-artifacts-v1.json](../../../backend/tests/walk/fixtures/sealed-artifacts-v1.json) | support | retain | 봉인 산출물/공개 import 경계 회귀 및 변경 전 284695e1의 저장 payload 고정 표본.  |
| [backend/tests/walk/fixtures/spatial-diary-view-promotion-v1.json](../../../backend/tests/walk/fixtures/spatial-diary-view-promotion-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/trajectory-contract-v1.json](../../../backend/tests/walk/fixtures/trajectory-contract-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/v2-after.json](../../../backend/tests/walk/fixtures/v2-after.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/v2-before.json](../../../backend/tests/walk/fixtures/v2-before.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/v2-budget.json](../../../backend/tests/walk/fixtures/v2-budget.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/v2-short.json](../../../backend/tests/walk/fixtures/v2-short.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/v4-observations.json](../../../backend/tests/walk/fixtures/v4-observations.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/walk-measurement-long-input-v1.json](../../../backend/tests/walk/fixtures/walk-measurement-long-input-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/walk-measurement-long-v1.json](../../../backend/tests/walk/fixtures/walk-measurement-long-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/fixtures/walk-measurement-v1.json](../../../backend/tests/walk/fixtures/walk-measurement-v1.json) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_artifact_boundaries.py](../../../backend/tests/walk/measurement/test_artifact_boundaries.py) | support | retain | 봉인 산출물/공개 import 경계 회귀 및 변경 전 284695e1의 저장 payload 고정 표본.  |
| [backend/tests/walk/measurement/test_cellophane.py](../../../backend/tests/walk/measurement/test_cellophane.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_finalize_contract.py](../../../backend/tests/walk/measurement/test_finalize_contract.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_hex_grid.py](../../../backend/tests/walk/measurement/test_hex_grid.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_motion_precision.py](../../../backend/tests/walk/measurement/test_motion_precision.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_motion_replay.py](../../../backend/tests/walk/measurement/test_motion_replay.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_owner_packages.py](../../../backend/tests/walk/measurement/test_owner_packages.py) | support | retain | 측정/조회 독립 로딩·옛 공개 객체 동일성·제품의 호환 경로 역참조 방지.  |
| [backend/tests/walk/measurement/test_projection_boundary.py](../../../backend/tests/walk/measurement/test_projection_boundary.py) | support | retain | 측정 투영 경계 회귀 및 변경 전 018a9ce7의 조회/저장 바이트·ID 고정 표본.  |
| [backend/tests/walk/measurement/test_stored_measurement.py](../../../backend/tests/walk/measurement/test_stored_measurement.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_trajectory.py](../../../backend/tests/walk/measurement/test_trajectory.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_trajectory_shadow.py](../../../backend/tests/walk/measurement/test_trajectory_shadow.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_trajectory_view.py](../../../backend/tests/walk/measurement/test_trajectory_view.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_walk_analysis_storage.py](../../../backend/tests/walk/measurement/test_walk_analysis_storage.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_walk_capsule.py](../../../backend/tests/walk/measurement/test_walk_capsule.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_walk_facts.py](../../../backend/tests/walk/measurement/test_walk_facts.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_walk_measurement.py](../../../backend/tests/walk/measurement/test_walk_measurement.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/test_walk_style.py](../../../backend/tests/walk/measurement/test_walk_style.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/measurement/trajectory_support.py](../../../backend/tests/walk/measurement/trajectory_support.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/photos/conftest.py](../../../backend/tests/walk/photos/conftest.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/photos/test_walk_photo_db.py](../../../backend/tests/walk/photos/test_walk_photo_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/photos/test_walk_photo_input.py](../../../backend/tests/walk/photos/test_walk_photo_input.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/storyboard/conftest.py](../../../backend/tests/walk/storyboard/conftest.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/storyboard/test_storyboard_observations.py](../../../backend/tests/walk/storyboard/test_storyboard_observations.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/storyboard/test_storyboard_pins.py](../../../backend/tests/walk/storyboard/test_storyboard_pins.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/storyboard/test_walk_storyboard.py](../../../backend/tests/walk/storyboard/test_walk_storyboard.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/storyboard/test_walk_storyboard_db.py](../../../backend/tests/walk/storyboard/test_walk_storyboard_db.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/storyboard/test_walk_storyboard_titles.py](../../../backend/tests/walk/storyboard/test_walk_storyboard_titles.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/base_board.py](../../../backend/tests/walk/support/base_board.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/board_contract.py](../../../backend/tests/walk/support/board_contract.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/diary.py](../../../backend/tests/walk/support/diary.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/diary_generation.py](../../../backend/tests/walk/support/diary_generation.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/entry_context.py](../../../backend/tests/walk/support/entry_context.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/entry_v2.py](../../../backend/tests/walk/support/entry_v2.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/observations.py](../../../backend/tests/walk/support/observations.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/paths.py](../../../backend/tests/walk/support/paths.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/photo_database.py](../../../backend/tests/walk/support/photo_database.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/photo_input.py](../../../backend/tests/walk/support/photo_input.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/pin_database.py](../../../backend/tests/walk/support/pin_database.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/route_contracts.py](../../../backend/tests/walk/support/route_contracts.py) | support | retain | 경로 경계 회귀 및 변경 전 d9b1b2da의 관측/장면/이동/과거 선정 고정 표본.  |
| [backend/tests/walk/support/route_patterns.py](../../../backend/tests/walk/support/route_patterns.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/storyboard.py](../../../backend/tests/walk/support/storyboard.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/support/writing_boundary.py](../../../backend/tests/walk/support/writing_boundary.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tests/walk/test_package_boundary.py](../../../backend/tests/walk/test_package_boundary.py) | support | retain | 검증/표본 소유. 제품 경계 이동 시 import·golden 의미를 함께 점검; 이번 단계에서는 실행하지 않음.  |
| [backend/tools/check_walk_motion_backup.py](../../../backend/tools/check_walk_motion_backup.py) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [backend/tools/check_walk_trajectory_shadow.py](../../../backend/tools/check_walk_trajectory_shadow.py) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [backend/tools/compare_diary_scenes.py](../../../backend/tools/compare_diary_scenes.py) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [backend/tools/diary_route_scenario.html](../../../backend/tools/diary_route_scenario.html) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [backend/tools/preview_diary_stamps.py](../../../backend/tools/preview_diary_stamps.py) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [backend/tools/replay_diary_activity.py](../../../backend/tools/replay_diary_activity.py) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [backend/tools/run_diary_final_titles.py](../../../backend/tools/run_diary_final_titles.py) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [backend/tools/run_diary_public_scenario.py](../../../backend/tools/run_diary_public_scenario.py) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [backend/tools/run_diary_route_scenario.py](../../../backend/tools/run_diary_route_scenario.py) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [backend/tools/run_diary_slots.py](../../../backend/tools/run_diary_slots.py) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [tools/inspect_walk_runtime.py](../../../tools/inspect_walk_runtime.py) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [tools/walk-context-backfill.ps1](../../../tools/walk-context-backfill.ps1) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [tools/walk-diary-runtime.ps1](../../../tools/walk-diary-runtime.ps1) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
| [tools/walk_runtime_smoke.py](../../../tools/walk_runtime_smoke.py) | support | retain | 명시 실행 도구/평가 접점. 새 진입점 전환 대상이며 소유 제품의 실행기 내부로 합치지 않음.  |
