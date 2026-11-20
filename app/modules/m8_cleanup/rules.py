from app.errors import DomainError
POLICY={"unit":"批次统一到期","scope":"逐票争议冻结","rule":"每批仅一份生效清单；赔付未结、复核未结、补传再争议的票冻结保留，其余到期释放","states":["待确认","继续保留","已释放"]}
FREEZE_REVIEW="复核未结"
FREEZE_CLAIM="赔付差异未结"
FREEZE_REDISPUTE="补传再争议"
RELEASE_BASIS="无争议，到期释放"
VERDICTS={"释放","保留"}
STATES={"待确认","继续保留","已释放"}
def freeze_reasons(run,parcel,redisputed):
    "质控冻结信号：任一命中即保留。复核未结为批次级，赔付差异与补传再争议为逐票级。"
    reasons=[]
    row=next((r for r in run["stages"][5]["rows"] if r["parcel"]==parcel),None)
    if row is None: raise DomainError("BAD_PARCEL","包裹不在批次内")
    if row.get("status")=="差异": reasons.append(FREEZE_CLAIM)
    if run.get("decision")!="已复核": reasons.append(FREEZE_REVIEW)
    if parcel in redisputed: reasons.append(FREEZE_REDISPUTE)
    return reasons
def evaluate(run,redisputed=None):
    "纯裁决：按批统一到期生成清单，清单内逐票冻结。同一输入永远得到同一清单。"
    redisputed=set(redisputed or [])
    items=[]
    for row in run["stages"][5]["rows"]:
        parcel=row["parcel"];reasons=freeze_reasons(run,parcel,redisputed)
        if reasons: items.append({"parcel":parcel,"verdict":"保留","state":"继续保留","basis":"；".join(reasons),"disputed":FREEZE_REDISPUTE in reasons,"released_at":None})
        else: items.append({"parcel":parcel,"verdict":"释放","state":"待确认","basis":RELEASE_BASIS,"disputed":False,"released_at":None})
    return {"policy":POLICY,"run_version":run["version"],"decision":run["decision"],"items":items}
def check(payload,run):
    "清单形态校验：必须恰好覆盖批次全部包裹一次，取值合法。"
    items=payload.get("items")
    if not isinstance(items,list): raise DomainError("BAD_CLEANUP_LIST","清单需要明细列表")
    parcels=[r["parcel"] for r in run["stages"][5]["rows"]]
    seen=[i.get("parcel") for i in items]
    if len(seen)!=len(set(seen)) or set(seen)!=set(parcels): raise DomainError("BAD_CLEANUP_LIST","清单必须覆盖批次全部包裹且不得重复")
    for item in items:
        if item.get("verdict") not in VERDICTS or item.get("state") not in STATES: raise DomainError("BAD_CLEANUP_LIST","清单裁决或状态非法")
        if not item.get("basis"): raise DomainError("BAD_CLEANUP_LIST","每票都需要保留依据")
    return payload
