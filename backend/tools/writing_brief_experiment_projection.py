"""Separate narrative relationships from audit diffs, and events from viewpoint.

This projection consumes the experimental brief. It does not alter source facts,
rank material families, generate prose, or enable a production execution strategy.
"""

from copy import deepcopy


def _fact(statement):
    result = deepcopy(statement)
    if result['predicate'] != 'registered_business_composition':
        return result
    source = result['value']
    composition = source['composition']
    # Consume normalized meanings, not raw counters or acquisition differences.
    mix = composition['업종구성']
    distribution = composition['조회영역_등록분포']
    mix = {'여러 업종 혼합': 'mixed_categories'}.get(mix, mix)
    distribution = {
        '등록 지점이 흩어져 있음': 'dispersed',
        '등록 지점이 모여 있음': 'clustered',
    }.get(distribution, distribution)
    result['predicate'] = 'area_characteristics'
    result['value'] = {'business_mix': mix, 'spatial_distribution': distribution}
    result['scope'] = {
        'kind': 'query_area',
        'radius_m': source.get('query', {}).get('radius_m'),
        'source_kind': 'business_catalog',
        'applies_to': 'whole_query_area_not_point_view',
        'does_not_establish': ['current_opening', 'visit', 'crowding'],
    }
    result['source_time'] = {
        'reference_date': statement['source_time'].get('reference_date'),
        'event_time_observation': False,
    }
    # Provider-qualified identity remains in the source archive, not authored content.
    result.pop('object_identity', None)
    return result


def _relation(relation, facts):
    if relation.get('family') != 'area_context':
        return deepcopy(relation)
    left, right = relation['earlier_evidence_ids'], relation['current_evidence_ids']
    if not left or not right or not relation.get('comparison_basis', {}).get('statistics_comparable'):
        return None
    before = [facts[key]['value'] for key in left]
    after = [facts[key]['value'] for key in right]
    return {
        'id': relation['id'], 'family': 'area_context', 'axis': 'query_area',
        'result': 'same_characteristics' if before == after else 'different_characteristics',
        'earlier_evidence_ids': left, 'current_evidence_ids': right,
        'earlier_characteristics': before, 'current_characteristics': after,
        'scope': {
            'comparison': 'two_query_areas',
            'applies_to': 'normalized_area_characteristics_only',
            'not_a_temporal_change': True,
        },
    }


def project_space(brief):
    result = deepcopy(brief)
    result['available_statements'] = [_fact(item) for item in result['available_statements']]
    facts = {item['id']: item for item in result['available_statements']}
    usable = set()
    for slot in result['relation_slots'].values():
        converted = [_relation(item, facts) for item in slot['items']]
        slot['items'] = [item for item in converted if item is not None]
        usable.update(item['id'] for item in slot['items'])
        # Acquisition/planner statuses are retained in the archived input.
        for key in ('reason', 'policy_version', 'status'):
            slot.pop(key, None)
    result['relation_ids'] = [key for key in result['relation_ids'] if key in usable]
    # Apply the same boundary to memory; otherwise old audit diffs leak back in.
    for memory in result['delivery_memory']:
        memory['selected_statements'] = [_fact(item) for item in memory['selected_statements']]
        memory_facts = {item['id']: item for item in memory['selected_statements']}
        memory['selected_relations'] = [
            projected for item in memory['selected_relations']
            if (projected := _relation(item, memory_facts)) is not None
        ]
    for anchor in result['anchors']:
        anchor.pop('collection', None)
    assert {item['id'] for item in result['available_statements']} == set(result['citation_ids'])
    return result


def project_action(brief):
    current = brief['current_action']
    event = {
        'id': current['id'],
        'actor': deepcopy(current['actor']),
        'behavior': current['behavior'],
        'recorded_at': current['recorded_at'],
        'anchor_ref': current['anchor_ref'],
    }
    assert event['actor']['entity_type'] == 'dog'
    result = {
        'version': 'event-centered-brief-v1', 'part': 'action',
        'scene_id': brief['scene_id'], 'scene_position': deepcopy(brief['scene_position']),
        'required_event': event,
        'context_options': [
            {'for_event_id': event['id'], 'evidence': deepcopy(item)}
            for item in brief['available_statements']
        ],
        'style': {'language': 'ko', 'tense': 'past', 'genre': 'walk_diary'},
        'required_evidence_ids': [event['id']],
        'citation_ids': deepcopy(brief['citation_ids']),
    }
    assert {event['id'], *(item['evidence']['id'] for item in result['context_options'])} == set(result['citation_ids'])
    return result


def separate_writing_responsibilities(brief):
    return project_space(brief) if brief['part'] == 'space' else project_action(brief)
