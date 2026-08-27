"""생활비서 RAG(①)와 실시간 산책(②) — `choiyc05/daengs-life` 에서 이관.

안의 다섯은 **형제**다. `crawler` 가 아래, 나머지가 위이고 그 방향을
`tests/test_import_direction*.py` 가 강제한다. 한 겹으로 묶은 이유는
그 형제 관계를 그대로 보존하기 위해서다 — 경계선을 다시 긋는 것은
`daengs_backend` 와의 규약 정합(D-018 의 '남은 것')에서 한다.

`daengs_backend` 는 이 패키지를 아직 import 하지 않는다. 서빙(`app/`)을
돌고 있는 앱에 붙이면 lifespan 이 임베딩 모델을 상주시켜, 배포되는
API 프로세스가 모델 로드분을 같이 문다 (RAG-028 ①).
"""
