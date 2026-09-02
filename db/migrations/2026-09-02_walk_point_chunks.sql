-- 2026-09-02_walk_point_chunks.sql
-- 산책 좌표를 점마다 한 줄에서 **묶음당 한 줄**로 옮긴다.
-- db/init/06_walks.sql 과 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다.** 이 저장소는 버전 테이블이 없어서 DB 가 적용 여부를
-- 기억하지 않는다 — db/migrations/README.md 참고.
--
-- 왜: walk_points 가 좌표 한 점에 한 줄이었다. 실기기 실측으로 초당 1.02점이 쌓여
-- 30분 산책이면 1,842줄이다. 그런데 이 좌표를 조건으로 거는 질의가 하나도 없다 —
-- 늘 "한 산책의 전부"를 통째로 읽어 JSON 으로 내보낼 뿐이다. 점당 실제 데이터는
-- 12바이트쯤인데 행 하나에 124바이트를 내고 있었다(90%가 행 헤더).
--
--   실측(131점 트랙) : 점당 한 줄 124 B → jsonb 배열 22 B  (5.6배)
--
-- **기존 행을 옮긴다.** 지금 서버에 132점(테스트 산책 2건)뿐이라 이전 비용이 사실상
-- 없지만, 옮기는 코드는 행이 많아도 같게 동작한다.

BEGIN;

CREATE TABLE IF NOT EXISTS walk_point_chunks (
    walk_id UUID NOT NULL REFERENCES walks(id) ON DELETE CASCADE,
    seq_from INTEGER NOT NULL,
    seq_to INTEGER NOT NULL,
    point_count INTEGER NOT NULL CHECK (point_count > 0),
    payload JSONB NOT NULL,
    CONSTRAINT walk_point_chunks_seq_order CHECK (seq_to >= seq_from),
    PRIMARY KEY (walk_id, seq_from)
);

COMMENT ON TABLE  walk_point_chunks            IS '기기가 준 원본 좌표를 묶음으로. 보관 기간은 탈퇴 시까지 (CASCADE)';
COMMENT ON COLUMN walk_point_chunks.seq_from   IS '묶음의 첫 client_seq. PK 의 일부 — 같은 묶음을 다시 보내도 한 줄';
COMMENT ON COLUMN walk_point_chunks.seq_to     IS '묶음의 마지막 client_seq. payload 를 풀지 않고 재시도를 판정하려고 둔다';
COMMENT ON COLUMN walk_point_chunks.point_count IS '묶음 안의 점 개수. 세려고 payload 를 풀지 않게 한다';
COMMENT ON COLUMN walk_point_chunks.payload    IS '{"v":1,"cols":["seq","chain","at","lat","lng","acc","mock"],"pts":[[...]]}. at 은 epoch ms, mock 은 0/1';

-- 있던 좌표를 한 산책당 한 묶음으로 옮긴다.
--
-- **walk_points 가 없으면 조용히 넘어간다** — 새 DB 에서는 db/init 이 이미 새 표를
-- 만들었고 옛 표가 아예 없다. to_regclass 가 그때 NULL 이다.
DO $$
BEGIN
    IF to_regclass('public.walk_points') IS NULL THEN
        RAISE NOTICE 'walk_points 가 없습니다 — 옮길 것이 없어 건너뜁니다.';
        RETURN;
    END IF;

    INSERT INTO walk_point_chunks (walk_id, seq_from, seq_to, point_count, payload)
    SELECT
        p.walk_id,
        MIN(p.client_seq),
        MAX(p.client_seq),
        COUNT(*),
        jsonb_build_object(
            'v', 1,
            'cols', jsonb_build_array('seq', 'chain', 'at', 'lat', 'lng', 'acc', 'mock'),
            'pts', jsonb_agg(
                jsonb_build_array(
                    p.client_seq,
                    p.chain_index,
                    -- epoch 밀리초. 앱이 밀리초로 보내고 TIMESTAMPTZ 가 그대로 담고 있다.
                    (EXTRACT(EPOCH FROM p.at) * 1000)::BIGINT,
                    p.lat,
                    p.lng,
                    p.accuracy_m,
                    CASE WHEN p.is_mock THEN 1 ELSE 0 END
                )
                ORDER BY p.client_seq
            )
        )
    FROM walk_points p
    GROUP BY p.walk_id
    ON CONFLICT (walk_id, seq_from) DO NOTHING;

    DROP TABLE walk_points;
END $$;

COMMIT;
