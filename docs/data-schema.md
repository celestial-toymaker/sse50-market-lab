# 数据字段与关联

原文件及校验信息见`evidence/source_manifest.json`。日期2026-09-23，50股。输入浮点价格在读取时四舍五入到整数分；十档量转换为整数股。

## ord_20260923.parquet

6,276,360行；SHA-256：`5e3fa7f96edc130262661bf379a5c367952327d36b0936e720cba907e78bd61b`。

| 字段 | Parquet类型 | 本项目解释 |
| --- | --- | --- |
| BizIndex | int32 | 频道内业务序号；按证券及频道排序 |
| Channel | int64 | 消息频道，盘后成交频道2301 |
| FunctionCode | int8 | 订单方向B=66/S=83；阶段消息保留原值 |
| OrderKind | int8 | A=65新增残量；D=68撤单；S=83状态 |
| OrderNumber | int32 | 消息编号 |
| OrderOriNo | int32 | 原始订单号，用于生命周期关联 |
| Price | float | 每股元，转换为整数分 |
| Time | int32 | HHMMSSmmm，转换为当日毫秒 |
| Volume | int32 | 股数 |
| Symbol | int32 | 证券代码 |

## exe_20260923.parquet

3,792,934行；SHA-256：`ec1774c557600549992f1c349705fdacdddeabdb58750a8fba4d2198f9207222`。

| 字段 | Parquet类型 | 本项目解释 |
| --- | --- | --- |
| AskOrder | int32 | 成交卖方原始订单号 |
| BSFlag | int8 | B=66主动买，S=83主动卖，0表示集合竞价 |
| BidOrder | int32 | 成交买方原始订单号 |
| BizIndex | int32 | 频道内业务序号；按证券及频道排序 |
| Channel | int32 | 消息频道，盘后成交频道2301 |
| FunctionCode | int8 | 订单方向B=66/S=83；阶段消息保留原值 |
| Index | int32 | 成交消息编号 |
| OrderKind | int8 | A=65新增残量；D=68撤单；S=83状态 |
| Price | float | 每股元，转换为整数分 |
| Time | int32 | HHMMSSmmm，转换为当日毫秒 |
| Volume | int32 | 股数 |
| Symbol | int32 | 证券代码 |

## snp_20260923.parquet

252,631行；SHA-256：`ac69d2f1810a24a15cb4c90183a6d3ea7ee31eccbcd98c58ed1f7f7f7c34aa87`。

| 字段 | Parquet类型 | 本项目解释 |
| --- | --- | --- |
| AccTurnover | int64 | 普通交易累计成交金额，元 |
| AccVolume | int64 | 普通交易累计成交股数 |
| AfterMatchItem | int32 | 盘后累计成交笔数 |
| AfterPrice | float | 盘后成交价，元/股 |
| AfterTurnover | int64 | 盘后累计成交金额，元 |
| AfterVolume | int32 | 盘后累计成交股数 |
| AskAvgPrice | float | 卖方平均委托价，元/股 |
| AskPrice1 | float | 卖方第1档价格，元/股 |
| AskPrice2 | float | 卖方第2档价格，元/股 |
| AskPrice3 | float | 卖方第3档价格，元/股 |
| AskPrice4 | float | 卖方第4档价格，元/股 |
| AskPrice5 | float | 卖方第5档价格，元/股 |
| AskPrice6 | float | 卖方第6档价格，元/股 |
| AskPrice7 | float | 卖方第7档价格，元/股 |
| AskPrice8 | float | 卖方第8档价格，元/股 |
| AskPrice9 | float | 卖方第9档价格，元/股 |
| AskPrice10 | float | 卖方第10档价格，元/股 |
| AskVolume1 | double | 卖方第1档挂量，股 |
| AskVolume2 | double | 卖方第2档挂量，股 |
| AskVolume3 | double | 卖方第3档挂量，股 |
| AskVolume4 | double | 卖方第4档挂量，股 |
| AskVolume5 | double | 卖方第5档挂量，股 |
| AskVolume6 | double | 卖方第6档挂量，股 |
| AskVolume7 | double | 卖方第7档挂量，股 |
| AskVolume8 | double | 卖方第8档挂量，股 |
| AskVolume9 | double | 卖方第9档挂量，股 |
| AskVolume10 | double | 卖方第10档挂量，股 |
| BSFlag | int8 | B=66主动买，S=83主动卖，0表示集合竞价 |
| BidAvgPrice | float | 买方平均委托价，元/股 |
| BidPrice1 | float | 买方第1档价格，元/股 |
| BidPrice2 | float | 买方第2档价格，元/股 |
| BidPrice3 | float | 买方第3档价格，元/股 |
| BidPrice4 | float | 买方第4档价格，元/股 |
| BidPrice5 | float | 买方第5档价格，元/股 |
| BidPrice6 | float | 买方第6档价格，元/股 |
| BidPrice7 | float | 买方第7档价格，元/股 |
| BidPrice8 | float | 买方第8档价格，元/股 |
| BidPrice9 | float | 买方第9档价格，元/股 |
| BidPrice10 | float | 买方第10档价格，元/股 |
| BidVolume1 | double | 买方第1档挂量，股 |
| BidVolume2 | double | 买方第2档挂量，股 |
| BidVolume3 | double | 买方第3档挂量，股 |
| BidVolume4 | double | 买方第4档挂量，股 |
| BidVolume5 | double | 买方第5档挂量，股 |
| BidVolume6 | double | 买方第6档挂量，股 |
| BidVolume7 | double | 买方第7档挂量，股 |
| BidVolume8 | double | 买方第8档挂量，股 |
| BidVolume9 | double | 买方第9档挂量，股 |
| BidVolume10 | double | 买方第10档挂量，股 |
| High | float | 当日最高，元/股 |
| Low | float | 当日最低，元/股 |
| MatchItem | int32 | 累计成交笔数 |
| Open | float | 开盘价，元/股 |
| PreClose | float | 昨收价，元/股 |
| Price | float | 每股元，转换为整数分 |
| Time | int32 | HHMMSSmmm，转换为当日毫秒 |
| TotalAskVolume | int64 | 全簿卖方挂量，股 |
| TotalBidVolume | int64 | 全簿买方挂量，股 |
| Turnover | int64 | 本条快照口径成交额，元 |
| Volume | int32 | 股数 |
| Symbol | int32 | 证券代码 |

## 归一化事件矩阵

每行12个int64值，列依次为`logical_ms, biz_index, channel, kind, side, price_cents, quantity, order_id, bid_id, ask_id, phase, raw_time`。kind为0新增、1撤单、2成交、3状态，side为+1买、−1卖、0集合。交易日与证券由分区上下文固定，订单键由频道与原始订单号组成。

连续主动单的成交可早于残量ADD，扣除被动方订单；集合成交同时扣双方。订单号与成交量结合判断生命周期。盘后成交量额单列，历史盘后队列信息由数据可观测范围界定。

## 快照与时钟

快照按标签时间直接对账，另寻找同秒及扩窗内曾经存在的完整十档加累计量状态。状态从较早事件持续到该秒也计入存在性。离线匹配承担诊断，策略以逐笔可见事件作决定。快照缺少频道业务水位，因此同秒存在性与精确标签时刻一致率分别呈现。

价格按分累计的量额与源文件元级汇总可能存在取整差，逐股差异完整保存在`evidence/audit.json`。异常事件保留时间、频道、订单与业务序号定位；检查点恢复保持FIFO序列。
