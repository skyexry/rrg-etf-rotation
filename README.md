# RRG 行业与 ETF 轮动研究

本仓库复现西部证券《相对旋转图 RRG 框架下的行业和 ETF 轮动策略》，并在统一数据、交易费用和执行规则下比较四种策略：

- 纯 RRG 第一象限 ETF 策略
- 扩散指标 + RRG 复合策略
- 行业动量 ETF 策略
- ETF 等权基准

研究进一步检验时间稳定性、交易成本、参数敏感性、统计显著性，以及 10 个月均线风控对照。仓库同时保存课程用英文报告、中文完整报告、客户阅读版和可核验的结果文件。

## 主要结果

样本期为 2018-12-28 至 2026-03-31，主设定采用月度调仓、信号后下一交易日开盘成交、买卖各 3bp、现金收益为 0。

| 策略 | 年化收益 | 最大回撤 |
| --- | ---: | ---: |
| 纯 RRG 第一象限 | 24.62% | -44.76% |
| 扩散指标 + RRG | 18.95% | -38.40% |
| 行业动量 | 21.83% | -46.19% |
| ETF 等权 | 10.12% | -38.78% |

纯第一象限策略在主设定下取得最高历史年化收益。它相对行业动量的月均收益差检验约为 `p=0.468`，因此历史经济表现与持续统计增量需要分开解释。主参数也是所检验网格中纯第一象限策略表现最高的一组。

## 仓库结构

```text
src/                         RRG 数据、回测、稳健性和核验脚本
vendor/quantsplaybook_rrg/   固定上游 commit 的算法实现与测试
reports/english/             JF 风格英文正文、技术附录和 TeX 源码
reports/chinese/             中文报告、完整附录和可编辑 Word 文件
reports/client/              面向非量化客户的阅读版本
results/                     最终表格、图表、净值记录与核验清单
data/                        数据取得与复现说明
```

结果阅读入口为 [`results/先看这个_实验结论.txt`](results/先看这个_实验结论.txt)，完整口径见 [`results/数据与策略口径.txt`](results/数据与策略口径.txt)。

## 环境

建议使用 Python 3.12：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

运行合成数据测试：

```bash
PYTHONPATH=vendor/quantsplaybook_rrg pytest vendor/quantsplaybook_rrg/tests -q
PYTHONPATH=src pytest src/test_rrg_pipeline.py -q
```

完整历史回测依赖授权数据快照。数据文件的准备方式和当前覆盖边界见 [`data/README.md`](data/README.md)。仓库内的最终结果与报告可以直接审阅，输入数据库和原始接口数据保留在本地。

## 数据与证据边界

- 2018/2019 年行业归属约覆盖 55.93%/59.97% 的股票日，对应自由流通市值覆盖约 35.55%/41.89%。
- ETF 持仓记录的完整度随报告期变化，历史产品分类和跟踪指数身份也存在覆盖差异。
- 回测加入已退市 ETF，并按事前约定的净值近似处理无法取得交易价格的退出事件。
- 结果来自同一历史样本；成本、参数和时间窗口分析属于样本内稳定性证据。
- 本项目用于研究与教学，不构成投资建议。

## 来源

RRG 算法实现参考 [hugo2046/QuantsPlaybook](https://github.com/hugo2046/QuantsPlaybook)，固定 commit `3f83a77f4c7996ed8185b8979a42c4372c1d811f`。文件来源与哈希记录见 [`vendor/quantsplaybook_rrg/UPSTREAM.json`](vendor/quantsplaybook_rrg/UPSTREAM.json)。

