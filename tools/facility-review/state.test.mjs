import test from 'node:test';
import assert from 'node:assert/strict';
import {LatestSearch,validateAi,validateAction,validateDogs,snapshots,receiveProfiles,dogLabel} from './web/state.mjs';

test('a mode or query change aborts and rejects the old response',()=>{const s=new LatestSearch();const first=s.start();const second=s.start();assert.equal(first.signal.aborted,true);assert.equal(s.current(first.version),false);assert.equal(s.current(second.version),true);s.cancel();assert.equal(s.current(second.version),false);});
test('existing Pet values supply snapshots; family day is not a birthday and size is never inferred',()=>{
  const got=snapshots([{id:'a',updated_at:'revision-1',weight_kg:'40.0',birth_date:'2023-09-06',birth_date_kind:'birthday',selected:true},{id:'b',weight_kg:null,birth_date:'2023-09-06',birth_date_kind:'family_day',selected:true}], '2026-09-06');
  assert.equal(got.length,2);assert.equal(got[0].dog_size,null);assert.equal(got[0].dog_weight_kg,40);assert.equal(got[0].revision,'revision-1');assert.equal(got[0].dog_age_years,1096/365.2425);assert.equal(got[1].dog_weight_kg,null);assert.equal(got[1].dog_age_years,null);
});
test('profile refresh retains only available selections of the same owner',()=>{
  const previous=[{id:'a',selected:true},{id:'b',selected:true}];
  const payload={pets:[{id:'a',weight_kg:'6.0'},{id:'b',farewell_on:'2026-01-01'},{id:'c',is_primary:true}]};
  const received=receiveProfiles(previous,payload,true);
  assert.deepEqual(received.map(d=>[d.id,d.selected]),[['a',true],['c',false]]);
  assert.equal(snapshots(received)[0].dog_weight_kg,6);
  assert.ok(receiveProfiles(previous,payload,false).every(d=>!d.selected));
});
test('future birthdays and invalid weights stay unknown',()=>{
  const [got]=snapshots([{id:'a',selected:true,weight_kg:'201',birth_date_kind:'birthday',birth_date:'2027-01-01'}],'2026-09-06');
  assert.equal(got.dog_weight_kg,null);assert.equal(got.dog_age_years,null);
});
test('stale dog echo or missing evaluations are rejected',()=>{const dogs=[{ref:'a'}];assert.throws(()=>validateDogs({groups:[],dogs:[]},dogs));assert.throws(()=>validateDogs({groups:[{results:[{evaluations:{}}]}],dogs,evaluated_at:'today'},dogs));});
test('AI result cannot silently change the selected radius',()=>{const request={spatial:{radius_m:10000},dogs:[]};assert.throws(()=>validateAi({contract_version:'facility-discovery-v1',request:{...request,spatial:{radius_m:3000}},lenses:[]},request));});
test('dog label follows app wording without promising admission',()=>{assert.equal(dogLabel({dog_access:{reason:'weight_allowed'},restrictions:{reason:'age_denied'}}),'체중 조건 충족 · 나이 제한 불일치');});
test('action response must match the same search, revision, action, and bundled request',()=>{
  const previous={search_id:'a',revision:1,request:{dogs:[]}};
  const action={client_request_id:'request-id',search_id:'a',expected_revision:1,action:{type:'confirm',lens_id:'lens-a'}};
  const response={contract_version:'facility-discovery-v1',search_id:'a',revision:2,request:previous.request,action_request:action,lenses:[]};
  assert.equal(validateAction(response,action,previous),response);
  assert.throws(()=>validateAction({...response,search_id:'b'},action,previous));
  assert.throws(()=>validateAction({...response,revision:3},action,previous));
  assert.throws(()=>validateAction({...response,action_request:{...action,client_request_id:'old'}},action,previous));
});
