from flask import Blueprint,request,jsonify
from app.errors import DomainError
from app import store
from app.modules.m8_cleanup import rules
bp=Blueprint("cleanup",__name__)
def _payload():
    body=request.get_json(silent=True)
    if not isinstance(body,dict): raise DomainError("BAD_BODY","请求需要对象")
    return body
def _run_id(body):
    run_id=(body or {}).get("run_id") or request.args.get("run_id")
    if run_id: return run_id
    latest=store.latest()
    return latest["run_id"] if latest else None
def _parcel(body):
    parcel=body.get("parcel")
    if not isinstance(parcel,str) or not parcel.strip(): raise DomainError("BAD_PARCEL","请提供包裹号")
    return parcel.strip()
@bp.get("/api/cleanup")
def view():
    run_id=_run_id(None)
    if not run_id: return jsonify({"run_id":None,"policy":rules.POLICY,"active":None,"versions":[],"releases":[],"events":[],"metrics":None,"execution":None})
    return jsonify(store.cleanup_view(run_id))
@bp.post("/api/cleanup/switch")
def switch():
    body=_payload();run_id=_run_id(body)
    if not run_id: raise DomainError("NOT_FOUND","批次不存在",404)
    base=body.get("base_version")
    if base is not None and not isinstance(base,int): raise DomainError("BAD_VERSION","请提供版本号")
    return jsonify(store.switch_cleanup(run_id,base,rules.evaluate))
@bp.post("/api/cleanup/execute")
def execute():
    body=_payload();run_id=_run_id(body)
    if not run_id: raise DomainError("NOT_FOUND","批次不存在",404)
    if not isinstance(body.get("version"),int): raise DomainError("BAD_VERSION","请提供版本号")
    key=body.get("release_key") or f"{run_id}:{body['version']}"
    if not isinstance(key,str): raise DomainError("BAD_KEY","幂等键需要文本")
    return jsonify(store.execute_cleanup(run_id,body["version"],key))
@bp.post("/api/cleanup/dispute")
def dispute():
    body=_payload();run_id=_run_id(body)
    if not run_id: raise DomainError("NOT_FOUND","批次不存在",404)
    return jsonify(store.dispute_cleanup(run_id,_parcel(body)))
@bp.post("/api/cleanup/confirm")
def confirm():
    body=_payload();run_id=_run_id(body)
    if not run_id: raise DomainError("NOT_FOUND","批次不存在",404)
    return jsonify(store.confirm_cleanup(run_id,_parcel(body)))
