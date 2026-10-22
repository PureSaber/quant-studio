(() => {
  const data=JSON.parse(document.getElementById('nav-observations').textContent);
  const svg=document.getElementById('nav-svg'),dd=document.getElementById('nav-dd');
  const start=document.getElementById('nav-start'),end=document.getElementById('nav-end');
  const unit=document.getElementById('nav-unit'),log=document.getElementById('nav-log');
  const ns='http://www.w3.org/2000/svg';
  let width=940,left=90,right=860;
  start.max=end.max=String(data.dates.length-1);end.value=end.max;
  let selected=0;
  function add(target,tag,attributes,text){const node=document.createElementNS(ns,tag);Object.entries(attributes).forEach(([k,v])=>node.setAttribute(k,v));if(text!==undefined)node.textContent=text;target.appendChild(node);return node;}
  function bounds(){return [Math.min(+start.value,+end.value),Math.max(+start.value,+end.value)];}
  function nice(low,high){const span=high-low||Math.abs(high)*0.1||1,raw=span/4,power=10**Math.floor(Math.log10(raw)),ratio=raw/power;const step=(ratio<=1?1:ratio<=2?2:ratio<=5?5:10)*power;return [Math.floor(low/step)*step,Math.ceil(high/step)*step,step];}
  function fmt(n){return n.toLocaleString('en-US',{maximumFractionDigits:5,minimumFractionDigits:2});}
  function draw(){width=Math.max(300,svg.clientWidth||940);right=width-90;
    svg.setAttribute('viewBox','0 0 '+width+' 350');dd.setAttribute('viewBox','0 0 '+width+' 180');
    document.getElementById('nav-brush-svg').setAttribute('viewBox','0 0 '+width+' 60');
    const [a,b]=bounds(),normalize=unit.value==='normalized',scale=normalize?Number(data.opening):1;
    const curves=[data.values,...(data.baseline?[data.baseline]:[])].map(row=>row.map(v=>Number(v)/scale));
    let low=Infinity,high=-Infinity;curves.forEach(row=>row.slice(a,b+1).forEach(v=>{low=Math.min(low,v);high=Math.max(high,v);}));
    let [lo,hi,step]=nice(low,high);if(hi===lo)hi=lo+step;
    const isLog=!!log?.checked,x=i=>left+(right-left)*(i-a)/Math.max(1,b-a);
    if(isLog){lo=Math.max(low*0.95,Number.MIN_VALUE);hi=high*1.05;}
    const y=v=>285-250*(isLog?(Math.log(v)-Math.log(lo))/(Math.log(hi)-Math.log(lo)):(v-lo)/(hi-lo));
    svg.replaceChildren();dd.replaceChildren();
    const ticks=[];if(isLog){for(let k=0;k<5;k++)ticks.push(Math.exp(Math.log(lo)+(Math.log(hi)-Math.log(lo))*k/4));}else{for(let v=lo;v<=hi+step/100;v+=step)ticks.push(v);}
    ticks.forEach(v=>{add(svg,'line',{x1:left,x2:right,y1:y(v),y2:y(v),stroke:'var(--line)'});add(svg,'text',{x:left-10,y:y(v)+4,'text-anchor':'end',fill:'var(--muted)'},fmt(v));});
    curves.forEach((values,k)=>{let points='';for(let i=a;i<=b;i++)points+=x(i)+','+y(values[i])+' ';const color=k?'var(--benchmark)':'var(--series-1)';add(svg,'polyline',{points,fill:'none',stroke:color,'stroke-width':2.5,'stroke-dasharray':k?'7 4':''});add(svg,'circle',{cx:x(b),cy:y(values[b]),r:3,fill:color});add(svg,'text',{x:right+7,y:y(values[b])+(k?15:0),fill:color},fmt(values[b]));});
    const stride=Math.max(1,Math.ceil((b-a)/(width<600?2:5)));for(let i=a;i<=b;i+=stride)add(svg,'text',{x:x(i),y:322,'text-anchor':'middle',fill:'var(--muted)'},String(data.dates[i]).slice(0,10));
    let worst=-0.1;for(let i=a;i<=b;i++)worst=Math.min(worst,Number(data.drawdowns[i]));
    const dy=v=>25+115*Number(v)/worst;add(dd,'line',{x1:left,x2:right,y1:25,y2:25,stroke:'var(--line)'});add(dd,'text',{x:10,y:25,fill:'var(--muted)'},'回撤 0.00%');add(dd,'text',{x:10,y:140,fill:'var(--muted)'},fmt(worst)+'%');
    let points='';for(let i=a;i<=b;i++)points+=x(i)+','+dy(data.drawdowns[i])+' ';add(dd,'polyline',{points,fill:'none',stroke:'var(--drawdown)','stroke-width':2});
    document.getElementById('nav-window').textContent=data.dates[a]+' → '+data.dates[b]+' · '+(b-a+1)+' 次观测';
    selected=Math.max(a,Math.min(b,selected));readout(selected,x,curves,y);
    const brush=document.getElementById('nav-brush-svg');brush.replaceChildren();const bx=i=>left+(right-left)*i/Math.max(1,data.dates.length-1);add(brush,'rect',{x:left,y:5,width:right-left,height:45,fill:'var(--info-bg)'});add(brush,'rect',{x:bx(a),y:5,width:Math.max(3,bx(b)-bx(a)),height:45,fill:'var(--line)',stroke:'var(--series-1)'});
  }
  function readout(i,x,curves,y){svg.querySelector('.crosshair')?.remove();add(svg,'line',{class:'crosshair',x1:x(i),x2:x(i),y1:30,y2:285,stroke:'var(--muted)','stroke-dasharray':'3 3'});document.getElementById('nav-readout').textContent=data.dates[i]+' · 策略 '+fmt(curves[0][i])+(curves[1]?' · 基准 '+fmt(curves[1][i]):' · 无基准')+' · 回撤 '+fmt(Number(data.drawdowns[i]))+'%';}
  svg.addEventListener('pointermove',event=>{const [a,b]=bounds(),rect=svg.getBoundingClientRect(),pos=(event.clientX-rect.left)*width/rect.width;selected=Math.max(a,Math.min(b,Math.round(a+(pos-left)*(b-a)/(right-left))));draw();});
  svg.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();selected+=event.key==='ArrowLeft'?-1:1;draw();});
  const brush=document.getElementById('nav-brush-svg');let anchor=null;function brushIndex(event){const rect=brush.getBoundingClientRect();return Math.max(0,Math.min(data.dates.length-1,Math.round(((event.clientX-rect.left)*width/rect.width-left)/(right-left)*(data.dates.length-1))));}
  brush.addEventListener('pointerdown',event=>{anchor=brushIndex(event);brush.setPointerCapture(event.pointerId);});
  brush.addEventListener('pointerup',event=>{if(anchor===null)return;const finish=brushIndex(event);start.value=Math.min(anchor,finish);end.value=Math.max(anchor,finish);anchor=null;draw();});
  [start,end,unit,log].filter(Boolean).forEach(input=>input.addEventListener('input',draw));
  document.getElementById('nav-explorer').hidden=false;draw();document.getElementById('nav-static').hidden=true;
  window.addEventListener('resize',draw);
})();
