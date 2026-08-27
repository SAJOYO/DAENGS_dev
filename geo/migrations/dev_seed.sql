-- 개발용 시드. 강남역(37.4979,127.0276) 주변 가상 데이터. 운영 적재 아님. 재실행 안전.
INSERT INTO place (kind,name,address,phone,location,is_night,is_24h,hours,tags,area_m2,staff_count,source,source_id) VALUES
('hospital','강남24시동물병원','서울 강남구 테헤란로 1','02-000-0001', ST_SetSRID(ST_MakePoint(127.0290,37.4990),4326)::geography, true, true, NULL, '{24h}', 320, 8,'dev','1'),
('hospital','역삼동물병원','서울 강남구 역삼로 10','02-000-0002',   ST_SetSRID(ST_MakePoint(127.0350,37.5000),4326)::geography, false,false,'{"tz":"Asia/Seoul","weekly":{"0":[["09:00","19:00"]],"1":[["09:00","19:00"]],"2":[["09:00","19:00"]],"3":[["09:00","19:00"]],"4":[["09:00","19:00"]],"5":[["09:00","14:00"]]}}', '{}', 95, 2,'dev','2'),
('hospital','서초야간동물병원','서울 서초구 서초대로 5','02-000-0003', ST_SetSRID(ST_MakePoint(127.0200,37.4930),4326)::geography, true, false,'{"tz":"Asia/Seoul","weekly":{"0":[["18:00","02:00"]],"1":[["18:00","02:00"]],"2":[["18:00","02:00"]],"3":[["18:00","02:00"]],"4":[["18:00","02:00"]],"5":[["18:00","02:00"]],"6":[["18:00","02:00"]]}}', '{}', 140, 3,'dev','3'),
('pharmacy','댕댕동물약국','서울 강남구 강남대로 3','02-000-0004',  ST_SetSRID(ST_MakePoint(127.0270,37.4965),4326)::geography, false,false,'{"tz":"Asia/Seoul","weekly":{"0":[["10:00","20:00"]],"1":[["10:00","20:00"]],"2":[["10:00","20:00"]],"3":[["10:00","20:00"]],"4":[["10:00","20:00"]],"5":[["10:00","15:00"]]}}', '{}', NULL, NULL,'dev','4'),
('hospital','멀리있는동물병원','서울 송파구 올림픽로 300','02-000-0005', ST_SetSRID(ST_MakePoint(127.1000,37.5150),4326)::geography, false,false,NULL, '{}', NULL, NULL,'dev','5'),
('hospital','논현동물의료센터','서울 강남구 학동로 29길 5','02-000-0006', ST_SetSRID(ST_MakePoint(127.0316,37.5145),4326)::geography, true, true, NULL, '{24h,center,secondary,ortho,eye,cardio,rehab}', 800, 15,'dev','6'),
('hospital','서초동물정형외과','서울 서초구 서초중앙로 20','02-000-0007', ST_SetSRID(ST_MakePoint(127.0150,37.4890),4326)::geography, false,false,'{"tz":"Asia/Seoul","weekly":{"0":[["10:00","19:00"]],"1":[["10:00","19:00"]],"2":[["10:00","19:00"]],"3":[["10:00","19:00"]],"4":[["10:00","19:00"]],"5":[["10:00","14:00"]]}}', '{surgery,ortho}', 210, 4,'dev','7'),
('hospital','강남고양이전문병원','서울 강남구 봉은사로 50','02-000-0008', ST_SetSRID(ST_MakePoint(127.0300,37.5050),4326)::geography, false,false,NULL, '{cat_only}', 120, 2,'dev','8'),
('hospital','역삼동물안과','서울 강남구 논현로 80길 3','02-000-0009', ST_SetSRID(ST_MakePoint(127.0380,37.5030),4326)::geography, false,false,'{"tz":"Asia/Seoul","weekly":{"1":[["10:00","18:00"]],"2":[["10:00","18:00"]],"3":[["10:00","18:00"]],"4":[["10:00","18:00"]],"5":[["10:00","18:00"]]}}', '{eye}', 160, 3,'dev','9')
ON CONFLICT (source, source_id) DO UPDATE SET
  name=EXCLUDED.name, location=EXCLUDED.location, is_night=EXCLUDED.is_night, is_24h=EXCLUDED.is_24h,
  hours=EXCLUDED.hours, tags=EXCLUDED.tags, area_m2=EXCLUDED.area_m2, staff_count=EXCLUDED.staff_count;
