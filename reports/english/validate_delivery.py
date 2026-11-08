"""Verify the delivered manuscripts against their archived experiment inputs."""
from pathlib import Path
import csv
import hashlib
import json
import re
import numpy as np
import pandas as pd
from pypdf import PdfReader

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
ORIGINAL=ROOT/'reports/rrg_final_en_20261107'
SRC=ROOT/'results/rrg_tushare_repaired_20261107'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(n):
    with (HERE/'tables'/n).open() as f:return list(csv.DictReader(f))
def images(resources,seen=None):
    seen=set() if seen is None else seen
    count=0
    if resources is None:return 0
    for obj in resources.get('/XObject',{}).values():
        obj=obj.get_object()
        key=id(obj)
        if key in seen:continue
        seen.add(key)
        if obj.get('/Subtype')=='/Image':count+=1
        else:count+=images(obj.get('/Resources'),seen)
    return count

expected_rows={'main_performance.csv':4,'cost_sensitivity.csv':16,'parameter_grid.csv':27,
'primary_pairwise_tests.csv':6,'all_HAC_tests.csv':18,'cost_pairwise_tests.csv':24,
'grid_multiple_tests.csv':54,'paired_block_bootstrap.csv':12,
'monthly_returns_complete.csv':87,'rolling_12m_returns.csv':76}
for n,k in expected_rows.items():assert len(read(n))==k,(n,k)
for f in (HERE/'tables').glob('*.csv'):
    assert sha(f)==sha(ORIGINAL/'tables'/f.name),f.name
for n in ['net_nav.csv','sma10_filter/net_nav.csv','sma10_filter/effective_daily_market_filter.csv']:
    assert sha(HERE/'data'/n)==sha(SRC/n),n
inputs=json.loads((HERE/'qa/figure_input_hashes.json').read_text())
for rel,h in inputs.items():assert sha(HERE/rel)==h,rel
nav=pd.read_csv(HERE/'data/net_nav.csv',index_col=0)
stats={r['strategy']:r for r in read('main_performance.csv')}
for n in nav:
    dd=(nav[n]/nav[n].cummax()-1).min()
    assert np.isclose(dd,float(stats[n]['最大回撤']),atol=1e-12)
    assert np.isclose(nav[n].iloc[-1]*1e7,float(stats[n]['ending_value_cny']),rtol=1e-12)

pages={};raster_counts={};figure_text={}
for f in sorted((HERE/'figures').glob('*.pdf')):
    r=PdfReader(f);assert len(r.pages)==1
    s=r.pages[0].extract_text()
    assert not re.search('[\u3400-\u9fff]',s),f.name
    assert images(r.pages[0].get('/Resources'))==0,f.name
    figure_text[f.name]=s
for f in [HERE/'RRG_Report.pdf',HERE/'RRG_Technical_Appendix.pdf']:
    r=PdfReader(f);pages[f.name]=len(r.pages)
    text='\n'.join(p.extract_text() for p in r.pages)
    assert not re.search('[\u3400-\u9fff]',text),f.name
    assert all(s not in text for s in ['??','TODO','Existing Chinese','Chinese labels'])
    raster_counts[f.name]=sum(images(p.get('/Resources')) for p in r.pages)
    assert raster_counts[f.name]==0
    (HERE/'qa'/f'{f.stem}_text.txt').write_text(text)
assert pages['RRG_Report.pdf']==5
assert pages['RRG_Technical_Appendix.pdf']==13
for n in ['RRG_Report','RRG_Technical_Appendix']:
    log=(HERE/f'{n}.log').read_text()
    assert 'Overfull' not in log,n
    assert 'Missing character' not in log,n
    tex=(HERE/f'{n}.tex').read_text()
    assert not re.search(r'\\includegraphics\[[^]]*\]\{[^}]*\.png\}',tex)
for v in ['24.62','18.95','21.83','10.12','44.76','0.967','0.892','0.468','0.101']:
    assert v in (HERE/'RRG_Report.tex').read_text(),v
result=dict(pages=pages,archived_table_hashes_verified=19,source_series_hashes_verified=3,
figure_input_files_verified=len(inputs),English_vector_figures_verified=len(figure_text),
embedded_raster_images=raster_counts,CJK_glyphs=0,overfull_boxes=0,
nav_endpoints_and_drawdowns_verified=True,
pdf_sha256={f.name:sha(f) for f in HERE.glob('*.pdf')})
(HERE/'qa/validation.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
