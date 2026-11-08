"""Author the Chinese report from the frozen repaired-pool experiment exports."""
from pathlib import Path
import hashlib
import json
import shutil

import pandas as pd
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
SRC=ROOT/'results/rrg_tushare_repaired_20261107'
ROB=SRC/'four_strategy_robustness'
SMA=SRC/'sma10_filter'
FILE=HERE/'RRG行业轮动投资价值研究_中文完整报告.docx'
NAMES=['Pure_RRG_Q1','Report_base','Industry_momentum_220_6','ETF_equal_weight']
LABELS=['纯第一象限','扩散＋RRG','行业动量','ETF等权']
SHORT=dict(zip(NAMES,['Q','C','M','E']))
PCT=lambda x:f'{float(x)*100:.2f}%'
PP=lambda x:f'{float(x)*100:.3f}'
NUM=lambda x:f'{float(x):.3f}'
PV=lambda x:f'{float(x):.4f}'
doc=Document()
table_no=0


def font(run,size=None,bold=None,color=None):
    run.font.name='Arial Unicode MS'
    run._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Arial Unicode MS')
    if size:run.font.size=Pt(size)
    if bold is not None:run.bold=bold
    if color:run.font.color.rgb=RGBColor.from_string(color)


def p(s='',style=None):
    obj=doc.add_paragraph(style=style);font(obj.add_run(s))
    return obj


def h(s,level=1):
    obj=doc.add_paragraph(s,style=f'Heading {level}')
    for r in obj.runs:font(r,bold=True,color='000000')
    return obj


def note(s):
    obj=p(s,'Caption');obj.paragraph_format.keep_with_next=False
    return obj


def table(title,headers,rows,widths=None,size=9.5):
    global table_no
    table_no+=1
    cap=p(f'表{table_no}  {title}','Caption');cap.paragraph_format.keep_with_next=True
    t=doc.add_table(rows=1,cols=len(headers));t.alignment=WD_TABLE_ALIGNMENT.CENTER;t.autofit=False
    if widths is None:widths=[6.9/len(headers)]*len(headers)
    for col,width in zip(t.columns,widths):col.width=Inches(width)
    pr=t._tbl.tblPr
    borders=OxmlElement('w:tblBorders')
    for edge in ['top','left','bottom','right','insideH','insideV']:
        e=OxmlElement('w:'+edge);e.set(qn('w:val'),'single');e.set(qn('w:sz'),'4');e.set(qn('w:color'),'D9D9D9');borders.append(e)
    pr.append(borders)
    margins=OxmlElement('w:tblCellMar')
    for side,val in [('top',75),('bottom',75),('left',85),('right',85)]:
        el=OxmlElement('w:'+side);el.set(qn('w:w'),str(val));el.set(qn('w:type'),'dxa');margins.append(el)
    pr.append(margins)
    repeat=OxmlElement('w:tblHeader');repeat.set(qn('w:val'),'true');t.rows[0]._tr.get_or_add_trPr().append(repeat)
    for i,values in enumerate([headers,*rows]):
        cells=t.rows[0].cells if i==0 else t.add_row().cells
        tr=cells[0]._tc.getparent();cant=OxmlElement('w:cantSplit');tr.get_or_add_trPr().append(cant)
        for j,(cell,value) in enumerate(zip(cells,values)):
            cell.width=Inches(widths[j]);cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cell.text=str(value)
            cp=cell.paragraphs[0];cp.paragraph_format.space_after=Pt(0);cp.paragraph_format.space_before=Pt(0)
            cp.paragraph_format.line_spacing=Pt(size*1.25+1);cp.paragraph_format.keep_with_next=False
            cp.alignment=WD_ALIGN_PARAGRAPH.LEFT if j==0 else WD_ALIGN_PARAGRAPH.CENTER
            for r in cp.runs:font(r,size,bold=i==0,color='FFFFFF' if i==0 else '000000')
            fill='243F55' if i==0 else ('F2F6F9' if i%2==0 else 'FFFFFF')
            sh=OxmlElement('w:shd');sh.set(qn('w:fill'),fill);cell._tc.get_or_add_tcPr().append(sh)
    doc.add_paragraph().paragraph_format.space_after=Pt(1)
    return t


def fig(file,title,caption,width=6.85):
    pp=doc.add_paragraph();pp.paragraph_format.space_after=Pt(3);pp.paragraph_format.keep_with_next=True
    pp.paragraph_format.line_spacing=1.0
    pp.alignment=WD_ALIGN_PARAGRAPH.CENTER
    pp.add_run().add_picture(str(HERE/'figures'/file),width=Inches(width))
    note(title+'。'+caption)


def eq(nodes):
    para=doc.add_paragraph();para.alignment=WD_ALIGN_PARAGRAPH.CENTER
    para.paragraph_format.line_spacing=1.0
    outer=OxmlElement('m:oMathPara');math=OxmlElement('m:oMath')
    for n in nodes:math.append(mr(n) if isinstance(n,str) else n)
    outer.append(math);para._p.append(outer)


def mr(s):
    r=OxmlElement('m:r');t=OxmlElement('m:t');t.text=s;r.append(t);return r


def sub(s,k):
    obj=OxmlElement('m:sSub');e=OxmlElement('m:e');e.append(mr(s));v=OxmlElement('m:sub');v.append(mr(k));obj.extend([e,v]);return obj


def fraction(a,b):
    f=OxmlElement('m:f')
    for tag,value in [('num',a),('den',b)]:
        node=OxmlElement('m:'+tag)
        for v in value:node.append(mr(v) if isinstance(v,str) else v)
        f.append(node)
    return f


def pair(label):
    a,b=label.split(' minus ');return SHORT[a]+'−'+SHORT[b]


def ci(lo,hi):return f'[{float(lo)*100:.3f}, {float(hi)*100:.3f}]'


def appendix(title):
    obj=h(title);obj.paragraph_format.page_break_before=True


def main():
    sec=doc.sections[0]
    sec.page_width=Inches(8.5);sec.page_height=Inches(11)
    sec.left_margin=sec.right_margin=Inches(.8);sec.top_margin=Inches(.7);sec.bottom_margin=Inches(.65)
    sec.header_distance=Inches(.25);sec.footer_distance=Inches(.25)
    for name in ['Normal','Title','Subtitle','Heading 1','Heading 2','Caption']:
        st=doc.styles[name];st.font.name='Arial Unicode MS';st.font.color.rgb=RGBColor(0,0,0)
        st.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),'Arial Unicode MS')
    for st in doc.styles:
        for node in st.element.iter(qn('w:pBdr')):
            node.getparent().remove(node)
        for node in st.element.iter(qn('w:rFonts')):
            for attr in list(node.attrib):
                if 'theme' in attr.lower():del node.attrib[attr]
    doc.styles['Subtitle'].font.italic=False
    doc.styles['Caption'].font.bold=False
    normal=doc.styles['Normal'];normal.font.size=Pt(11)
    normal.paragraph_format.line_spacing=Pt(17);normal.paragraph_format.space_after=Pt(6)
    normal.paragraph_format.widow_control=True
    for n,size in [('Title',23),('Heading 1',15),('Heading 2',12),('Subtitle',11),('Caption',9)]:doc.styles[n].font.size=Pt(size)
    for n in ['Heading 1','Heading 2']:
        doc.styles[n].paragraph_format.keep_with_next=True
        doc.styles[n].paragraph_format.space_before=Pt(12);doc.styles[n].paragraph_format.space_after=Pt(7)
        doc.styles[n].paragraph_format.line_spacing=Pt(21 if n=='Heading 1' else 17)
    doc.styles['Title'].paragraph_format.line_spacing=Pt(30)
    doc.styles['Caption'].paragraph_format.line_spacing=Pt(12.5)
    footer=sec.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.CENTER
    fld=OxmlElement('w:fldSimple');fld.set(qn('w:instr'),'PAGE')
    r=OxmlElement('w:r');props=OxmlElement('w:rPr');size=OxmlElement('w:sz');size.set(qn('w:val'),'18');props.append(size)
    r.append(props);t=OxmlElement('w:t');t.text='1';r.append(t);fld.append(r);footer._p.append(fld)
    doc.core_properties.title='RRG行业轮动的投资价值研究'
    doc.core_properties.subject='新历史ETF池下的研报方案重建与纯第一象限配置延伸'
    doc.core_properties.author=''
    stats=pd.read_csv(SRC/'performance.csv',index_col=0)
    annual=pd.read_csv(ROB/'annual_returns.csv',index_col=0)
    roll=pd.read_csv(ROB/'rolling_summary.csv',index_col=0)
    cost=pd.read_csv(ROB/'cost_cagr.csv',index_col=0)
    grid=pd.read_csv(ROB/'parameter_grid.csv')
    primary=pd.read_csv(ROB/'primary_pairwise_tests.csv')
    p('RRG行业轮动的投资价值研究','Title')
    p('研报方案重建  纯第一象限ETF配置延伸  收益风险与稳健性检验','Subtitle')
    p('Portfolio Management 课程研究报告    2026年11月7日','Subtitle')
    h('摘要')
    p('本研究评估相对旋转图（Relative Rotation Graph，RRG）行业轮动的实际投资价值。我们以西部证券研报的扩散指标与RRG复合ETF方案为起点，将研报行业指数层的纯第一象限规则延伸至ETF配置，并与简单行业动量及同池ETF等权基准进行统一比较。所有策略采用同一修订后的历史ETF候选池、行业暴露映射、月度调仓和次日开盘执行规则，买卖各计3bp交易费用。')
    p('2018年12月28日至2026年3月31日，纯第一象限ETF组合取得24.62%的扣费年化收益，高于行业动量的21.83%、复合方案的18.95%和ETF等权的10.12%。初始1000万元对应累计净利润3635.65万元，含期末持仓市值。主设置下，其最大回撤为44.76%，相对复合方案更深，相对动量略浅。')
    p('所测3—20bp成本情景中，纯第一象限始终领先动量；九组参数中有六组领先同参数动量、七组领先复合方案。与此同时，相对动量月均净收益差的HAC检验p值为0.468，显示持续增量仍有较大不确定性。本研究的贡献是给出纯RRG信号的ETF实施延伸及可复现的投资价值比较，识别其历史经济吸引力、回撤代价和适用条件。')
    p('关键词  行业轮动  RRG  ETF配置  交易成本  组合管理')
    h('一 研究问题与文献起点')
    p('行业轮动的目标是在不同市场阶段调整行业暴露。对组合管理者而言，一套方法的价值取决于扣除实施成本后的收益、承担的风险，以及相对简单配置规则的改善程度。因此，本文的主问题是：RRG行业轮动相比行业动量和ETF等权，能够多创造多少扣费收益，其风险是否值得承担？子问题是：纯第一象限与扩散＋RRG两种筛选方案，具有怎样的收益与回撤取舍？')
    p('西部证券《指数化配置系列研究（6）：相对旋转图RRG框架下的行业研究》先在行业指数层比较单一及相邻象限，再将扩散＋RRG复合信号映射为ETF组合。研报报告纯第一象限行业指数组合年化18.34%，复合ETF方案年化21.51%［1］。本文关注的延伸，是把行业指数层已有的纯第一象限规则接入ETF配置层。')
    p('这项延伸有明确的投资动机。纯第一象限要求行业相对强度及相对强度动量同时超过中心值，选行业信号主要依赖行业指数价格；复合方案另外引入个股上涨广度。将两者置于同一ETF实施框架，可以评估更简洁的信号是否保留投资价值。ETF行业暴露映射仍需要基金持仓数据。')
    p('本文将贡献定位为相对原研报的应用延伸、统一的策略对照以及适用条件评估。理论与策略规则参考原研报，核心信号、优化及回测函数复用固定版本QuantsPlaybook［2］；我们负责数据接入与修订、ETF组合延伸、统一比较及扩展检验。')
    h('二 策略设计与比较逻辑')
    table('四种策略的角色与选行业规则',['策略','规则','研究角色'],[
        ['纯第一象限','保留RRG第一象限行业，按距中心距离取最多6个','核心ETF配置延伸'],
        ['扩散＋RRG','扩散指标前6行业，再保留第一或第二象限，不补足','研报复合方案重建'],
        ['行业动量','过去220个交易日行业累计收益排名前6','简单主动轮动基准'],
        ['ETF等权','在每期相同可用ETF池内恢复等权','同池分散配置基准']],widths=[1.1,3.55,2.25],size=10)
    p('RRG横轴衡量行业相对基准的长期强弱，纵轴衡量相对强度的近期变化。主设置采用220日长期回看、60日动量回看和20日均线平滑。第一象限对应两个指标均大于100，行业超过6个时按到中心点（100，100）的欧氏距离选取最远者。扩散指标采用过去220日上涨成分股的自由流通市值占比，并进行20日平滑。完整公式见附录B。')
    p('三个主动策略选出行业后，均进入同一个ETF优化器：在总目标仓位为1、单ETF权重不超过1、每个选中行业暴露不超过1/k的约束下，最大化ETF组合对选中行业的总暴露，k为当期实际选中行业数。因此，“最多6行业”并不等于“持有6只ETF”，也不意味着ETF权重等分。')
    p('行业动量采用相同的220日回看、最多6行业和ETF映射规则，主要用于比较行业信号的增量价值。ETF等权在同一可用池内按月再平衡，作为分散配置基准。纯第一象限与复合方案同时涉及扩散筛选、象限集合和实际行业数量变化，本文据此讨论方案取舍；独立扩散贡献需要固定其余条件的额外消融。')
    h('三 数据与统一实施')
    p('本文使用自行接入的Tushare兼容接口数据，覆盖股票、行业指数、ETF行情、复权因子、基金持仓和产品元数据。正式回测区间为2018年12月28日至2026年3月31日，共1756个交易日；指标预热从2017年1月3日开始，主设置首次成交为2019年1月2日。各策略共用修订后的历史ETF池。')
    p('ETF池根据Tushare指数目录中的行业或主题类别筛选，补入已退市产品，并利用同一基金的场外代码记录修复跟踪指数等字段。全样本共有718只满足日期及类型资格的ETF，其中54只目前已退市。经过历史上市状态、实际行情、可见持仓、境外持仓过滤和同指数成交额去重后，每期有效池由首期48只增至末期240只。')
    table('共同回测设置',['项目','统一设定'],[
        ['目标形成与执行','月末收盘形成目标，下个交易日复权开盘成交，先卖后买，退出目标归零'],
        ['费用与资金','本金1000万元；买卖各3bp；额外滑点0；无借款；现金收益0'],
        ['估值与指标','复权收盘估值；252交易日年化；夏普采用零无风险利率'],
        ['ETF行业暴露','使用形成日已经公告的基金持仓和历史行业归属；未披露部分不归一化'],
        ['历史退市处理','按已确认的披露净值近似，在退市后的首个月度调仓估算转回现金']],widths=[1.35,5.55],size=10)
    p('数据边界影响结论的外推。补查历史中信归属后新增记录为0，2018和2019年股票日覆盖约56%和60%，自由流通市值覆盖约36%和42%；2020年起对应市值覆盖约98.66%以上。ETF持仓部分披露、当前分类标签及退市净值结算近似继续保留。附录A给出完整口径、覆盖率和核验记录。')
    p('因此，本文将复合策略称为“按研报方法重建”，并区分原文公布值与本次结果。新池复合方案年化18.95%，原研报为21.51%；候选池、持仓覆盖和数据定义等口径存在差异，各项差异对收益的独立贡献尚待进一步归因。')
    p('与研报方向一致的发现，是复合ETF轮动在历史样本中相对分散配置基准具有收益优势。本次复合方案年化18.95%，高于同池ETF等权的10.12%。研报表13主要对照中信行业等权与沪深300，本文的主要对照则是同池ETF等权与行业动量；基准定义不同，因此这项一致性是经济方向上的支持。纯第一象限ETF的表现属于本研究的新增发现。')
    h('四 主结果与投资价值')
    table('主设置的扣费收益与风险',['策略','年化收益','净利润 万元','最大回撤','夏普','Calmar'],[
        [label,PCT(stats.loc[n,'年化收益率']),f'{stats.loc[n,"profit_cny"]/10000:,.2f}',PCT(stats.loc[n,'最大回撤']),NUM(stats.loc[n,'夏普比率']),NUM(stats.loc[n,'Calmar 比率'])]
        for n,label in zip(NAMES,LABELS)],widths=[1.35,1.05,1.3,1.15,1.0,1.05],size=10)
    note('注：净利润为期末总资产减初始1000万元，包含持仓浮动盈亏；已扣交易费用，末日额外强制清仓费用另未计入。')
    p('纯第一象限相对动量年化高2.79个百分点，最大回撤浅1.44个百分点，夏普由0.892提高至0.967。初始1000万元的期末资产分别为4635.65万元和3958.39万元，纯第一象限累计净利润多677.26万元。这是主设置下最直接的投资比较。')
    fig('01_net_nav.png','图1 四策略统一净值与回撤','同一历史ETF池、单边3bp。曲线为研究回测，不代表实际账户收益。')
    p('与复合方案相比，纯第一象限年化高5.67个百分点，最大回撤深6.35个百分点。复合方案在本设置下体现更好的回撤控制；纯第一象限则取得更高收益与夏普。与ETF等权相比，纯第一象限年化高14.50个百分点，最大回撤深5.97个百分点，主动轮动伴随更大的风险承担。')
    p('收益还应结合集中度理解。纯第一象限每日最大单只ETF权重的平均值约42.72%，复合方案约50.50%，动量约41.95%。这些数值反映持仓集中度，不能解释为行业权重上限。当前结果更适合支持一套具有历史经济吸引力的主动配置方案，而非已经证实可持续的alpha。')
    h('五 收益优势的稳定性')
    h('时间与市场阶段',2)
    p('在87个完整月份构成的76个滚动12月窗口中，纯第一象限有47个窗口超过动量，53个超过复合方案，55个超过ETF等权；四策略中排名第一29次。其领先覆盖多个阶段，但各阶段的策略排名会变化。2020年纯第一象限收益93.54%，2022和2023年则分别亏损16.47%和16.55%。完整年度和滚动结果见附录C。')
    p('收益集中度也值得关注。纯第一象限收益最高的3个月贡献了全期对数增长的34.92%。将各策略自身最好的3个月机械设为零、其余月份与样本长度保持原值，纯第一象限年化为15.40%，动量为13.67%。这是事后压力诊断，用于描述收益对少数月份的依赖。')
    h('交易成本',2)
    p('将单边总交易摩擦由3bp提高到5、10和20bp，全部情景重新模拟订单、现金和费用。在所测范围内，纯第一象限始终领先动量。20bp时，两者年化分别为21.49%与19.27%，差距约2.22个百分点。成本压力结果支持其历史收益优势并非仅由低费用假设维持；实际市场冲击仍需结合规模和流动性判断。')
    fig('02_cost.png','图2 成本敏感性','单边总摩擦作为费率统一收取，额外滑点为0；退市估算结算使用同一规则。')
    h('参数选择',2)
    p('我们固定RS-momentum为60日、平滑为20日、ETF池及实施规则，考察180、220、260日长期回看与最多4、6、8行业的九种设置。三个主动策略共27组回测，纯第一象限有六组超过同参数动量、七组超过复合方案；三种主动策略各九组均超过固定ETF等权。')
    fig('03_parameters.png','图3 完整参数网格','单元格为扣费年化百分比；ETF等权年化固定为10.12%。复合策略同时改变扩散及RS-ratio长期回看。')
    p('纯第一象限年化范围为12.73%—24.62%，中位数19.40%；最大回撤范围约37.57%—57.89%。220日／6行业沿用原设置，同时也是本样本网格中收益最高的一组。因此，参数检验支持多数设置下的经济吸引力，也显示收益与风险会随参数明显变化。')
    h('六 统计证据与解释边界')
    p('主设置使用87个完整月的配对净收益差进行双侧HAC检验，主滞后阶数为3，并对四策略全部六组比较进行Holm调整。纯第一象限相对动量的月均收益差约0.198个百分点，原始p值0.468；相对复合方案的差约0.395个百分点，p值0.328。相对等权的原始p值约0.017，六组校正后约0.101。')
    fig('04_significance.png','图4 主设置月均净收益差','误差线为单项95%置信区间；HAC检验对象是月均差，Holm按全部六组比较调整。')
    p('配对循环块bootstrap进一步估计复利年化差的不确定性。纯第一象限对动量的年化差点估计为2.79个百分点，块长3个月时95%区间约为−4.85至10.76个百分点。统计证据对持续增量的确认仍较弱；较大的p值也不等于已经证明两策略等效。完整HAC、成本及参数检验、bootstrap和基准回归见附录F。')
    p('本研究使用的是已经观察过的历史样本。ETF池修订与多种策略检验提升了比较的规范性，也意味着本文应被理解为探索性实证研究。多重比较校正覆盖本次指定的检验族，历史选题与所有研究选择带来的不确定性仍需结合独立样本验证。')
    h('七 结论与组合管理含义')
    p('本研究从研报复合方案出发，将纯第一象限行业信号延伸到ETF配置。主设置下，纯第一象限在扣费收益、夏普和Calmar上优于简单行业动量，在所测成本范围及多数参数组合中保持吸引力。相对复合方案，它提供更高历史收益，也承担更深最大回撤；相对等权，主动轮动的收益提升伴随更大的风险。')
    p('对组合管理的实际启示是，将选行业信号、ETF映射和交易实施作为一个完整决策链条评估。更复杂的筛选规则需要用实际持仓和扣费结果证明其价值，组合风险还取决于集中度、市场阶段与参数选择。我们测试的共同10个月均线过滤降低了波动，但收益下降且最大回撤加深，因而保留未过滤纯第一象限作为本研究的主方案，过滤实验作为风控对照保存在附录G。')
    p('本文的结论是识别一套简洁RRG轮动方案的历史经济价值及适用边界。其相对动量的持续统计增量、在更完整数据和独立样本中的表现，仍是后续验证的重点。')
    refs=[
        '［1］西部证券研发中心．2026．指数化配置系列研究（6）：相对旋转图RRG框架下的行业研究．2026年5月25日．方法定位：第2.1节纯象限行业配置，第3.1节复合ETF配置。',
        '［2］hugo2046．QuantsPlaybook．相对旋转图RRG行业轮动项目．固定代码版本3f83a77f4c7996ed8185b8979a42c4372c1d811f．https://github.com/hugo2046/QuantsPlaybook',
        '［3］Tushare接口文档．中信行业成分、ETF基础信息、指数基本信息、基金持仓及基金净值．实际数据通过用户授权的兼容接口获取，口径与官方商业数据的一致性未独立核验。',
        '［4］Faber M．2007，更新版2013．A Quantitative Approach to Tactical Asset Allocation．The Journal of Wealth Management．https://ssrn.com/abstract=962461；作者规则说明：https://mebfaber.com/2017/12/13/episode-86-quantitative-approach-tactical-asset-allocation/',
        '［5］statsmodels文档．HAC稳健协方差与multipletests多重比较方法．https://www.statsmodels.org/stable/generated/statsmodels.regression.linear_model.RegressionResults.get_robustcov_results.html',
        '［6］arch文档．时间序列bootstrap与CircularBlockBootstrap．https://bashtage.github.io/arch/bootstrap/timeseries-bootstraps.html']

    appendix('附录A 数据口径与实施核验')
    p('本报告仅使用新ETF池版本。先前作者数据快照、旧池和旧窗口实验保留在项目记录中，正文及本附录的策略比较均来自本次统一的新池版本。修订同时涉及ETF分类、退市产品纳入、指数代码和可交易状态，收益差表示这些修订的合计效果。')
    table('年度历史行业覆盖',['年份','股票日数','已分类股票日','股票日覆盖','自由流通市值覆盖'],[
        [int(r.year),f'{r.stock_days:,}',f'{r.classified_stock_days:,}',PCT(r.stock_day_coverage),PCT(r.free_float_value_coverage)]
        for r in pd.read_csv(SRC/'historical_membership_coverage.csv').itertuples()],widths=[.65,1.5,1.6,1.4,1.75],size=9.5)
    p('股票日覆盖的分母为该年有股票日行情的沪深证券记录，分子为具备明确中信行业归属的记录。市值覆盖为这些已分类股票日的自由流通市值之和除以全部可用股票日的相应市值之和；2026年仅统计至3月31日。缺失行业归属股票从相关计算中剔除，当前分类未向历史回填。')
    p('历史行业补查包括全市场当前／历史记录、30个一级行业分别查询，以及30只早期缺失股票抽查，共134个查询任务；返回的6750条归属区间与原记录一致，新增为0。自由流通市值使用free_share乘未复权收盘价再乘10000，单位为元，其定义与Wind自由流通市值的一致性仍需核对。个股上涨判断沿用未复权价格，原研报相应复权细节尚未完整核实。')
    p('ETF元数据通过同基金六位代码匹配场外记录修复248个字段；718只为全样本类型及日期资格数量，并非每月实际可投数量。其中54只目前已退市，35只曾进入有效历史候选池。行业／主题类型使用当前指数目录标签，历史产品类别、名称和跟踪指数变更信息仍有限。CNI与SZ代码后缀采用明确映射，其余未知类别列入审计。')
    p('ETF持仓仅使用形成日前已公告记录，部分报告期仅披露前十大。行业暴露以占基金净值比例聚合，未披露和未分类部分不按已知部分归一化。公告修订沿用固定源代码的最新公告去重方式；这会影响历史可见持仓的覆盖。境外持仓过滤及同指数成交额去重在形成日执行，新上市不足20日的成交额均值采用可用历史。')
    p('退市ETF停牌期间以最后可得复权价格估值；退市后的首个月度形成日，以当时已公告的最新单位净值乘历史复权因子，次日估算转回现金并计费用。为34只候选准备了结算计划，主设置等权发生25笔估算结算，三个主动组合为0笔。参数网格中动量260／6及260／8各发生1笔，其余主动情景为0笔。该金额和日期是研究近似，实际清盘到账仍可能不同。')
    table('研报公布值与本次复合方案重建',['指标','研报表13','本次新池','本次减研报'],[
        ['年化收益','21.51%','18.95%','−2.56个百分点'],['最大回撤','−34.98%','−38.40%','回撤深3.42个百分点'],
        ['年化波动','24.93%','27.02%','＋2.09个百分点'],['夏普','0.800','0.778','−0.022'],['Calmar','0.620','0.493','−0.127']],widths=[1.6,1.5,1.5,2.3])
    p('原研报资料来源列示Wind与聚源，本次使用可获取的兼容接口数据。双方数据覆盖、候选定义和实施细节存在差异，因此表中差额用于描述重建偏差，不作为单项方法改进的归因。')
    p('执行中允许连续份额，未模拟整手、最低佣金或经资金规模校准的市场冲击。ETF复权价格用于估值与成交，费用按成交额计提。43组成本及参数回测的费用、现金、期末市值、次日成交和退市后余额核验通过；4条主设置净值与前次逐日一致，3条220／6参数净值与主设置一致。270个补充原始文件的校验和核对通过。')

    appendix('附录B 信号构造与组合方法')
    p('设P为行业指数收盘价，B为28个中信一级行业日收益等权复合形成的基准净值，剔除综合与综合金融。RS为行业相对基准的强度，R为平滑后的相对强度比率，M为其动量。MA20表示过去20个交易日的简单平均。实际计算沿用固定版本比率法。')
    eq([sub('RS','i,t'),' = 100 × ',fraction([sub('P','i,t')],[sub('B','t')])])
    eq([sub('R','i,t'),' = MA20 [100 × ',fraction([sub('RS','i,t')],[sub('RS','i,t−220')]),']'])
    eq([sub('M','i,t'),' = MA20 [100 × ',fraction([sub('R','i,t')],[sub('R','i,t−60')]),']'])
    p('第一象限要求R与M均严格大于100；第二象限要求R小于100且M大于100。恰等于100的行业不强行归边。纯第一象限在合格行业中按到（100，100）的欧氏距离排序，最多选择N个。动量方案以P在过去L日的累计收益排序，选择前N个。')
    p('扩散指标D为同一行业中，过去L日价格上涨股票的自由流通市值占比，再经MA20平滑。复合方案先取D最高的N个行业，再保留第一、第二象限，不向后递补。有效分母存在而全部成分均未上涨时，扩散值按0处理；该边界修正保持在数据适配层，核心vendor文件不改。')
    p('令E为ETF对各行业的可见持仓暴露，s为0／1行业得分，w为ETF配置权重。三个主动方案共用以下目标与约束。选中行业暴露上限只作用于得分为1的行业，其他行业暴露可能随ETF持仓一并进入组合。')
    eq(['max  Σ',sub('w','f'),' (Σ ',sub('E','f,i'),sub('s','i'),')'])
    eq(['Σ ',sub('w','f'),' = 1    0 ≤ ',sub('w','f'),' ≤ 1'])
    eq(['Σ ',sub('w','f'),sub('E','f,i'),' ≤ 1/k    对选中行业i'])
    p('k为实际选中的行业数。该约束鼓励选中行业接近分散配置，但并未限制单只ETF为1/k。优化不直接惩罚换手，交易成本在订单回放中收取。ETF等权则对每期可用暴露矩阵中的ETF赋相同目标权重。')
    table('指标与统计量的定义',['指标','口径'],[
        ['年化收益','期末与期初资产比值按252／1756次幂换算，再减1'],['最大回撤','每日净值相对此前历史最高净值的最大跌幅'],
        ['年化波动与夏普','由每日组合收益计算，252日年化，夏普无风险利率设为0'],['Calmar','年化收益除以最大回撤绝对值'],
        ['双边换手','买卖成交额分别相对前一日净值标准化后求和，再按252／1756年化'],['净利润','期末资产减初始1000万元，包含持仓市值'],
        ['HAC检验','87个完整月的配对净收益差；双侧，小样本校正及t近似']],widths=[1.5,5.4],size=10)

    appendix('附录C 完整时间稳定性与月份依赖')
    table('全部分年度收益',['年份',*LABELS],[['2026年一季度' if int(y)==2026 else str(y),*[PCT(row[n]) for n in NAMES]] for y,row in annual.loc[2019:].iterrows()],widths=[1.3,1.4,1.4,1.4,1.4],size=10)
    note('2018年仅12月28日，尚未成交，四组合收益均为0。2026年只展示1—3月区间收益。')
    fig('05_annual_returns.png','图C1 分年度收益','2026Q1为一季度收益，未进行年化。')
    table('全部滚动12月统计',['策略','第一次数','正收益次数','中位数','最差','最好'],[
        [label,int(roll.loc[n,'top_windows']),int(roll.loc[n,'positive_windows']),PCT(roll.loc[n,'median_12m']),PCT(roll.loc[n,'worst_12m']),PCT(roll.loc[n,'best_12m'])]
        for n,label in zip(NAMES,LABELS)],widths=[1.35,1.05,1.2,1.1,1.1,1.1],size=9.5)
    fig('06_rolling_returns.png','图C2 滚动12月收益','共76个相互重叠窗口，仅作阶段性描述。')
    dep=pd.read_csv(ROB/'month_dependency.csv').set_index('strategy')
    table('收益月份集中度诊断',['策略','正／负月份','最优3月增长占比','最好1月置零年化','最好3月置零年化'],[
        [label,f'{int(dep.loc[n,"positive_months"])}/{int(dep.loc[n,"negative_months"])}',PCT(dep.loc[n,'top3_share_log_growth']),PCT(dep.loc[n,'best1_flat_cagr']),PCT(dep.loc[n,'best3_flat_cagr'])]
        for n,label in zip(NAMES,LABELS)],widths=[1.3,1.1,1.5,1.5,1.5])
    note('最优3月增长占比为这些月份的对数收益之和除以全期对数增长；置零保留样本长度，属于事后压力场景，不是可执行策略。')
    table('最佳与最差月份',['策略','最佳月份','收益','最差月份','收益'],[
        [label,dep.loc[n,'best_month'][:7],PCT(dep.loc[n,'best_month_return']),dep.loc[n,'worst_month'][:7],PCT(dep.loc[n,'worst_month_return'])]
        for n,label in zip(NAMES,LABELS)],widths=[1.35,1.5,1.25,1.5,1.3])

    appendix('附录D 全部成本敏感性结果')
    p('单边3、5、10、20bp作为总交易摩擦情景统一计入fee，额外滑点为0；各组重新模拟现金与成交，目标权重规则固定。下表完整列示16组。')
    c=pd.read_csv(ROB/'cost_sensitivity.csv')
    table('16组成本情景',['策略','bp','年化','最大回撤','波动','夏普','Calmar'],[
        [SHORT[r.strategy],int(r.one_way_bp),PCT(getattr(r,'年化收益率')),PCT(getattr(r,'最大回撤')),PCT(getattr(r,'年化波动率')),NUM(getattr(r,'夏普比率')),NUM(c.iloc[i]['Calmar 比率'])]
        for i,r in enumerate(c.itertuples(index=False))],widths=[.65,.5,1.15,1.2,1.15,1.1,1.2])
    note('本附录及后续统计表：Q＝纯第一象限，C＝扩散＋RRG，M＝行业动量，E＝ETF等权。')

    appendix('附录E 全部参数稳健性结果')
    p('L为长期回看交易日，N为最多行业数。RS-momentum60日、MA20、3bp费用及新ETF池固定。复合策略同时改变扩散与RS-ratio长期回看期。ETF等权沿用10.12%年化的固定基准，不包含这两个信号参数。')
    table('27组参数情景',['策略','L','N','年化','最大回撤','波动','夏普','Calmar'],[
        [SHORT[r.strategy],int(r.lookback),int(r.max_industries),PCT(r['年化收益率']),PCT(r['最大回撤']),PCT(r['年化波动率']),NUM(r['夏普比率']),NUM(r['Calmar 比率'])]
        for _,r in grid.iterrows()],widths=[.55,.55,.45,1.1,1.15,1.1,1.0,1.05],size=9.2)
    p('同参数比较指相同L和名义最多N，实际选中行业数仍可能不同。纯第一象限优于固定220／6动量基准的参数组为2／9；优于同参数动量为6／9。两种比较的研究问题不同，应分别解释。')

    appendix('附录F 完整统计检验')
    p('样本为2019年1月至2026年3月87个完整月。HAC主滞后为3，另列1和6；配对差的95%置信区间为单项区间。Holm分别对主设置六组、成本24组、参数54组进行族内调整。三个族在5%阈值下拒绝数均为0，调整并未覆盖此前全部研究选择。')
    table('主设置六组检验',['比较','月均差 pp','95%区间 pp','原始p','Holm p'],[
        [pair(r.comparison),PP(r.mean_monthly_difference),ci(r.ci95_low,r.ci95_high),PV(r.p_two_sided),PV(r.p_Holm_6)] for r in primary.itertuples()],widths=[.75,1.25,2.5,1.2,1.2])
    a=pd.read_csv(ROB/'all_HAC_tests.csv')
    table('HAC滞后敏感性全部18组',['比较','滞后','月均差 pp','标准误 pp','t值','原始p','95%区间 pp'],[
        [pair(r.comparison),r.HAC_lags,PP(r.mean_monthly_difference),PP(r.standard_error),NUM(r.t_stat),PV(r.p_two_sided),ci(r.ci95_low,r.ci95_high)] for r in a.itertuples()],widths=[.65,.5,1.05,1.05,.75,.85,2.05],size=9)
    cp=pd.read_csv(ROB/'cost_pairwise_tests.csv')
    table('成本情景配对检验全部24组',['bp','比较','月均差 pp','95%区间 pp','原始p','Holm p'],[
        [r.one_way_bp,pair(r.comparison),PP(r.mean_monthly_difference),ci(r.ci95_low,r.ci95_high),PV(r.p_two_sided),PV(r.p_Holm_24)] for r in cp.itertuples()],widths=[.5,.7,1.15,2.3,1.1,1.15],size=9.2)
    gp=pd.read_csv(ROB/'grid_multiple_tests.csv')
    table('参数情景配对检验全部54组',['L','N','比较','月均差 pp','95%区间 pp','原始p','Holm p'],[
        [r.lookback,r.max_industries,pair(r.comparison),PP(r.mean_monthly_difference),ci(r.ci95_low,r.ci95_high),PV(r.p_two_sided),PV(r.p_Holm_54)] for r in gp.itertuples()],widths=[.5,.4,.65,1.1,2.15,1.05,1.05],size=9)
    p('每种参数设置包含三种主动策略之间的三组比较，以及它们分别对固定等权的三组比较。54项共同进行Holm调整。')
    b=pd.read_csv(ROB/'paired_block_bootstrap.csv')
    p('配对循环块bootstrap保持同月两策略收益的相关性，重复10000次，随机种子20261107。下表区间为百分位95%区间，年化差按252／1756换算。bootstrap的中心化双侧p值检验月均差，年化差另行估计。')
    table('Bootstrap全部12组',['比较','块长 月','月均差pp','中心化p','年化差pp','年化差95%区间pp'],[
        [pair(r.comparison),r.block_months,PP(r.observed_mean_monthly_gap),PV(r.mean_gap_centered_bootstrap_p),f'{r.observed_cagr_gap*100:.2f}',f'[{r.cagr_gap_ci95_low*100:.2f}, {r.cagr_gap_ci95_high*100:.2f}]'] for r in b.itertuples()],widths=[.7,.7,1.05,1.05,1.05,2.35],size=9.2)
    br=pd.read_csv(ROB/'benchmark_adjusted_regression.csv')
    p('另以动量与等权月收益解释两种RRG方案，截距采用HAC3。它衡量相对两个可投资基准的回归增量，不是FF风险因子调整alpha。')
    table('可投资基准回归',['策略','月截距pp','95%区间pp','原始p','Holm p','R平方'],[
        [SHORT[r.strategy],PP(r.monthly_intercept),ci(r.ci95_low,r.ci95_high),PV(r.p_intercept),PV(r.p_Holm_2),NUM(r.r_squared)] for r in br.itertuples()],widths=[.65,1.2,2.2,1.0,1.0,1.1])
    note('纯第一象限对动量与等权的系数分别为0.823、0.230；复合方案分别为0.783、0.139。该回归未单独识别行业、规模或其他风险暴露。')

    appendix('附录G 共同10个月均线过滤对照')
    p('我们固定原RRG的28行业等权基准为市场开关：月末基准高于包含当前月在内最近10个月末均值时，保持原策略ETF配置；其余月份目标归零持币，下个交易日开盘执行。两种策略使用同一信号、3bp费用、零现金收益及原有退市近似。该规则借鉴Faber趋势过滤并应用到本研究行业基准［4］。')
    fs=pd.read_csv(SMA/'performance.csv',index_col=0)
    flabels={'Pure_RRG_Q1':'纯第一象限','Pure_RRG_Q1_SMA10':'纯第一象限加均线','Industry_momentum_220_6':'动量','Industry_momentum_220_6_SMA10':'动量加均线'}
    table('均线过滤四组比较',['策略','年化','最大回撤','波动','夏普','净利润 万元'],[
        [label,PCT(fs.loc[n,'年化收益率']),PCT(fs.loc[n,'最大回撤']),PCT(fs.loc[n,'年化波动率']),NUM(fs.loc[n,'夏普比率']),f'{fs.loc[n,"profit_cny"]/10000:.2f}'] for n,label in flabels.items()],widths=[1.65,1.0,1.1,1.0,.85,1.3],size=9.5)
    p('过滤后纯第一象限年化下降8.28个百分点，最大回撤加深2.28个百分点；动量年化下降8.67个百分点，回撤加深7.77个百分点。纯第一象限仍领先同样过滤的动量3.18个百分点，但低于未过滤动量5.49个百分点。两条过滤组合平均持币约30.47%，波动与费用下降，回撤路径仍恶化。')
    fig('07_sma10.png','图G1 均线过滤与未过滤方案','虚线为未过滤，实线为过滤，灰区为过滤后持币期。')
    p('2024年9月是一个典型案例：8月底基准仍低于均线，因此9月持币，错过原纯第一象限17.58%的上涨；9月底转为开启后，10月8日才重新入场，过滤组合10月亏损9.78%，原组合同期为正0.53%。这一案例体现延迟入场及快速反弹的代价。完整逐月对照另存表格，本文未据此搜索其他窗口来替代预先固定的10个月规则。')
    sfannual=pd.read_csv(SMA/'annual_returns.csv',index_col=0)
    table('过滤实验全部年度收益',['年份','纯第一象限','加均线','动量','动量加均线'],[
        ['2026年一季度' if y==2026 else str(y),*[PCT(row[n]) for n in flabels]] for y,row in sfannual.loc[2019:].iterrows()],widths=[1.3,1.4,1.4,1.4,1.4],size=9.5)
    p('两条未过滤净值逐日重现原结果，过滤关闭期股票持仓归零，费用、现金、次日成交及期末估值核验通过。本实验结果支持将该固定过滤作为风控对照，主方案保留未过滤纯第一象限。')

    appendix('附录H 可复现结果索引')
    p('本报告的全部数字由保存的CSV结果读取，图表按同一数据重新绘制以便阅读。完整精度表格随报告存于tables目录，图形存于figures目录。源结果目录为Factor_Replication/results/rrg_tushare_repaired_20261107。')
    exports={
        'main_performance.csv':SRC/'performance.csv','annual_returns.csv':ROB/'annual_returns.csv',
        'rolling_summary.csv':ROB/'rolling_summary.csv','monthly_returns_complete.csv':ROB/'monthly_returns_complete.csv',
        'rolling_12m_returns.csv':ROB/'rolling_12m_returns.csv','cost_sensitivity.csv':ROB/'cost_sensitivity.csv',
        'parameter_grid.csv':ROB/'parameter_grid.csv','primary_pairwise_tests.csv':ROB/'primary_pairwise_tests.csv',
        'all_HAC_tests.csv':ROB/'all_HAC_tests.csv','cost_pairwise_tests.csv':ROB/'cost_pairwise_tests.csv',
        'grid_multiple_tests.csv':ROB/'grid_multiple_tests.csv','paired_block_bootstrap.csv':ROB/'paired_block_bootstrap.csv',
        'benchmark_adjusted_regression.csv':ROB/'benchmark_adjusted_regression.csv','month_dependency.csv':ROB/'month_dependency.csv',
        'historical_membership_coverage.csv':SRC/'historical_membership_coverage.csv',
        'sma10_performance.csv':SMA/'performance.csv','sma10_monthly_comparison.csv':SMA/'monthly_filter_comparison.csv',
        'sma10_market_filter.csv':SMA/'monthly_market_filter.csv','sma10_drawdown_periods.csv':SMA/'drawdown_periods.csv'}
    for name,src in exports.items():shutil.copy2(src,HERE/'tables'/name)
    table('关键实验文件与完整度',['文件','记录数','内容'],[
        ['main_performance.csv','4','主设置收益 风险 费用 净利润'],['cost_sensitivity.csv','16','全部成本情景'],
        ['parameter_grid.csv','27','全部参数情景'],['all_HAC_tests.csv','18','三种HAC滞后'],
        ['cost_pairwise_tests.csv','24','成本情景检验'],['grid_multiple_tests.csv','54','参数情景检验'],
        ['paired_block_bootstrap.csv','12','两种块长全部六组比较'],['monthly_returns_complete.csv','87','完整月度收益'],
        ['rolling_12m_returns.csv','76','全部滚动窗口'],['sma10_performance.csv','4','均线风控对照']],widths=[3.1,.8,3.0],size=9.5)
    p('核心输入数据库SHA256为e71a0043112ba173307eac16acbecc1f72c232bb1780ddac455d32c02a407524；代码固定版本为3f83a77f4c7996ed8185b8979a42c4372c1d811f。数据来源、输入文件哈希、运行环境及核验记录分别保存在源目录的run.json、input_audit.json、validation.json，以及稳健性子目录的input_file_hashes.json和final_readback.json。')
    p('主策略脚本为rrg_repair_inputs.py与rrg_repair_backtest.py；稳健性为rrg_report_robustness.py的repaired分支，统计为rrg_report_inference.py；均线过滤为rrg_sma10_overlay.py。旧结果未覆盖，当前报告与旧池版本保持分离。')
    p('本研究保留所有上述检验结果，包括较弱统计证据和未改善表现的均线过滤，以便后续在更完整数据及独立样本中检验。')
    h('参考文献与资料')
    for r in refs:note(r)
    doc.save(FILE)
    audit={'input_database_sha256':json.loads((SRC/'run.json').read_text())['input_sha256'],
           'main_strategies':4,'cost_cases':16,'grid_cases':27,'HAC_rows':18,'cost_tests':24,'grid_tests':54,
           'bootstrap_rows':12,'sma10_cases':4,'tables':table_no,'figures':7,
           'exported_tables':{n:hashlib.sha256((HERE/'tables'/n).read_bytes()).hexdigest() for n in exports}}
    (HERE/'report_data_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print(FILE)
    print(f'{table_no} tables; 7 figures; all 16 cost and 27 parameter cases plus full statistical families included.')


if __name__=='__main__':main()
