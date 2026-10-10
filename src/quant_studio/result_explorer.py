"""Interactive views over already validated, compatible experiment series."""

import json
from html import escape

from quant_studio import QuantStudioError
from quant_studio.nav import validate_comparison


def comparison_explorer(rows):
    if not 2 <= len(rows) <= 4:
        raise QuantStudioError("交互比较需要2至4个实验")
    reference = rows[0]["series"]
    payload = []
    for row in rows:
        series = row["series"]
        validate_comparison(reference, series)
        opening = (
            series.initial_nav if series.initial_nav is not None else series.rows[0][1]
        )
        if opening <= 0:
            raise QuantStudioError("比较需要正的期初净值")
        payload.append(
            {
                "title": row["title"],
                "run_id": row["run_id"],
                "values": [str(value / opening - 1) for _, value in series.rows],
            }
        )
    data = (
        json.dumps(
            {"dates": [d for d, _ in reference.rows], "series": payload},
            ensure_ascii=False,
        )
        .replace("<", "\\u003c")
        .replace("&", "\\u0026")
    )
    choices = "".join(
        '<label class="check"><input type="checkbox" class="curve-choice" '
        f'value="{index}" checked>'
        f"{escape(row['title'])}</label>"
        for index, row in enumerate(rows)
    )
    return (
        """<section id="comparison-explorer"><h3>全部实验曲线</h3>
<p>纵轴为各自原始期初归一化的累计收益；横轴按观测点等距排列。缩放只改变显示区间。</p>
<div class="recipe-grid">"""
        + choices
        + """</div>
<label>显示起点<input type="range" id="curve-start" min="0" value="0"></label>
<label>显示终点<input type="range" id="curve-end" min="0"></label>
<p id="curve-window" aria-live="polite"></p>
<svg id="curve-svg" viewBox="0 0 900 340" role="img"
aria-label="所选实验累计收益曲线" style="width:100%;height:auto"></svg>
<button class="btn" id="curve-export" disabled>导出当前区间CSV</button>
<noscript><p>交互曲线需要JavaScript，原始指标与报告链接仍可查看。</p></noscript>
<script type="application/json" id="curve-data">"""
        + data
        + r"""</script>
<script>(function(){
const data=JSON.parse(document.getElementById('curve-data').textContent);
const a=document.getElementById('curve-start'),b=document.getElementById('curve-end');
const svg=document.getElementById('curve-svg'), ns='http://www.w3.org/2000/svg';
const colors=['#1677ff','#c2410c','#047857','#7c3aed'];
const choices=Array.from(document.querySelectorAll('.curve-choice'));
choices.forEach((choice,i)=>choice.parentElement.style.color=colors[i]);
a.max=b.max=String(data.dates.length-1);b.value=b.max;
function bounds(){return [Math.min(+a.value,+b.value),Math.max(+a.value,+b.value)];}
function node(tag,attributes,text){const e=document.createElementNS(ns,tag);
Object.entries(attributes).forEach(([k,v])=>e.setAttribute(k,v));
if(text!==undefined)e.textContent=text;svg.appendChild(e);return e;}
function draw(){
const [start,end]=bounds();svg.replaceChildren();
document.getElementById('curve-window').textContent=
data.dates[start]+' → '+data.dates[end]+' · '+(end-start+1)+'个观测点';
const selected=choices.filter(c=>c.checked).map(c=>+c.value);
let lo=0,hi=0;
selected.forEach(i=>{for(let j=start;j<=end;j++){
const v=Number(data.series[i].values[j]);lo=Math.min(lo,v);hi=Math.max(hi,v);}});
const span=hi-lo||0.01,x=j=>70+800*(j-start)/Math.max(1,end-start);
const y=v=>285-240*(v-lo)/span;
for(let k=0;k<5;k++){const v=lo+span*k/4;
node('line',{x1:70,x2:870,y1:y(v),y2:y(v),stroke:'#e6ebf2'});
node('text',{x:2,y:y(v)+4,fill:'#667085','font-size':12},(v*100).toFixed(2)+'%');}
selected.forEach(i=>{let points='';for(let j=start;j<=end;j++){
points+=x(j)+','+y(Number(data.series[i].values[j]))+' ';}
node('polyline',{points,fill:'none',stroke:colors[i],'stroke-width':2});
if(start===end)node('circle',{cx:x(start),cy:y(Number(data.series[i].values[start])),
r:4,fill:colors[i]});});
node('text',{x:70,y:320,'font-size':12},data.dates[start]);
node('text',{x:870,y:320,'text-anchor':'end','font-size':12},data.dates[end]);}
[a,b,...choices].forEach(e=>e.addEventListener('input',draw));draw();
const button=document.getElementById('curve-export');button.disabled=false;
button.addEventListener('click',()=>{const [start,end]=bounds();
const selected=choices.filter(c=>c.checked).map(c=>+c.value);
function cell(v,header=false){let s=String(v);
if(header&&/^[\s]*[=+@-]/.test(s))s="'"+s;
return '"'+s.replaceAll('"','""')+'"';}
const headers=['date',...selected.map(i=>data.series[i].title+'累计收益（比例）')];
const lines=[headers.map(v=>cell(v,true)).join(',')];
for(let j=start;j<=end;j++){
const values=[data.dates[j],...selected.map(i=>data.series[i].values[j])];
lines.push(values.map(v=>cell(v)).join(','));}
const blob=new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'});
const url=URL.createObjectURL(blob),link=document.createElement('a');
link.href=url;link.download='experiment-comparison.csv';link.click();
setTimeout(()=>URL.revokeObjectURL(url),1000);});
})();</script></section>"""
    )
