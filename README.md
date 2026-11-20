# 跨境包裹扫描补传与妥投时效核验

供跨境集运客服质控员核对原始记录、处理差异并封存证据。

## 启动

```sh
docker compose up --build -d
docker compose exec -T app pytest -q
```

打开 http://localhost:18247 。首次进入自动运行仓内脏样例。工具条可重跑、切换样例或上传CSV。SQLite保存在命名卷records中，重建服务保留批次。

## 模块

- 1. 扫描采信：`/scans`，`/api/scans`。
- 2. 节点映射：`/nodes`，`/api/nodes`。
- 3. 时区归整：`/timezones`，`/api/timezones`。
- 4. 路径裁决：`/paths`，`/api/paths`。
- 5. 时效计算：`/timeliness`，`/api/timeliness`。
- 6. 赔付对账：`/compensation`，`/api/compensation`。
- 7. 批次归档：`/archives`，`/api/archives`。
- 8. 清理裁决：`/cleanup`，`/api/cleanup`。

## 核心规则

以事件发生时刻重建路径，补传不改变既有妥投时间；承诺时钟按目的地节假日停表。扫描证据按批统一到期清理，批次内逐票裁决：赔付未结、复核未结、补传再争议的票冻结保留，其余到期释放；每批仅一份生效清单，执行幂等，切换失败回滚旧版。

## 演示

1. 打开首页，核对当次计算、差异和隔离原因。
2. 切换模块查看源行及裁决依据。
3. 重跑相同样例，批次号与行数保持一致。
4. 复核后导出一份证据CSV。版本已变化的复核提交返回409。

## 输入与输出

输入列见 `docs/DATA-DICTIONARY.md`。项目包含dirty.csv、clean.csv、legacy.csv，统一走实际导入路径。上传限制4MB，拒绝不带时区的时间。业务键按字符串保留，Decimal参与计算，空值不会当零。

`POST /api/runs` 接收sample或csv；`GET /api/runs`返回历史批次；`POST /api/runs/<id>/review`需要version与decision；`GET /api/runs/<id>/archive`返回唯一证据导出。

`POST /api/cleanup/switch`生成并切换生效清单（可带base_version做版本校验）；`POST /api/cleanup/execute`按version与release_key幂等执行；`POST /api/cleanup/dispute`登记补传再争议；`POST /api/cleanup/confirm`确认保留；`GET /api/cleanup`返回生效清单、版本、释放记录与执行结论。

## 范围

样例均为虚构。单机复核工具不连接生产系统；不提供登录、远端数据库或自动支付。内存计算按4MB输入上限控制，SQLite事务保证结果完整写入。

## 目录

app为服务与规则，sample_data为输入，tests为测试，docs为定义与深度登记。
