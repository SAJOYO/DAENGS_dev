FROM postgis/postgis:18-3.6

# 팀 공용 DB와 같은 pgvector 세대를 설치한다. PostGIS와 PostgreSQL은
# 베이스 이미지가 제공하고, vector 확장 바이너리만 PGDG에서 추가한다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-18-pgvector=0.8.6-1.pgdg13+1 \
    && rm -rf /var/lib/apt/lists/*

# 베이스 이미지의 10_postgis.sh 뒤에 실행된다. init 디렉터리를 호스트
# 볼륨으로 가리지 않고 파일만 이미지에 더해 PostGIS 초기화도 그대로 보존한다.
COPY 20_vector.sql /docker-entrypoint-initdb.d/20_vector.sql
