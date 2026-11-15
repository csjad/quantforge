# -*- coding: utf-8 -*-
"""qf.console_page — Web 控制台页面（W12 taste 重做版）。

按 design-taste-frontend 门禁重做：方向 type-only-statement（seed 1221479467），
想法=三守恒信任主张成为首屏双色陈述；live thing=资金滑杆→巨号手数（真实含费公式）；
黑+深红取自行情终端与A股红涨；系统字体栈（离线禁 CDN，D-008/S46）；
lint/inspect 0 FAIL。占位符 __OPT_S__/__OPT_Y__ 由 webconsole 注入。
"""

PAGE_HTML = r"""
<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QuantForge 控制台</title><style>
:root{
  --bg:#0a0a0a; --surface:#151515; --ink:#efefef; --muted:#8c8c8c;
  --accent:#b11226; --accent-ink:#ffffff; --down:#16a34a;
  --field:#efefef; --field-ink:#0a0a0a;
  --step-small:13px; --step-body:17px; --step-lead:24px;
  --step-head:clamp(28px,3.2vw,48px); --step-display:clamp(64px,10vw,150px);
  --step-giant:clamp(48px,8vw,120px);
  --m:3vw;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",system-ui,sans-serif;
  font-size:var(--step-body);line-height:1.55;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
button{font:inherit;cursor:pointer;border:0;border-radius:0;background:var(--accent);
  color:var(--accent-ink);min-height:44px;padding:12px 26px;font-size:var(--step-body);
  transition:transform .12s ease,background .2s ease}
button:hover{transform:translateY(-2px)}
button:active{transform:translateY(0)}
button.line{background:#1f1f1f;color:var(--ink);padding:12px 18px}
button.line:hover{background:#2a2a2a}
select,input[type=text],textarea{font:inherit;color:var(--ink);background:transparent;
  border:0;border-bottom:1.5px solid var(--muted);padding:8px 2px;outline:0;min-width:170px}
select:focus,input:focus,textarea:focus{border-bottom-color:var(--accent)}
select option{background:var(--bg);color:var(--ink)}
input[type=range]{-webkit-appearance:none;appearance:none;width:min(100%,560px);height:2px;
  background:var(--muted);outline:0}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;
  width:26px;height:26px;background:var(--accent);border-radius:0;cursor:grab}
.num{font-variant-numeric:tabular-nums}
.up{color:var(--accent)} .dn{color:var(--down)}
section{padding:clamp(48px,6vh,84px) var(--m)}
.chapter{position:relative}
.chapter.surface{background:var(--surface)}
.chapter.field{background:var(--accent);color:#ffffff}
.chapter.invert{background:var(--field);color:var(--field-ink)}
.reveal{opacity:0;transform:translateY(26px);transition:opacity .7s ease,transform .7s ease}
.reveal.on{opacity:1;transform:none}
.reveal.d1{transition-delay:.08s}.reveal.d2{transition-delay:.16s}
.reveal.d3{transition-delay:.24s}
@media (prefers-reduced-motion:reduce){
  .reveal{opacity:1;transform:none;transition:none}
  button,button:hover{transition:none;transform:none}
  html{scroll-behavior:auto}
}

/* ── HERO：色场双色陈述 ── */
.hero{min-height:62svh;padding-top:clamp(20px,3vh,34px);display:flex;
  flex-direction:column;justify-content:space-between}
.hero .corners{display:flex;justify-content:flex-end;align-items:flex-start;
  font-size:15px}
.hero .mark{font-weight:800}
.hero .say{margin:0;font-weight:800;letter-spacing:-.03em;line-height:.88;
  font-size:var(--step-display)}
.hero .say .l1{display:block;color:var(--ink)}
.hero .say .l2{display:block;color:#9a9a9a}
.hero .foot{display:flex;justify-content:space-between;align-items:flex-end;gap:24px}
.hero .support{max-width:32ch;font-size:var(--step-body);color:var(--ink);margin:0}
.hero .support b{color:var(--ink);font-weight:500}

/* ── STATS：滑杆定巨号 ── */
.ask{font-size:var(--step-head);font-weight:800;letter-spacing:-.01em;margin:0 0 34px}
.ctrl{max-width:680px}
.ctrl .row{display:flex;gap:14px;align-items:center;margin-bottom:18px;flex-wrap:wrap}
.ctrl .val{font-size:var(--step-lead);font-weight:500}
.result{display:flex;justify-content:space-between;align-items:flex-end;
  gap:30px;margin-top:clamp(36px,5vh,64px)}
.figure{font-size:clamp(90px,12vw,170px);font-weight:800;line-height:.8;
  letter-spacing:-.04em;font-variant-numeric:tabular-nums}
.figure .unit{font-size:.22em;font-weight:500;letter-spacing:0;margin-left:.1em}
.formula{max-width:36ch;font-size:var(--step-body);color:var(--ink);margin:0;text-align:right}
.formula b{color:var(--ink);font-weight:500}

/* ── LIST：一种字号的阵容 ── */
.lineup{width:100%;border-collapse:collapse;font-size:clamp(16px,1.5vw,21px)}
.lineup td{padding:.42em 0;vertical-align:baseline;font-weight:400}
.lineup tr{cursor:pointer}
.lineup tr:hover td{color:var(--accent)}
.lineup .t{color:var(--muted);font-variant-numeric:tabular-nums}
.lineup .r{text-align:right;font-variant-numeric:tabular-nums}
.lineup .sel td{color:var(--ink);font-weight:800}
.lineup .sel .t{color:var(--accent);font-weight:800}
.howto{font-size:var(--step-body);color:var(--muted);margin:22px 0 0}

/* ── SHOWCASE：放大的界面碎片 ── */
.panel h3{font-size:var(--step-head);font-weight:800;margin:0 0 8px;letter-spacing:-.01em}
.panel .big{font-size:clamp(48px,6vw,88px);font-weight:800;line-height:.9;
  letter-spacing:-.03em;font-variant-numeric:tabular-nums;margin:18px 0 6px}
.panel .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
  gap:8px 30px;margin:34px 0 0;font-variant-numeric:tabular-nums}
.panel .grid .k{font-size:15px;color:var(--muted)}
.panel .grid .v{font-size:var(--step-lead);font-weight:500}
.panel svg{width:100%;height:auto;display:block;margin-top:10px}
.panel .cap{font-size:15px;color:var(--muted);margin:6px 0 0}
.mini{display:grid;grid-template-columns:1fr 1fr;gap:clamp(24px,4vw,56px);
  margin-top:clamp(30px,4vh,56px)}
.mini h4{font-size:var(--step-lead);font-weight:500;margin:0 0 16px;color:var(--muted)}
.ytable{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
.ytable td{padding:.5em 0;font-size:var(--step-lead)}
.ytable td:last-child{text-align:right;font-weight:500}
.heat{display:grid;grid-template-columns:repeat(12,1fr);gap:4px}
.heat .cell{aspect-ratio:1;display:flex;align-items:center;justify-content:center;
  font-size:11px;font-variant-numeric:tabular-nums;color:#fff}
.heat .yr{grid-column:1/-1;font-size:14px;color:var(--muted);margin-top:8px}

/* ── PROOF：名字连成一句 ── */
.names{font-size:clamp(19px,1.8vw,26px);line-height:1.55;letter-spacing:0;
  font-weight:500;margin:0;max-width:80ch}
.names .lead{color:#ffffff;font-weight:800}
.names .n{color:rgba(255,255,255,.55);transition:color .4s ease}
.names .n.lit{color:#ffffff;font-weight:800}

/* ── SWEEP：巨号+表 ── */
.best{display:flex;align-items:flex-end;gap:22px;margin:0 0 clamp(28px,4vh,48px)}
.best .lab{font-size:var(--step-head);font-weight:800;letter-spacing:-.01em}
.best .fig{font-size:clamp(48px,6vw,88px);font-weight:800;line-height:.85;
  letter-spacing:-.04em;font-variant-numeric:tabular-nums;color:var(--accent)}
.swtable{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
.swtable th{text-align:left;font-size:var(--step-small);font-weight:500;color:var(--muted);
  padding:10px 14px 10px 0;border-bottom:1.5px solid var(--muted)}
.swtable td{padding:12px 14px 12px 0;font-size:17px}
.swtable .flag{color:var(--accent);font-size:14px}
.swtable tr:hover td{background:var(--surface)}
.note{font-size:var(--step-body);color:var(--muted);margin:20px 0 0}
.cmp{margin-top:clamp(60px,8vh,100px)}
.cmp .pick{display:flex;gap:20px;align-items:center;flex-wrap:wrap;margin-bottom:26px}

/* ── STAGES：一条进度杆 ── */
.stages{display:flex;gap:6px;height:16vh;min-height:120px;margin:clamp(28px,4vh,48px) 0 0}
.stages .seg{position:relative;padding:20px;min-width:12%;color:#fff}
.stages .seg .dur{font-size:var(--step-lead);font-weight:500}
.stages .seg .cl{position:absolute;left:0;bottom:-58px;font-size:15px;color:var(--ink);
  width:22ch}
.stages .s1{flex-grow:1;background:var(--accent)}
.stages .s2{flex-grow:3;background:#3a3a3a}
.stages .s3{flex-grow:4;background:#5a5a5a}
.verify{margin-top:clamp(70px,9vh,110px);max-width:640px}
.verify textarea{width:100%;font-family:Consolas,monospace;font-size:14px; /* taste-skill-ignore: 策略代码编辑器 */
  border:1.5px solid var(--muted);background:var(--bg);padding:14px;resize:vertical}
.verify .row{display:flex;gap:18px;align-items:center;margin:18px 0}
.vout{font-family:Consolas,monospace;font-size:14px;color:var(--muted); /* taste-skill-ignore: 校验输出=字面代码 */
  white-space:pre-wrap;margin:18px 0 0}
.vout .ok{color:var(--down)} .vout .bad{color:var(--accent)}

/* ── POOL：右半窄栏陈述 ── */
.pool .stmt{font-size:var(--step-head);font-weight:800;letter-spacing:-.01em;
  line-height:1.15;margin:0 0 40px}
.pool .right{max-width:560px;margin-left:auto}
.pool textarea{width:100%;font-size:var(--step-body);border:1.5px solid var(--muted);
  background:transparent;padding:12px}
.pool .acts{display:flex;gap:14px;flex-wrap:wrap;margin-top:22px}
.pool .fresh{margin-top:26px;font-size:var(--step-body);color:var(--muted)}
.pool .fresh b{color:var(--ink);font-weight:500}

/* ── LOG：终端带 ── */
.term{background:#020617;color:#94a3b8;font-family:Consolas,monospace;font-size:14px; /* taste-skill-ignore: 真实终端输出 */
  line-height:1.6;padding:22px;min-height:220px;max-height:340px;overflow-y:auto;
  white-space:pre-wrap}

/* ── FOOTER ── */
.end{min-height:55svh;display:flex;flex-direction:column;justify-content:space-between;
  overflow:hidden}
.end .top{display:flex;justify-content:space-between;gap:30px;flex-wrap:wrap}
.end .inv{font-size:clamp(26px,3vw,44px);font-weight:800;letter-spacing:-.02em;
  line-height:1.02;margin:0;max-width:12ch}
.end .links{display:flex;flex-direction:column;gap:14px;font-size:var(--step-body)}
.end .links a:hover{color:var(--accent)}
.end .word{font-size:16vw;font-weight:800;letter-spacing:-.04em;line-height:.8;
  white-space:nowrap;transform:translateY(22%);color:#0a0a0a}
.end .legal{display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap;
  font-size:var(--step-small);color:#5c5c5c;padding-bottom:6px}

@media (max-width:640px){
  :root{--m:18px}
  .lineup{font-size:17px}
  .lineup td{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:34vw}
  .names{font-size:var(--step-body);line-height:1.7}
  .hero .support{max-width:80%}
  .result{flex-direction:column;align-items:flex-start}
  .formula{text-align:left;max-width:100%}
  .mini{grid-template-columns:1fr}
  .stages{flex-direction:column;height:auto}
  .stages .seg{min-height:72px}
  .stages .seg .cl{position:static;display:block;margin-top:10px;width:auto}
  .figure{font-size:30vw}
}
</style></head><body>

<!-- HERO -->
<section class="hero chapter" id="top">
  <div class="corners">
    <button onclick="run('demo')">跑一次回测</button>
  </div>
  <h1 class="say"><span class="l1">钱没有</span><span class="l2">凭空出现</span></h1>
  <div class="foot">
    <p class="support">本地A股量化回测控制台 · 合成样例与真实行情双通道</p>
    <p class="support"><b>每一条权益曲线都由逐笔校验的三条守恒式撑着</b>——初始资金 + 已实现 + 未实现 = 期末权益，错了当场报。</p>
  </div>
</section>

<!-- STATS：买得起吗 -->
<section class="chapter surface" id="afford">
  <h2 class="ask reveal">这一手，买得起吗？</h2>
  <div class="ctrl reveal d1" data-ui>
    <div class="row">
      <select id="afsym" onchange="calcAfford()">
        <option value="600519">贵州茅台 600519</option>
        <option value="000001">平安银行 000001</option>
        <option value="600028">中国石化 600028</option>
        <option value="600006">样例沪主板E 600006</option>
      </select>
      <select id="st" onchange="calcAfford()">__OPT_S__</select>
      <span class="val num" id="afpx">—</span>
    </div>
    <label for="afcash" style="display:block;color:var(--muted);font-size:15px;margin-bottom:10px">初始资金 <span class="num" id="afcashlab">100,000 元</span></label>
    <input type="range" id="afcash" min="1000" max="1000000" step="1000" value="100000" oninput="calcAfford()">
    <div class="row" style="margin:26px 0 0">
      <button onclick="doBacktest()">用这组配置回测</button>
      <label style="color:var(--muted);font-size:15px;margin:0"><input type="checkbox" id="src" style="min-width:0"> 用真实行情池</label>
    </div>
  </div>
  <div class="result">
    <div class="figure num reveal d2" id="aflots">0<span class="unit">手</span></div>
    <p class="formula reveal d3">一手含费 = <b>价×100×(1+1‰滑点)</b> + 佣金(万2.5，最低5元) + 过户费(沪市0.01‰)。手数向下取整到整手——资金不足一手，回测里直接拒单并记录原因。</p>
  </div>
</section>

<!-- LIST：你的回测 -->
<section class="chapter" id="runs">
  <h2 class="ask reveal">你的回测</h2>
  <div style="max-height:360px;overflow-y:auto"><table class="lineup reveal d1" data-ui id="runtable"><tbody><tr><td class="t">尚无记录</td><td>点「跑一次回测」，这里就有阵容</td><td></td><td></td></tr></tbody></table></div>
  <p class="howto reveal d2">点一行，整页跟着这条回测走。</p>
</section>

<!-- SHOWCASE：放大的界面 -->
<section class="chapter surface" id="showcase">
  <div class="panel">
    <h3 class="reveal" id="sc-title">—</h3>
    <div class="big num" id="sc-ret">—</div>
    <p class="cap" id="sc-empty">点上面任意一行回测，曲线、年度与热力落在这里。</p>
    <p class="cap" id="sc-cap"></p>
    <div id="sc-chart" class="reveal d1" data-ui></div>
    <div class="grid reveal d2" id="sc-kpi" data-ui></div>
    <div class="mini">
      <div><h4>年度收益</h4><table class="ytable" id="sc-yearly"><tbody></tbody></table></div>
      <div><h4>月度热力</h4><div class="heat" id="sc-heat"></div></div>
    </div>
  </div>
</section>

<!-- PROOF：名字连成一句 -->
<section class="chapter field" id="proof">
  <p class="names"><span class="lead">凭什么信这些数字：</span><span class="n">三守恒</span>，<span class="n">九步撮合时序</span>，<span class="n">断点续跑</span>，<span class="n">校验和双确认</span>，<span class="n">末日陷阱</span>，<span class="n">双后端逐位一致</span>，<span class="n">一百二十九项测试</span>，<span class="n">质量门禁五连绿</span>，<span class="n">沙箱三步校验</span>，<span class="n">前复权连续</span>，<span class="n">数据快照号</span>，<span class="n">不剔除原则</span>。</p>
</section>

<!-- SWEEP：寻优与对比 -->
<section class="chapter" id="sweep">
  <div class="best">
    <span class="lab reveal">最好一组</span>
    <span class="fig num reveal d1" id="sw-best">—</span>
  </div>
  <table class="swtable reveal d2" data-ui id="swtable"></table>
  <div class="cmp reveal d3">
    <div class="pick">
      <span class="lab" style="font-size:var(--step-head);font-weight:800">对比两条</span>
      <select id="cmpA"></select><select id="cmpB"></select>
      <button class="line" onclick="doCompare()">并排看十二项</button>
    </div>
    <table class="swtable" id="cmptab" data-ui></table>
    <p class="note" id="sw-note"></p>
  </div>
</section>

<!-- STAGES：先过关再上线 -->
<section class="chapter surface" id="sandbox">
  <h2 class="ask reveal">先过关，再上线</h2>
  <div class="stages reveal d1" data-ui>
    <div class="seg s1"><span class="dur">毫秒级</span><span class="cl">第一道：导入白名单、钩子契约、正偏移索引扫描</span></div>
    <div class="seg s2"><span class="dur">约十五秒</span><span class="cl">第二道：独立子进程试跑，运行期挂钩拦文件越权与联网</span></div>
    <div class="seg s3"><span class="dur">约二十秒</span><span class="cl">第三道：小样本回测，三条守恒逐笔校验通过才算合格</span></div>
  </div>
  <div class="verify reveal d2" data-ui>
    <div class="row">
      <input type="text" id="sgname" placeholder="策略名，如 my_alpha" style="flex:1">
    </div>
    <textarea id="sgcode" rows="6" placeholder="class Strategy:   # 必须定义 Strategy 类，含 attach / on_bar 钩子"></textarea>
    <div class="row"><button onclick="doVerify()">校验并接入</button>
      <input type="file" id="sgfile" accept=".py" style="border:0;color:var(--muted)"></div>
    <pre class="vout" id="vreport"></pre>
  </div>
</section>

<!-- POOL：行情在云端，数据在本地 -->
<section class="chapter pool" id="pool">
  <div class="right">
    <p class="stmt reveal">行情在云端，<br>数据在本地。</p>
    <div class="reveal d1" data-ui>
      <textarea id="poolbox" rows="2" placeholder="池内代码，逗号分隔，6位数字，如 600519,000001"></textarea>
      <div class="acts">
        <button onclick="savePool()">保存自选池</button>
        <button class="line" onclick="run('fetch-pool')">拉取整池行情</button>
        <button class="line" onclick="refreshData()">更新行情</button>
      </div>
      <p class="fresh">真实行情池最近文件：<b id="fresh">—</b> · 东财限流自动切新浪 · 前复权。真实数据版权归原数据商，<b>仅限个人研究，禁止二次分发</b>。</p>
    </div>
  </div>
</section>

<!-- LOG -->
<section class="chapter surface" id="logsec">
  <h2 class="ask reveal">运行输出</h2>
  <div class="term reveal d1" id="log" data-ui>就绪。</div>
  <p class="howto"><span id="stat">空闲</span> · <button class="line" onclick="openReport()">打开最新报告</button> <button class="line" onclick="openReview()">打开最新复盘</button></p>
</section>

<!-- FOOTER -->
<section class="chapter invert end" id="end">
  <div class="top">
    <div>
      <h2 class="inv">把引擎带回你的机器</h2>
      <p style="max-width:40ch;color:#5c5c5c;margin:18px 0 26px">离线五件事，解压即跑；不接自动下单、不承诺收益、不隐藏配置。</p>
      <button onclick="run('demo')">跑一次回测</button>
    </div>
    <nav class="links">
      <a href="#afford">买得起吗</a>
      <a href="#runs">你的回测</a>
      <a href="#sweep">寻优与对比</a>
      <a href="#sandbox">策略沙箱</a>
      <a href="#pool">真实行情</a>
    </nav>
  </div>
  <div>
    <div class="word">QUANTFORGE</div>
    <div class="legal"><span>© QuantForge · 本地单机部署</span><span>历史回测不代表未来表现 · 不构成投资建议</span></div>
  </div>
</section>

<script>
const $=id=>document.getElementById(id);
let cur=null,runs=[],POOL=[],prices={};
async function api(p,o){const r=await fetch(p,o);return r.json()}
async function doBacktest(){await api('/api/run',{method:'POST',body:JSON.stringify(
 {action:'backtest',strategy:($('st').value||'ma_cross').split('（')[0],
 symbol:$('afsym').value,cash:+$('afcash').value,real:$('src').checked})});
 setTimeout(refresh,600)}
async function refresh(){const s=await api('/api/state');
 $('log').textContent=s.log.join('')||'就绪。';
 $('stat').textContent=s.running?'运行中…':'空闲';
 document.querySelectorAll('button').forEach(b=>b.disabled=!!s.running);
 if(s.pool)POOL=s.pool,$('poolbox').value=POOL.join(',');
 renderRuns(s.runs||[]);
 if(!cur&&s.latest)selectRun(s.latest.run_id,s.latest);
 loadSweep();
}
function renderRuns(rs){runs=rs.filter(r=>!r.error);
 const tb=$('runtable').querySelector('tbody');tb.innerHTML='';
 for(const r of runs){const m=r.metrics||{};
  const tr=document.createElement('tr');tr.className=r.run_id===cur?'sel':'';
  tr.onclick=()=>selectRun(r.run_id);
  const ret=m['累计收益'],cls=ret==null?'':(ret>=0?'up':'dn');
  tr.innerHTML=`<td class="t num">${(r.created_at||'').slice(0,16)}</td>
   <td>${r.strategy}</td>
   <td class="num ${cls}">${ret!=null?(ret*100).toFixed(2)+'%':'—'}</td>
   <td class="r num">${m['成交笔数']??'—'}</td>`;
  tb.appendChild(tr);}
 const c=$('cmpA'),c2=$('cmpB');c.innerHTML='';c2.innerHTML='';
 for(const r of runs){for(const box of [c,c2]){const o=document.createElement('option');
  o.value=r.run_id;o.textContent=r.created_at.slice(5)+' '+r.strategy;box.appendChild(o);}}
 if(c2.options.length>1)c2.selectedIndex=1;}
async function selectRun(id,preset){cur=id;renderRuns(runs);
 const d=preset||await api('/api/run/'+id);if(d.error)return;
 $('sc-title').textContent=d.strategy+' · '+d.run_id;
 const m=d.metrics||{};
 const k1=$('sc-ret');k1.textContent=m['累计收益']!=null?(m['累计收益']*100).toFixed(2)+'%':'—';
 k1.className='big num '+(m['累计收益']>=0?'up':'dn');
 $('sc-cap').textContent='逐笔校验：守恒失败 '+(m['守恒失败']??0)+' 次';
 $('sc-chart').innerHTML=d.charts||'';const em=$('sc-empty');if(em)em.style.display='none';
 const kp=[['夏普 · √250',m['夏普']],['最大回撤 · 峰值分母',
   m['最大回撤']!=null?(m['最大回撤']*100).toFixed(1)+'%':null],
  ['成交笔数',m['成交笔数']],['胜率',m['胜率']],['盈亏比',m['盈亏比']],
  ['卡玛',m['卡玛']],['年化收益',m['年化收益']!=null?(m['年化收益']*100).toFixed(1)+'%':null],
  ['年化波动',m['年化波动']!=null?(m['年化波动']*100).toFixed(1)+'%':null]];
 $('sc-kpi').innerHTML=kp.map(([k,v])=>`<div><div class="k">${k}</div>
  <div class="v num">${v==null?'—':(typeof v==='number'?v.toFixed(3):v)}</div></div>`).join('');
 const yt=$('sc-yearly').querySelector('tbody');
 yt.innerHTML=(d.yearly||[]).map(y=>`<tr><td>${y.year}</td>
  <td class="${y.ret>=0?'up':'dn'}">${(y.ret*100).toFixed(2)}%</td></tr>`).join('');
 const mm=d.monthly||[];const by={};mm.forEach(x=>by[x.month]=x.ret);
 const years=[...new Set(mm.map(x=>x.month.slice(0,4)))];
 $('sc-heat').innerHTML=years.map(y=>`<div class="yr">${y}</div>`+
  Array.from({length:12},(_,i)=>{const v=by[y+'-'+String(i+1).padStart(2,'0')];
   if(v==null)return '<div class="cell" style="background:#222;color:#666">·</div>';
   const a=Math.min(.9,Math.abs(v)*4+.1);
   const bg=v>=0?`rgba(177,18,38,${a})`:`rgba(22,163,74,${a})`;
   return `<div class="cell" style="background:${bg}" title="${y}-${i+1} ${(v*100).toFixed(1)}%">${(v*100).toFixed(0)}</div>`;
  }).join('')).join('');
}
async function loadSweep(){const s=await api('/api/sweep');
 if(!s.total){$('swtable').innerHTML='';$('sw-note').textContent='还没有寻优记录，点「开始寻优」跑一批。';
  $('sw-best').textContent='—';return}
 const top=s.rows[0]||{};
 $('sw-best').textContent=top.score!=null?top.score.toFixed(3):'—';
 let h='<tr><th>参数</th><th>验证分</th><th>验证年化</th><th>验证回撤</th><th>笔数</th><th>标记</th></tr>';
 (s.rows||[]).slice(0,12).forEach(r=>{const p=Object.entries(r.params||{}).map(([k,v])=>k+'='+v).join(', ');
  const v=r.val||{};
  h+=`<tr><td>${p}</td><td class="num">${r.score!=null?r.score.toFixed(3):'—'}</td>
   <td class="num">${v['年化收益']!=null?(v['年化收益']*100).toFixed(1)+'%':'—'}</td>
   <td class="num">${v['最大回撤']!=null?(v['最大回撤']*100).toFixed(1)+'%':'—'}</td>
   <td class="num">${v['成交笔数']??'—'}</td>
   <td class="flag">${(r.flags||[]).join('；')||'—'}</td></tr>`});
 $('swtable').innerHTML=h;
 $('sw-note').textContent='共 '+s.total+' 组 · 带红标的排名不作结论：笔数不足、参数贴边、单年贡献、样本外失效。';}
async function doCompare(){const a=$('cmpA').value,b=$('cmpB').value;
 const d=await api(`/api/compare?a=${a}&b=${b}`);
 const ma=d.a&&d.a.metrics||{},mb=d.b&&d.b.metrics||{};
 const keys=[...new Set([...Object.keys(ma),...Object.keys(mb)])];
 let h='<tr><th>指标</th><th>A</th><th>B</th><th>差</th></tr>';
 keys.forEach(k=>{const x=ma[k],y=mb[k];
  const fx=x==null?'—':(typeof x==='number'?x.toFixed(4):x);
  const fy=y==null?'—':(typeof y==='number'?y.toFixed(4):y);
  const df=(typeof x==='number'&&typeof y==='number')?(x-y).toFixed(4):'—';
  h+=`<tr><td>${k}</td><td class="num">${fx}</td><td class="num">${fy}</td><td class="num">${df}</td></tr>`});
 $('cmptab').innerHTML=h;}
async function savePool(){const syms=$('poolbox').value.split(/[,,\s]+/).filter(x=>x);
 const j=await api('/api/pool',{method:'POST',body:JSON.stringify({symbols:syms})});
 toast(j.msg);}
async function refreshData(){run('refresh-data')}
async function run(action){await api('/api/run',{method:'POST',
 body:JSON.stringify({action})});setTimeout(refresh,600)}
async function doVerify(){const name=$('sgname').value.trim(),code=$('sgcode').value;
 if(!name||!code){toast('策略名与内容都要填');return}
 $('vreport').textContent='沙箱校验中，约10到30秒';
 const j=await api('/api/verify',{method:'POST',body:JSON.stringify({name,content:code})});
 $('vreport').innerHTML=(j.report||j.msg||'');
 toast(j.ok?'策略已接入':'校验未通过');refresh();}
$('sgfile').addEventListener('change',function(e){const f=e.target.files[0];if(!f)return;
 const rd=new FileReader();rd.onload=()=>{$('sgcode').value=rd.result;
 $('sgname').value=f.name.replace(/\.py$/,'')};rd.readAsText(f,'utf-8')});
async function openReport(){await api('/api/open-report')}
async function openReview(){await api('/api/open-review')}
function toast(msg){const t=$('stat');const old=t.textContent;
 t.textContent=msg;setTimeout(()=>{t.textContent=old;refresh()},2600)}

/* 买得起吗：真实含费公式 */
async function loadPrices(){for(const s of ['600519','000001','600006']){
  try{const d=await api('/api/price/'+s);prices[s]=d.close}catch(e){}}}
function calcAfford(){const s=$('afsym').value,cash=+$('afcash').value;
 $('afcashlab').textContent=cash.toLocaleString('zh-CN')+' 元';
 const px=prices[s]||0;$('afpx').textContent=px?('现价 '+px.toFixed(2)+' 元'):'—';
 const per=px*100*1.001,lot=per+Math.max(per*0.00025,5)+(s[0]==='6'?Math.round(per*0.00001*100)/100:0);
 const lots=lot>0?Math.floor(cash/lot):0;
 $('aflots').innerHTML=lots.toLocaleString('zh-CN')+'<span class="unit">手</span>';}
$('afsym').addEventListener('change',calcAfford);
$('afcash').addEventListener('input',calcAfford);

/* 名字逐个点亮 */
let litI=-1;setInterval(()=>{const ns=document.querySelectorAll('.names .n');
 if(!ns.length)return;ns.forEach(n=>n.classList.remove('lit'));
 litI=(litI+1)%ns.length;ns[litI].classList.add('lit')},2000);

/* 入场 */
const io=new IntersectionObserver(es=>es.forEach(e=>{
 if(e.isIntersecting){e.target.classList.add('on');io.unobserve(e.target)}}),
 {threshold:.12});
document.querySelectorAll('.reveal').forEach(el=>io.observe(el));

loadPrices().then(()=>{calcAfford();refresh()});
setInterval(refresh,1200);
</script></body></html>

"""
