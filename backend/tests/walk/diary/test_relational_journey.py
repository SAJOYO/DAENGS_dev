from copy import deepcopy
from dataclasses import replace

from daengs_evals.diary_slots_demo import demo_input
from daengs_walk.diary.relational.journey import extract_journey


def test_canonical_gap_and_uncertain_anchor_do_not_become_connected_journey():
    source, route, _ = demo_input()
    a,b=[{'anchor':r.anchor.model_dump(mode='json')} for r in source.records[:2]]
    j=extract_journey(a,b,route.evidence,'version')
    assert j['status']=='connected' and j['elapsed_seconds']==100
    assert j['observed_seconds']==100
    assert j['moving_distance_m']>0
    keep=tuple(s for s in route.evidence.segments if s.a.at!=source.records[0].anchor.event_at)
    broken=extract_journey(a,b,replace(route.evidence,segments=keep),'version')
    assert broken['status']=='partial' and broken['observed_seconds']==90
    assert broken['uncovered_intervals']
    uncertain=deepcopy(b);uncertain['anchor']['method']='last_known'
    assert extract_journey(a,uncertain,route.evidence,'version')['status']=='partial'
