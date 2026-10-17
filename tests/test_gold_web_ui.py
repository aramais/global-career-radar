from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from job_intake.crm.ui import render_crm_page


def _browser(expression: str) -> object:
    node = shutil.which("node")
    bundled = Path(
        "/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    )
    if node is None and bundled.is_file():
        node = str(bundled)
    if node is None:
        pytest.skip("Node is unavailable for browser workflow checks")
    script = render_crm_page().split("<script>", 1)[1].split("</script>", 1)[0]
    helpers = script.split(
        "document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener", 1
    )[0]
    # A deliberately small DOM runs the real form and save functions, including null/0
    # handling, optimistic concurrency, and navigation after a filtered row disappears.
    dom = r"""
const nodes=new Map();
class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase(); this.children=[]; this.value='';
    this.hidden=false; this.disabled=false; this.dataset={}; this.textContent=''; this.name='';
    this.classList={add:()=>{},remove:()=>{},toggle:()=>{}}; }
  append(...children) { children.forEach(child=>{child.parentElement=this;
    this.children.push(child);}); }
  replaceChildren(...children) { this.children=[]; this.append(...children); }
  setAttribute(key,value) { this[key]=value; }
  addEventListener() {}
  focus() { document.activeElement=this; }
  querySelectorAll(selector) { const tags=selector.split(',').map(s=>s.toUpperCase());
    return this.children.flatMap(child=>[...(tags.includes(child.tagName)?[child]:[]),
      ...child.querySelectorAll(selector)]); }
  get elements() { return {namedItem:(name)=>this.querySelectorAll('input,select,textarea')
    .find(node=>node.name===name)}; }
}
const document={activeElement:null,createElement:tag=>new Element(tag),
 createTextNode:text=>Object.assign(new Element('#text'),{textContent:text}),
 getElementById:id=>{if(!nodes.has(id)) nodes.set(id,new Element()); return nodes.get(id);},
 querySelectorAll:()=>[]};
const window={location:{hash:'',pathname:'/',search:''},confirm:()=>true};
const textTree=node=>[node.textContent,...node.children.flatMap(textTree)];
document.getElementById('modalShade').hidden=true;
document.getElementById('goldStatusFilter').value='all';
const vm=require('node:vm');
const context=vm.createContext({URL,URLSearchParams,document,window,textTree});
"""
    harness = dom + f"vm.runInContext({json.dumps(helpers)},context);"
    harness += (
        f"Promise.resolve(vm.runInContext({json.dumps(expression)},context))"
        ".then(value=>process.stdout.write(JSON.stringify(value)))"
        ".catch(error=>{console.error(error);process.exitCode=1;});"
    )
    result = subprocess.run([node, "-e", harness], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


_SETUP = """
view='gold'; state={};
const detail={job_uid:'one',source:{title:'Manager, Analytics',company:'Acme',
 description_clean:'Lead projects and support decisions.',original_url:'https://example.com/job'},
 labels:{role_label:null,relevance:3,gate_outcome:null,notes:'Human note'},
 version:'v1',complete:false,can_save:true,warnings:[]};
goldPage={cases:[{job_uid:'one',relevance:3,has_labels:true,complete:false},
 {job_uid:'two',relevance:null,has_labels:false,complete:false}],
 profiles:[{value:'analytics',label:'Analytics Leadership'}],
 stats:{total:2,labeled:0,partial:1,unlabeled:1,relevant:1,strong:1},
 offset:0,limit:100,total:2,has_more:false};
"""


def test_gold_partial_prefills_human_three_without_inventing_role_or_gate() -> None:
    assert _browser(
        _SETUP
        + "renderGoldEditor(detail); const initial=goldFormValues(goldEditor.form);"
        "const clean=!goldIsDirty(); goldEditor.form.elements.namedItem('relevance').value='0';"
        "[initial,clean,goldIsDirty(),goldPayload(goldFormValues(goldEditor.form),'v1'),"
        "goldCaseStatus(goldPage.cases[0])]"
    ) == [
        {"role_label": "", "relevance": "3", "gate_outcome": "", "notes": "Human note"},
        True,
        True,
        {"expected_version": "v1", "role_label": None, "relevance": 0,
         "gate_outcome": None, "notes": "Human note"},
        "partial",
    ]


def test_gold_source_is_literal_text_and_machine_answers_are_not_rendered() -> None:
    result = _browser(
        _SETUP
        + "detail.source.title='<img src=x onerror=alert(1)>';"
        "detail.source.description_clean='<script>alert(1)</script> Full original text';"
        "detail.source.score=987654;detail.source.tier='MACHINE_TIER';"
        "detail.classification={label:'MACHINE_LABEL'};renderGoldEditor(detail);"
        "[textTree($('modal')).join(' '),$('modal').querySelectorAll('img,script').length]"
    )
    assert result[1] == 0
    assert "<img src=x onerror=alert(1)>" in result[0]
    assert "<script>alert(1)</script> Full original text" in result[0]
    assert "987654" not in result[0]
    assert "MACHINE_TIER" not in result[0]
    assert "MACHINE_LABEL" not in result[0]


def test_gold_raw_html_fallback_retains_full_safe_readable_text() -> None:
    assert _browser(
        "goldDescription({description_raw:'<h2>About &amp; role</h2><p>First</p>'"
        "+'<script>secret()</script><p>Last &#x1F680;</p>'})"
    ) == "About & role\n\nFirst\n\nLast 🚀"


def test_gold_save_has_version_nulls_and_explicit_zero_then_marks_clean() -> None:
    assert _browser(
        _SETUP
        + "renderGoldEditor(detail);goldEditor.form.elements.namedItem('relevance').value='0';"
        "const calls=[];api=async(path,method,payload)=>{calls.push({path,method,payload});"
        "return {...detail,labels:{...detail.labels,relevance:0},version:'v2'};};"
        "refreshGold=async()=>true;"
        "(async()=>{const saved=await saveGold();return [saved,calls,goldIsDirty(),"
        "goldEditor.detail.version,goldEditor.message.textContent];})()"
    ) == [
        True,
        [{"path": "/api/gold/one", "method": "PATCH", "payload": {
            "expected_version": "v1", "role_label": None, "relevance": 0,
            "gate_outcome": None, "notes": "Human note"}}],
        False,
        "v2",
        "Разметка сохранена.",
    ]


def test_conflict_retains_unsaved_draft_and_does_not_silently_reload_or_retry() -> None:
    result = _browser(
        _SETUP
        + "renderGoldEditor(detail);goldEditor.form.elements.namedItem('notes').value='My draft';"
        "let requests=0;api=async()=>{requests++;"
        "throw Object.assign(new Error('conflict'),{status:409});};"
        "(async()=>{await saveGold();return [requests,goldFormValues(goldEditor.form),"
        "goldIsDirty(),"
        "goldEditor.detail.version,goldEditor.conflicts.hidden,goldEditor.message.textContent,"
        "goldEditor.form.elements.namedItem('notes').disabled];})()"
    )
    assert result[:5] == [1, {"role_label": "", "relevance": "3", "gate_outcome": "",
                              "notes": "My draft"}, True, "v1", False]
    assert "Ваши изменения остаются в форме" in result[5]
    assert result[6] is False


def test_save_and_next_uses_successor_before_saved_row_leaves_pending_filter() -> None:
    assert _browser(
        _SETUP
        + "renderGoldEditor(detail); const calls=[];"
        "api=async(path,method,payload)=>{calls.push(path);return {...detail,version:'v2'};};"
        "refreshGold=async()=>{goldPage.cases=[{job_uid:'two'}];return true;};"
        "openGold=async(uid)=>{calls.push('open:'+uid);return true;};"
        "(async()=>{await saveGold(true);return calls;})()"
    ) == ["/api/gold/one", "open:two"]


def test_unsaved_changes_block_close_and_navigation_when_user_declines_discard() -> None:
    assert _browser(
        _SETUP
        + "renderGoldEditor(detail);goldEditor.form.elements.namedItem('notes').value='Draft';"
        "window.confirm=()=>false;let requests=0;api=async()=>{requests++;return detail;};"
        "(async()=>{const closed=closeModal();await moveGold(1);return [closed,requests,"
        "$ ('modalShade').hidden,goldIsDirty(),"
        "goldEditor.form.elements.namedItem('notes').value];})()"
    ) == [False, 0, False, True, "Draft"]


def test_gold_query_has_independent_filters_and_pagination() -> None:
    assert _browser(
        "$ ('goldQuery').value='Customer.io';$('goldStatusFilter').value='partial';"
        "$ ('profileFilter').value='machine-profile';$('tierFilter').value='C';"
        "goldQuery(100)"
    ) == "/api/gold?status=partial&q=Customer.io&offset=100&limit=100"


def test_gold_direct_link_and_safe_persistence_contract_are_present() -> None:
    page = render_crm_page()
    assert 'data-view="gold"' in page
    assert "window.location?.hash === '#gold'" in page
    assert "if (view === 'gold') await refreshGold(false)" in page
    assert "window.addEventListener('beforeunload'" in page
    assert "innerHTML" not in page
    assert "localStorage" not in page


def test_save_next_does_not_restore_stale_prefetch_when_filter_shifts_page_boundary() -> None:
    assert _browser(
        _SETUP
        + "goldPage.cases=Array.from({length:100},(_,i)=>({job_uid:i===99?'one':'job'+i}));"
        "goldPage.has_more=true;goldPage.total=101;renderGoldEditor(detail);const calls=[];"
        "api=async(path,method)=>{calls.push(path);"
        "return method==='PATCH'?{...detail,version:'v2'}:"
        "{...goldPage,cases:[{job_uid:'successor'}],offset:100,has_more:false};};"
        "refreshGold=async()=>{goldPage={...goldPage,cases:[...goldPage.cases.slice(0,99),"
        "{job_uid:'successor'}],total:100,has_more:false};return true;};"
        "openGold=async(uid)=>{calls.push('open:'+uid);return true;};"
        "(async()=>{await saveGold(true);return [calls,goldOffset,"
        "goldPage.cases[goldPage.cases.length-1].job_uid];})()"
    ) == [
        ["/api/gold?status=all&q=&offset=100&limit=100", "/api/gold/one", "open:successor"],
        0,
        "successor",
    ]


def test_save_without_next_can_navigate_when_saved_row_leaves_filter() -> None:
    assert _browser(
        _SETUP
        + "renderGoldEditor(detail);api=async()=>({...detail,version:'v2'});"
        "refreshGold=async()=>{goldPage.cases=[{job_uid:'two'}];return true;};"
        "(async()=>{await saveGold(false);return await goldNeighborUID(1);})()"
    ) == {"uid": "two", "page": None}


def test_inflight_detail_does_not_open_after_user_leaves_gold_view() -> None:
    assert _browser(
        _SETUP
        + "let finish;api=()=>new Promise(resolve=>{finish=resolve;});"
        "(async()=>{const pending=openGold('two');view='contacts';goldEditor=null;"
        "finish(detail);return [await pending,goldEditor,$('modalShade').hidden];})()"
    ) == [False, None, True]


def test_confirmed_navigation_failure_preserves_unsaved_draft_state() -> None:
    assert _browser(
        _SETUP
        + "renderGoldEditor(detail);goldEditor.form.elements.namedItem('notes').value='Draft';"
        "api=async()=>{throw new Error('offline');};"
        "(async()=>{await moveGold(1);return [goldIsDirty(),"
        "goldEditor.form.elements.namedItem('notes').value];})()"
    ) == [True, "Draft"]


def test_gold_structured_raw_preserves_paragraphs_and_list_instead_of_flat_clean() -> None:
    assert _browser(
        "goldDescription({description_clean:'About role First Last',"
        "description_raw:'<h2>About role</h2><p>First</p><ul><li><p>Item one</p></li>'"
        "+'<li>Item two</li></ul><p>Last</p>'})"
    ) == "About role\n\nFirst\n\n• Item one\n\n• Item two\n\nLast"
    assert _browser(
        "goldDescription({description_clean:'Usable clean text',"
        "description_raw:'<div><script>ignored()</script></div>'})"
    ) == "Usable clean text"
