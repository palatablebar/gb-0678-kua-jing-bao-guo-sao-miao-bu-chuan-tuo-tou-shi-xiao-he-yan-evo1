import pytest
from app import store
from app.pipeline import execute,load_sample
from app.modules.m8_cleanup import rules
from app.errors import DomainError
@pytest.fixture
def run(): return execute(load_sample("dirty.csv"))
@pytest.fixture
def reviewed(run):
    store.review(run["run_id"],run["version"],"已复核")
    return store.get(run["run_id"])
def switch(run,base=None): return store.switch_cleanup(run["run_id"],base,rules.evaluate)
def items(view): return {i["parcel"]:i for i in view["active"]["items"]}
def released_parcels(view): return {r["parcel"] for r in view["releases"]}
def test_policy_batch_unit_per_ticket_freeze():
    "裁决：批次为执行单元统一到期，逐票为冻结单元。"
    assert rules.POLICY["unit"]=="批次统一到期" and rules.POLICY["scope"]=="逐票争议冻结"
def test_rules_deterministic(reviewed):
    assert rules.evaluate(reviewed,{"0002"})==rules.evaluate(reviewed,{"0002"})
def test_unreviewed_batch_retains_everything(run):
    view=switch(run)
    assert all(i["verdict"]=="保留" and i["state"]=="继续保留" for i in view["active"]["items"])
    assert all("复核未结" in i["basis"] for i in view["active"]["items"])
def test_reviewed_batch_freezes_only_disputed(reviewed):
    by=items(switch(reviewed))
    assert by["0001"]["verdict"]=="保留" and "赔付差异未结" in by["0001"]["basis"]
    assert all(by[p]["verdict"]=="释放" and by[p]["state"]=="待确认" for p in by if p!="0001")
def test_every_item_has_basis(reviewed):
    assert all(i["basis"] for i in switch(reviewed)["active"]["items"])
def test_single_active_list_per_batch(reviewed):
    switch(reviewed);view=switch(reviewed,1)
    assert [v["state"] for v in view["versions"]].count("生效中")==1
    assert view["active"]["version"]==2 and view["versions"][1]["state"]=="已替换"
def test_stale_switch_409_keeps_old_list(reviewed):
    switch(reviewed)
    with pytest.raises(DomainError) as e: store.switch_cleanup(reviewed["run_id"],99,rules.evaluate)
    assert e.value.status==409
    view=store.cleanup_view(reviewed["run_id"])
    assert view["active"]["version"]==1 and len(view["versions"])==1
def test_unsafe_switch_rolls_back(reviewed):
    "切换的新清单含争议票释放项：激活失败，旧版清单保持生效。"
    switch(reviewed)
    def unsafe(run,redisputed): return {"items":[{"parcel":r["parcel"],"verdict":"释放","state":"待确认","basis":"x","disputed":False,"released_at":None} for r in run["stages"][5]["rows"]]}
    with pytest.raises(DomainError) as e: store.switch_cleanup(reviewed["run_id"],1,unsafe)
    assert e.value.code=="CLEANUP_UNSAFE"
    view=store.cleanup_view(reviewed["run_id"])
    assert view["active"]["version"]==1 and len(view["versions"])==1
def test_raising_switch_rolls_back(reviewed):
    switch(reviewed)
    def boom(run,redisputed): raise DomainError("INJECTED","注入失败")
    with pytest.raises(DomainError): store.switch_cleanup(reviewed["run_id"],1,boom)
    assert store.cleanup_view(reviewed["run_id"])["active"]["version"]==1
def test_incomplete_list_rejected(reviewed):
    with pytest.raises(DomainError) as e: store.switch_cleanup(reviewed["run_id"],None,lambda run,dis:{"items":[]})
    assert e.value.code=="BAD_CLEANUP_LIST"
def test_execute_releases_clean_tickets(reviewed):
    view=switch(reviewed)
    view=store.execute_cleanup(reviewed["run_id"],view["active"]["version"],"key-1")
    assert released_parcels(view)=={p for p in items(view) if p!="0001"}
    assert view["metrics"]["released"]==7 and items(view)["0001"]["state"]=="继续保留"
def test_execute_retry_no_double_release_same_conclusion(reviewed):
    "同一清理重试：不重复释放、不改变结论，换幂等键也一样。"
    view=switch(reviewed);v=view["active"]["version"]
    first=store.execute_cleanup(reviewed["run_id"],v,"key-1")
    again=store.execute_cleanup(reviewed["run_id"],v,"key-1")
    assert again["execution"]==first["execution"] and len(again["releases"])==len(first["releases"])
    assert [r["released_at"] for r in again["releases"]]==[r["released_at"] for r in first["releases"]]
    third=store.execute_cleanup(reviewed["run_id"],v,"key-other")
    assert third["execution"]==first["execution"] and len(third["releases"])==len(first["releases"])
def test_execute_skips_disputed_pending(reviewed):
    view=switch(reviewed);v=view["active"]["version"]
    store.dispute_cleanup(reviewed["run_id"],"0002")
    view=store.execute_cleanup(reviewed["run_id"],v,"k")
    assert items(view)["0002"]["state"]=="待确认" and "0002" not in released_parcels(view)
    assert view["execution"]["skipped_disputed"]==["0002"]
    again=store.execute_cleanup(reviewed["run_id"],v,"k")
    assert again["execution"]==view["execution"] and items(again)["0002"]["state"]=="待确认"
def test_redispute_three_states_visible(reviewed):
    "补传再争议后页面三态：已释放、继续保留、待确认。"
    view=switch(reviewed);v=view["active"]["version"]
    store.dispute_cleanup(reviewed["run_id"],"0002")
    store.execute_cleanup(reviewed["run_id"],v,"k")
    view=store.dispute_cleanup(reviewed["run_id"],"0003")
    by=items(view)
    assert by["0003"]["state"]=="已释放" and by["0003"]["disputed"] is True
    assert by["0001"]["state"]=="继续保留"
    assert by["0002"]["state"]=="待确认" and by["0002"]["disputed"] is True
    assert {i["state"] for i in by.values()}=={"待确认","继续保留","已释放"}
def test_confirm_retains_disputed_pending(reviewed):
    view=switch(reviewed);v=view["active"]["version"]
    store.dispute_cleanup(reviewed["run_id"],"0002")
    view=store.confirm_cleanup(reviewed["run_id"],"0002")
    assert items(view)["0002"]["state"]=="继续保留" and "争议确认保留" in items(view)["0002"]["basis"]
    view=store.execute_cleanup(reviewed["run_id"],v,"k")
    assert "0002" not in released_parcels(view)
def test_disputed_parcel_never_released_across_versions(reviewed):
    view=switch(reviewed)
    store.execute_cleanup(reviewed["run_id"],view["active"]["version"],"k1")
    view=switch(reviewed,view["active"]["version"])
    view=store.execute_cleanup(reviewed["run_id"],view["active"]["version"],"k2")
    assert "0001" not in released_parcels(view) and items(view)["0001"]["state"]=="继续保留"
def test_released_stays_released_in_new_version(reviewed):
    view=switch(reviewed)
    store.execute_cleanup(reviewed["run_id"],view["active"]["version"],"k1")
    view=switch(reviewed,view["active"]["version"])
    assert items(view)["0002"]["state"]=="已释放" and items(view)["0002"]["released_at"]
    view=store.execute_cleanup(reviewed["run_id"],view["active"]["version"],"k2")
    assert view["execution"]["released"]==[] and len(view["releases"])==7
def test_dispute_before_list_freezes_next_plan(reviewed):
    store.dispute_cleanup(reviewed["run_id"],"0002")
    by=items(switch(reviewed))
    assert by["0002"]["verdict"]=="保留" and "补传再争议" in by["0002"]["basis"]
def test_dispute_event_idempotent(reviewed):
    store.dispute_cleanup(reviewed["run_id"],"0002");view=store.dispute_cleanup(reviewed["run_id"],"0002")
    assert len(view["events"])==1
def test_execute_stale_version_409(reviewed):
    view=switch(reviewed);v=view["active"]["version"]
    switch(reviewed,v)
    with pytest.raises(DomainError) as e: store.execute_cleanup(reviewed["run_id"],v,"k")
    assert e.value.status==409
def test_execute_without_list_422(run):
    with pytest.raises(DomainError) as e: store.execute_cleanup(run["run_id"],1,"k")
    assert e.value.code=="NO_CLEANUP_LIST"
def test_dispute_unknown_parcel_422(reviewed):
    with pytest.raises(DomainError) as e: store.dispute_cleanup(reviewed["run_id"],"9999")
    assert e.value.code=="BAD_PARCEL"
def test_concurrent_execute_single_conclusion(reviewed):
    "并发执行同一生效清单：结论唯一，释放记录不翻倍。"
    import threading
    view=switch(reviewed);v=view["active"]["version"]
    conclusions=[]
    def work():
        for _ in range(5):
            try: conclusions.append(store.execute_cleanup(reviewed["run_id"],v,"k")["execution"]);return
            except DomainError as e:
                if e.code!="CONCURRENT_WRITE": raise
    threads=[threading.Thread(target=work) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert conclusions and all(c==conclusions[0] for c in conclusions)
    assert len(store.cleanup_view(reviewed["run_id"])["releases"])==7
def test_concurrent_switch_single_active(reviewed):
    "并发切换：无论成败，批次始终至多一份生效清单。"
    import threading
    errors=[]
    def work():
        try: store.switch_cleanup(reviewed["run_id"],None,rules.evaluate)
        except DomainError as e: errors.append(e)
    threads=[threading.Thread(target=work) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    view=store.cleanup_view(reviewed["run_id"])
    assert [v["state"] for v in view["versions"]].count("生效中")==1
    assert all(e.code in {"STALE_VERSION","CONCURRENT_WRITE"} for e in errors)
def test_api_cleanup_flow(client):
    assert client.get("/api/cleanup").status_code==200
    rid=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()["run_id"]
    assert client.get("/api/cleanup",query_string={"run_id":rid}).status_code==200
    assert client.post("/api/runs/"+rid+"/review",json={"version":1,"decision":"已复核"}).status_code==200
    view=client.post("/api/cleanup/switch",json={"run_id":rid}).get_json()
    assert view["active"]["version"]==1
    assert client.post("/api/cleanup/dispute",json={"run_id":rid,"parcel":"0002"}).status_code==200
    first=client.post("/api/cleanup/execute",json={"run_id":rid,"version":1,"release_key":"k"}).get_json()
    retry=client.post("/api/cleanup/execute",json={"run_id":rid,"version":1,"release_key":"k"}).get_json()
    assert retry["execution"]==first["execution"]
    assert client.post("/api/cleanup/switch",json={"run_id":rid,"base_version":99}).status_code==409
    page=client.get("/cleanup")
    assert page.status_code==200
    for state in ("已释放","继续保留","待确认"): assert state.encode() in page.data
