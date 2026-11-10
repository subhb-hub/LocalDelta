"""Offline dashboard and optional token-authenticated loopback read-only API."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit
import webbrowser

import analytics
import pdp


HTML = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>LocalDelta · 账户看板</title>
<style>
:root{font-family:Inter,"Noto Sans SC",system-ui,sans-serif;color:#dce8ed;background:#0b121b;color-scheme:dark;font-size:14px}*{box-sizing:border-box}body{margin:0}button,input,select{font:inherit;color:inherit;background:#152331;border:1px solid #344656;border-radius:7px;padding:8px 10px}button{cursor:pointer}button:hover{border-color:#64dcc1}button:disabled{opacity:.4;cursor:default}header{padding:28px 4vw 20px;background:#101d2a;border-bottom:1px solid #2b3a48}h1{font-size:27px;margin:0 0 8px;letter-spacing:-.7px}.brand{font-size:12px;letter-spacing:3px;color:#75e0c8;margin-bottom:13px}.sub,.muted{color:#9eafbd;line-height:1.6}.badge{padding:4px 8px;border-radius:4px;background:#243441;color:#94dfd1;font-size:11px;margin-left:10px}main{max-width:1600px;margin:auto;padding:22px 4vw}.notice{border:1px solid #655138;background:#29231c;padding:13px 16px;border-radius:10px;line-height:1.8;color:#e2c396}.toolbar{display:flex;align-items:end;gap:12px;flex-wrap:wrap;margin:20px 0}label{font-size:12px;color:#a9bbc6;display:grid;gap:6px}select[multiple]{min-width:225px;height:88px}.grow{flex:1}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0}.metric{padding:18px;background:#111e2b;border:1px solid #283a49;border-radius:10px}.metric strong{display:block;font-size:29px;color:#76e5c9;margin:6px 0}.metric small{color:#99aeba}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.block{border:1px solid #2a3b4b;background:#111c28;border-radius:12px;min-width:0;overflow:hidden}.block.wide{grid-column:1/-1}.blockhead{display:flex;align-items:center;gap:8px;padding:15px 18px;border-bottom:1px solid #2a3b4b}.blockhead h2{font-size:16px;margin:0;flex:1}.blockhead button{font-size:12px;padding:3px 7px}.content{padding:16px;overflow:auto}.content p{line-height:1.7}.scroll{max-height:440px;overflow:auto}table{border-collapse:collapse;font-size:12px;min-width:100%;white-space:nowrap}td,th{text-align:left;padding:10px 12px;border-bottom:1px solid #283746}th{color:#91aab8;font-weight:500;position:sticky;top:0;background:#162433}td.wrap{white-space:normal;max-width:300px;min-width:200px}td.amount{font-variant-numeric:tabular-nums}.pill{display:inline-block;padding:2px 6px;border-radius:3px;background:#423624;color:#e4bd78;font-size:11px}.legend{font-size:12px;color:#9fb6c4;display:flex;gap:16px;margin:8px 0 12px}.legend span:first-child{color:#8bdeca}.legend span:nth-child(2){color:#e0bb71}.empty{padding:35px;text-align:center;color:#92a8b8}.note{font-size:12px;color:#94a9b8;margin:12px 0 0;line-height:1.6}.error{color:#ffb4a8}.pager{display:flex;align-items:center;gap:12px;margin-top:12px;font-size:12px}.pager span{flex:1}.edge{cursor:pointer;fill:none;stroke:#d6ad64;stroke-width:1.7;stroke-dasharray:5 4;opacity:.65}.edge:hover{stroke:#f5daac;stroke-width:4;opacity:1}.edge.confirmed{stroke:#6de4c3;stroke-dasharray:none}.edge.conflicting{stroke:#ef8b8b}.lane{stroke:#304451;stroke-dasharray:3 5}.svgtext{fill:#a2b9c6;font-size:11px;font-family:inherit}.tip{border:1px solid #3e5665;background:#152532;padding:12px;border-radius:7px;font-size:12px;line-height:1.7;white-space:pre-wrap}.footer{font-size:12px;color:#829aa8;padding:28px 0}.positive{color:#78d8c0}.negative{color:#ecb483}@media(max-width:800px){.grid{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}main{padding:18px}.block.wide{grid-column:auto}header{padding:22px 18px}}
</style>
<header><div class="brand">LOCALDELTA / ACCOUNT WORKSPACE</div><h1>看清账户之间的每一次流动 <span class="badge" id="mode">本地看板</span></h1><div class="sub">账户是泳道，日期是横轴。将流水、配对证据与持仓观察放在同一个工作台。</div></header>
<main><div class="notice">资料尚未完整对账。虚线表示待确认的转账配对；未知收支方向的记录保留在核验区。现金净变动包含账户搬运和赎回，不代表投资收益。缺失资料不按零处理。</div>
<div class="toolbar"><label>起始日期<input type="date" id="start"></label><label>截止日期<input type="date" id="end"></label><label>账户筛选（按 Ctrl / ⌘ 多选；空选为全部）<select id="accounts" multiple></select></label><button id="apply">应用筛选</button><button id="reset">重置筛选</button><div class="grow"></div><label>添加看板块<select id="blocktype"></select></label><button id="add">＋ 添加</button><button id="layoutreset">恢复布局</button></div>
<div id="status" class="muted" role="status"></div><div id="metrics" class="metrics"></div><div id="grid" class="grid"></div>
<div class="footer">LocalDelta · 数据仅在本机处理 · 页面不加载外部脚本、字体或分析服务。离线导出包含个人财务数据，请按私密文件保存。</div></main>
<script id="snapshot" type="application/json">__SNAPSHOT__</script>
<script>
'use strict';
const snapshot=JSON.parse(document.getElementById('snapshot').textContent),live=__LIVE__,token=location.hash.slice(1);if(live)history.replaceState(null,'',location.pathname);
const $=id=>document.getElementById(id),el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
let defaultLayout=['account_timeline','cash_movements','review_queue','position_history','cash_table','source_coverage'];
const structureLayout=['portfolio_accounts','portfolio_assets','account_timeline','confirmed_capital'];let layoutWasSaved=false,portfolioMode=false;
let layout=defaultLayout.slice(),current=snapshot,names=new Map(),pages={};
try{const saved=JSON.parse(localStorage.getItem('localdelta.block-layout.v1'));if(Array.isArray(saved)&&saved.length<=20){layout=saved.filter(t=>snapshot.blocks.some(b=>b.type===t));layoutWasSaved=true;}}catch{}
function save(){try{localStorage.setItem('localdelta.block-layout.v1',JSON.stringify(layout));}catch{}}
function opts(source=snapshot){names=new Map(source.data.accounts.accounts.map(a=>[a.account_id,a.display_name]));for(const a of source.data.accounts.accounts){const o=el('option',a.display_name);o.value=a.account_id;$('accounts').append(o);}for(const b of source.blocks){const o=el('option',b.title);o.value=b.type;$('blocktype').append(o);}}
function filters(){return{start:$('start').value,end:$('end').value,accounts:Array.from($('accounts').selectedOptions).map(x=>x.value)}}
function selectedAccount(a,f){return !f.accounts.length||f.accounts.includes(a)}
function filtered(){const f=filters(),d=structuredClone(snapshot);for(const view of ['movements','positions','review','fx','asset_catalog','capital']){const v=d.data[view];v.rows=v.rows.filter(x=>selectedAccount(x.account_id,f)&&(!x.date||(!f.start||x.date>=f.start)&&(!f.end||x.date<=f.end)));v.total=v.rows.length;}
const t=d.data.timeline;t.edges=t.edges.filter(x=>(!f.accounts.length||f.accounts.includes(x.from_account_id)||f.accounts.includes(x.to_account_id))&&(!f.start||[x.sent_date,x.received_date].sort()[1]>=f.start)&&(!f.end||[x.sent_date,x.received_date].sort()[0]<=f.end));t.total=t.edges.length;
// Exact decimal cents via BigInt. Conversion to Number is reserved for chart coordinates.
const sums=new Map(),units=(x,ccy)=>{const scale=snapshot.currency_units[ccy],factor=10n**BigInt(scale),[a,b='']=x.split('.');return BigInt(a.replace('-',''))*factor+BigInt(b.padEnd(scale,'0')||'0')},fmt=(n,ccy)=>{const scale=snapshot.currency_units[ccy],factor=10n**BigInt(scale),abs=n<0n?-n:n;return(n<0n?'-':'')+String(abs/factor)+(scale?'.'+String(abs%factor).padStart(scale,'0'):'')};
for(const r of d.data.movements.rows){const k=r.account_id+'|'+r.currency;if(!sums.has(k))sums.set(k,{account_id:r.account_id,currency:r.currency,inflow:0n,outflow:0n,net_movement:0n,known_rows:0,unknown_direction_rows:0});const a=sums.get(k);if(r.signed_amount===null){a.unknown_direction_rows++;continue;}const n=units(r.signed_amount,r.currency)*(r.signed_amount.startsWith('-')?-1n:1n);a.known_rows++;a.net_movement+=n;if(n>=0n)a.inflow+=n;else a.outflow-=n;}
d.data.portfolio.rows=d.data.portfolio.rows.filter(x=>selectedAccount(x.account_id,f));
const caps=new Map();for(const r of d.data.capital.rows){caps.set(r.basis_currency,(caps.get(r.basis_currency)||0n)+units(r.basis_amount,r.basis_currency));}d.data.capital.basis_totals=[...caps].map(([currency,value])=>({currency,amount:fmt(value,currency)}));
d.data.summary.rows=[...sums.values()].map(x=>({...x,inflow:x.known_rows?fmt(x.inflow,x.currency):null,outflow:x.known_rows?fmt(x.outflow,x.currency):null,net_movement:x.known_rows?fmt(x.net_movement,x.currency):null}));return d;}
async function apply(){if($('start').value&&$('end').value&&$('start').value>$('end').value){$('status').textContent='起始日期不能晚于截止日期。';return;}pages={};try{if(live){const f=filters(),q=new URLSearchParams();if(f.start)q.set('start',f.start);if(f.end)q.set('end',f.end);f.accounts.forEach(x=>q.append('account_id',x));const res=await fetch('/api/dashboard?'+q,{headers:{Authorization:'Bearer '+token},cache:'no-store'});if(!res.ok)throw new Error('读取被拒绝（'+res.status+'）');current=await res.json();if(!$('accounts').options.length){$('blocktype').replaceChildren();opts(current);}}else current=filtered();render();}catch(e){$('status').textContent=e.message;$('status').className='error';}}
function table(parent,rows,columns,key){const per=15,page=pages[key]||0,wrap=el('div',undefined,'scroll'),t=el('table'),thead=el('thead'),hr=el('tr');columns.forEach(([label])=>hr.append(el('th',label)));thead.append(hr);t.append(thead);const body=el('tbody');for(const row of rows.slice(page*per,(page+1)*per)){const tr=el('tr');for(const [,get,cls]of columns){const v=get(row);tr.append(el('td',v===null||v===undefined||v===''?'—':String(v),cls||''));}body.append(tr);}t.append(body);wrap.append(t);if(!rows.length)parent.append(el('div','当前筛选下没有记录','empty'));else parent.append(wrap);const nav=el('div',undefined,'pager');nav.append(el('span',rows.length+' 条 · 第 '+(page+1)+' / '+Math.max(1,Math.ceil(rows.length/per))+' 页'));for(const [txt,step]of[['上一页',-1],['下一页',1]]){const b=el('button',txt);b.disabled=page+step<0||(page+step)*per>=rows.length;b.onclick=()=>{pages[key]=page+step;render()};nav.append(b);}parent.append(nav);}
function timeline(parent,data){const rows=data.edges,lanes=data.account_lanes;if(!rows.length){parent.append(el('div','当前筛选下没有转账配对','empty'));return;}const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');const w=1080,h=lanes.length*72+85;svg.setAttribute('viewBox',`0 0 ${w} ${h}`);svg.style.minWidth='760px';const dates=rows.flatMap(e=>[e.sent_date,e.received_date]).sort();let lo=Date.parse(dates[0]+'T00:00:00Z'),hi=Date.parse(dates.at(-1)+'T00:00:00Z');if(lo===hi)hi+=86400000;const x=d=>170+(Date.parse(d+'T00:00:00Z')-lo)/(hi-lo)*870,y=new Map(lanes.map((a,i)=>[a.account_id,42+i*72]));function shape(tag,attrs,text){const n=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text)n.textContent=text;svg.append(n);return n;}
const defs=document.createElementNS(ns,'defs'),marker=document.createElementNS(ns,'marker');marker.id='arrow'+parent.dataset.key;marker.setAttribute('viewBox','0 0 10 10');marker.setAttribute('refX','9');marker.setAttribute('refY','5');marker.setAttribute('markerWidth','5');marker.setAttribute('markerHeight','5');marker.setAttribute('orient','auto-start-reverse');const triangle=document.createElementNS(ns,'path');triangle.setAttribute('d','M 0 0 L 10 5 L 0 10 z');triangle.setAttribute('fill','#d6ad64');marker.append(triangle);defs.append(marker);svg.append(defs);
for(const a of lanes){shape('line',{x1:165,x2:1050,y1:y.get(a.account_id),y2:y.get(a.account_id),class:'lane'});shape('text',{x:0,y:y.get(a.account_id)+4,class:'svgtext'},a.display_name);}for(let i=0;i<6;i++){const d=new Date(lo+(hi-lo)*i/5).toISOString().slice(0,10);shape('text',{x:170+870*i/5,y:h-15,class:'svgtext','text-anchor':'middle'},d);}
const detail=el('div','点击或聚焦一条连线，查看两端原币金额、日期和证据位置。','tip');
rows.forEach((r,i)=>{let xa=x(r.sent_date),xb=x(r.received_date),ya=y.get(r.from_account_id),yb=y.get(r.to_account_id);const bend=(i%5-2)*9;const path=shape('path',{d:`M${xa},${ya} C${xa+25+bend},${ya+(yb-ya)*.25} ${xb+25+bend},${yb-(yb-ya)*.25} ${xb},${yb}`,class:'edge '+r.certainty,'marker-end':`url(#${marker.id})`,tabindex:0});const label=(names.get(r.from_account_id)||'?')+' → '+(names.get(r.to_account_id)||'?')+'\n'+r.sent_date+' '+r.debit_amount+' '+r.sent_currency+' → '+r.received_date+' '+r.credit_amount+' '+r.received_currency+'\n配对状态：'+r.certainty+' · 来源仅有日期精度\n付款证据：'+r.debit_source+'\n收款证据：'+r.credit_source;const title=document.createElementNS(ns,'title');title.textContent=label;path.append(title);path.onclick=path.onfocus=()=>detail.textContent=label;shape('circle',{cx:xa,cy:ya,r:2.5,fill:'#d6ad64'});});parent.append(el('div','虚线：待确认配对　实线：已确认配对　箭头：付款账户 → 收款账户','legend'));parent.append(svg,detail);parent.append(el('p','日期使用两端账单记账日期；可能先收到入账记录，后见扣账记录。配对中的不同币种金额分别保留，不推算换汇损失。','note'));}
function render(){const d=current.data;if(d.portfolio?.enabled){defaultLayout=structureLayout.slice();if(!portfolioMode&&!layoutWasSaved)layout=defaultLayout.slice();portfolioMode=true;document.querySelector('h1').firstChild.textContent='资产组合与账户结构 ';document.querySelector('header .sub').textContent='按账户整理资产档案、归属与已确认的资金关系。';document.querySelector('.notice').textContent='本页整理账户和资产档案，历史记录与待核实记录保留原状态。资产快照估值单独保存，暂不计算当前市值、配置权重或收益率。';}$('grid').replaceChildren();$('metrics').replaceChildren();const metrics=portfolioMode?[['账户节点',d.portfolio.rows.length,'按平台和账户分组'],['资产档案',d.asset_catalog.total,'包含历史与待核实档案'],['已确认转账',d.timeline.edges.filter(x=>x.certainty==='confirmed').length,'用户确认，保留原始依据'],['未定平台档案',d.portfolio.unresolved_asset_accounts,'全库档案数，不阻塞本次整理']]:[['已建账户',current.data.accounts.accounts.length,'账户与币种独立查询'],['转账配对',d.timeline.total,'配对可信度逐笔保留'],['现金记录',d.movements.total,'原始方向与余额字段保留'],['待核验记录',d.review.total,'未知方向、候选交易和资料缺口']];for(const [a,b,c]of metrics){const m=el('div',undefined,'metric');m.append(el('span',a),el('strong',String(b)),el('small',c));$('metrics').append(m);}$('mode').textContent=live?'本机只读 API':'离线私密快照';$('status').className='muted';$('status').textContent='数据库修订 '+current.revision+' · '+(live?'按当前筛选读取数据库':'导出时数据快照，可离线筛选')+' · 布局自动保存在当前浏览器';
layout.forEach((type,i)=>{const spec=snapshot.blocks.find(b=>b.type===type);if(!spec)return;const block=el('section',undefined,'block '+(spec.view==='timeline'?'wide':'')),head=el('div',undefined,'blockhead');head.append(el('h2',spec.title));for(const [txt,action]of[['↑',()=>{if(i){[layout[i-1],layout[i]]=[layout[i],layout[i-1]];save();render();}}],['↓',()=>{if(i<layout.length-1){[layout[i+1],layout[i]]=[layout[i],layout[i+1]];save();render();}}],['移除',()=>{layout.splice(i,1);save();render();}]]){const b=el('button',txt);b.onclick=action;head.append(b);}const content=el('div',undefined,'content');content.dataset.key=String(i);const key=type+':'+i,account=r=>names.get(r.account_id)||r.account_id;
if(spec.view==='portfolio'){table(content,d.portfolio.rows,[['账户',r=>r.display_name],['平台',r=>r.institution],['资产档案',r=>r.asset_records],['登记品种',r=>r.registered_instruments]],key);content.append(el('p','档案数不等于当前持有数。未提供持仓资料的账户仍予以登记，不按零资产处理。','note'));}
else if(spec.view==='asset_catalog'){table(content,d.asset_catalog.rows,[['资产',r=>r.asset_name,'wrap'],['类型',r=>r.asset_type],['币种',r=>r.currency],['账户',r=>r.account_name||('平台待核实；原标签：'+r.original_account_label),'wrap'],['原记录状态',r=>r.original_status],['归属依据',r=>({documented:'账单来源明确',legacy_label:'原账户标签',unresolved:'平台待核实'}[r.mapping_status])]],key);content.append(el('p','资产目录保留历史记录、讨论占位和原状态，暂不从旧快照推断当前持仓、市值或配置权重。','note'));}
else if(spec.view==='capital'){table(content,d.capital.rows,[['到账日期',r=>r.date],['原始入金依据',r=>r.basis_amount],['依据币种',r=>r.basis_currency],['实际收到',r=>r.received_amount],['到账币种',r=>r.received_currency],['用户确认',r=>r.user_note,'wrap']],key);content.append(el('p','已确认入金依据：'+d.capital.basis_totals.map(r=>r.amount+' '+r.currency).join('；')+'。内部搬运和非投资转账不重复计入；此处不是全部历史本金或当前资产总额。','note'));}
else if(spec.view==='timeline')timeline(content,d.timeline);
else if(spec.view==='summary'){table(content,d.summary.rows,[['账户',account],['币种',r=>r.currency],['已知流入',r=>r.inflow,'amount'],['已知流出',r=>r.outflow,'amount'],['净变动',r=>r.net_movement,'amount'],['方向待核验',r=>r.unknown_direction_rows]],key);content.append(el('p','只汇总已知方向的现金记录。不同币种不相加；内部转账和赎回仍是现金变动；余额、净值和投资收益尚不可据此计算。','note'));}
else if(spec.view==='review')table(content,d.review.rows,[['账户',account],['日期',r=>r.date],['类型',r=>r.kind],['摘要',r=>r.description,'wrap'],['原币金额',r=>r.statement_amount],['币种',r=>r.currency],['证据位置',r=>r.source_locator,'wrap']],key);
else if(spec.view==='fx')table(content,d.fx.rows,[['账户',account],['日期',r=>r.date],['卖出金额',r=>r.sold_amount],['卖出币种',r=>r.sold_currency],['买入金额',r=>r.bought_amount],['买入币种',r=>r.bought_currency],['记录汇率',r=>r.recorded_rate]],key);
else if(spec.view==='positions')table(content,d.positions.rows,[['账户',account],['估值日期',r=>r.date],['资产',r=>r.description,'wrap'],['数量',r=>r.quantity],['原币估值',r=>r.market_value],['币种',r=>r.currency]],key);
else if(spec.view==='movements')table(content,d.movements.rows,[['账户',account],['日期',r=>r.date],['摘要',r=>r.description,'wrap'],['已知方向金额',r=>r.signed_amount],['方向未定金额',r=>r.signed_amount===null?r.statement_amount:null],['币种',r=>r.currency],['账单记录余额',r=>r.reported_balance]],key);
else if(spec.view==='coverage'){table(content,d.coverage.rows.filter(r=>selectedAccount(r.account_id,filters())),[['账户',account],['资料类型',r=>r.kind],['最早日期',r=>r.first_date],['最晚日期',r=>r.last_date],['记录数',r=>r.records]],key);content.append(el('p','此处显示已观察到的日期范围，不代表区间内账单齐全。汇丰配对证据不构成完整现金流水，旧版投资资产表仍单独保留。','note'));}
if(!content.childNodes.length){const v=d[spec.view];if(v&&Array.isArray(v.rows)){const keys=Object.keys(v.rows[0]||{});table(content,v.rows,keys.map(k=>[k,r=>r[k]]),key);}else content.append(el('p','此视图需要本地渲染组件。','note'));}block.append(head,content);$('grid').append(block);});}
opts();$('apply').onclick=apply;$('reset').onclick=()=>{$('start').value='';$('end').value='';Array.from($('accounts').options).forEach(o=>o.selected=false);apply();};$('add').onclick=()=>{if(layout.length>=20){$('status').textContent='最多添加 20 个块';return;}layout.push($('blocktype').value);save();render();};$('layoutreset').onclick=()=>{layout=defaultLayout.slice();save();render();};if(live)apply();else render();
</script></html>'''


def snapshot_data(db, **filters):
    """Read all table pages in one consistent snapshot for offline filtering."""
    with analytics.QuerySession(db) as session:
        data = {}
        for view in sorted(analytics.VIEWS):
            options = dict(filters)
            if view in {'movements', 'positions', 'review', 'timeline', 'fx', 'asset_catalog'}:
                options.update(limit=1000, offset=0)
            value = session.query(view, **options)
            key = 'edges' if view == 'timeline' else 'rows'
            while value.get('has_more'):
                options['offset'] += options['limit']
                page = session.query(view, **options)
                value[key].extend(page[key])
                value['has_more'] = page['has_more']
            data[view] = value
        from multicurrency import MINOR_UNITS
        return {'blocks': list(analytics.BLOCKS.values()), 'currency_units': MINOR_UNITS,
                'revision': pdp.get_meta(session.c)['revision'], 'data': data}


def render(data, live=False):
    # JSON in a script element must escape HTML delimiters, including </script>.
    payload = json.dumps(data, ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    return HTML.replace('__LIVE__', 'true' if live else 'false').replace('__SNAPSHOT__', payload)


def export(db, out):
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(snapshot_data(db)), encoding='utf-8')
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return {'status': 'exported', 'path': str(target.resolve()), 'private': True}


def make_server(db, port=0):
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log URLs, bearer tokens, or financial data.

        def reply(self, code, value, html=False):
            content = value.encode('utf-8') if html else json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            expected_host = '127.0.0.1:' + str(self.server.server_port)
            if self.headers.get('Host') != expected_host:
                return self.reply(403, {'error': 'Host rejected'})
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + expected_host:
                return self.reply(403, {'error': 'Origin rejected'})
            url = urlsplit(self.path)
            if url.path == '/':
                # The unauthenticated shell contains no financial snapshot.
                shell = {'blocks': list(analytics.BLOCKS.values()), 'revision': 0,
                         'data': {'accounts': {'accounts': []}}}
                return self.reply(200, render(shell, live=True), html=True)
            if not hmac.compare_digest(self.headers.get('Authorization', '').encode('utf-8'), ('Bearer ' + token).encode('utf-8')):
                return self.reply(401, {'error': 'Bearer token required'})
            try:
                q = parse_qs(url.query, keep_blank_values=True)
                if set(q) - {'view', 'account_id', 'start', 'end', 'limit', 'offset'}:
                    return self.reply(400, {'error': 'Unknown query parameter'})
                for k, v in q.items():
                    if k != 'account_id' and len(v) != 1:
                        return self.reply(400, {'error': 'Duplicate query parameter'})
                filters = {}
                if 'account_id' in q:
                    filters['account_ids'] = q['account_id']
                for k in ('start', 'end'):
                    if k in q:
                        filters[k] = q[k][0]
                for k in ('limit', 'offset'):
                    if k in q:
                        filters[k] = int(q[k][0])
                if url.path == '/api/blocks':
                    return self.reply(200, {'blocks': list(analytics.BLOCKS.values())})
                if url.path == '/api/dashboard':
                    return self.reply(200, snapshot_data(db, **filters))
                if url.path.startswith('/api/records/'):
                    return self.reply(200, analytics.record(db, url.path.removeprefix('/api/records/')))
                view = q.get('view', [''])[0] if url.path == '/api/query' else url.path.removeprefix('/api/')
                if not url.path.startswith('/api/') or view not in analytics.VIEWS:
                    return self.reply(404, {'error': 'Unknown endpoint'})
                return self.reply(200, analytics.query(db, view, **filters))
            except (pdp.PDPError, ValueError, TypeError):
                return self.reply(400, {'error': 'Invalid query or unaudited database; check local status'})
            except Exception:
                return self.reply(500, {'error': 'Local query failed'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server, token


def serve(db, port=0, open_browser=True):
    # Validate once before opening a listener; every query uses an audited snapshot.
    analytics.query(db, 'accounts')
    server, token = make_server(db, port)
    url = 'http://127.0.0.1:' + str(server.server_port) + '/#' + token
    print('Private local dashboard URL (do not share): ' + url, flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
