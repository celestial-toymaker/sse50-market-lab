# 来源与设计参考

## 课程与市场规则

- [课程Issue #6](https://github.com/aslan9/pku_quantllm/issues/6)：读取全部31条讨论，作为数据语义及研究外部先验。后续结论由本项目实际运行核验。
- [课程数据Release](https://github.com/aslan9/pku_quantllm/releases/tag/data-sse50-20260923)：数据文件、日期、官方校验值。
- [上交所交易规则（2026年修订）及暂缓实施条文](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)：2026-07-06生效。重点采用第三章申报、竞价、成交、盘后固定价格交易及第六章科创板特别规定。
- [上交所股票投资一件事](https://one.sse.com.cn/onething/gptz/)：“交易费用”列明规费、过户费、印花税及最低佣金。默认模拟佣金为万分之2.5并包含交易规费，另计双边过户费0.01‰及卖方印花税0.5‰。

## 开源系统

| 项目 | 采用的设计思想 | 对应实现 |
| --- | --- | --- |
| [VeighNa](https://github.com/vnpy/vnpy) | 事件驱动、订单管理、账户与策略分层 | 会话指令、执行报告和账户 |
| [NautilusTrader](https://nautilustrader.io/docs/latest/concepts/backtesting/) | 回测数据、执行、账户及策略接口 | 公共模型、会话、实验入口 |
| [HftBacktest](https://hftbacktest.readthedocs.io/en/latest/order_fill.html) | 固定路径回放、排队、部分成交及假设边界 | 独立容量账本、FIFO与保守情景 |
| [ABIDES](https://github.com/abides-sim/abides) | 离散事件和通信延迟 | 报单、撤单、市场及回报时钟 |

参考仓库的实际commit、默认分支和许可证元数据保存在`references.json`。本项目实现自己的课程数据适配、订单簿、撮合、账户及实验代码；开源项目承担设计参考。VeighNa与HftBacktest采用MIT，NautilusTrader采用LGPL-3.0；ABIDES原始LICENSE.txt标明BSD-3-Clause，GitHub的NOASSERTION元数据另行保留。原文定位、版本与哈希见参考清单。
