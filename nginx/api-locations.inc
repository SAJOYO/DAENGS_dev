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

# 보행 영상 분석 — /gait 접두사를 뗍니다 (컨테이너는 /analyze 로 받음, D-038)
location /gait/ {
    set $gait_upstream gait-analysis:8000;
    rewrite ^/gait/(.*)$ /$1 break;

    proxy_pass http://$gait_upstream;
    proxy_http_version 1.1;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # 영상 업로드(실측 5~60MB) + 분 단위 추론 — default.conf 의 근거 주석 참고
    client_max_body_size 200m;
    proxy_connect_timeout   5s;
    proxy_send_timeout    600s;
    proxy_read_timeout    600s;
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
