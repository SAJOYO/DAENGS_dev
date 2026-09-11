# 일기 스탬프 준비 결과

입력 출처: `mock`. LLM 호출 없음. 제목은 시스템 기본값.

사용자 기록 3개 / 보충 2개 / 목표 5개 / 남은 부족분 0개.

| 순서 | 한국 시각 | 중심 기록/관측 | 위치 방법 | 배경 조각 |
| --- | --- | --- | --- | --- |
| 1 | 2026-09-09T09:05:00+09:00 | 행동 기록: sniffing | last_known | 합성 카페 A: 35m (등록 위치까지), 합성 카페 B: 90m (등록 위치까지), 합성 카페 C: 140m (등록 위치까지) |
| 2 | 2026-09-09T09:12:00+09:00 | 기기 관측: observed_dwell | observed | 없음 |
| 3 | 2026-09-09T09:18:00+09:00 |   사진 대신 글로 남긴 합성 기록.<br>원문 공백도 유지한다.   | last_known | 합성 카페 A: 35m (등록 위치까지), 합성 카페 B: 90m (등록 위치까지), 합성 카페 C: 140m (등록 위치까지) |
| 4 | 2026-09-09T09:25:00+09:00 | 사진 기록 | last_known | 없음 |
| 5 | 2026-09-09T09:32:00+09:00 | 기기 관측: observed_slow | observed | 없음 |

## 중심 선택

| 재료 참조 | 결정 |
| --- | --- |
| walk_entry:0a90be84-69a1-5c37-b0e5-9310d72ce8ba | user_record |
| walk_entry:d2c76343-5770-5e19-ab1a-ffd8ca618731 | user_record |
| walk_photo:eb92770e-481d-542e-9588-08aa222e5807 | user_record |
| observation:dwell | fill_scene_deficit |
| observation:near-action | near_user_record_time |
| observation:overlap | overlapping_selected_observation |
| observation:slow | fill_scene_deficit |

## 배경 선택

| 봉투 | 조각 | 결정 |
| --- | --- | --- |
| 3264395d-8777-5cd7-8d8f-9ae07212e6db | — | source_not_requested |
| f58d675a-3226-5208-99fd-43c826b5d47b | piece:a54dba96edf2e74af5899b8701a91c910ef819c67245249210cd6680b6a9ccf0 | admit |
| f58d675a-3226-5208-99fd-43c826b5d47b | piece:6e34011be2a58eba6b7578bb88c87cefb141fec2af402b89975e9c49b225c20a | admit |
| f58d675a-3226-5208-99fd-43c826b5d47b | piece:4ddcb960c59a0e3f2b5918e72629352ab2ed0fe6727535d3966eb770bcf61257 | admit |
| f58d675a-3226-5208-99fd-43c826b5d47b | piece:b7e1c3654c79178bbd0957b780de9e48a3ca0a28bb4acb6933a492d99eac60f3 | slot_capacity |
| 149ef508-1eae-582d-ab8f-1d52dc5ab595 | piece:a22135977fda7b7ce19964418d384954c91d388f437463902251c32202343175 | admit |
| 149ef508-1eae-582d-ab8f-1d52dc5ab595 | piece:a8a7d550be75c2d7bb4200f358337b0890b7f71b66e92d8a1a7747840dd03d35 | admit |
| 149ef508-1eae-582d-ab8f-1d52dc5ab595 | piece:d6600af3b26c50bb77ffcaf23be2896c01e9858725e626dbd03d5ab768ac00b3 | admit |
| 149ef508-1eae-582d-ab8f-1d52dc5ab595 | piece:4b80d43592463431bf9b5ec3680e42cba22b325191470bb1e156f9ab832c06c5 | slot_capacity |

제약: 추가 상태 없음

원문·위치·근거는 input.json, 선택 정책과 전체 사유는 prepared.json에 보존된다.
