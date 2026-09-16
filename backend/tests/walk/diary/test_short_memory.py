import json

from daengs_backend.services.walk_diary.writing.short_memory import write_with_short_memory
from daengs_walk.value_contracts import digest
from daengs_walk.diary.relational.contracts import VERSION
from daengs_walk.diary.relational.planning import make_plan
from tests.walk.diary.test_relational_planning import frame, review_double


async def test_memory_preserves_failures_recovers_and_does_not_copy_prose():
    frames=[frame(0),frame(20),frame(30,'숲',action=True)]
    plans=[];state=None
    for i,f in enumerate(frames):
        p=make_plan(f,frames[i-1] if i else None,None,state);plans.append(p);state=p['state_after']
    snapshot={'version':VERSION,'input_revision':'fixture','frames':frames,'plans':plans,'originals':[]}
    seen=[]
    async def send(stage,payload,schema):
        seen.append((stage,payload))
        if len(seen)==1:
            raise TimeoutError('first space failure')
        if stage=='review':
            return review_double(payload)
        if stage=='action':
            assert 'short_memory' not in payload
            refs=[payload['recorded_action']['id']]
        else:
            assert 'invented prose' not in json.dumps(payload)
            refs=payload['required_relation_ids'] or [payload['current_space'][0]['id']]
        return json.dumps({'text':'invented prose','evidence_ids':refs})
    result=await write_with_short_memory(
        {'snapshot':snapshot,'revision':digest(snapshot)},send=send,review=True
    )
    assert result['prepared']['snapshot']['plans'][1]['memory_recovery']
    space_calls=[payload for stage,payload in seen if stage=='space']
    assert space_calls[1]['short_memory'][0]['publication_status']=='failed'
    assert space_calls[2]['short_memory'][-1]['publication_status']=='model_reviewed'
    assert all(len(payload.get('short_memory',[]))<=2 for _,payload in seen)
    from daengs_backend.services.walk_diary.writing.relational import validate_prepared
    validate_prepared(result['prepared'])
