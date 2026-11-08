"""Create a short reading report and a separate complete technical appendix."""
from copy import deepcopy
from pathlib import Path
import re

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt

import build_report as b

HERE=Path(__file__).resolve().parent
FULL=HERE/'RRG行业轮动投资价值研究_中文完整报告.docx'


def clear_body(d):
    for child in list(d._element.body):
        if child.tag!=qn('w:sectPr'):d._element.body.remove(child)


def main():
    d=Document(FULL);clear_body(d)
    b.doc=d;b.table_no=0
    d.core_properties.title='RRG行业轮动的投资价值 中文阅读版'
    b.p('RRG行业轮动的投资价值','Title')
    b.p('研报方案重建与纯第一象限ETF配置延伸','Subtitle')
    b.p('Portfolio Management 课程研究    2026年11月7日','Subtitle')
    b.h('一 我们研究什么')
    b.p('我们的核心问题是：更简洁的RRG行业轮动方案，相比研报复合策略、简单行业动量和ETF等权，能创造多少扣费收益，又承担什么风险？')
    b.p('研报在行业指数层研究了纯第一象限，在ETF层采用“扩散＋RRG”复合方案。我们把纯第一象限接入同一ETF配置框架：每月选择相对强势且相对动量为正的行业，最多6个，再通过ETF组合实现行业暴露。选行业信号省去个股扩散指标，ETF映射仍使用持仓数据。这是相对原研报的应用延伸。')
    b.p('动量基准每月选过去220日涨幅最高的6个行业；等权基准在同一期可用ETF池中平均配置。')
    b.p('四策略使用同一修订后的历史ETF池、月度调仓、次日开盘和先卖后买规则，买卖各扣3bp（单边0.03%）。主设置为220日回看、最多6行业；本金1000万元，样本为2018年12月28日至2026年3月31日。数据与退市净值估算规则在各策略间保持一致。')
    b.h('二 扣费后赚多少 风险多大')
    import pandas as pd
    s=pd.read_csv(b.SRC/'performance.csv',index_col=0)
    b.table('主设置收益与风险',['策略','年化收益','净利润 万元','最大回撤'],[
        [label,b.PCT(s.loc[n,'年化收益率']),f'{s.loc[n,"profit_cny"]/10000:,.2f}',b.PCT(s.loc[n,'最大回撤'])]
        for n,label in zip(b.NAMES,b.LABELS)],widths=[1.8,1.5,1.9,1.7],size=10.5)
    b.note('净利润为期末资产减初始1000万元，含持仓浮动盈亏；已扣交易费用。复合方案为本次可获取数据下的研报方法重建。')
    b.p('纯第一象限相对动量年化高2.79个百分点，最大回撤浅1.44个百分点；相对复合方案年化高5.67个百分点，但回撤深6.35个百分点。它在主设置下具有较高收益，也需要承担明显的回撤风险。')
    d.add_page_break()
    b.h('三 从净值看投资价值')
    b.fig('01_net_nav.png','图1 四策略净值与回撤','相同ETF池及实施规则，单边3bp。')
    b.p('按相同的1000万元初始资金比较：纯第一象限期末资产约4636万元，动量约3958万元，复合方案约3351万元，等权约1957万元。纯第一象限相对动量累计多赚约677万元。')
    b.p('研报与本研究方向一致的发现，是复合轮动相对分散配置具有历史收益优势。不过，研报主要与行业等权和沪深300比较，本文与同池ETF等权及行业动量比较，数据口径也有差异。纯第一象限ETF的收益风险表现属于我们的新增结果。')
    b.p('收益并非每个阶段都领先。纯第一象限在76个滚动12月窗口中，有47个超过动量；2022和2023年仍分别亏损16.47%和16.55%。因此，报告关注的是完整持有期的收益风险取舍，而非每年排名第一。')
    d.add_page_break()
    b.h('四 优势有多稳')
    b.fig('03_parameters.png','图2 参数稳健性','扣费年化百分比；黑框为220日／6行业主设置。')
    b.p('参数方面，九组设置中纯第一象限有六组超过同参数动量、七组超过复合方案；九组均超过等权。其年化范围为12.73%—24.62%，主设置恰好是本网格最高的一组。参数会同时改变收益与回撤。')
    b.p('成本方面，在所测单边3—20bp情景中，纯第一象限始终领先动量。20bp时，两者年化分别为21.49%和19.27%。时间、成本和多数参数对照支持它在本样本中的经济吸引力。')
    b.p('统计证据仍需保留分寸。纯第一象限对动量的月均净收益差检验p值约0.468；对等权原始p值约0.017，六组比较校正后约0.101。当前结果适合说明历史经济优势，持续增量仍需独立样本验证。')
    b.h('五 研究贡献与最终判断')
    b.p('我们的贡献，是将研报的纯第一象限行业规则延伸到ETF配置，并在统一数据和实施条件下评估其扣费投资价值。最终主方案保留未过滤的纯第一象限：它在主设置及多数对照中有较好的历史表现，同时承担44.76%的最大回撤。')
    b.p('我们也测试了共同10个月均线风控，结果为收益下降且回撤加深，因此将其保留为对照。早期行业归属覆盖不足、部分ETF持仓披露及退市净值结算近似等口径，在独立技术附录中完整说明。')
    b.note('方法来源：西部证券2026年5月25日RRG研报；计算函数复用QuantsPlaybook固定版本。全部成本、参数、统计检验、均线对照及数据说明另见《完整检验与数据附录》。')
    d.save(HERE/'中文研究报告_精简阅读版.docx')

    app=Document(FULL)
    body=app._element.body
    found=False
    for el in list(body):
        if el.tag==qn('w:sectPr'):continue
        txt=''.join(el.itertext())
        if el.tag==qn('w:p') and '附录A 数据口径与实施核验' in txt:found=True
        if not found:body.remove(el)
    assert found
    app.core_properties.title='RRG行业轮动研究 完整检验与数据附录'
    b.doc=app
    first=app.paragraphs[0]
    first.paragraph_format.page_break_before=False
    intro=first.insert_paragraph_before('完整检验与数据附录',style='Title')
    for run in intro.runs:b.font(run,size=21,bold=True)
    pre=first.insert_paragraph_before('与中文阅读版配套。完整保留成本、参数、统计检验、风控对照及数据口径，供需要时查阅。')
    for run in pre.runs:b.font(run)
    # Use appendix-local table numbering; main report contains only one table.
    letter='A';counter=0
    for para in app.paragraphs:
        match=re.match(r'附录([A-H]) ',para.text)
        if match:letter=match.group(1);counter=0
        if re.match(r'表\d+\s',para.text):
            counter+=1
            value=re.sub(r'^表\d+\s+',f'表{letter}{counter}  ',para.text)
            para.text=value
            for run in para.runs:b.font(run,size=9)
        # Keep the short parameter interpretation with its introduction.
    interp=next(p for p in app.paragraphs if p.text.startswith('同参数比较指'))
    introE=next(p for p in app.paragraphs if p.text.startswith('L为长期回看交易日'))
    introE._p.addnext(interp._p)
    # Put annual and rolling diagnostics on distinct pages, avoiding tiny spill pages.
    rollcap=next(p for p in app.paragraphs if p.text.startswith('表C2'))
    rollcap.paragraph_format.page_break_before=True
    for prefix in ['表B1','表D1','表E1']:
        cap=next(p for p in app.paragraphs if p.text.startswith(prefix))
        t=cap._p.getnext()
        for margin in t.iter(qn('w:tblCellMar')):
            for side in ['top','bottom']:
                node=margin.find(qn('w:'+side))
                if node is not None:node.set(qn('w:w'),'45')
    refhead=next(p for p in app.paragraphs if p.text=='参考文献与资料')
    refhead.paragraph_format.page_break_before=True
    # Add the two charts moved out of the short report.
    for heading,file,title,caption in [
        ('附录D','02_cost.png','图D1 成本敏感性','四策略使用相同费用情景。'),
        ('附录F','04_significance.png','图F1 主设置月均收益差','HAC3单项95%区间，Holm按六组比较调整。')]:
        anchor=next(p for p in app.paragraphs if p.text.startswith(heading))
        b.fig(file,title,caption)
        imagepara=app.paragraphs[-2]._p;captionpara=app.paragraphs[-1]._p
        anchor._p.addnext(imagepara);imagepara.addnext(captionpara)
    app.save(HERE/'完整检验与数据附录.docx')
    print('Short reading report and separate complete appendix created.')


if __name__=='__main__':main()
