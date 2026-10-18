from test_gold_web_ui import _SETUP, _browser


def test_discovery_clock_excludes_hidden_idle_and_suspended_time():
    result = _browser("""
const clock=newReviewClock(0);
for(let time=1000;time<=70000;time+=1000) tickReviewClock(clock,time,true);
const idle=clock.seconds;
tickReviewClock(clock,71000,false,true);
tickReviewClock(clock,72000,false);
const hidden=clock.seconds;
tickReviewClock(clock,1000000,true,true);
const suspended=clock.seconds;
tickReviewClock(clock,1001000,true);
[idle,hidden,suspended,clock.seconds]
""")
    assert result == [60, 60, 60, 61]


def test_discovery_save_sends_elapsed_seconds_separately_from_human_labels():
    result = _browser(_SETUP + """
detail.mode='discovery';let now=1000;reviewNow=()=>now;renderGoldEditor(detail);
now=3500;let sent=null;
api=async(path,method,payload)=>{sent=payload;return {...detail,version:'v2'};};
refreshGold=async()=>true;
(async()=>{await saveGold();return [sent.review_seconds,sent.relevance,
  Object.keys(goldEditor.savedLabels),goldEditor.reviewClock.seconds];})()
""")
    assert result == [2.5, 3, ["role_label", "relevance", "gate_outcome", "notes"], 0]


def test_discovery_aggregate_report_has_ratios_and_hides_provider_seed_data():
    result = _browser("""
const body=el('section');
renderDiscoveryReport(body,{counts:{relevant:3,strong:1},cost:{total_usd:1.5,usd_per_relevant:0.5},
 time:{machine_wall_seconds:120,machine_seconds_per_relevant:40,human_active_seconds:180,
 human_seconds_per_relevant:60},provider:'PRIVATE_PROVIDER',seed_ids:['PRIVATE_SEED']});
textTree(body).join(' ')
""")
    assert "Расходы на релевантную вакансию" in result
    assert "Время поиска на релевантную вакансию" in result
    assert "PRIVATE_PROVIDER" not in result and "PRIVATE_SEED" not in result
