"""A7-M1: awaited navigation must preserve the current human draft and choice.

These tests run the real UI functions in the existing small Node Element harness;
they do not claim native browser event or details-element coverage.
"""

from __future__ import annotations

import json

import pytest
from test_gold_web_ui import _browser
from test_labels_gold_web_ui import _V3_SETUP

from job_intake.crm.ui import render_crm_page

_DRAFT = {
    "role_label": "", "relevance": "0", "gate_outcome": "ineligible",
    "notes": "New human draft", "gate_reasons": ["personal_constraint"],
    "tags": {
        "function_core": {"value": "analytics_reporting", "quote": "Lead projects"},
        "preferred_focus": {"values": ["marketplace_economics"],
                            "quote": "support decisions."},
    },
}

_NAV_SETUP = _V3_SETUP + r"""
async function waitUntil(predicate) {
  for(let turn=0;turn<100;turn++){
    if(predicate())return;
    await Promise.resolve();
  }
  throw new Error('Expected pending UI request was not reached');
}
function detailFor(uid) {return {...detail,job_uid:uid,version:'version-'+uid};}
function editHumanDraft() {
  const form=goldEditor.form;
  form.elements.namedItem('notes').value='New human draft';
  form.elements.namedItem('relevance').value='0';
  form.elements.namedItem('gate_outcome').value='ineligible';
  form.goldGateReasons.find(item=>item.value==='personal_constraint').input.checked=true;
  const core=form.goldTagControls.function_core;
  core.enabled.checked=true;core.value.value='analytics_reporting';core.quote.value='Lead projects';
  const focus=form.goldTagControls.preferred_focus;
  focus.enabled.checked=true;focus.quote.value='support decisions.';
  focus.choices.find(item=>item.value==='marketplace_economics').input.checked=true;
  syncGoldTagStates(form);
}
function editorState() {return {uid:goldEditor?.detail.job_uid,
  draft:goldEditor?goldFormValues(goldEditor.form):null,
  dirty:goldIsDirty(),offset:goldOffset,cases:goldPage.cases.map(item=>item.job_uid)};}
"""


@pytest.mark.parametrize("route", ["detail", "page"])
@pytest.mark.parametrize("initial_dirty", [False, True])
def test_pending_navigation_retains_new_draft_then_retry_uses_current_discard(route, initial_dirty):
    options = f"const route={json.dumps(route)}, initialDirty={str(initial_dirty).lower()};"
    result = _browser(_NAV_SETUP + options + r"""
(async()=>{
  if(route==='page') {goldPage.cases=[goldPage.cases[0]];goldPage.has_more=true;goldPage.total=101;}
  renderGoldEditor(detail);
  if(initialDirty)goldEditor.form.elements.namedItem('notes').value='Previously confirmed draft';
  let release, confirms=0, gets=0, patches=0;
  window.confirm=()=>{confirms++;return true;};
  api=async(path,method)=>{
    if(method==='PATCH'){patches++;throw new Error('Navigation must not save');}
    gets++;
    if(route==='page' && path.includes('?'))return await new Promise(resolve=>release=()=>resolve(
      {...goldPage,cases:[{job_uid:'two'}],offset:100,has_more:false}));
    if(route==='detail' && path.endsWith('/two'))return await new Promise(resolve=>
      release=()=>resolve(detailFor('two')));
    return detailFor('two');
  };
  const beforeEditor=goldEditor;
  const pending=route==='detail'?openGold('two'):moveGold(1);
  await waitUntil(()=>release);
  const inputEnabled=!beforeEditor.form.elements.namedItem('notes').disabled;
  editHumanDraft();
  release();await pending;
  const afterPending={...editorState(),sameEditor:goldEditor===beforeEditor,
    confirms,gets,patches,inputEnabled};
  api=async(path,method)=>{
    if(method==='PATCH'){patches++;throw new Error('Navigation must not save');}
    gets++;
    return path.includes('?')?
      {...goldPage,cases:[{job_uid:'two'}],offset:100,has_more:false}:detailFor('two');
  };
  window.confirm=()=>{confirms++;return false;};
  await (route==='detail'?openGold('two'):moveGold(1));
  const afterDecline={...editorState(),confirms,gets,patches};
  window.confirm=()=>{confirms++;return true;};
  await (route==='detail'?openGold('two'):moveGold(1));
  return {afterPending,afterDecline,afterAccept:{...editorState(),confirms,gets,patches}};
})()
""")
    pending = result["afterPending"]
    assert pending["uid"] == "one" and pending["sameEditor"]
    assert pending["inputEnabled"] and pending["dirty"]
    assert pending["draft"] == _DRAFT
    assert pending["offset"] == 0
    assert pending["cases"] == (["one"] if route == "page" else ["one", "two"])
    assert pending["confirms"] == int(initial_dirty)
    assert pending["patches"] == 0
    declined = result["afterDecline"]
    assert declined["uid"] == "one" and declined["dirty"] and declined["draft"] == _DRAFT
    assert declined["gets"] == pending["gets"] and declined["patches"] == 0
    assert declined["confirms"] == int(initial_dirty) + 1
    accepted = result["afterAccept"]
    assert accepted["uid"] == "two" and not accepted["dirty"] and accepted["patches"] == 0
    assert accepted["confirms"] == int(initial_dirty) + 2


@pytest.mark.parametrize("route", ["detail", "page"])
@pytest.mark.parametrize("initial_dirty", [False, True])
def test_navigation_without_intervening_edits_keeps_normal_discard_flow(route, initial_dirty):
    options = f"const route={json.dumps(route)}, initialDirty={str(initial_dirty).lower()};"
    result = _browser(_NAV_SETUP + options + r"""
(async()=>{
  if(route==='page'){goldPage.cases=[goldPage.cases[0]];goldPage.has_more=true;goldPage.total=101;}
  renderGoldEditor(detail);
  if(initialDirty)editHumanDraft();
  let confirms=0,patches=0;
  window.confirm=()=>{confirms++;return true;};
  api=async(path,method)=>{
    if(method==='PATCH')patches++;
    return path.includes('?')?
      {...goldPage,cases:[{job_uid:'two'}],offset:100,has_more:false}:detailFor('two');
  };
  await (route==='detail'?openGold('two'):moveGold(1));
  return {...editorState(),confirms,patches};
})()
""")
    assert result["uid"] == "two" and not result["dirty"]
    assert result["confirms"] == int(initial_dirty) and result["patches"] == 0
    assert result["offset"] == (100 if route == "page" else 0)


@pytest.mark.parametrize("initial_dirty", [False, True])
def test_page_navigation_does_not_commit_queue_when_draft_changes_during_detail_get(initial_dirty):
    result = _browser(_NAV_SETUP + f"const initialDirty={str(initial_dirty).lower()};" + r"""
(async()=>{
  goldPage.cases=[goldPage.cases[0]];goldPage.has_more=true;goldPage.total=101;
  renderGoldEditor(detail);
  if(initialDirty)goldEditor.form.elements.namedItem('notes').value='Previously confirmed draft';
  let finishDetail,confirms=0;
  window.confirm=()=>{confirms++;return true;};
  api=async(path)=>path.includes('?')?
    {...goldPage,cases:[{job_uid:'two'}],offset:100,has_more:false}:
    await new Promise(resolve=>finishDetail=()=>resolve(detailFor('two')));
  const current=goldEditor,pending=moveGold(1);
  await waitUntil(()=>finishDetail);
  const whileLoading=editorState();
  editHumanDraft();finishDetail();await pending;
  return {...editorState(),sameEditor:goldEditor===current,whileLoading,confirms};
})()
""")
    assert result["sameEditor"] and result["uid"] == "one" and result["dirty"]
    assert result["draft"] == _DRAFT
    assert result["offset"] == result["whileLoading"]["offset"] == 0
    assert result["cases"] == result["whileLoading"]["cases"] == ["one"]
    assert result["confirms"] == int(initial_dirty)


@pytest.mark.parametrize("outcome,replacement", [
    ("success", "new_request"), ("error", "new_request"),
    ("success", "same_uid_editor"), ("error", "same_uid_editor"),
    ("success", "close"), ("success", "leave_view"), ("error", "leave_view"),
])
def test_stale_detail_response_cannot_replace_or_report_errors_into_current_context(outcome,
                                                                                replacement):
    options = f"const outcome={json.dumps(outcome)}, replacement={json.dumps(replacement)};"
    result = _browser(_NAV_SETUP + options + r"""
(async()=>{
  renderGoldEditor(detail);
  let settle;
  api=async(path)=>{
    if(path.endsWith('/two'))return await new Promise((resolve,reject)=>settle=()=>
      outcome==='success'?resolve(detailFor('two')):reject(new Error('Stale detail failure')));
    return detailFor('three');
  };
  const pending=openGold('two');
  if(replacement==='new_request')await openGold('three');
  else if(replacement==='same_uid_editor')
    renderGoldEditor({...detail,version:'new-current-version'});
  else if(replacement==='close')closeModal();
  else {view='contacts';goldEditor=null;$('modalShade').hidden=true;}
  goldMessage('Current context message');
  const before=goldEditor;
  const currentState=editorState();
  const hidden=$('modalShade').hidden;
  settle();const opened=await pending;
  return {opened,sameEditor:before===goldEditor,before:currentState,after:editorState(),
    hiddenBefore:hidden,hiddenAfter:$('modalShade').hidden,message:$('goldMessage').textContent};
})()
""")
    assert result["opened"] is False and result["sameEditor"]
    assert result["after"] == result["before"]
    assert result["hiddenAfter"] == result["hiddenBefore"]
    assert result["message"] == "Current context message"


@pytest.mark.parametrize("route", ["detail", "page"])
@pytest.mark.parametrize("save_outcome", ["saved", "conflict"])
def test_save_started_during_pending_navigation_invalidates_its_later_response(route,
                                                                             save_outcome):
    options = f"const route={json.dumps(route)}, saveOutcome={json.dumps(save_outcome)};"
    result = _browser(_NAV_SETUP + options + r"""
(async()=>{
  if(route==='page'){goldPage.cases=[goldPage.cases[0]];goldPage.has_more=true;goldPage.total=101;}
  renderGoldEditor(detail);editHumanDraft();
  let settle,patches=0;
  window.confirm=()=>true;
  refreshGold=async()=>true;
  api=async(path,method,payload)=>{
    if(method==='PATCH'){
      patches++;
      if(saveOutcome==='conflict')throw Object.assign(new Error('conflict'),{status:409});
      const {expected_version,...labels}=payload;
      return {...detail,labels,version:'saved-current-version'};
    }
    return await new Promise(resolve=>settle=()=>resolve(path.includes('?')?
      {...goldPage,cases:[{job_uid:'two'}],offset:100}:detailFor('two')));
  };
  const navigation=route==='detail'?openGold('two'):moveGold(1);
  await waitUntil(()=>settle);
  const saved=await saveGold(false);
  const before=goldEditor, beforeState=editorState(), message=goldEditor.message.textContent;
  const mainBefore=$('goldMessage').textContent;
  settle();await navigation;
  return {saved,patches,sameEditor:before===goldEditor,before:beforeState,after:editorState(),
    messageBefore:message,messageAfter:goldEditor.message.textContent,
    mainBefore,mainAfter:$('goldMessage').textContent,
    enabled:!goldEditor.form.elements.namedItem('notes').disabled};
})()
""")
    assert result["saved"] == (save_outcome == "saved") and result["patches"] == 1
    assert result["sameEditor"] and result["after"] == result["before"]
    assert result["after"]["uid"] == "one" and result["after"]["draft"] == _DRAFT
    assert result["after"]["dirty"] == (save_outcome == "conflict")
    assert result["messageAfter"] == result["messageBefore"] and result["enabled"]
    assert result["mainBefore"] == result["mainAfter"] == ""


def _save_next_case(stage, newer_action, outcome="success"):
    options = (f"const stage={json.dumps(stage)}, newerAction={json.dumps(newer_action)}, "
               f"outcome={json.dumps(outcome)};")
    return _browser(_NAV_SETUP + options + r"""
(async()=>{
  if(stage==='offset'){
    goldPage.cases=[goldPage.cases[0]];goldPage.has_more=true;goldPage.total=101;
  }else goldPage.cases.push({job_uid:'three'});
  renderGoldEditor(detail);
  let settle,finishNewDetail,queriesAt100=0,patches=0;
  const calls=[];
  api=async(path,method)=>{
    calls.push([path,method||'GET']);
    if(method==='PATCH'){patches++;return {...detail,version:'saved-v2'};}
    if(path.includes('?')){
      const offset=Number(new URL('http://local'+path).searchParams.get('offset'));
      if(offset===100 && ++queriesAt100===1)
        return {...goldPage,cases:[{job_uid:'two'}],offset:100,has_more:false};
      if((stage==='refresh' && offset===0)||(stage==='offset' && offset===100))
        return await new Promise((resolve,reject)=>settle=()=>outcome==='success'?
          resolve({...goldPage,cases:[{job_uid:'stale-queue'}],offset:100,has_more:false}):
          reject(new Error('Stale queue failure')));
      return {...goldPage,cases:[{job_uid:'one'},{job_uid:'three'}],offset:0,has_more:true};
    }
    if(path.endsWith('/three') && newerAction==='pending_selection')
      return await new Promise(resolve=>finishNewDetail=()=>resolve(detailFor('three')));
    return detailFor(decodeURIComponent(path.split('/').pop()));
  };
  const saving=saveGold(true);
  await waitUntil(()=>settle);
  let newerRequest;
  if(newerAction==='draft')editHumanDraft();
  else if(newerAction==='selection')await openGold('three');
  else newerRequest=openGold('three');
  goldMessage('Current context message');
  const current=goldEditor,before=editorState(),beforeEditorMessage=goldEditor.message.textContent;
  settle();await saving;
  const afterOld={...editorState(),sameEditor:goldEditor===current,
    message:$('goldMessage').textContent,editorMessage:goldEditor.message.textContent};
  if(newerRequest){finishNewDetail();await newerRequest;}
  return {before,afterOld,final:editorState(),beforeEditorMessage,patches,calls};
})()
""")


@pytest.mark.parametrize("stage", ["refresh", "offset"])
@pytest.mark.parametrize("newer_action", ["selection", "pending_selection", "draft"])
def test_old_save_next_does_not_override_new_selection_draft_or_queue(stage, newer_action):
    result = _save_next_case(stage, newer_action)
    after = result["afterOld"]
    assert after["sameEditor"]
    assert {key: after[key] for key in result["before"]} == result["before"]
    if newer_action == "draft":
        assert "Изменения остаются в форме" in after["editorMessage"]
    else:
        assert after["message"] == "Current context message"
    assert result["patches"] == 1
    assert not any(path == "/api/gold/two" for path, _ in result["calls"])
    if newer_action == "draft":
        assert result["final"]["uid"] == "one" and result["final"]["dirty"]
        assert result["final"]["draft"] == _DRAFT
    else:
        assert result["final"]["uid"] == "three" and not result["final"]["dirty"]


@pytest.mark.parametrize("stage", ["refresh", "offset"])
def test_stale_save_next_queue_errors_do_not_annotate_new_editor(stage):
    result = _save_next_case(stage, "selection", "error")
    after = result["afterOld"]
    assert after["sameEditor"] and after["uid"] == "three"
    assert {key: after[key] for key in result["before"]} == result["before"]
    assert after["message"] == "Current context message"
    assert after["editorMessage"] == result["beforeEditorMessage"]
    assert result["patches"] == 1
    assert not any(path == "/api/gold/two" for path, _ in result["calls"])


def test_save_next_commits_offset_hint_page_only_with_accepted_destination_detail():
    result = _browser(_NAV_SETUP + r"""
(async()=>{
  goldPage.cases=[goldPage.cases[0]];goldPage.has_more=true;goldPage.total=101;
  renderGoldEditor(detail);
  let finishDetail,patches=0,offsetPages=0;
  api=async(path,method)=>{
    if(method==='PATCH'){patches++;return {...detail,version:'saved-v2'};}
    if(path.includes('?')){
      const offset=Number(new URL('http://local'+path).searchParams.get('offset'));
      if(offset===100){offsetPages++;return {...goldPage,cases:[{job_uid:'two'}],offset:100};}
      return {...goldPage,cases:[{job_uid:'one'},{job_uid:'three'}],offset:0};
    }
    return await new Promise(resolve=>finishDetail=()=>resolve(detailFor('two')));
  };
  const saving=saveGold(true);
  await waitUntil(()=>finishDetail);
  const editor=goldEditor,before=editorState();
  editHumanDraft();finishDetail();await saving;
  return {...editorState(),sameEditor:goldEditor===editor,before,patches,offsetPages};
})()
""")
    assert result["sameEditor"] and result["uid"] == "one" and result["dirty"]
    assert result["draft"] == _DRAFT
    assert result["cases"] == result["before"]["cases"] == ["one", "three"]
    assert result["offset"] == result["before"]["offset"] == 0
    assert result["patches"] == 1 and result["offsetPages"] == 2


def test_plain_refresh_does_not_replace_queue_after_user_opens_another_card():
    result = _browser(_NAV_SETUP + r"""
(async()=>{
  renderGoldEditor(detail);
  let finishPage;
  api=async(path)=>path.includes('?')?
    await new Promise(resolve=>finishPage=()=>resolve(
      {...goldPage,cases:[{job_uid:'stale-queue'}],offset:100,has_more:false})):
    detailFor('three');
  const refreshing=refreshGold(false);
  await waitUntil(()=>finishPage);
  await openGold('three');
  goldMessage('Current context message');
  const editor=goldEditor,before=editorState();
  finishPage();const refreshed=await refreshing;
  return {...editorState(),sameEditor:goldEditor===editor,before,refreshed,
    message:$('goldMessage').textContent,loading:goldLoading};
})()
""")
    assert result["sameEditor"] and result["uid"] == "three" and not result["dirty"]
    assert {key: result[key] for key in result["before"]} == result["before"]
    assert result["offset"] == 0 and result["cases"] == ["one", "two"]
    assert result["message"] == "Current context message"
    assert result["refreshed"] is False and result["loading"] is False


def _main_pager_handler(button):
    """Register the production callback in the Element harness, then click it."""
    prefix = f"$('{button}').addEventListener('click',"
    statements = [line for line in render_crm_page().splitlines() if line.startswith(prefix)]
    assert len(statements) == 1
    return (f"$('{button}').addEventListener=(event,handler)=>{{"
            "if(event!=='click')throw Error('Unexpected pager event');"
            "globalThis.mainPagerClick=handler;};\n" + statements[0])


@pytest.mark.parametrize("direction", [-1, 1])
def test_main_pager_then_old_card_keeps_committed_offset_and_visits_adjacent_page(direction):
    button = "nextGold" if direction > 0 else "previousGold"
    setup = _NAV_SETUP + _main_pager_handler(button) + f"const direction={direction};"
    result = _browser(setup + r"""
(async()=>{
  const initial=direction>0?0:100,target=initial+direction*100;
  goldOffset=initial;
  goldPage={...goldPage,offset:initial,total:301,has_more:true,
    cases:Array.from({length:100},(_,i)=>({job_uid:
      i===(direction>0?99:0)?'one':'row-'+i}))};
  renderGoldList();
  let release;const queries=[];
  api=async(path)=>{
    if(!path.includes('?'))return detailFor(decodeURIComponent(path.split('/').pop()));
    const offset=Number(new URL('http://local'+path).searchParams.get('offset'));
    queries.push(offset);
    const page={...goldPage,offset,cases:[{job_uid:'destination-'+offset}]};
    if(queries.length===1)return await new Promise(resolve=>release=()=>resolve(page));
    return page;
  };
  mainPagerClick();await waitUntil(()=>release);
  const pendingOffset=goldOffset;
  await openGold('one');release();await waitUntil(()=>!goldLoading);
  const beforeMove={...editorState(),pageOffset:goldPage.offset,
    editorPageOffset:goldEditor.pageOffset,anchor:goldEditor.anchorIndex};
  await moveGold(direction);
  return {pendingOffset,beforeMove,queries,target,initial,...editorState()};
})()
""")
    assert result["pendingOffset"] == result["initial"]
    before = result["beforeMove"]
    assert before["offset"] == before["pageOffset"] == result["initial"]
    assert before["editorPageOffset"] == result["initial"]
    assert before["anchor"] == (99 if direction > 0 else 0)
    assert result["queries"] == [result["target"], result["target"]]
    assert result["uid"] == f"destination-{result['target']}"
    assert result["offset"] == result["target"] and not result["dirty"]


@pytest.mark.parametrize("direction", [-1, 1])
def test_rejected_main_pager_request_keeps_the_committed_page(direction):
    button = "nextGold" if direction > 0 else "previousGold"
    setup = _NAV_SETUP + _main_pager_handler(button)
    result = _browser(setup + r"""
(async()=>{
  goldOffset=100;goldPage={...goldPage,offset:100,total:301,has_more:true};
  renderGoldList();const before=JSON.stringify(goldPage);
  api=async()=>{throw Error('Synthetic page failure');};
  mainPagerClick();await waitUntil(()=>!goldLoading);
  return {offset:goldOffset,pageUnchanged:before===JSON.stringify(goldPage),
    message:$('goldMessage').textContent,messageHidden:$('goldMessage').hidden};
})()
""")
    assert result["offset"] == 100 and result["pageUnchanged"]
    assert result["message"] and not result["messageHidden"]


@pytest.mark.parametrize("response", ["success", "error"])
def test_close_after_successful_patch_clears_the_finished_refresh_loading_message(response):
    result = _browser(_NAV_SETUP + f"const response={json.dumps(response)};" + r"""
(async()=>{
  renderGoldEditor(detail);let release;
  api=async(path,method)=>method==='PATCH'?{...detail,version:'saved'}:
    await new Promise((resolve,reject)=>release=()=>response==='success'?
      resolve({...goldPage}):reject(Error('Synthetic page failure')));
  const saving=saveGold(false);await waitUntil(()=>release);
  const closed=closeModal();release();const saved=await saving;
  return {closed,saved,editorNull:goldEditor===null,modalHidden:$('modalShade').hidden,
    loading:goldLoading,message:$('goldMessage').textContent,messageHidden:$('goldMessage').hidden};
})()
""")
    assert result["closed"] and result["saved"] and result["editorNull"] and result["modalHidden"]
    assert result["loading"] is False and result["messageHidden"] and result["message"] == ""


@pytest.mark.parametrize("message", ["Later context message", "Загрузка выборки…"])
def test_finishing_stale_refresh_preserves_a_message_set_by_a_later_action(message):
    result = _browser(_NAV_SETUP + f"const laterMessage={json.dumps(message)};" + r"""
(async()=>{
  renderGoldEditor(detail);let release;
  api=async()=>await new Promise(resolve=>release=()=>resolve({...goldPage}));
  const refreshing=refreshGold(false);await waitUntil(()=>release);
  closeModal();goldMessage(laterMessage,'error');release();await refreshing;
  return {loading:goldLoading,message:$('goldMessage').textContent,
    messageHidden:$('goldMessage').hidden,messageClass:$('goldMessage').className};
})()
""")
    assert result["loading"] is False
    assert result["message"] == message and not result["messageHidden"]
    assert result["messageClass"] == "gold-message error"


@pytest.mark.parametrize("response", ["success", "error"])
def test_close_while_detail_is_pending_clears_only_its_finished_loading_message(response):
    result = _browser(_NAV_SETUP + f"const response={json.dumps(response)};" + r"""
(async()=>{
  renderGoldEditor(detail);let release;
  api=async()=>await new Promise((resolve,reject)=>release=()=>response==='success'?
    resolve(detailFor('two')):reject(Error('Synthetic detail failure')));
  const opening=openGold('two');await waitUntil(()=>release);
  const closed=closeModal();release();const opened=await opening;
  return {closed,opened,editorNull:goldEditor===null,modalHidden:$('modalShade').hidden,
    message:$('goldMessage').textContent,messageHidden:$('goldMessage').hidden};
})()
""")
    assert result["closed"] and result["opened"] is False
    assert result["editorNull"] and result["modalHidden"]
    assert result["messageHidden"] and result["message"] == ""


def test_filter_reset_then_old_card_preserves_the_committed_page_context():
    result = _browser(_NAV_SETUP + r"""
(async()=>{
  goldOffset=100;
  goldPage={...goldPage,offset:100,total:301,has_more:true,
    cases:Array.from({length:100},(_,i)=>({job_uid:i===99?'one':'row-'+i}))};
  renderGoldList();let release;const queries=[];
  api=async(path)=>{
    if(!path.includes('?'))return detailFor(decodeURIComponent(path.split('/').pop()));
    const offset=Number(new URL('http://local'+path).searchParams.get('offset'));
    queries.push(offset);
    const page={...goldPage,offset,cases:[{job_uid:'destination-'+offset}]};
    if(queries.length===1)return await new Promise(resolve=>release=()=>resolve(page));
    return page;
  };
  const resetting=refreshGold();await waitUntil(()=>release);
  const pendingOffset=goldOffset;
  await openGold('one');release();await resetting;
  const beforeMove={offset:goldOffset,pageOffset:goldPage.offset,
    editorOffset:goldEditor.pageOffset};
  await moveGold(1);
  return {pendingOffset,beforeMove,queries,uid:goldEditor.detail.job_uid,offset:goldOffset};
})()
""")
    assert result["pendingOffset"] == 100
    assert result["beforeMove"] == {"offset": 100, "pageOffset": 100, "editorOffset": 100}
    assert result["queries"] == [0, 200]
    assert result["uid"] == "destination-200" and result["offset"] == 200
