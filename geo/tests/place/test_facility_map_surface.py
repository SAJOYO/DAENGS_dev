"""`/facility-map`이 legacy 차집합이 아니라 canonical Place 계약을 소비한다."""

from pathlib import Path

HTML = (
    Path(__file__).resolve().parents[2] / "app" / "static" / "facility.html"
).read_text(encoding="utf-8")


def test_facility_map_uses_one_canonical_post_contract():
    assert "fetch('/v2/places/search'" in HTML
    assert "method:'POST'" in HTML
    assert "'/facility/search" not in HTML
    assert "Promise.all" not in HTML


def test_facility_map_reads_place_hit_without_internal_database_ids():
    assert "const f = hit.place" in HTML
    assert "hit.evaluations.dog_access" in HTML
    assert "f.facts.pet_access" in HTML
    assert "f.key.source" in HTML
    assert "okIds" not in HTML


def test_facility_map_keeps_all_three_evaluation_states_visible():
    assert "const counts = {compatible:0, incompatible:0, unknown:0}" in HTML
    assert "입장 조건상 가능" in HTML
    assert "조건 불일치" in HTML
    assert "정보 부족 · 확인 필요" in HTML
    assert "미만/이하 미확인" in HTML
    assert "≤" not in HTML


def test_facility_map_does_not_present_unevaluated_places_as_compatible():
    assert "장소 (평가 없음)" in HTML
    assert "setEvaluationLegend(Boolean(dog))" in HTML
    assert "hasDog ? '입장 조건상 가능' : '장소 (평가 없음)'" in HTML
    assert "lg-incompatible-row" in HTML
    assert "lg-unknown-row" in HTML


def test_facility_map_shows_the_sources_of_borrowed_facts_it_displays():
    assert "const fields = f.field_sources || {}" in HTML
    assert "fields['facts.pet_access']" in HTML
    assert "fields['facts.parking']" in HTML
    assert "fields['facts.hours_text']" in HTML
    assert "입장정보 출처" in HTML
    assert "주차정보 출처" in HTML
    assert "영업시간 출처" in HTML
    assert "대표 출처" in HTML


def test_facility_map_clears_stale_results_before_a_new_request():
    assert "function resetSearchState()" in HTML
    assert "resetSearchState();" in HTML
    assert "layer.clearLayers();" in HTML
    assert "검색 결과를 표시하지 못했습니다" in HTML


def test_facility_map_sends_parking_preference_only_when_explicitly_enabled():
    assert 'id="prefer-parking" type="checkbox"' in HTML
    assert "if ($('prefer-parking').checked) body.preferences = {parking:true};" in HTML
    assert "['dog', 'where', 'radius', 'prefer-parking']" in HTML
    assert "장소를 빼지 않고 서버가 정한 거리 구간 안에서만 우선한다" in HTML
    assert "같은 500m 거리 구간" not in HTML


def test_facility_map_explains_server_sort_and_three_state_parking_coverage():
    assert "renderSort(group.sort, group.results.length);" in HTML
    assert "(sort.applied || []).includes('parking')" in HTML
    assert "sort.coverage && sort.coverage.parking" in HTML
    assert "coverage.known_true" in HTML
    assert "coverage.known_false" in HTML
    assert "coverage.unknown" in HTML
    assert "주차 정보가 모두 미상입니다" in HTML


def test_facility_map_preserves_server_order_in_a_clickable_result_list():
    assert "renderResults(group.results);" in HTML
    assert "results.forEach(function (hit, index)" in HTML
    assert "group.results.sort" not in HTML
    assert "resultMarkers.set(placeKey(hit.place), hitMarker);" in HTML
    assert "resultMarkers.get(placeKey(f))" in HTML
    assert "hitMarker.openPopup();" in HTML
    assert "parkingLabel(f.facts.parking)" in HTML
