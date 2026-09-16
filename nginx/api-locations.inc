# API location 블록 모음 — gcp.conf 전용 include 입니다.
#
# 같은 내용이 두 server 블록(443 daengback / 8000 호스트 내부용)에 필요해서
# 여기로 뺐습니다. **각 블록의 논리와 함정 주석의 원본은 nginx/default.conf 입니다** —
# set 이 rewrite 보다 먼저, 접두사 유지/제거 규칙, 타임아웃 근거 전부 그쪽을 보세요.
# 여기를 고치면 default.conf 의 8000 블록도 같이 맞춰야 합니다.
# **반대도 마찬가지입니다** — default.conf 에 API 경로를 더하고 여기에 안 더하면
# 로컬 서버만 멀쩡하고 GCP 에서만 깨집니다. 배포 전에는 안 보이는 종류입니다 (#150).
#
# 확장자가 .conf 가 아닌 이유: conf.d/*.conf 는 http 컨텍스트로 자동 include 되는데
# 이 파일은 server 컨텍스트용이라 자동으로 걸리면 기동이 깨집니다.

# 피부 병변 스크리닝 — 접두사를 떼지 않습니다 (D-039·D-040, 라우터 prefix="/screen")
location /screen/ {
    set $screening_upstream backend:8000;
    proxy_pass http://$screening_upstream;
    proxy_http_version 1.1;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_connect_timeout   5s;
    proxy_read_timeout    120s;
}

# 보행 영상 분석 — **옛 무인증 경로. 닫혀 있습니다** (#64 · #145). 근거 주석의 원본은
# default.conf 이고, 여기는 클라우드가 읽는 사본입니다.
#
# ⚠️ **여기를 열어 두면 집 서버만 닫히고 클라우드는 그대로 공개됩니다.** 실제로 그럴 뻔했고
#    (#145 최초안이 default.conf 만 닫았습니다), 반대 방향의 같은 사고가 #150 이었습니다.
#    이 파일과 default.conf 의 location 목록은 **항상 같아야 합니다.**
location /gait/ {
    default_type application/json;
    return 410 '{"detail":"이 주소는 더 이상 쓰지 않습니다. 앱을 최신 버전으로 업데이트해 주세요."}';
}

# Place 검색 — 경로 변환 없음 (D-027)
location /v2/places/ {
    limit_req zone=place_search_per_ip burst=10 nodelay;
    limit_req_status 429;

    set $place_search_upstream place-search:8000;
    proxy_pass http://$place_search_upstream;
    proxy_http_version 1.1;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_connect_timeout 5s;
    proxy_read_timeout    60s;
}

# 점령 게임판 읽기 — 경로 변환 없이 place-search로 전달합니다.
location /territory/sites/ {
    limit_req zone=place_search_per_ip burst=10 nodelay;
    limit_req_status 429;

    set $place_search_upstream place-search:8000;
    proxy_pass http://$place_search_upstream;
    proxy_http_version 1.1;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_connect_timeout 5s;
    proxy_read_timeout    60s;
}

# 장소 선택 뒤 단발 이동 스냅샷 (D-039)
location = /journey {
    set $journey_upstream journey-service:8000;
    proxy_pass http://$journey_upstream;
    proxy_http_version 1.1;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_connect_timeout 5s;
    proxy_read_timeout    30s;
}

# 관계 일기 — 앱이 직접 부르는 경로. 접두사를 떼지 않습니다. 근거 주석의 원본은 default.conf.
# 기본 상한: 64회 × 15s + 63간격 × 10s + 처리 15s + 수집 12s + 저장 15s.
#
# ⚠️ #559 까지가 default.conf 에만 넣어 여기서 빠져 있었습니다 (#561). 이 파일 머리의 #150
#    경고가 말하는 모양 그대로입니다 — 아래 `location /` 의 60s 로 떨어져 **개발서버만 멀쩡하고
#    GCP 에서만** 깨집니다. 지금은 `DAENGS_WALK_DIARY_ENABLED` 가 꺼져 있어 긴 경로가 404 라
#    드러나지 않지만, 켜는 순간 504 가 됩니다.
location ~ ^/app/walks/[0-9a-fA-F-]+/storyboard$ {
    set $fastapi_upstream backend:8000;
    proxy_pass http://$fastapi_upstream;
    proxy_http_version 1.1;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_connect_timeout    5s;
    proxy_read_timeout    1650s;
}

# 보행 분석 **새 흐름** — 접두사를 떼지 않고 backend 로 넘깁니다 (D-043). 위 `/gait/`
# (옛 무인증 직접 HTTP)와는 다른 경로·다른 서비스입니다. 근거 주석의 원본은 default.conf.
#
# ⚠️ 이 블록이 빠지면 **절반만 조용히 깨집니다** — 목적지가 아래 `location /` 과 같은
#    backend 라 analyze·confirm·records(작은 JSON)는 멀쩡하고, GAIT_STORAGE=local 의
#    영상 업로드(`PUT /app/gait/_bridge/upload/...`, 표본 5~120MB)만 server 블록 기본값
#    20m 에 걸려 413 이 됩니다. 실제로 #133 이후 2026-09-02 까지 그 상태였습니다 (#150).
location /app/gait/ {
    set $fastapi_upstream backend:8000;
    proxy_pass http://$fastapi_upstream;
    proxy_http_version 1.1;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # /gait/ 과 같은 이유 — 영상 업로드라 20m 로는 413.
    client_max_body_size 200m;

    proxy_connect_timeout   5s;
    proxy_send_timeout    600s;
    proxy_read_timeout    600s;
}

# 기본 API — backend 로 그대로
location / {
    set $fastapi_upstream backend:8000;
    proxy_pass http://$fastapi_upstream;
    proxy_http_version 1.1;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_connect_timeout 5s;
    proxy_read_timeout    60s;
}
