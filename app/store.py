import sqlite3,json
from pathlib import Path
from datetime import datetime,timezone
from app.config import DATA_DIR
from app.errors import DomainError
from app.modules.m8_cleanup import rules as cleanup_rules
def _serialized(fn):
    "SQLite快照冲突（并发写）转为可重试的409，而不是500。"
    import functools
    @functools.wraps(fn)
    def wrapper(*args,**kwargs):
        try: return fn(*args,**kwargs)
        except sqlite3.OperationalError as e:
            if "locked" in str(e): raise DomainError("CONCURRENT_WRITE","并发写入冲突，请重试",409)
            raise
    return wrapper
def connect():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA_DIR/"records.db",timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    db.execute("CREATE TABLE IF NOT EXISTS cleanup_lists (run_id TEXT NOT NULL,version INTEGER NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(run_id,version))")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS cleanup_one_active ON cleanup_lists(run_id) WHERE state='生效中'")
    db.execute("CREATE TABLE IF NOT EXISTS cleanup_releases (run_id TEXT NOT NULL,parcel TEXT NOT NULL,list_version INTEGER NOT NULL,release_key TEXT NOT NULL,released_at TEXT NOT NULL,PRIMARY KEY(run_id,parcel))")
    db.execute("CREATE TABLE IF NOT EXISTS cleanup_events (run_id TEXT NOT NULL,parcel TEXT NOT NULL,kind TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(run_id,parcel,kind))")
    return db
def save(run_id,payload):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)",(run_id,json.dumps(payload,ensure_ascii=False)))
    return get(run_id)
def get(run_id):
    with connect() as db: row=db.execute("SELECT payload,version,decision FROM runs WHERE id=?",(run_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND","批次不存在",404)
    return {**json.loads(row[0]),"version":row[1],"decision":row[2]}
def latest():
    with connect() as db: row=db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None
def history():
    with connect() as db: return [{"run_id":i,"version":v,"decision":d} for i,v,d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]
def review(run_id,version,decision):
    if decision not in {"已复核","退回复核"}: raise DomainError("BAD_DECISION","复核状态无效")
    with connect() as db:
        cursor=db.execute("UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?",(decision,run_id,version))
        if cursor.rowcount!=1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?",(run_id,)).fetchone(): raise DomainError("NOT_FOUND","批次不存在",404)
            raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
    return get(run_id)
def _now(): return datetime.now(timezone.utc).isoformat()
def _redisputed(db,run_id): return {r[0] for r in db.execute("SELECT parcel FROM cleanup_events WHERE run_id=? AND kind='补传再争议'",(run_id,))}
def _active(db,run_id): return db.execute("SELECT version,state,payload,created_at FROM cleanup_lists WHERE run_id=? AND state='生效中'",(run_id,)).fetchone()
def _released_map(db,run_id): return {p:(v,k,t) for p,v,k,t in db.execute("SELECT parcel,list_version,release_key,released_at FROM cleanup_releases WHERE run_id=?",(run_id,))}
@_serialized
def switch_cleanup(run_id,base_version,build):
    "生成并切换生效清单。校验、停用旧版、启用新版在同一事务；任何失败整体回滚，旧版清单保持生效。"
    run=get(run_id)
    with connect() as db:
        old=_active(db,run_id)
        if old and base_version is not None and old[0]!=base_version: raise DomainError("STALE_VERSION","清单版本已更新，请刷新",409)
        payload=cleanup_rules.check(build(run,_redisputed(db,run_id)),run)
        claims={r["parcel"] for r in run["stages"][5]["rows"] if r.get("status")=="差异"}
        frozen=_redisputed(db,run_id)|claims|({i["parcel"] for i in payload["items"]} if run.get("decision")!="已复核" else set())
        if any(i["verdict"]=="释放" and i["parcel"] in frozen for i in payload["items"]): raise DomainError("CLEANUP_UNSAFE","清单含争议票释放项，已保留旧版清单")
        released=_released_map(db,run_id)
        for item in payload["items"]:
            if item["parcel"] in released: item["state"],item["released_at"]="已释放",released[item["parcel"]][2]
        version=(old[0]+1) if old else 1
        db.execute("UPDATE cleanup_lists SET state='已替换' WHERE run_id=? AND state='生效中'",(run_id,))
        try: db.execute("INSERT INTO cleanup_lists VALUES (?,?,?,?,?)",(run_id,version,"生效中",json.dumps(payload,ensure_ascii=False),_now()))
        except sqlite3.IntegrityError: raise DomainError("STALE_VERSION","清单版本已更新，请刷新",409)
    return cleanup_view(run_id)
@_serialized
def execute_cleanup(run_id,version,release_key):
    "执行生效清单：仅释放未争议的待确认票。结论首次执行即落库，重试返回原结论，不重复释放、不改变结论。"
    get(run_id)
    with connect() as db:
        row=_active(db,run_id)
        if not row: raise DomainError("NO_CLEANUP_LIST","批次还没有生效清单，请先生成")
        if row[0]!=version: raise DomainError("STALE_VERSION","清单版本已更新，请刷新",409)
        payload=json.loads(row[2])
        if "execution" not in payload:
            released=[];skipped=[];now=_now()
            for item in payload["items"]:
                if item["verdict"]=="释放" and item["state"]=="待确认" and not item["disputed"]:
                    db.execute("INSERT OR IGNORE INTO cleanup_releases VALUES (?,?,?,?,?)",(run_id,item["parcel"],version,release_key,now))
                    item["state"]="已释放";item["released_at"]=now;released.append(item["parcel"])
                elif item["verdict"]=="释放" and item["state"]=="待确认": skipped.append(item["parcel"])
            payload["execution"]={"release_key":release_key,"executed_at":now,"released":released,"skipped_disputed":skipped}
            db.execute("UPDATE cleanup_lists SET payload=? WHERE run_id=? AND version=? AND json_extract(payload,'$.execution') IS NULL",(json.dumps(payload,ensure_ascii=False),run_id,version))
    return cleanup_view(run_id)
@_serialized
def dispute_cleanup(run_id,parcel):
    "补传再争议：事件落库（同票同事种幂等），并同步生效清单三态——已释放不回退、继续保留加依据、待确认保持待确认且执行跳过。"
    run=get(run_id)
    if parcel not in {r["parcel"] for r in run["stages"][5]["rows"]}: raise DomainError("BAD_PARCEL","包裹不在批次内")
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO cleanup_events VALUES (?,?,?,?)",(run_id,parcel,"补传再争议",_now()))
        row=_active(db,run_id)
        if row:
            payload=json.loads(row[2])
            for item in payload["items"]:
                if item["parcel"]==parcel:
                    item["disputed"]=True
                    if item["state"]=="继续保留" and "补传再争议" not in item["basis"]: item["basis"]+="；补传再争议"
            db.execute("UPDATE cleanup_lists SET payload=? WHERE run_id=? AND version=?",(json.dumps(payload,ensure_ascii=False),run_id,row[0]))
    return cleanup_view(run_id)
@_serialized
def confirm_cleanup(run_id,parcel):
    "质控确认：待确认且再争议的票转为继续保留并记依据；其余状态不变。"
    get(run_id)
    with connect() as db:
        row=_active(db,run_id)
        if not row: raise DomainError("NO_CLEANUP_LIST","批次还没有生效清单，请先生成")
        payload=json.loads(row[2]);found=False
        for item in payload["items"]:
            if item["parcel"]==parcel:
                found=True
                if item["state"]=="待确认" and item["disputed"]: item["state"]="继续保留";item["basis"]="补传再争议；争议确认保留"
        if not found: raise DomainError("BAD_PARCEL","包裹不在清单内")
        db.execute("UPDATE cleanup_lists SET payload=? WHERE run_id=? AND version=?",(json.dumps(payload,ensure_ascii=False),run_id,row[0]))
    return cleanup_view(run_id)
def cleanup_view(run_id):
    run=get(run_id)
    with connect() as db:
        versions=[{"version":v,"state":s,"created_at":c} for v,s,c in db.execute("SELECT version,state,created_at FROM cleanup_lists WHERE run_id=? ORDER BY version DESC",(run_id,))]
        active_row=_active(db,run_id)
        releases=[{"parcel":p,"list_version":v,"release_key":k,"released_at":t} for p,(v,k,t) in sorted(_released_map(db,run_id).items())]
        events=[{"parcel":p,"kind":k,"created_at":t} for p,k,t in db.execute("SELECT parcel,kind,created_at FROM cleanup_events WHERE run_id=? ORDER BY created_at",(run_id,))]
    active=metrics=execution=None
    if active_row:
        payload=json.loads(active_row[2]);items=payload["items"]
        active={"version":active_row[0],"state":active_row[1],"created_at":active_row[3],"items":items}
        metrics={"parcels":len(items),"pending":sum(i["state"]=="待确认" for i in items),"retained":sum(i["state"]=="继续保留" for i in items),"released":sum(i["state"]=="已释放" for i in items)}
        execution=payload.get("execution")
    return {"run_id":run_id,"decision":run["decision"],"policy":cleanup_rules.POLICY,"active":active,"versions":versions,"releases":releases,"events":events,"metrics":metrics,"execution":execution}
