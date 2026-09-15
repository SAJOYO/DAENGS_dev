"""Offline regression tests. Injected writer/reviewer answers are NOT live model evaluation."""

import asyncio
import gzip
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from daengs_walk.value_contracts import digest
from daengs_walk.contracts import WalkEvidencePoint
from daengs_walk.diary.relational.contracts import VERSION, SpaceInput
from daengs_walk.diary.relational.planning import make_plan
from daengs_walk.diary.relational.journey import extract_journey
from daengs_backend.services.walk_diary.writing.relational import validate_prepared, write_relational_diary
from daengs_backend.services.walk_diary.writing.short_memory import write_with_short_memory
from daengs_backend.services.walk_diary.writing.relational_transport import (
    CallCoordinator, ProviderFailure, CallsStopped, CallBudgetExceeded,
)
from daengs_backend.orchestration.relational_diary import generate_prepared_relational_diary
from daengs_backend.services.walk_diary.storage.relational import save_skeleton, read_skeleton

FIXTURES = Path(__file__).resolve().parents[3] / 'evals' / 'relational_takeover'
T0 = datetime(2026, 9, 14, 1, tzinfo=timezone.utc)


def frame(index, cover='길', action=False):
    at = (T0 + timedelta(minutes=index)).isoformat()
    return {
        'scene_id': f's{index}', 'walk_session': 'walk-1', 'at_s': index*60, 'block': 0,
        'anchor': {'event_at': at, 'point': {'lat': 37.5 + index*0.001, 'lng': 127.0},
                   'method': 'observed', 'position_state': 'resolved', 'accuracy_m': 5.0},
        'space': {'materials': [] if cover is None else [
            {'id': 'm1', 'role': 'point_land_cover', 'material': {'피복': cover},
             'relation': '현재 기록 위치의 지도 배경. 구간 전체는 미확인'}],
            'narration': {'companions': [{'name': '보리'}]}},
        'action': {'recorded_action': {'id': 'a1', 'actor': '보리', 'action': '냄새 맡기'}} if action else None,
    }


def prepared(frames):
    plans, state = [], None
    for i, f in enumerate(frames):
        plan = make_plan(f, frames[i-1] if i else None, None, state)
        plans.append(plan)
        state = plan['state_after']
    snapshot = {'version': VERSION, 'input_revision': 'synthetic', 'frames': frames,
                'plans': plans, 'originals': []}
    return {'snapshot': snapshot, 'revision': digest(snapshot)}


def assessment(refs, **changes):
    return {**{k: True for k in ('supported', 'preserves_subjects', 'preserves_relation_axis',
                                'preserves_scope', 'no_invented_experience',
                                'required_meanings_present', 'readable_as_diary')},
            'used_evidence_ids': refs, 'issues': [], **changes}


def refs_for(stage, payload):
    if stage == 'action':
        return [payload['recorded_action']['id']]
    refs = payload['required_relation_ids'] or [payload['current_space'][0]['id']]
    if payload['mode'] == 'spatial_journey':
        refs = list(refs) + ['journey']
    return refs


async def passing_double(stage, payload, schema):
    if stage == 'review':
        return json.dumps(assessment(payload['candidate']['evidence_ids']))
    if stage == 'title':
        return json.dumps({'title': '산책 기록'})
    return json.dumps({'text': '보리가 냄새를 맡았다.' if stage == 'action' else '길이 배경인 곳이었다.',
                       'evidence_ids': refs_for(stage, payload)}, ensure_ascii=False)


def test_point_contrast_without_fake_observation_date():
    value = prepared([frame(0), frame(1, '숲')])
    r = value['snapshot']['plans'][1]['space_task']['payload']['relations'][0]
    assert r['comparison_axis'] == 'record_location'
    assert [s['value'] for s in r['subjects']] == [{'피복': '길'}, {'피복': '숲'}]
    assert all(s['observed_at'] is None for s in r['subjects'])
    assert r['record_chronology']['same_source_observation_time'] is None
    assert r['record_chronology']['same_walk'] is True


@pytest.mark.parametrize('change', ['overlap', 'uncertain', 'missing_accuracy', 'other_walk', 'naive'])
def test_uncertain_record_comparison_stays_current_context(change):
    a, b = frame(0), frame(1, '숲')
    if change == 'overlap':
        b['anchor']['point'] = a['anchor']['point']
    if change == 'uncertain':
        b['anchor']['method'] = 'last_known'
    if change == 'missing_accuracy':
        b['anchor'].pop('accuracy_m')
    if change == 'other_walk':
        b['walk_session'] = 'other'
    if change == 'naive':
        b['anchor']['event_at'] = '2026-09-14T01:01:00'
    plan = make_plan(b, a, None)
    assert plan['space_task']['payload']['mode'] == 'current_context'
    assert plan['space_task']['payload']['relations'] == []


def test_source_observation_change_is_separate_from_record_change():
    a, b = frame(0), frame(1, '숲')
    b['anchor']['point'] = a['anchor']['point']
    for i, f in enumerate((a, b)):
        f['observation_basis'] = {'point_land_cover': {'source_series': 'map', 'subject_key': 'area-X',
            'observed_at': f'2026-09-0{i+1}T00:00:00Z', 'support_radius_m': 5}}
    assert make_plan(b, a, None)['space_task']['payload']['relations'][0]['comparison_axis'] == 'observation_time'
    b['observation_basis']['point_land_cover']['observed_at'] = a['observation_basis']['point_land_cover']['observed_at']
    b['fetched_at'] = '2026-09-15T00:00:00Z'
    assert make_plan(b, a, None)['space_task']['payload']['relations'] == []


def test_all_changed_roles_preserved_not_first_two_only():
    a, b = frame(0), frame(1, '숲')
    for i, f in enumerate((a, b)):
        f['space']['materials'].append({'id': 'm2', 'role': 'location_label',
                                       'material': {'dong': f'동{i}'}, 'relation': '행정 위치'})
        f['road_reference'] = {'id': 'road1', 'road_nm': f'도로{i}', 'scope': '주소 대응 도로명'}
    p = make_plan(b, a, None)['space_task']['payload']
    assert len(p['relations']) == len(p['required_relation_ids']) == 3


def test_same_space_does_not_drop_repeated_action_pins():
    p = prepared([frame(0, action=True), frame(1, action=True)])['snapshot']['plans']
    assert p[1]['space_task'] is None
    assert p[0]['action_task']['id'] != p[1]['action_task']['id']
    assert 'relations' not in p[1]['action_task']['payload']
    assert 'short_memory' not in p[1]['action_task']['payload']


def test_missing_current_space_never_inherits_previous_action_location():
    p = prepared([frame(0), frame(1, None, action=True)])['snapshot']['plans'][1]
    assert p['space_task'] is None
    assert p['action_task']['payload']['current_space'] == []
    assert p['state_transition'] == 'suspend'


def test_generated_prose_cannot_be_inserted_into_memory_contract():
    payload = prepared([frame(0)])['snapshot']['plans'][0]['space_task']['payload']
    with pytest.raises(ValidationError):
        SpaceInput.model_validate({**payload, 'short_memory': [{'text': 'invented memory'}]})


@pytest.mark.parametrize('alter', ['space', 'pin', 'road'])
def test_rehashed_task_cannot_take_other_frame_facts(alter):
    p = prepared([frame(0, action=True)])
    plan = p['snapshot']['plans'][0]
    task = plan['action_task']
    if alter == 'space':
        task['payload']['current_space'][0]['material'] = {'피복': '숲'}
    if alter == 'pin':
        task['payload']['pin_at'] = '2026-09-14T02:00:00Z'
    if alter == 'road':
        task['payload']['road_reference'] = {'id': 'r', 'road_nm': '다른 도로', 'scope': '주소'}
    task['revision'] = digest([task['stage'], task['scene_id'], task['payload']])
    plan['revision'] = digest({k: v for k, v in plan.items() if k != 'revision'})
    p['revision'] = digest(p['snapshot'])
    with pytest.raises(ValueError):
        validate_prepared(p)


def test_id_contract_and_actual_request_are_saved():
    async def send(stage, payload, schema):
        if stage != 'review':
            assert set(payload['citation_ids']) == set(schema['properties']['evidence_ids']['items']['enum'])
            assert 'source_ids' not in json.dumps(payload)
        return await passing_double(stage, payload, schema)
    r = asyncio.run(write_relational_diary(prepared([frame(0)]), send=send))['results'][0]
    assert r['status'] == 'returned' and r['semantic_status'] == 'model_reviewed'
    assert r['semantic_review']['method'] == 'model_review_not_proof'
    assert r['request_revision']


@pytest.mark.parametrize('text,field', [
    ('이전에는 길이었던 곳이 지금은 숲으로 바뀌어 있었다.', 'preserves_relation_axis'),
    ('길을 따라 울창한 숲으로 들어섰다.', 'preserves_scope'),
    ('푸른 풀밭의 풍경이 인상적이었다.', 'no_invented_experience'),
    ('보리가 냄새를 맡으려고 멈춰 섰다.', 'preserves_subjects'),
])
def test_known_bad_prose_with_valid_ids_is_rejected_on_semantic_assessment(text, field):
    # Reuses historical failure forms with an explicitly authored reviewer test double.
    async def send(stage, payload, schema):
        if stage == 'review':
            return json.dumps(assessment(payload['candidate']['evidence_ids'], **{field: False},
                                         issues=['annotated unsupported claim']))
        return json.dumps({'text': text, 'evidence_ids': refs_for(stage, payload)}, ensure_ascii=False)
    result = asyncio.run(write_relational_diary(prepared([frame(0)]), send=send))['results'][0]
    assert result['status'] == 'failed' and result['failure_phase'] == 'semantic_review'
    assert result['candidate']['text'] == text
    assert 'answer' not in result
    assert result['semantic_review']['raw_text']


@pytest.mark.parametrize('kind', ['invalid_id', 'duplicate_id', 'no_ids', 'empty_text'])
def test_invalid_writer_references_never_reach_review(kind):
    seen = []
    async def send(stage, payload, schema):
        seen.append(stage)
        refs = {'invalid_id': ['internal:m1'], 'duplicate_id': ['m1', 'm1'], 'no_ids': [], 'empty_text': ['m1']}[kind]
        return json.dumps({'text': '' if kind == 'empty_text' else '길이었다.', 'evidence_ids': refs})
    r = asyncio.run(write_relational_diary(prepared([frame(0)]), send=send))['results'][0]
    assert r['failure_phase'] == 'references' and seen == ['space']
    assert r['raw_text']


def test_invalid_reviewer_json_preserved_and_fails_closed():
    async def send(stage, payload, schema):
        return 'bad review JSON' if stage == 'review' else await passing_double(stage, payload, schema)
    r = asyncio.run(write_relational_diary(prepared([frame(0)]), send=send))['results'][0]
    assert r['status'] == 'failed'
    assert r['semantic_review']['raw_text'] == 'bad review JSON'


def test_failed_intro_recovers_narrator_and_keeps_actions_separate():
    seen = []
    async def send(stage, payload, schema):
        seen.append((stage, deepcopy(payload)))
        if len(seen) == 1:
            raise ProviderFailure(503)
        return await passing_double(stage, payload, schema)
    r = asyncio.run(write_with_short_memory(prepared([frame(0), frame(1, action=True), frame(2)]), send=send))
    plans = r['prepared']['snapshot']['plans']
    assert plans[1]['memory_recovery']
    assert plans[1]['space_task']['payload']['narration']['companions'][0]['name'] == '보리'
    assert plans[2]['space_task'] is None
    requests = [p for s, p in seen if s in ('space', 'action')]
    assert all('길이 배경인 곳이었다.' not in json.dumps(p.get('short_memory', []), ensure_ascii=False)
               and all('text' not in item and 'raw_text' not in item for item in p.get('short_memory', []))
               for p in requests)
    assert all('short_memory' not in p for s, p in seen if s == 'action')
    assert r['receipt']['cards'][1]['parts']['action']['status'] == 'returned'
    validate_prepared(r['prepared'])


def test_unmentioned_optional_context_does_not_force_repeated_introductions():
    frames = [frame(0), frame(1)]
    for f in frames:
        f['space']['materials'].append({'id': 'm2', 'role': 'location_label',
                                       'material': {'dong': '가동'}, 'relation': '행정 위치'})
    r = asyncio.run(write_with_short_memory(prepared(frames), send=passing_double))
    assert r['prepared']['snapshot']['plans'][1]['space_task'] is None
    delivery = r['prepared']['snapshot']['plans'][0]['delivery_after']
    assert 'location_label' not in delivery['reviewed_covered_roles']
    assert delivery['all_context_facts_delivered'] is False


def test_utc_order_not_lexical_timestamp_order():
    a, b = frame(0), frame(1)
    a['anchor']['event_at'] = '2026-09-14T10:00:00+09:00'
    b['anchor']['event_at'] = '2026-09-14T01:01:00+00:00'
    asyncio.run(write_with_short_memory(prepared([a, b]), send=passing_double))
    with pytest.raises(ValueError):
        asyncio.run(write_with_short_memory(prepared([b, a]), send=passing_double))


def measured():
    points = [WalkEvidencePoint(client_seq=i, chain_index=0, at=T0+timedelta(seconds=i*10),
                                lat=37.5+i*0.0001, lng=127.0, accuracy_m=5) for i in range(4)]
    segments = [SimpleNamespace(a=a, b=b, chain_index=0, dt=10.0, dist=11.0, moving=True)
                for a, b in zip(points, points[1:])]
    def anchor(point):
        return {'walk_session': 'walk-1', 'anchor': {
            'event_at': point.at.isoformat(), 'location_at': point.at.isoformat(),
            'point': {'lat': point.lat, 'lng': point.lng}, 'method': 'observed',
            'position_state': 'resolved', 'source_fixes': [
                {'client_seq': point.client_seq, 'chain_index': 0, 'at': point.at.isoformat()}]}}
    return anchor(points[0]), anchor(points[-1]), SimpleNamespace(accepted_points=points, segments=segments)


def test_canonical_journey_sums_whole_segments_and_keeps_gaps():
    a, b, e = measured()
    j = extract_journey(a, b, e, 'canonical-revision')
    assert j['status'] == 'connected' and j['observed_seconds'] == 30 and j['observed_distance_m'] == 33
    e.segments.pop(1)
    j = extract_journey(a, b, e, 'canonical-revision')
    assert j['status'] == 'partial' and j['observed_seconds'] == 20
    assert len(j['uncovered_intervals']) == 1


@pytest.mark.parametrize('kind', ['duplicate', 'overlap', 'wrong_dt', 'nonfinite'])
def test_invalid_measurements_not_summed(kind):
    a, b, e = measured()
    if kind == 'duplicate':
        e.segments.append(e.segments[0])
    if kind == 'overlap':
        e.segments.append(SimpleNamespace(a=e.accepted_points[0], b=e.accepted_points[2], chain_index=0, dt=20, dist=22, moving=True))
    if kind == 'wrong_dt':
        e.segments[0].dt = 8
    if kind == 'nonfinite':
        e.segments[0].dist = float('nan')
    with pytest.raises(ValueError):
        extract_journey(a, b, e, 'canonical-revision')


def test_wrong_first_fix_does_not_pass_just_because_both_anchors_exist():
    a, b, e = measured()
    other = e.accepted_points[0].model_copy(update={'client_seq': 77})
    e.accepted_points.append(other)
    e.segments[0].a = other
    assert extract_journey(a, b, e, 'v')['status'] == 'partial'


def test_uncertain_anchor_cross_session_and_naive_time():
    a, b, e = measured()
    b['anchor']['method'] = 'last_known'
    assert extract_journey(a, b, e, 'v')['status'] == 'partial'
    b['walk_session'] = 'other'
    assert extract_journey(a, b, e, 'v') is None
    b['walk_session'] = 'walk-1'; b['anchor']['event_at'] = '2026-09-14T01:00:30'
    assert extract_journey(a, b, e, 'v') is None


def test_invalid_connected_metrics_rejected_before_llm():
    data = json.loads(gzip.decompress((FIXTURES / 'prepared_v6.json.gz').read_bytes()))
    payload = data['snapshot']['plans'][1]['space_task']['payload']
    payload['journey']['observed_seconds'] = 50
    with pytest.raises(ValidationError):
        SpaceInput.model_validate(payload)


def test_rate_limit_and_global_budget_do_not_retry():
    async def work():
        calls = []
        async def limited(stage, payload, schema):
            calls.append(stage); raise ProviderFailure(429)
        coordinator = CallCoordinator(limited)
        with pytest.raises(ProviderFailure):
            await coordinator('space', {}, {})
        with pytest.raises(CallsStopped):
            await coordinator('action', {}, {})
        assert calls == ['space'] and coordinator.calls == 1
        c = CallCoordinator(passing_double, max_calls=0)
        with pytest.raises(CallBudgetExceeded):
            await c('space', {}, {})
        assert c.calls == 0
    asyncio.run(work())


def test_pacing_measures_from_completion_for_all_stages():
    async def work():
        now = [0.0]
        async def sleep(seconds): now[0] += seconds
        async def send(stage, payload, schema): now[0] += 2; return '{}'
        c = CallCoordinator(send, minimum_interval_s=10, clock=lambda: now[0], sleep=sleep)
        for stage in ('space', 'review', 'action', 'title'):
            await c(stage, {}, {})
        assert [x['started_s'] for x in c.trace] == [0, 12, 24, 36]
        assert c.calls == 4
    asyncio.run(work())


def test_full_prepared_pipeline_preserves_originals_and_saves_final_requests(tmp_path):
    data = json.loads(gzip.decompress((FIXTURES / 'prepared_v6.json.gz').read_bytes()))
    original = deepcopy(data)
    seen = []
    async def send(stage, payload, schema):
        seen.append((stage, deepcopy(payload)))
        return await passing_double(stage, payload, schema)
    result = asyncio.run(generate_prepared_relational_diary(data, send=send, model='test-double'))
    assert data == original
    assert result['receipt']['execution']['model_call_attempts'] == 10
    assert result['receipt']['execution']['sender_kind'] == 'injected_sender'
    assert result['prepared']['snapshot']['originals'] == original['snapshot']['originals']
    for entry in original['snapshot']['originals']:
        text = entry['record']['content']['text']
        assert text not in json.dumps(seen, ensure_ascii=False)
    save_skeleton(tmp_path/'result.json', result)
    assert read_skeleton(tmp_path/'result.json') == result['receipt']
    with pytest.raises(FileExistsError):
        save_skeleton(tmp_path/'result.json', result)
    doc = json.loads((tmp_path/'result.json').read_text(encoding='utf-8'))
    doc['payload']['receipt']['cards'][0]['body'] = 'tampered'
    (tmp_path/'result.json').write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises(ValueError):
        read_skeleton(tmp_path/'result.json')


def test_old_v4_receipt_is_read_only_and_still_readable(tmp_path):
    old = tmp_path / 'recorded_v4.json'
    old.write_bytes(gzip.decompress((FIXTURES / 'recorded_v4.json.gz').read_bytes()))
    receipt = read_skeleton(old)
    assert receipt['version'] == 'relational-diary-skeleton-v4'
    assert '울창한' in receipt['cards'][2]['body']


def test_rejected_title_never_replaces_fallback():
    async def send(stage, payload, schema):
        if stage == 'title':
            return json.dumps({'title': '울창한 숲길에서 느낀 행복'})
        if stage == 'review' and payload['part'] == 'title':
            return json.dumps(assessment([], supported=False, issues=['invented title']))
        return await passing_double(stage, payload, schema)
    result = asyncio.run(generate_prepared_relational_diary(prepared([frame(0)]), send=send))
    title = result['receipt']['title']
    assert title['status'] == 'failed' and title['text'] == '산책 기록'
    assert title['candidate'] == '울창한 숲길에서 느낀 행복'


def test_explicit_unreviewed_ablation_never_claims_semantic_success():
    r = asyncio.run(write_relational_diary(prepared([frame(0)]), send=passing_double, review=False))
    assert r['semantic_validation'] == 'not_performed'
    assert r['results'][0]['semantic_status'] == 'unverified'


def test_empty_publication_never_calls_model():
    result = asyncio.run(generate_prepared_relational_diary(prepared([]), send=passing_double))
    assert result['receipt']['execution']['model_call_attempts'] == 0
    assert result['receipt']['title']['status'] == 'not_requested'
