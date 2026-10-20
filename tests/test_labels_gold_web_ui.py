"""P1 runs the actual v3 editor/save/shortcut functions in the small browser DOM."""

from __future__ import annotations

import json

import pytest
from test_gold_web_ui import _SETUP, _browser
from test_labels_gold_ui import labeling as labeling

from job_intake.annotation.gold import _quote_bindings, _snapshot
from job_intake.annotation.gold_ui import GoldLabelStore
from job_intake.annotation.text import source_hash, source_units
from job_intake.labeling.schema import (
    BASIS,
    DOMAIN_REQUIREMENTS,
    GATE_REASONS,
    INDUSTRIES,
    PREFERRED_FOCUS,
    TAG_VALUES,
)

_OPTIONS = {
    "gate_reasons": list(GATE_REASONS),
    "basis": list(BASIS),
    "tags": {
        **{name: {"values": list(values)} for name, values in TAG_VALUES.items()},
        "domain_fit": {"industries": list(INDUSTRIES),
                       "requirements": list(DOMAIN_REQUIREMENTS), "domain_max": 60},
        "preferred_focus": {"values": list(PREFERRED_FOCUS)},
    },
}
_V3_SETUP = _SETUP + "\n" + f"""
detail.schema='vacancy-labels-v3';detail.label_options={json.dumps(_OPTIONS)};
detail.labels.tags={{}};detail.labels.gate_reasons=[];
goldPage.schema=detail.schema;goldPage.label_options=detail.label_options;
"""


def test_v3_unlabeled_tags_stay_absent_and_form_starts_clean():
    result = _browser(_V3_SETUP + """
renderGoldEditor(detail);
[goldFormValues(goldEditor.form),goldIsDirty(),
 Object.keys(goldEditor.form.goldTagControls),
 Object.values(goldEditor.form.goldTagControls).every(c=>!c.enabled.checked && c.quote.disabled)]
""")
    assert result == [
        {"role_label": "", "relevance": "3", "gate_outcome": "", "notes": "Human note",
         "tags": {}, "gate_reasons": []},
        False,
        ["hiring_eligibility", "work_mode", "working_language", "function_core",
         "domain_fit", "preferred_focus"],
        True,
    ]


def test_all_six_human_tags_roundtrip_without_defaults_or_dirty_noise():
    tags = {
        "hiring_eligibility": {"quote": "Lead projects", "value": "unspecified"},
        "work_mode": {"basis": "explicit", "office_location": "São Paulo",
                      "quote": "Lead projects", "value": "office_sao_paulo"},
        "working_language": {"value": "english", "quote": "Lead projects", "basis": "implied"},
        "function_core": {"value": "analytics_for_decisions", "quote": "Lead projects"},
        "domain_fit": {"industry": "fintech", "requirement": "preferred",
                       "required_domain": "other", "domain": "insurance", "quote": "projects"},
        "preferred_focus": {"values": ["marketplace_economics", "experimentation_causal"],
                            "quote": "support decisions."},
    }
    result = _browser(_V3_SETUP + f"detail.labels.tags={json.dumps(tags)};" + """
detail.labels.gate_reasons=['personal_constraint','office_outside_sp'];
detail.labels.gate_outcome='ineligible';renderGoldEditor(detail);
[goldFormValues(goldEditor.form).tags,goldIsDirty(),
 goldFormValues(goldEditor.form).gate_reasons]
""")
    assert result[0] == tags
    assert result[1] is False
    assert result[2] == ["personal_constraint", "office_outside_sp"]


def test_explicit_empty_focus_is_distinct_from_unlabeled_focus():
    result = _browser(_V3_SETUP + """
detail.labels.tags={preferred_focus:{values:[],quote:''}};renderGoldEditor(detail);
const c=goldEditor.form.goldTagControls.preferred_focus;
const explicit=goldFormValues(goldEditor.form).tags;
c.enabled.checked=false;syncGoldTagStates(goldEditor.form);
[explicit,goldFormValues(goldEditor.form).tags,goldIsDirty()]
""")
    assert result == [{"preferred_focus": {"values": [], "quote": ""}}, {}, True]


def test_explicit_unspecified_is_not_dropped_or_inferred_for_other_tags():
    result = _browser(_V3_SETUP + """
renderGoldEditor(detail);const c=goldEditor.form.goldTagControls.hiring_eligibility;
c.enabled.checked=true;c.value.value='unspecified';syncGoldTagStates(goldEditor.form);
goldPayload(goldFormValues(goldEditor.form),'v1').tags
""")
    assert result == {"hiring_eligibility": {"value": "unspecified", "quote": ""}}


@pytest.mark.parametrize("has_null", [False, True])
def test_domain_none_preserves_absent_vs_explicit_null_required_domain(has_null):
    domain = {"industry": "fintech", "requirement": "none", "quote": ""}
    if has_null:
        domain["required_domain"] = None
    result = _browser(_V3_SETUP + f"detail.labels.tags={{domain_fit:{json.dumps(domain)}}};" + """
renderGoldEditor(detail);[goldFormValues(goldEditor.form).tags.domain_fit,goldIsDirty(),
 goldEditor.form.goldTagControls.domain_fit.required_domain.disabled]
""")
    assert result == [domain, False, True]


def test_quote_insertion_uses_only_selected_source_and_selected_tag():
    result = _browser(_V3_SETUP + """
renderGoldEditor(detail);const inside={},outside={};
goldEditor.quoteSource.contains=node=>node===inside;
goldEditor.quoteBlocks=[{contains:node=>node===inside}];
goldEditor.quoteTarget.value='function_core';
window.getSelection=()=>({rangeCount:1,isCollapsed:false,
 getRangeAt:()=>({startContainer:inside,endContainer:inside}),toString:()=> 'Lead projects'});
const inserted=insertGoldQuote();const first=goldFormValues(goldEditor.form).tags;
window.getSelection=()=>({rangeCount:1,isCollapsed:false,
 getRangeAt:()=>({startContainer:outside,endContainer:inside}),toString:()=> 'UI label'});
const rejected=insertGoldQuote();
[inserted,first,rejected,goldFormValues(goldEditor.form).tags,
 goldEditor.message.textContent,goldIsDirty()]
""")
    assert result[:4] == [True, {"function_core": {"value": "", "quote": "Lead projects"}},
                          False, {"function_core": {"value": "", "quote": "Lead projects"}}]
    assert "Выделите цитату" in result[4]
    assert result[5] is True


def test_quote_source_excludes_ui_headings_links_help_and_warnings():
    result = _browser(_V3_SETUP + """
detail.warnings=['SOURCE_WARNING'];renderGoldEditor(detail);
[textTree(goldEditor.quoteSource).join(' '),
 goldEditor.quoteSource.querySelectorAll('h3,a,button,label').length]
""")
    assert result[1] == 0
    assert "Lead projects and support decisions." in result[0]
    for marker in ["Исходное описание", "SOURCE_WARNING", "Цитата для признака", "Открыть"]:
        assert marker not in result[0]


def test_v3_raw_html_list_selection_saves_exact_canonical_quote(labeling):
    config, path, database, cases, records = labeling
    record = records[0][1]
    record.description_clean = "Lead analytics projects."
    record.description_raw = (
        "<ul><li>Lead analytics projects.</li><li>Full raw detail retained.</li></ul>"
    )
    case = cases[0]
    case.update(source=_snapshot(record), source_hash=source_hash(record), tags={})
    path.write_text(json.dumps(case) + "\n")

    class SourceStore(GoldLabelStore):
        def _source_records(self, uid):
            return database, [(uid, record)], {}

    store = SourceStore(config, path)
    detail = store.detail(case["job_uid"])
    database_before = database.read_bytes(), database.stat().st_mtime_ns
    result = _browser(_V3_SETUP + f"Object.assign(detail,{json.dumps(detail)});" + """
const proto=Object.getPrototypeOf(el('div'));
proto.contains=function(node){return this===node || this.children.some(c=>c.contains(node));};
renderGoldEditor(detail);goldEditor.quoteTarget.value='function_core';
const block=goldEditor.quoteBlocks[goldEditor.quoteBlocks.length-1];
window.getSelection=()=>({rangeCount:1,isCollapsed:false,
 getRangeAt:()=>({startContainer:block,endContainer:block}),toString:()=>block.textContent});
const inserted=insertGoldQuote();
goldEditor.form.goldTagControls.function_core.value.value='analytics_for_decisions';
let sent=null;api=async(path,method,payload)=>{sent=payload;return {...detail,version:'v2'};};
refreshGold=async()=>true;
(async()=>{await saveGold();return [inserted,sent,block.textContent,
 textTree($('modal')).join(' ')];})()
""")
    assert result[0] is True
    assert result[1]["tags"]["function_core"]["quote"] == "Lead analytics projects."
    assert result[2] == "Lead analytics projects."
    assert "Full raw detail retained." in result[3]
    assert "• Lead analytics projects." in result[3]  # readable preview, not quote source
    saved = store.save(case["job_uid"], result[1])
    quote = saved["labels"]["tags"]["function_core"]["quote"]
    assert len(_quote_bindings(record, quote)) == 1
    assert quote == "Lead analytics projects."
    assert (database.read_bytes(), database.stat().st_mtime_ns) == database_before


@pytest.mark.parametrize("metadata_field,values,tag,value,selected_index", [
    ("applicant_location_requirements", "Applicants must live in Europe.",
     "hiring_eligibility", "restricted_elsewhere", 1),
    ("applicant_location_requirements", ["Applicants must live in Europe.",
                                         "Applicants must live in Canada."],
     "hiring_eligibility", "restricted_elsewhere", 2),
    ("working_language", "German required.", "working_language", "other_language_required", 1),
    ("working_language", ["German required.", "French required."],
     "working_language", "other_language_required", 2),
])
def test_metadata_only_constraint_selection_binds_and_saves_in_real_store(
    labeling, metadata_field, values, tag, value, selected_index,
):
    config, path, database, cases, records = labeling
    record = records[0][1]
    record.description_clean = record.description_raw = ""
    record.source_metadata = {metadata_field: values, "description_complete": False}
    case = cases[0]
    case.update(source=_snapshot(record), source_hash=source_hash(record), tags={})
    path.write_text(json.dumps(case) + "\n")

    class SourceStore(GoldLabelStore):
        def _source_records(self, uid):
            return database, [(uid, record)], {}

    store = SourceStore(config, path)
    detail = store.detail(case["job_uid"])
    assert detail["can_save"]
    quote = values[selected_index - 1] if isinstance(values, list) else values
    database_before = database.read_bytes(), database.stat().st_mtime_ns
    result = _browser(_V3_SETUP + f"Object.assign(detail,{json.dumps(detail)});" + f"""
const proto=Object.getPrototypeOf(el('div'));
proto.contains=function(node){{return this===node || this.children.some(c=>c.contains(node));}};
renderGoldEditor(detail);goldEditor.quoteTarget.value={json.dumps(tag)};
const block=goldEditor.quoteBlocks.find(node=>node.textContent==={json.dumps(quote)});
window.getSelection=()=>({{rangeCount:1,isCollapsed:false,
 getRangeAt:()=>({{startContainer:block,endContainer:block}}),toString:()=>block.textContent}});
const inserted=insertGoldQuote(),control=goldEditor.form.goldTagControls[{json.dumps(tag)}];
control.value.value={json.dumps(value)};control.basis.value='explicit';
let sent=null;api=async(path,method,payload)=>{{sent=payload;return {{...detail,version:'v2'}};}};
refreshGold=async()=>true;
(async()=>{{await saveGold();return [inserted,sent,block.textContent,
 goldEditor.quoteBlocks.map(node=>node.textContent),textTree($('modal')).join(' ')];}})()
""")
    assert result[0] is True
    assert result[1]["tags"][tag]["quote"] == result[2] == quote
    for item in values if isinstance(values, list) else [values]:
        assert item in result[3]
    title = ("Требования к местоположению кандидата" if metadata_field.startswith("applicant")
             else "Требования к рабочему языку")
    assert title in result[4] and title not in result[3]
    saved = store.save(case["job_uid"], result[1])
    assert saved["labels"]["tags"][tag]["quote"] == quote
    assert _quote_bindings(record, quote) == [[
        f"source_metadata.{metadata_field}:{selected_index:06d}",
    ]]
    assert (database.read_bytes(), database.stat().st_mtime_ns) == database_before


def test_v3_shows_all_canonical_source_fields_as_separate_literal_blocks(labeling):
    _, _, _, _, records = labeling
    record = records[0][1]
    record.location_text = "São Paulo, Brasil"
    record.remote_text = "Remote in Brazil"
    record.employment_type = "Full time"
    record.salary_text = "$90,000–120,000"
    record.timezone_text = "UTC−3 to UTC+1"
    record.source_metadata = {"applicant_location_requirements": ["Live in Brazil.",
                                                                "Work permit required."],
                              "working_language": "Português e inglês."}
    source = _snapshot(record)
    result = _browser(_V3_SETUP + f"detail.source={json.dumps(source)};" + """
renderGoldEditor(detail);
[goldEditor.quoteBlocks.map(node=>node.textContent),textTree($('modal')).join(' ')]
""")
    for unit in source_units(record):
        # Descriptions may contain multiple units; each is retained literally in its field.
        assert any(unit["text"] in block for block in result[0])
    assert result[0][:-1] == [record.title, record.company, record.location_text,
                            record.remote_text, record.employment_type, record.salary_text,
                            record.timezone_text, "Live in Brazil.", "Work permit required.",
                            "Português e inglês."]
    for title in ["Тип занятости", "Оплата", "Часовой пояс",
                  "Требования к местоположению кандидата", "Требования к рабочему языку"]:
        assert title in result[1] and title not in result[0]


def test_v3_canonical_source_whitelist_ignores_nonstring_and_machine_metadata():
    result = _browser(_V3_SETUP + """
detail.source.location_text={value:'NONSTRING_LOCATION'};
detail.source.remote_text=['NONSTRING_REMOTE'];detail.source.employment_type=654321;
detail.source.salary_text={value:'NONSTRING_SALARY'};detail.source.timezone_text=true;
detail.source.source_metadata={applicant_location_requirements:[
 'Applicants must live in Europe.',{value:'NONSTRING_METADATA'},123456,null,false],
 working_language:{value:'NONSTRING_LANGUAGE'},description_complete:true,
 labels:{reason:'MACHINE_METADATA_LABELS'},labels_v3:{reason:'MACHINE_METADATA_V3'},
 annotation:'MACHINE_METADATA_ANNOTATION',predictions:['MACHINE_METADATA_PREDICTIONS']};
detail.source.labels={quote:'MACHINE_SOURCE_LABELS'};
detail.source.annotation={reason:'MACHINE_SOURCE_ANNOTATION'};
detail.source.predictions=['MACHINE_SOURCE_PREDICTIONS'];renderGoldEditor(detail);
[goldEditor.quoteBlocks.map(node=>node.textContent),textTree($('modal')).join(' ')]
""")
    assert result[0] == ["Manager, Analytics", "Acme", "Applicants must live in Europe.",
                         "Lead projects and support decisions."]
    for marker in ["NONSTRING", "MACHINE_", "654321", "123456", "description_complete"]:
        assert marker not in result[1]


def test_v3_quote_fallback_is_literal_raw_and_legacy_renderer_is_unchanged():
    result = _browser(_V3_SETUP + """
detail.source.description_clean='';detail.source.description_raw='<ul><li>Raw only.</li></ul>';
renderGoldEditor(detail);
[goldEditor.quoteBlocks.at(-1).textContent,goldDescription(detail.source)]
""")
    assert result == ["<ul><li>Raw only.</li></ul>", "• Raw only."]


def test_quote_selection_cannot_cross_source_fields_or_select_formatted_preview():
    result = _browser(_V3_SETUP + """
detail.source.location_text='São Paulo';detail.source.remote_text='Hybrid';renderGoldEditor(detail);
const proto=Object.getPrototypeOf(el('div'));
proto.contains=function(node){return this===node || this.children.some(c=>c.contains(node));};
const [first,second]=goldEditor.quoteBlocks;
window.getSelection=()=>({rangeCount:1,isCollapsed:false,
 getRangeAt:()=>({startContainer:first,endContainer:second}),toString:()=> 'Joined UI text'});
[goldSelectedQuote(),goldEditor.quoteBlocks.map(node=>node.textContent)]
""")
    assert result[0] == ""
    assert result[1] == ["Manager, Analytics", "Acme", "São Paulo", "Hybrid",
                         "Lead projects and support decisions."]


def test_quote_selection_rejects_metadata_labels_cross_metadata_values_and_raw_preview():
    result = _browser(_V3_SETUP + """
detail.source.source_metadata={applicant_location_requirements:['Europe only.','Canada only.']};
detail.source.description_raw='<ul><li>Lead projects and support decisions.</li></ul>';
const proto=Object.getPrototypeOf(el('div'));
proto.contains=function(node){return this===node || this.children.some(c=>c.contains(node));};
renderGoldEditor(detail);
const first=goldEditor.quoteBlocks.find(node=>node.textContent==='Europe only.');
const second=goldEditor.quoteBlocks.find(node=>node.textContent==='Canada only.');
const sourceLabel=goldEditor.quoteSource.children.find(node=>
 node.textContent==='Требования к местоположению кандидата');
const preview=$('modal').querySelectorAll('details')[0].querySelectorAll('div')[0];
const selectionFrom=(start,end,text)=>goldSelectedQuote({rangeCount:1,isCollapsed:false,
 getRangeAt:()=>({startContainer:start,endContainer:end}),toString:()=>text});
[selectionFrom(first,second,'Europe only. Canada only.'),
 selectionFrom(sourceLabel,first,'Требования к местоположению кандидата Europe only.'),
 selectionFrom(preview,preview,preview.textContent),goldEditor.quoteBlocks.includes(preview)]
""")
    assert result == ["", "", "", False]


@pytest.mark.parametrize("key,field,value", [
    ("0", "relevance", "0"), ("3", "relevance", "3"),
    ("e", "gate_outcome", "eligible"), ("i", "gate_outcome", "ineligible"),
    ("u", "gate_outcome", "unknown"),
])
def test_shortcut_updates_exact_human_field_when_not_editing(key, field, value):
    result = _browser(_V3_SETUP + f"""
renderGoldEditor(detail);let prevented=false;
const handled=handleGoldShortcut({{key:{json.dumps(key)},target:el('div'),
 preventDefault:()=>prevented=true}});
[handled,prevented,goldEditor.form.elements.namedItem({json.dumps(field)}).value]
""")
    assert result == [True, True, value]


@pytest.mark.parametrize("target", ["input", "textarea", "select", "button", "a", "summary",
                                    "editable"])
def test_shortcuts_do_not_hijack_typing_or_native_controls(target):
    result = _browser(_V3_SETUP + f"""
renderGoldEditor(detail);const target=el({json.dumps(target)});
target.isContentEditable={str(target == 'editable').lower()};let prevented=false;
const handled=handleGoldShortcut({{key:'0',target,preventDefault:()=>prevented=true}});
[handled,prevented,goldEditor.form.elements.namedItem('relevance').value]
""")
    assert result == [False, False, "3"]


@pytest.mark.parametrize("key", ["Enter", "i", "0", "3"])
@pytest.mark.parametrize("nested", [False, True])
def test_raw_preview_summary_and_descendants_keep_native_keys_without_save_or_next(key, nested):
    result = _browser(_V3_SETUP + f"""
detail.source.description_raw='<ul><li>Lead projects and support decisions.</li></ul>';
renderGoldEditor(detail);const summary=$('modal').querySelectorAll('summary')[0];
const target={str(nested).lower()} ? el('span','Preview') : summary;
if (target!==summary) summary.append(target);
const calls=[];api=async(...args)=>{{calls.push(['api',...args]);return detail;}};
saveGold=next=>calls.push(['save',next]);openGold=uid=>calls.push(['open',uid]);
let prevented=false;const before=goldFormValues(goldEditor.form);
const handled=handleGoldShortcut({{key:{json.dumps(key)},target,
 preventDefault:()=>prevented=true}});
[handled,prevented,before,goldFormValues(goldEditor.form),goldIsDirty(),calls]
""")
    assert result[0:2] == [False, False]
    assert result[2] == result[3]
    assert result[4:] == [False, []]


@pytest.mark.parametrize("modifier", [
    "ctrlKey", "altKey", "metaKey", "shiftKey", "isComposing", "repeat",
])
def test_shortcuts_ignore_modifiers_composition_and_repeated_keys(modifier):
    assert _browser(_V3_SETUP + f"""
renderGoldEditor(detail);
[handleGoldShortcut({{key:'0',{modifier}:true,target:el('div'),preventDefault:()=>{{}}}}),
 goldEditor.form.elements.namedItem('relevance').value]
""") == [False, "3"]


def test_enter_shortcut_saves_next_only_when_editor_can_save():
    assert _browser(_V3_SETUP + """
renderGoldEditor(detail);const calls=[];saveGold=next=>calls.push(next);
const first=handleGoldShortcut({key:'Enter',target:el('div'),preventDefault:()=>{}});
goldEditor.detail.can_save=false;
const blocked=handleGoldShortcut({key:'Enter',target:el('div'),preventDefault:()=>{}});
[first,blocked,calls]
""") == [True, False, [True]]


def test_eligible_shortcut_clears_gate_reasons_and_disables_incompatible_choices():
    result = _browser(_V3_SETUP + """
detail.labels.gate_outcome='ineligible';detail.labels.gate_reasons=['office_outside_sp'];
renderGoldEditor(detail);handleGoldShortcut({key:'e',target:el('div'),preventDefault:()=>{}});
[goldPayload(goldFormValues(goldEditor.form),'v1').gate_reasons,
 goldEditor.form.goldGateReasons.every(c=>!c.input.checked && c.input.disabled)]
""")
    assert result == [[], True]


def test_v3_save_sends_tags_and_review_time_separately_then_marks_clean():
    result = _browser(_V3_SETUP + """
let now=1000;reviewNow=()=>now;renderGoldEditor(detail);now=4000;
const c=goldEditor.form.goldTagControls.preferred_focus;c.enabled.checked=true;
c.choices[0].input.checked=true;c.quote.value='Lead projects';
goldEditor.form.elements.namedItem('relevance').value='0';let sent=null;
api=async(path,method,payload)=>{sent=payload;
 const {expected_version,review_seconds,...labels}=payload;
 return {...detail,labels,version:'v2'};};refreshGold=async()=>true;
(async()=>{const saved=await saveGold();return [saved,sent,goldIsDirty(),
 Object.hasOwn(goldEditor.savedLabels,'review_seconds'),goldEditor.reviewClock.seconds];})()
""")
    assert result == [True, {
        "expected_version": "v1", "role_label": None, "relevance": 0,
        "gate_outcome": None, "notes": "Human note", "gate_reasons": [],
        "tags": {"preferred_focus": {"values": ["pricing_monetization"], "quote": "Lead projects"}},
        "review_seconds": 3,
    }, False, False, 0]


def test_v3_conflict_keeps_tag_draft_and_enabled_state():
    result = _browser(_V3_SETUP + """
renderGoldEditor(detail);const c=goldEditor.form.goldTagControls.work_mode;
c.enabled.checked=true;c.value.value='remote';c.quote.value='Lead projects';
api=async()=>{throw Object.assign(new Error('conflict'),{status:409});};
(async()=>{await saveGold();return [goldIsDirty(),goldFormValues(goldEditor.form).tags,
 goldEditor.conflicts.hidden,c.value.disabled,
 goldEditor.form.goldTagControls.hiring_eligibility.quote.disabled];})()
""")
    assert result == [True, {"work_mode": {"value": "remote", "quote": "Lead projects"}},
                      False, False, True]


def test_v3_editor_remains_blind_to_machine_labels_scores_and_annotations():
    result = _browser(_V3_SETUP + """
detail.source.annotation={labels_v3:{relevance:999,tags:{function_core:'MACHINE_SOURCE'}}};
detail.source.fit_score=987654;detail.tier='MACHINE_TIER';detail.labels_v3={reason:'MACHINE_REASON'};
detail.source.description_clean='<img onerror=alert(1)> Lead projects';renderGoldEditor(detail);
[textTree($('modal')).join(' '),$('modal').querySelectorAll('img,script').length]
""")
    assert result[1] == 0
    for marker in ["MACHINE_SOURCE", "987654", "MACHINE_TIER", "MACHINE_REASON"]:
        assert marker not in result[0]
    assert "<img onerror=alert(1)> Lead projects" in result[0]


def test_v2_schema_keeps_legacy_payload_and_also_records_review_time():
    result = _browser(_SETUP + """
detail.schema='vacancy-tags-v2';let now=0;reviewNow=()=>now;renderGoldEditor(detail);now=2000;
let sent=null;api=async(path,method,payload)=>{sent=payload;return {...detail,version:'v2'};};
refreshGold=async()=>true;
(async()=>{await saveGold();return [sent,Object.keys(goldEditor.savedLabels),
 Boolean(goldEditor.form.goldTagControls)];})()
""")
    assert result == [{"expected_version": "v1", "role_label": None, "relevance": 3,
                       "gate_outcome": None, "notes": "Human note", "review_seconds": 2},
                      ["role_label", "relevance", "gate_outcome", "notes"], False]
