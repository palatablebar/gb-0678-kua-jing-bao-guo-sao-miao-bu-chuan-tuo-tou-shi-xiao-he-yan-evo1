# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 清理裁决

`GET /api/cleanup?run_id=<id>`返回生效清单、版本历史、释放记录、补传事件与执行结论；缺省run_id取最新批次。`POST /api/cleanup/switch`接收run_id与可选base_version，生成并原子切换生效清单，版本不符返回409，校验失败回滚旧版。`POST /api/cleanup/execute`需要run_id、version与可选release_key，只释放未争议的待确认票，同一清单重试返回原结论。`POST /api/cleanup/dispute`接收run_id与parcel登记补传再争议，同票幂等。`POST /api/cleanup/confirm`将待确认的再争议票确认为继续保留。
