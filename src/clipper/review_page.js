"use strict";
const $=id=>document.getElementById(id), val=id=>$(id).value;
const NL=String.fromCharCode(10);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clock=s=>{s=Math.max(0,Math.round(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return h+':'+String(m).padStart(2,'0')+':'+String(x).padStart(2,'0')};
const HAS_PANEL=__HAS_PANEL__, PAGE_STAMP=__STAMP__;
/* Errors in plain words. The tools report faults in their own technical language; this table (kept in clipper/plain.py)
   says for each kind what happened and what to do. The original text is never dropped: it sits under "Technical details". */
const PLAIN=__PLAIN__, PLAIN_RX=PLAIN.rules.map(r=>new RegExp(r[0],'i')), TECH_RX=new RegExp(PLAIN.technical,'i');
function plainOf(msg,onlyTechnical){
  msg=String(msg||''); const i=PLAIN_RX.findIndex(rx=>rx.test(msg)), tech=TECH_RX.test(msg);
  if(!(tech||(i>=0&&!onlyTechnical)))return null;   /* a sentence the program wrote for a person is left as it is */
  const body=msg.replace(/^\s*(\d\d:\d\d:\d\d\s+)?((ERROR|WARNING)\s+[\w.]+:\s*)?((draft|finish|render) failed:\s*)?/i,''), m=/^(.{3,80}?) failed: /i.exec(body);
  return {what:i>=0?PLAIN.rules[i][1]:PLAIN.unknown[0], todo:i>=0?PLAIN.rules[i][2]:PLAIN.unknown[1], step:m?m[1].trim():''};
}
function plainHtml(msg,lead){
  const p=plainOf(msg), head=lead?'<b>'+esc(lead)+'</b> ':'';
  if(!p)return head+esc(msg);
  return head+esc(p.what)+'<br><b>What to do:</b> '+esc(p.todo)+(p.step?'<br><span class="errstep">It happened during: '+esc(p.step)+'</span>':'')
    +'<details class="errtech"><summary>Technical details (for whoever set this up)</summary><div>'+esc(msg)+'</div></details>';
}
const LOG_LEAD='A background job reported a problem. ';
const MODES={chat:['A','Chat only','Makes a clip when the chat suddenly gets very busy. Uses almost no internet.'],
  audio:['B','Chat + sound','Also catches loud moments that the chat missed. Uses about 70 MB of internet an hour for each channel.'],
  video:['C','Chat + picture','Also catches fast action on screen. Uses about 100 MB of internet an hour for each channel.'],
  all:['D','Chat + sound + picture','Uses all three. Uses about 100 MB of internet an hour for each channel.']};
let drawn=false, toastTimer=0, last={};

let errShown=[]; const errClosed=new Set();
/* The red banner can be closed. Each message is remembered once closed, so it stays closed;
   only a message that was not there before brings the banner back. */
let errHtml='';
function setErr(lines){if(typeof lines==='string')lines=lines?[lines]:[];errShown=lines;
  const open=lines.filter(l=>!errClosed.has(l));
  const html=open.map(l=>'<div class="errline">'+(l.startsWith(LOG_LEAD)?plainHtml(l.slice(LOG_LEAD.length),'A job in the background hit a problem (it is also in the Activity log, in red):'):plainHtml(l))+'</div>').join('');
  if(html!==errHtml){errHtml=html;$('errtext').innerHTML=html}   /* only when it changed: an opened "Technical details" must not snap shut every second */
  $('errbox').style.display=open.length?'block':'none';
  $('b-restart').hidden=!(open.some(l=>l.startsWith('THE PROGRAM NEEDS A RESTART'))&&last.health&&last.health.launcher)}
function closeErr(){for(const l of errShown)errClosed.add(l);$('errbox').style.display='none'}
async function restartNow(){const r=await send('/api/restart');if(r.error){toast(r.error,true);return}
  toast('Restarting… this page reconnects by itself.');setTimeout(()=>location.reload(),6000)}
function toast(msg,err){if(err&&typeof HELP!=='undefined')HELP.said=String(msg);const t=$('toast'), p=err?plainOf(msg,true):null;
  if(p)$('toasttext').innerHTML=plainHtml(msg);else $('toasttext').textContent=msg;
  t.className=err?'err':'';t.style.display='block';clearTimeout(toastTimer);
  /* an explained error stays until it is closed: there is more to read, and its technical details can be opened */
  if(!p)toastTimer=setTimeout(()=>t.style.display='none',err?9000:2500)}
let curTab='vod', tabRunning=false;
function tab(name){if(name==='live'){go('live');return}go('new');sub(name)}
function mode(){return (document.querySelector('[name=o-mode]:checked')||{}).value}
/* A finished file's line ("READY ...He FAILED the jump.mp4") and ordinary INFO lines are not errors, whatever words a clip's title holds. */
function isBad(l){return !/^(\d\d:\d\d:\d\d )?(READY|INFO) /.test(l)&&/ERROR|FAILED|NOT MADE|Traceback|Error:|fatal exception|STOPPED BY ITSELF/.test(l)}
function logTo(id,lines){const el=$(id), key=lines.join(NL), sel=window.getSelection&&getSelection();
  if(el._shown===key)return;   /* nothing new: leave it alone */
  /* lines are being selected to copy: redrawing would drop the selection. For half a minute; a selection forgotten there must not freeze the log */
  if(sel&&!sel.isCollapsed&&el.contains(sel.anchorNode)){el._held=el._held||Date.now();if(Date.now()-el._held<30000)return}
  el._held=0;
  el._shown=key;const stick=el.scrollTop+el.clientHeight>=el.scrollHeight-30;el.textContent='';
  for(const l of lines){const d=document.createElement('div');d.textContent=l||' ';if(isBad(l))d.className='lerr';else if(/WARNING/.test(l))d.className='lwarn';el.appendChild(d)}
  if(stick)el.scrollTop=el.scrollHeight}
/* A bar with a real percentage once the job reports one; the moving stripe only until then. */
function setBar(id,active,p){
  const bar=$(id+'-bar'),t=$(id+'-pct');bar.hidden=!active;t.hidden=!active;if(!active)return;
  if(!p){bar.classList.add('indet');bar.firstElementChild.style.width='';t.textContent='Starting…';return}
  bar.classList.remove('indet');bar.firstElementChild.style.width=Math.max(1,Math.min(100,p.percent))+'%';
  const el=p.at-p.started, left=p.percent>=5&&p.percent<100&&el>20?el*(100-p.percent)/p.percent:0;
  t.innerHTML=(p.steps?'':'<b>'+Math.floor(p.percent)+'%</b> · ')+(p.stages>1&&p.stage_no?'Step '+p.stage_no+' of '+p.stages+': ':'')+esc(p.stage)+(p.text?' · '+esc(p.text):'')
    +(left?' · roughly '+(left<90?'a minute':Math.round(left/60)+' minutes')+' left':'');
}
function lastLine(lines){for(let i=lines.length-1;i>=0;i--){if(lines[i].trim())return lines[i]}return ''}

function show(s){
  last=s;
  /* An older program may be answering (it was updated but not restarted). Draw what it sends without breaking. */
  s.ai=s.ai||{backend:'api',backends:{},model:'',stream_model:'',models:[],problem:'this running program is older than the page; restart it'};
  s.run_log=s.run_log||[]; s.watch_log=s.watch_log||[]; s.finish_log=s.finish_log||[];
  if(!drawn){
    $('o-modes').innerHTML=Object.keys(s.modes).map(k=>{const m=MODES[k]||['?',k,s.modes[k]];
      return '<label class="mode" data-k="'+k+'"><input type="radio" name="o-mode" value="'+k+'" onchange="pickMode()"><b>'+m[0]+'</b><span>'+m[1]+'</span><em>'+m[2]+'</em></label>'}).join('');
    $('o-channels').value=s.channels; drawn=true;
    $('o-camp').innerHTML='<option value="">No campaign</option>'+(s.campaigns||[]).map(c=>'<option value="'+esc(c.id)+'">'+esc(c.name)+(c.problem?' (file has a problem)':'')+'</option>').join('');
    $('o-thumb').innerHTML=Object.entries(s.thumbnails||{off:'Off'}).map(([k,t])=>'<option value="'+k+'">'+esc(t)+'</option>').join('');
    $('o-cap').innerHTML=Object.entries(s.caption_presets||{}).map(([k,p])=>'<option value="'+k+'">'+esc(p.label)+'</option>').join('');
    $('a-backend').innerHTML=Object.entries(s.ai.backends).map(([k,t])=>'<option value="'+k+'">'+esc(t)+'</option>').join('');
    $('a-prefer').innerHTML=Object.entries(s.ai.ways||{}).map(([k,w])=>'<option value="'+k+'">'+esc(w.name)+'</option>').join('');
    const names={'claude-haiku-5-5':'Haiku (fastest, cheapest)','claude-sonnet-5-5':'Sonnet (balanced)','claude-opus-5-5':'Opus (best judgement)','claude-fable-5-2':'Fable (most capable)'};
    const opts=s.ai.models.map(m=>'<option value="'+m+'">'+esc(names[m]||m)+'</option>').join('');
    $('a-model').innerHTML=opts; $('a-stream').innerHTML=opts;
  }
  if(!document.querySelector('#a-backend:focus,#a-model:focus,#a-stream:focus,#a-prefer:focus')){$('a-backend').value=s.ai.backend;$('a-model').value=s.ai.model;$('a-stream').value=s.ai.stream_model;$('a-prefer').value=s.ai.prefer}
  $('a-fallback').checked=!!s.ai.fallback; $('a-auto').hidden=s.ai.backend!=='auto';
  const ways=s.ai.ways||{};
  $('a-ways').innerHTML=Object.entries(ways).map(([k,w])=>{
    const st=w.missing?['#98a1b0','Not set up',w.missing]:(w.ok===true?['var(--ok)','Works','']:(w.ok===false?['var(--bad)','Does not work',w.problem]:['var(--warn)',s.ai.checking?'Checking…':'Not checked yet','']));
    return '<div style="display:flex;gap:8px;align-items:baseline;font-size:12.5px;padding:3px 0"><span class="dot" style="background:'+st[0]+';flex:none;position:relative;top:-1px"></span><span><b>'+esc(w.name)+'</b>: '+st[1]+(st[2]?'<br><span style="color:var(--muted)">'+esc(st[2])+'</span>':'')+'</span></div>'}).join('');
  const la=s.ai.local||{};
  $('a-local').innerHTML='<b>Free AI on this computer:</b> '+(la.ready?'ready ('+esc(la.model)+'). It writes clip text when Claude cannot be reached. Its text is simpler than what Claude writes. It cannot look at pictures from the clip or read whole streams.'
    :(!la.supported?'not used on this computer ('+esc(la.why||'it cannot run it')+'). Nothing is downloaded for it.'
      :'this computer can run it. Setting it up installs Ollama (a free program that runs AI) and one AI model, about 3 GB in all.'));
  $('b-localai').hidden=!!la.ready||!la.supported; $('b-localai').disabled=!!(s.health&&s.health.maint.running);
  const use=ways[s.ai.using];
  $('a-note').innerHTML=(s.ai.problem?'<b>Claude cannot be used yet:</b> '+esc(s.ai.problem)+'.'
    :'<b>Using now:</b> '+esc(use?use.name:'')+'. '+(s.ai.using==='claude-code'?'Nothing is billed per use, but it counts towards your plan\'s usage limits.':'Each use costs money and is written to the usage log.'))
    +(s.ai.note?'<br><b style="color:var(--warn)">'+esc(s.ai.note)+'</b>':'');
  $('b-aicheck').disabled=!!s.ai.checking; $('b-aicheck').textContent=s.ai.checking?'Checking…':'Check again';
  const r=document.querySelector('[name=o-mode][value='+s.mode+']'); if(r&&!r.checked&&!document.querySelector('[name=o-mode]:focus'))r.checked=true;
  for(const m of document.querySelectorAll('.mode'))m.classList.toggle('on',m.querySelector('input').checked);
  $('o-opening').checked=s.opening;
  if(document.activeElement!==$('o-thumb'))$('o-thumb').value=s.thumbnail||'off';
  $('o-face').checked=s.thumbnail_face!==false;
  if(document.activeElement!==$('o-avoid')&&!avoidDirty)$('o-avoid').value=s.thumbnail_avoid||'';
  $('thumb-sum').textContent=(s.thumbnail_face!==false?'face big and excited':'face not always big')+(s.thumbnail_avoid?' · you have a list of things to leave out':' · no list of things to leave out');
  const gpt=s.chatgpt||{text:'this running program is older than the page; restart it'};
  $('thumb-note').innerHTML=({off:'No thumbnail is made automatically. You can still make one for any clip: press Edit on it and open its <b>Thumbnail</b> tab.',
    frame:'Free, no AI: the sharpest picture from the clip with its opening text in big letters. Saved beside the video with the same name.',
    chatgpt:'ChatGPT looks at still pictures from the clip and draws the thumbnail itself; you write nothing. It takes one to three minutes per clip. The pictures, the title and the words of the clip are sent to OpenAI, and it counts towards your ChatGPT plan.'}[s.thumbnail||'off'])
    +((s.thumbnail==='chatgpt'||gpt.signed_in)?'<br><b>ChatGPT:</b> <span style="color:'+(gpt.signed_in?'var(--ok)':(gpt.checking?'var(--muted)':'var(--bad)'))+'">'+esc(gpt.text)+'</span>':'');
  drawLooks(s);drawFrame(s);
  if(!document.querySelector('#langbox select:focus')){$('l-speech').value=s.speech_language||'en';$('l-script').value=s.caption_script||'roman';$('l-text').value=s.text_language||'English';$('l-yt').value=s.youtube_captions||'auto'}
  $('lang-sum').textContent=({en:'English',ur:'Urdu',hi:'Hindi',auto:'detected automatically'})[s.speech_language||'en']+((s.speech_language||'en')!=='en'?', captions in '+(s.caption_script==='roman'?'Latin letters':'its own letters')+', text in '+(s.text_language||'English'):'');
  if(document.activeElement!==$('o-camp'))$('o-camp').value=s.campaign||'';
  const cc=(s.campaigns||[]).find(c=>c.id===s.campaign);
  $('camp-rules').textContent=cc?(cc.problem?'This campaign file cannot be read: '+cc.problem:'Rules: '+(cc.rules||'none that can be checked automatically')+'. Rule files are in the briefs folder.'):'Pick a campaign and the program checks every clip against its rules for length, hashtags and the ad label. Rule files are in the briefs folder.';
  const fp=s.finish_progress||{done:0,total:0,now:''};
  setBar('f',s.finishing,s.finish_percent); setBar('r',s.running,s.run_progress); setBar('m',s.running,s.run_progress); setBar('cb',s.running,s.run_progress);
  const hm=s.health?s.health.maint:null, hs=hm&&hm.step;
  setBar('h',!!(hm&&hm.running),hs?{percent:(hs[0]-.5)/hs[1]*100,stage:hs[2],stage_no:hs[0],stages:hs[1],text:'',at:0,started:0,steps:true}:null);
  const pw=$('p-watch'); pw.className='pill'+(s.watching?' live':''); pw.lastElementChild.textContent=s.watching?'Watching '+(s.channels||'').split(' ').filter(Boolean).join(', '):'Not watching';
  $('b-start').disabled=s.watching; $('b-stop').disabled=!s.watching; $('b-clipnow').disabled=!s.watching;
  if(document.activeElement!==$('o-auto'))$('o-auto').checked=!!s.watch_autostart;
  /* Who is live, and a hype meter of each chat against its own usual pace. */
  const HC={quiet:'var(--muted)','getting busy':'var(--accent)','very busy':'var(--warn)','at its busiest':'var(--bad)'};
  $('w-live').innerHTML=Object.entries(s.live||{}).map(([n,c])=>'<div class="live1"><b>'+esc(n)+'</b>'+(c.live
      ?'<span class="hbar"><i style="width:'+c.percent+'%;background:'+(HC[c.label]||'var(--accent)')+'"></i></span><span title="How busy this chat is right now, compared with its own usual pace in this stream. 100% is the busiest it gets. A clip is made when it suddenly jumps.">'+c.percent+'% · '+esc(c.label)+' · '+c.per_minute+' chat messages a minute'+(c.waiting?' · clip being made':'')+'</span>'
      :'<span style="color:var(--muted)">not live yet; checked every half minute</span>')+'</div>').join('');
  $('w-note').innerHTML=s.watching?'<b>Watching.</b> Drafts appear under My clips as moments happen. '+esc(lastLine(s.watch_log)):(s.watch_problem?'<b>Stopped with a problem.</b> Open the activity log.':'Not watching.');
  logTo('wlog',s.watch_log);
  $('b-run').disabled=s.running; $('b-runstop').disabled=!s.running;
  $('r-note').innerHTML=s.running?'<b>Working…</b> '+esc(lastLine(s.run_log)):(s.run_problem?'<b>It stopped with a problem.</b> Open the activity log.':(s.run_log.length?(/ stopped$/.test(lastLine(s.run_log))?'<b>Stopped by you.</b>':'<b>Done.</b> '+esc(lastLine(s.run_log))):'Nothing running.'));
  logTo('rlog',s.run_log);
  const ready=s.finish_log.filter(l=>l.startsWith('READY ')), notMade=s.finish_log.filter(l=>l.startsWith('NOT MADE ')||l.startsWith('FAILED'));
  $('f-note').innerHTML=s.finishing?'<b>Working…</b> '+(fp.total?'clip '+Math.min(fp.total,fp.done+1)+' of '+fp.total+(fp.now?': '+esc(fp.now):''):'starting')+'. The bar above shows how far it is.'
      +(s.finish_stopping==='after'?' <b>Stopping after this video.</b>':(s.finish_stopping==='now'?' <b>Stopping now…</b>':''))
    :(s.finish_log.length?(s.finish_stopped?'<b>Stopped by you.</b> ':'')+(ready.length?'<b>'+ready.length+' video file'+(ready.length>1?'s':'')+' made.</b> ':(s.finish_stopped?'No video was finished before the stop. ':(notMade.length?'':'<b>Nothing to make:</b> no approved clip is waiting for its videos. ')))+(notMade.length?notMade.length+' could not be made. ':'')+(ready.length?'They are in the <b>ready</b> folder inside your data folder; press Open the videos folder.':'')+(s.finish_stopped?(s.finish_left?' '+s.finish_left+' clip'+(s.finish_left>1?'s were':' was')+' not made and '+(s.finish_left>1?'are':'is')+' still under Approved.':' Nothing else was waiting.'):''):'Nothing made yet since the program was started.');
  $('f-ready').innerHTML=ready.map(l=>'<li>'+esc(l.slice(6).split(/[\\/]/).pop())+'</li>').join('')+notMade.map(l=>'<li class="nm">'+esc(l)+'</li>').join('');
  /* How far the making of the videos is, at the top of the page too: it is seen from wherever you are. */
  const pm=$('p-make'), fq=s.finish_percent; pm.hidden=!s.finishing;
  if(s.finishing){pm.firstElementChild.firstElementChild.style.width=(fq?Math.max(2,Math.min(100,fq.percent)):2)+'%';
    pm.lastElementChild.textContent='Making videos '+(fq?Math.floor(fq.percent)+'%':'…')+(fp.total?' · clip '+Math.min(fp.total,fp.done+1)+' of '+fp.total:'')+(s.finish_stopping?' · stopping':'')}
  $('b-finish').disabled=s.finishing; $('b-fstop').disabled=!s.finishing||s.finish_stopping==='now';
  if(!$('stopbox').hidden){if(!s.finishing)stopAsk(false);else stopText(s)}
  logTo('flog',s.finish_log);
  if(s.health)drawHealth(s.health);
  suDraw(s);
  /* The newest red line of EACH log: one log's old error must not hide another log's new one. */
  const lastBad=a=>{a=(a||[]).filter(isBad);return a.length?[a[a.length-1]]:[]};
  const bad=[...new Set([...lastBad(s.watch_log),...lastBad(s.finish_log),...lastBad(s.run_log),...lastBad(s.health&&s.health.maint.lines)])], probs=[s.watch_problem,s.run_problem].filter(Boolean);
  if(s.stamp&&s.stamp!==PAGE_STAMP)probs.unshift('THE PROGRAM NEEDS A RESTART. Its files were updated, but the copy that is running is still the old one, so some buttons will not work. '
    +(s.health&&s.health.launcher?'Press Restart now.':'Close its window (or press Ctrl+C in it), then double-click Stream-Clipper.cmd to start it again.'));
  /* One banner line per distinct error, without counts, so closing it once keeps it closed. */
  setErr(probs.concat(bad.map(l=>LOG_LEAD+l)));
  if(s.running&&!tabRunning&&curTab==='live')tab('vod');
  tabRunning=s.running;
  $('b-own').disabled=s.running; $('b-ownstop').disabled=!s.running;
  $('m-note').innerHTML=s.running?'<b>Working…</b> '+esc(lastLine(s.run_log)):(s.run_problem?'<b>It stopped with a problem.</b> Open the activity log.':(s.run_log.length?(/ stopped$/.test(lastLine(s.run_log))?'<b>Stopped by you.</b>':'<b>Done.</b> '+esc(lastLine(s.run_log))):'Nothing running.'));
  logTo('mlog',s.run_log);
  $('b-board').disabled=s.running; $('cb-stop').disabled=!s.running; $('cb-note').innerHTML=$('m-note').innerHTML;
  if(cbRunning!==s.running){cbRunning=s.running;if(CB)cbRows();if(TW)twDraw()}
  /* A job started from this page has ended: go where its result is. */
  if(cbWasRunning&&!s.running&&cbJob){const job=cbJob;cbJob='';
    const here=curPage==='new'&&!openKey;   /* still where the job was started: go on to the next step. Anywhere else: only say so */
    if(!s.run_problem&&job==='range'){CB=null;if(here){view('board');toast('Downloaded. Mark your cuts on the timeline.')}else toast('Downloaded. Open New clips, Cutting board, to mark your cuts.')}
    if(!s.run_problem&&job==='pieces'){if(here){view('clips');setFilter('candidate');toast('Your clips are ready to review.')}else{loadClips();toast('Your clips are ready: My clips, To review.')}}
    if(s.run_problem)toast('It stopped with a problem. The Activity log (bottom of the menu) says why.',true)}
  cbWasRunning=s.running;
}
async function act(url,body){
  try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok){toast(await r.text(),true);return false} show(await r.json());if(curPage==='look')lookSoon();return true}
  catch(e){toast('The program is not answering: '+e,true);return false}
}
/* ---------- Look and branding: the preview phone ---------- */
const LK={n:0,busy:false,again:false,which:'open',timer:0,v:''};
function lookSoon(){clearTimeout(LK.timer);LK.timer=setTimeout(lookDraw,900)}
function lookShow(w){LK.which=w;for(const b of $('lk-tabs').children)b.classList.toggle('on',b.dataset.w===w);
  if(LK.v&&!LK.busy){$('lk-img').src='/api/look/preview/'+w+'.jpg?v='+LK.v;$('lk-img').hidden=false;$('lk-empty').textContent=''}}
function lookOther(){LK.n++;lookDraw()}
async function lookDraw(){
  if(curPage!=='look')return;
  if(LK.busy){LK.again=true;return}
  LK.busy=true;$('lk-phone').classList.add('wait');$('lk-empty').textContent='Making the preview… a few seconds';$('lk-other').disabled=true;
  let r;try{const q=await fetch('/api/look/preview?n='+LK.n);r=q.ok?await q.json():{error:await q.text()}}catch(e){r={error:'The program is not answering: '+e}}
  LK.busy=false;$('lk-phone').classList.remove('wait');$('lk-other').disabled=false;
  if(r.error){LK.v='';$('lk-img').hidden=true;if(plainOf(r.error,true))$('lk-empty').innerHTML=plainHtml(r.error);else $('lk-empty').textContent=r.error;$('lk-note').textContent=''}
  else{LK.v=r.v;lookShow(LK.which);$('lk-other').hidden=r.of<2;
    $('lk-note').textContent='Your clip: '+r.title+'. Shown as if it were '+Math.round(r.seconds)+' seconds long, so the end comes quickly.'+(r.has_hook?'':' It has no opening text yet, so an example text is shown.')}
  if(LK.again){LK.again=false;lookDraw()}}
async function localAi(){
  if(!confirm('Set up the free AI on this computer?'+NL+NL+'It installs Ollama (a free program that runs AI models, about 1 GB) and downloads one model (about 2 GB). This takes a while. Progress shows under Setup check.'))return;
  const r=await send('/api/ai/local-setup'); if(r.error){toast(r.error,true);return}
  $('healthbox').open=true; go('settings'); toast('Setting up the free AI on this computer. Watch the progress under Setup check.'); show(r);
}
async function clipNow(){
  const n=+val('w-last'), r=await send('/api/watch/clip-now',{seconds:n});
  if(r.error){toast(r.error,true);return}
  toast('Asked for the last '+n+' seconds. The draft appears at the top of the list in a minute or two.');show(r);
}
function startWatch(){act('/api/watch/start',{channels:val('o-channels'),mode:mode()})}
function pickMode(){for(const m of document.querySelectorAll('.mode'))m.classList.toggle('on',m.querySelector('input').checked);saveOpts()}
const HCOL={ok:'var(--ok)',warn:'var(--warn)',bad:'var(--bad)',todo:'var(--muted)'};
let healthOpened=false;
function drawHealth(h){
  const sm=h.summary, col=sm.bad?'bad':(sm.todo?'todo':(sm.warn?'warn':'ok'));
  $('h-items').innerHTML=h.items.map(i=>'<div class="hrow"><span class="dot" style="background:'+HCOL[i.state]+'"></span><span><b>'+esc(i.name)+'</b></span>'
    +(i.detail?'<small>'+esc(i.detail)+'</small>':'')+(i.fix?'<small class="fix"><b>What to do:</b> '+esc(i.fix)+'</small>':'')+'</div>').join('');
  $('h-sum').textContent=h.maint.running?h.maint.name+' running…':sm.text;
  const b=$('h-badge'); b.textContent=sm.bad?'!':(sm.todo?'?':'✓'); b.style.background=sm.bad?'var(--bad)':(sm.warn||sm.todo?'var(--surface2)':'var(--ok)'); b.style.color=(sm.bad||col==='ok')?'#fff':'var(--text)';
  const p=$('p-health'); p.querySelector('.dot').style.background=HCOL[col]; p.lastElementChild.textContent=h.maint.running?h.maint.name+'…':(sm.bad?sm.text:(sm.todo?'Setup not fully checked':'Setup OK'));
  if(sm.bad&&!healthOpened){$('healthbox').open=true;healthOpened=true}
  for(const id of ['b-hcheck','b-hrepair','b-hupdate','b-hdoctor'])$(id).disabled=h.maint.running;
  $('b-hdoctor').hidden=!(h.items||[]).some(i=>i.id==='speech'&&i.state==='bad');
  $('b-hupdate').hidden=!h.can_update;
  const last=h.maint.lines.length?h.maint.lines[h.maint.lines.length-1]:'';
  $('h-note').hidden=!h.maint.lines.length; $('h-note').innerHTML=(h.maint.running?'<b>'+esc(h.maint.name)+' is running…</b> ':'')+esc(last);
  logTo('hlog',h.maint.lines);
}
async function hAct(url,ask){if(ask&&!confirm(ask))return;act(url,{})}
/* ---------- your own clip ---------- */
function hms(p){return (+val(p+'h')||0)*3600+(+val(p+'m')||0)*60+(+val(p+'s')||0)}
function ownHint(){
  const mode=val('m-split'), toEnd=$('m-toend').checked, a=hms('m-s'), b=hms('m-e'), len=b-a;
  $('m-count').hidden=mode!=='count'; $('m-secs').hidden=mode!=='secs'; $('m-labelrow').hidden=mode==='one';
  for(const id of ['m-eh','m-em','m-es'])$(id).disabled=toEnd;
  $('b-own').textContent=mode==='one'?'Make the clip':'Make the parts';
  let t='';
  if(toEnd)t=mode==='one'?'One clip from the start time to the end of the video.':'From the start time to the end of the video, split into parts.';
  else if(len<=0)t='The end must be after the start.';
  else if(mode==='one')t='One clip, '+Math.round(len)+' seconds long.'+(len>90?' That is long for a short video; think about splitting it into parts.':'');
  else if(mode==='count'){const n=+val('m-parts')||1;t=n+' parts of about '+Math.round(len/n)+' seconds each.'}
  else{const p=+val('m-plen')||50, n=Math.max(1,Math.floor(len/p)), rest=len-n*p;t=(rest>=8?n+1:n)+' parts of '+p+' seconds'+(rest>=8?', the last one '+Math.round(rest)+' seconds.':(rest>0?', the last one a little longer.':'.'))}
  $('m-hint').textContent=t;
}
function makeOwn(){
  const mode=val('m-split'), body={source:val('m-src').trim(),start:hms('m-s'),end:hms('m-e'),to_end:$('m-toend').checked,part_label:$('m-label').checked};
  if(!body.source){toast('Paste a video link, or choose a file, first.',true);return}
  if(mode==='count')body.parts=+val('m-parts'); if(mode==='secs')body.part_seconds=+val('m-plen');
  act('/api/cut',body);
}
async function uploadFile(inp){
  const f=inp.files[0]; if(!f)return; const st=$('m-up'); st.className='saved'; st.textContent='Copying '+f.name+' ('+Math.round(f.size/1e6)+' MB)…';
  /* Sent with a request that reports how much has gone, so the copy shows a percentage. */
  try{const d=await new Promise((ok,bad)=>{const x=new XMLHttpRequest();x.open('POST','/api/upload?name='+encodeURIComponent(f.name));
      x.upload.onprogress=e=>{if(e.lengthComputable)st.textContent='Copying '+f.name+': '+Math.floor(100*e.loaded/e.total)+'% of '+Math.round(f.size/1e6)+' MB'};
      x.onload=()=>{if(x.status===200){try{ok(JSON.parse(x.responseText))}catch(e){bad('the answer could not be read')}}else bad(x.responseText||('error '+x.status))};
      x.onerror=()=>bad('the program is not answering');x.send(f)});
    $('m-src').value=d.path; st.textContent='Ready: '+f.name}
  catch(e){st.textContent='';toast('The file could not be copied: '+e,true)}
  inp.value='';
}
function saveLang(){
  /* Choosing Urdu or Hindi for the first time: write the text in Roman letters too, which is what most people want. */
  if(val('l-speech')!=='en'&&(last.speech_language||'en')==='en'&&val('l-text')==='English'){$('l-text').value=val('l-speech')==='hi'?'Hinglish':'Roman Urdu'}
  act('/api/options',{speech_language:val('l-speech'),caption_script:val('l-script'),text_language:val('l-text'),youtube_captions:val('l-yt')})}
function saveAi(){act('/api/options',{llm_backend:val('a-backend'),llm_prefer:val('a-prefer'),llm_fallback:$('a-fallback').checked,llm_model:val('a-model'),ai_model:val('a-stream')})}
async function act2(url){if(await post(url))poll1()}
async function poll1(){try{const r=await fetch('/api/options');if(r.ok)show(await r.json())}catch(e){}}
function saveOpts(){const o={mode:mode(),opening:$('o-opening').checked};o.campaign=val('o-camp');act('/api/options',o)}
async function poll(){
  let s=null;
  try{const r=await fetch('/api/options');if(r.ok)s=await r.json();else setErr('The page could not read the program state: '+r.status+' '+await r.text())}
  catch(e){setErr('THE PROGRAM IS NOT ANSWERING. It has stopped or crashed: look at the terminal window (the text window that opened with the program); the reason is printed there.')}
  /* Drawing is kept apart from fetching: a fault in the page itself must not be reported as the program having stopped. */
  if(s){try{show(s)}catch(e){setErr('THE PAGE HIT A FAULT OF ITS OWN while drawing ('+e+'). The program is still running. Reload the page; if this stays, restart the program and report this line.')}}
  setTimeout(poll,(s&&(s.running||s.finishing||(s.health&&s.health.maint.running)))?1500:5000)}

/* ---------- clips ---------- */
const STATUS_TEXT={candidate:'To review',approved:'Approved, videos not made yet',pending:'Pending: taken back from Approved',done:'Done: videos made',rejected:'Rejected',bin:'In the bin'};
/* Where a clip is listed: an approved clip whose videos are made (and not edited since) is Done. */
function place(c){return c.binned?'bin':(c.made?'done':(c.status==='candidate'&&c.held?'pending':c.status))}
let filter='candidate';
function foundBy(c){const t=String(c.trigger||'chat'), parts=[];
  if(t.includes('chat'))parts.push('Chat '+c.score+'× busier than usual');
  if(t.includes('audio'))parts.push('Loud moment');
  if(t.includes('video'))parts.push('Fast action');
  if(t.includes('said'))parts.push('Excited words');
  else if(c.said>=5)parts.push('Excited words');
  if(t==='ai')parts.push('Found by AI from the speech');
  if(t==='manual')return [c.parts?'Part '+c.part+' of '+c.parts:'Chosen by you'];
  return parts.length?parts:[t]}
const coverText=new Map();   /* a thumbnail description being typed, kept through redraws and failures */
const coverOpen=new Set(), COVER_BY={frame:'Made from the clip',chatgpt:'Drawn by ChatGPT',prompt:'Drawn by ChatGPT from your description'};
function cardHtml(c){
  /* a clip whose own record cannot be read: say so, and offer the one thing that can be done about it */
  if(c.broken)return '<div class="tile" style="grid-column:1/-1"><div class="cliperr" style="white-space:normal"><b>This clip is damaged.</b> '+esc(c.error||'')+'</div><div style="margin-top:10px"><button class="btn quiet" title="Remove this clip and its files forever" onclick="delClip(\''+c.key+'\')">Delete</button></div></div>';
  const cv=c.cover||{}, k=c.key, shown=c.video?(c.video==='final.mp4'?'Draft with captions':'Downloaded video only, draft not finished'):'';
  const VN={'final.mp4':'A','final_b.mp4':'B','explained.mp4':'Explained A','explained_b.mp4':'Explained B','source.mp4':'Original'};
  const tabs=(c.videos||[]).length>1||(c.videos||[]).includes('final.mp4')?'<div class="vtabs">'+(c.videos||[]).map(n=>'<button class="'+(n===c.video?'on':'')+'" onclick="showVid(\''+k+'\',\''+n+'\',this)">'+VN[n]+'</button>').join('')
    +(cv.made?'<button onclick="showCover(\''+k+'\',this)" title="This clip\'s thumbnail">Thumbnail</button>':'')
    +((c.videos||[]).includes('final.mp4')&&!(c.videos||[]).includes('final_b.mp4')?'<button onclick="makeB(\''+k+'\',this)" title="Make version B now so you can compare it with version A">Make B preview</button>':'')+'</div>':'';
  /* The picture: fill the screen (some of a wide stream is cut off), or the whole picture with a blurred copy above and below. */
  const fm=c.framing, whole=fm&&fm.layout==='blur', boxes=fm&&!whole&&fm.custom;
  const wk=WORK.get(k), dis=wk?' disabled':'';
  const frow=fm&&(c.videos||[]).includes('source.mp4')?'<div class="frow"><span>'+(wk?esc(wk):'Picture:')+'</span>'
    +'<button class="'+(!whole&&!boxes?'on':'')+'" title="The program chooses what to show: the video fills the screen, and the face of the streamer goes on top when the stream has a face camera"'+dis+' onclick="quickFrame(\''+k+'\',\'auto\',this)">Automatic</button>'
    +'<button class="'+(whole?'on':'')+'" title="Nothing is cut off: the whole stream picture in the middle, with a blurred, moving copy of it filling the space above and below"'+dis+' onclick="quickFrame(\''+k+'\',\'whole\',this)">Whole picture</button>'
    +'<button class="'+(boxes?'on':'')+'" title="Draw yourself what is shown: a box for the face and a box for the video. The boxes keep the right shape by themselves"'+dis+' onclick="openBoxes(\''+k+'\')">Choose boxes…</button></div>':'';
  const vid=c.video?'<video'+(openKey===k?' controls':'')+' preload="metadata" src="/media/'+k+'/'+c.video+'?v='+c.mtime+(openKey===k?'':'#t=1.5')+'"></video>'+tabs+'<small>'+shown+'</small>'+frow
    :'<div class="none">'+(c.error?'This clip could not be made.':'The video is not ready yet.')+'</div>';
  const pc=c.points>=70?'good':(c.points>=40?'':'meh');
  const chips='<span class="chip '+pc+'" title="One score from chat, sound, picture and AI. Higher is better."><b>Score '+c.points+'</b></span>'
    +(c.ai_rating!=null?'<span class="chip" title="How good AI thinks this clip is. It judges from the words and three still pictures">AI rating '+c.ai_rating+'</span>':'')
    +foundBy(c).map(x=>'<span class="chip">'+esc(x)+'</span>').join('')
    +(c.activity!=null&&c.trigger!=='manual'?'<span class="chip">'+esc(c.activity)+' people chatting</span>':'')
    +(c.ai_pick?'<span class="chip good">AI pick</span>':'')
    +(c.ai_judged?(c.copy_source==='plain'?'<span class="chip meh">Text written without AI</span>'
        :'<span class="chip '+(c.ai_keep?'good':'meh')+'">'+(c.ai_keep?'AI: worth posting':'AI: probably not worth posting')+'</span>'):'')
    +(String(c.copy_source||'').startsWith('local')?'<span class="chip" title="Claude could not be reached, so the free AI on this computer wrote the text. It is redone by Claude when Claude is available.">Text by the free AI</span>':'')
    +(c.words_from==="YouTube's captions"?'<span class="chip" title="The captions are YouTube\'s own words for this video. This program did not listen to the speech itself. Fix any wrong word under Captions.">Captions from YouTube</span>':'')
    +(c.exported?'<span class="chip good">Videos made</span>':'')
    +(cv.made?'<span class="chip good" title="'+esc(COVER_BY[cv.by]||'')+'">Thumbnail made</span>':'');
  const lay=[['','Automatic'],['split','Face on top, video below'],['crop','Video only, full height'],['split_under','Video on top, face below'],['blur','Whole picture on a blurred background'],['pip','Face in a corner over the video'],['circle','Face in a circle over the video']].map(o=>'<option value="'+o[0]+'"'+((c.layout||'')===o[0]?' selected':'')+'>'+o[1]+'</option>').join('');
  const title=c.title||c.hook||String(c.transcript||'').slice(0,70)||'Untitled clip', who=c.channel||c.stream_title||'Your video', secs=Math.round((c.cut_end_s??c.end_s)-(c.cut_start_s??c.start_s));
  const where=place(c), isOpen=openKey===k;
  const etabs=[['text','Text'],['versions','Versions']].concat(c.has_words?[['captions','Captions']]:[],[['trim','Start, end and picture'],['thumb','Thumbnail',!!cv.error]],c.exported?[['posted','Posted']]:[]);
  let tabNow=isOpen?(edTab.get(k)||'text'):'';if(isOpen&&!etabs.some(t=>t[0]===tabNow))tabNow='text';
  const P=n=>' class="sec pane" data-tab="'+n+'"'+(tabNow===n?' open':'');
  const decide=''
   +(c.status!=='approved'?'<button class="btn ok" onclick="save(\''+k+'\',{status:\'approved\'})">Approve</button>':'')
   +(c.status==='approved'&&!c.made?'<button class="btn ok" title="Make the videos of this one clip now, and leave the other approved clips for later" onclick="makeOne(\''+k+'\')">Make this clip\'s videos now</button>':'')
   +(c.status==='approved'&&c.made?'<button class="btn" title="Make videos leaves finished clips alone. Press this if its file is gone from the videos folder, or you changed the caption look and want this clip made with the new one" onclick="makeOne(\''+k+'\',true)">Make its videos again</button>':'')
   +(c.status==='approved'?'<button class="btn" title="Takes it out of Approved without mixing it into To review: it waits in the Pending list" onclick="save(\''+k+'\',{status:\'candidate\',held:true})">Move to Pending</button>':'')
   +(c.status!=='candidate'||c.held?'<button class="btn" onclick="save(\''+k+'\',{status:\'candidate\',held:false})">Back to review</button>':'')
   +(c.status!=='rejected'?'<button class="btn bad" onclick="save(\''+k+'\',{status:\'rejected\'})">Reject</button>':'');
  /* On the tile: the one or two things most people do next. Everything else is in the editor. */
  const quick=where==='candidate'?'<button class="btn ok" title="Approve: its videos are made when you press Make videos" onclick="save(\''+k+'\',{status:\'approved\'})">'+ICON.check+'Approve</button><button class="btn icon bad" title="Reject" aria-label="Reject" onclick="save(\''+k+'\',{status:\'rejected\'})">'+ICON.x+'</button>'
    :where==='pending'?'<button class="btn ok" onclick="save(\''+k+'\',{status:\'approved\'})">'+ICON.check+'Approve</button>'
    :where==='approved'?'<button class="btn" title="Make the videos of this one clip now, and leave the other approved clips for later" onclick="makeOne(\''+k+'\')">Make now</button>'
    :where==='rejected'?'<button class="btn" onclick="save(\''+k+'\',{status:\'candidate\',held:false})">Back to review</button>'
    :where==='bin'?'<button class="btn ok" title="Put this clip back in the list it was in" onclick="restoreClip(\''+k+'\')">Bring back</button><button class="btn quiet" title="Remove this clip and its files for good, now" onclick="delClip(\''+k+'\',true)">Delete forever</button>':'';
  return '<div class="edhead"><button class="btn" onclick="closeClip()" title="Back to the list (Esc). Changes are saved.">'+ICON.back+'Clips</button><h3>'+esc(title)+'</h3><span class="badge">'+STATUS_TEXT[where]+'</span>'
   +'<span class="when">'+esc(who)+' · at '+clock(c.start_s)+' in the stream · '+secs+' s</span><span class="sp"></span>'+decide+'</div>'
   +'<div class="vid">'+vid+'<label class="pk" title="Tick to select this clip"><input type="checkbox" aria-label="Select this clip"'+(SEL.has(k)?' checked':'')+' onchange="selSet(\''+k+'\',this.checked)"></label><span class="score num '+pc+'" title="One score from chat, sound, picture and AI. Higher is better.">'+c.points+'</span><span class="dur num">'+Math.floor(secs/60)+':'+String(secs%60).padStart(2,'0')+'</span></div>'
   +'<div class="tile"><button class="tt" onclick="openClip(\''+k+'\')" title="Open this clip to edit it">'+esc(title)+'</button>'
   +'<div class="tm"><span class="who">'+esc(who)+'</span><span class="newtag" title="Made in the last hour and not reviewed yet"'+(isNew(c)?'':' hidden')+'>NEW</span>'
   +'<span class="when2" title="When this clip was made'+(c.decided_at?'. Decided '+esc(c.decided_at):'')+'">'+madeText(c.made_at)+'</span></div>'
   +'<div class="tfound" title="Why this clip is here. Press Edit for the details.">'+(c.binned?'In the bin. Gone for good in '+c.bin_days_left+(c.bin_days_left===1?' day.':' days.'):esc(foundBy(c).concat(c.ai_pick?['AI pick']:[]).join(' · ')))+'</div>'
   +(c.error?'<div class="tprob">Has a problem. Open it to read why.</div>':'')
   +'<div class="ta">'+quick+(where==='bin'?'':'<button class="btn" onclick="openClip(\''+k+'\')" title="Change its text, captions, start and end, picture, thumbnail and versions">'+ICON.edit+'Edit</button>')+'</div></div>'
   +'<div class="info">'
   +'<div class="chips">'+chips+'</div>'
   +(c.ai_pick?'<div class="ai"><b>Why AI picked it:</b> '+esc(c.ai_pick)+'</div>':'')
   +(c.ai_judged&&c.copy_source==='plain'?'<div class="ai"><b>The opening text and titles below are basic ones,</b> because AI was not used: '+esc(c.copy_problem||'no reason recorded')+'. Type your own, or press "Write text with AI".</div>'
     :(c.ai_judged&&c.ai_reason?'<div class="ai">'+esc(c.ai_reason)+'</div>':''))
   +(c.campaign?'<div class="issues"><b>'+esc(c.campaign)+':</b> '+((c.campaign_issues||[]).some(i=>i.level==='fail')?'<span style="color:var(--bad)">does not meet the rules yet</span>':'meets the rules that can be checked')
      +((c.campaign_issues||[]).length?'<ul>'+c.campaign_issues.map(i=>'<li class="'+(i.level==='fail'?'fail':'')+'">'+(i.level==='fail'?'Fix: ':'Check by hand: ')+esc(i.message)+'</li>').join('')+'</ul>':'')+'</div>':'')
   +(c.error?'<div class="cliperr">'+plainHtml(c.error,'This clip has a problem'+(/^\d\d:\d\d:\d\d /.test(c.error)?' (at '+c.error.slice(0,5)+')':'')+':')+'<button class="x" title="Close this message" aria-label="Close this message" onclick="clearErr(\''+k+'\')">×</button></div>':'')
   +(c.notes?'<div class="ai"><b>Note:</b> '+esc(c.notes)+'</div>':'')
   +((c.files||[]).length?'<div class="ai"><b>Finished file'+(c.files.length>1?'s':'')+' in the ready folder:</b> '+c.files.map(esc).join(' · ')+'</div>':'')
   +'<div class="said">'+(c.transcript?'“'+esc(c.transcript)+'”':'<span style="color:var(--muted)">The words of this clip are not written down yet.</span>')+'</div>'
   +(String(c.transcript||'').length>260?'<button class="more" onclick="this.previousElementSibling.classList.toggle(\'open\');this.textContent=this.textContent===\'Show all\'?\'Show less\':\'Show all\'">Show all</button>':'')
   +'<div class="edtabs">'+etabs.map(t=>'<button class="'+(tabNow===t[0]?'on':'')+(t[2]?' flag':'')+'" data-tab="'+t[0]+'" onclick="edGo(\''+k+'\',\''+t[0]+'\')">'+t[1]+'</button>').join('')+'</div>'
   +'<details'+P('text')+'><summary>Text <small>opening text, title, description and hashtags</small></summary>'
   +'<div class="two"><div><label class="f">Opening text on the video (version A)</label><input type="text" name="hook" value="'+esc(c.hook)+'"></div>'
   +'<div><label class="f">Opening text, version B <small>(empty = same as A)</small></label><input type="text" name="hook_b" value="'+esc(c.hook_b)+'"></div></div>'
   +'<div class="two"><div><label class="f">Title (version A)</label><input type="text" name="title" value="'+esc(c.title)+'"></div>'
   +'<div><label class="f">Title, version B</label><input type="text" name="title_b" value="'+esc(c.title_b)+'"></div></div>'
   +'<label class="f">Description</label><textarea name="description">'+esc(c.description)+'</textarea>'
   +'<label class="f">Hashtags (separated by spaces)</label><input type="text" name="tags" value="'+esc((c.tags||[]).join(' '))+'"></details>'
   +'<details'+P('versions')+'><summary>Versions to make <small>now: '+(c.simple===false?'the explainer only':(c.explainer?'the normal video and an explainer':'the normal video'))+(c.ab!==false?', each as A and B':', version A only')+'</small></summary>'
   +'<label class="check"><input type="checkbox" name="simple"'+(c.simple!==false?' checked':'')+'> Make the normal video (the clip as it is, without a narrator)</label>'
   +'<label class="check"><input type="checkbox" name="ab"'+(c.ab!==false?' checked':'')+'> Make a version B too <small style="color:var(--muted)">(the same clip with the other opening text and title, to test which does better; this clip only)</small></label>'
   +'<label class="check"><input type="checkbox" name="explainer"'+(c.explainer?' checked':'')+'> Make an explainer version (a narrator voice introduces the moment first, then the clip plays)</label>'
   +'<div class="two"><div><label class="f">What the narrator says (version A)'+(last&&last.voice&&last.voice.language&&last.voice.language!=='English'?' <small>in '+esc(last.voice.language)+' letters</small>':'')+'</label><input type="text" name="intro" value="'+esc(c.intro)+'"></div>'
   +'<div><label class="f">Narrator, version B <small>(empty = same as A)</small></label><input type="text" name="intro_b" value="'+esc(c.intro_b)+'"></div></div></details>'
   +(c.has_words?'<details'+P('captions')+' ontoggle="if(this.open)initWords(\''+k+'\')"><summary>Captions <small>fix a wrong word, or empty a box to remove it</small></summary>'
     +'<div class="words"></div><div class="btns"><button class="btn" onclick="saveWords(\''+k+'\')">Save captions</button>'
     +'<a class="btn" href="/api/clip/'+k+'/captions.srt" download>Download .srt</a><a class="btn" href="/api/clip/'+k+'/captions.vtt" download>Download .vtt</a>'
     +'<button class="btn" onclick="this.nextElementSibling.click()">Use a subtitle file…</button><input type="file" accept=".srt,.vtt,.txt" hidden onchange="subFile(this,\''+k+'\')"></div>'
     +'<p class="help" style="margin-top:6px">The two Download buttons save these captions as subtitle files (.srt or .vtt) that other video apps can open. The button Use a subtitle file replaces these captions with a file of your own. The times in that file must count from the start of the clip.</p></details>':'')
   +'<details'+P('trim')+' ontoggle="if(this.open)initTrim(\''+k+'\')"><summary>Start, end and picture <small>where it starts and ends, what the clip shows, ad label</small></summary>'
   +'<div class="trim"></div>'
   +'<div class="three"><div><label class="f">Start (seconds into the stream)</label><input type="number" step="0.5" name="cut_start_s" value="'+esc(c.cut_start_s??c.start_s)+'"></div>'
   +'<div><label class="f">End</label><input type="number" step="0.5" name="cut_end_s" value="'+esc(c.cut_end_s??c.end_s)+'"></div>'
   +'<div><label class="f">What the clip shows</label><select name="layout">'+lay+'</select></div></div>'
   +'<p class="help" style="margin-top:6px">Start and end can be anywhere from '+esc(c.lo)+' to '+esc(c.hi)+' seconds. What the clip shows now: '+esc(c.layout_reason||'not checked yet')+'.</p>'
   +((c.videos||[]).includes('source.mp4')?'<div class="btns wrow" style="margin-top:0;align-items:center"><span class="help" style="margin:0">'+(wk?esc(wk):'Need more of the stream?')+'</span>'
       +'<button class="btn" title="Download 15 more seconds in front of what this clip has, so its start can be moved earlier"'+dis+' onclick="widen(\''+k+'\',15,0,this)">Add 15 s before</button>'
       +'<button class="btn" title="Download 15 more seconds after what this clip has, so its end can be moved later"'+dis+' onclick="widen(\''+k+'\',0,15,this)">Add 15 s after</button>'
       +'<button class="btn" title="Download 30 more seconds on each side"'+dis+' onclick="widen(\''+k+'\',30,30,this)">Add 30 s on both sides</button></div>':'')
   +(c.framing?'<div class="btns" style="margin-top:0"><button class="btn" onclick="openBoxes(\''+k+'\')">Set the face box and the video box</button>'
       +'<button class="btn" title="Make the draft of every unfinished clip of this video again, with the caption style, template, logo and music chosen now" onclick="remakeAll(\''+k+'\')">Make this video\'s drafts again</button></div>':'')
   +'<label class="check"><input type="checkbox" name="disclosure"'+(c.disclosure?' checked':'')+'> Show an on-screen ad label (for paid campaign clips)</label></details>'
   +'<details'+P('thumb')+' ontoggle="this.open?coverOpen.add(\''+k+'\'):coverOpen.delete(\''+k+'\')"><summary>Thumbnail <small>a cover picture for Shorts, Reels and TikTok (optional)'+(cv.made?' · made':'')+(cv.error?' · <b style="color:var(--bad)">could not be made</b>':'')+'</small></summary>'
   +(cv.made?'<img class="cover" alt="This clip\'s thumbnail" src="/media/'+k+'/cover.jpg?v='+cv.mtime+'"><p class="help" style="margin:0 0 6px">'+esc(COVER_BY[cv.by]||'Made')+(cv.at?' · '+esc(cv.at):'')+(cv.words&&cv.by!=='frame'?' · words on it: '+esc(cv.words):'')+'. '+(c.exported?'It is saved beside the finished video, with the same name.':'It is put beside the finished video, with the same name, when the videos are made.')+'</p>'
     :'<p class="help" style="margin-top:8px">No thumbnail yet. You do not have to make one: a thumbnail is optional.</p>')
   +(cv.error?'<div class="cliperr"><b>The thumbnail could not be made:</b> '+esc(cv.error)+'</div>':'')
   +'<div class="btns" style="margin-top:0"><button class="btn" title="Free, no AI: the sharpest picture from the clip with its opening text" onclick="makeCover(\''+k+'\',\'frame\',this)">Make it from the clip (free)</button>'
   +'<button class="btn" title="ChatGPT looks at the clip and decides the picture itself. Uses your ChatGPT plan; one to three minutes" onclick="makeCover(\''+k+'\',\'chatgpt\',this)">Let ChatGPT make it</button></div>'
   +'<label class="f">Or describe the picture yourself <small>(ChatGPT draws it; it still sees the clip)</small></label>'
   +'<textarea name="t_cover" maxlength="2000" style="min-height:64px" oninput="coverText.set(\''+k+'\',this.value)" placeholder="For example: the streamer shocked on the left, the bike upside down in the air on the right, big yellow text NO WAY">'+esc(coverText.has(k)?coverText.get(k):(cv.prompt||''))+'</textarea>'
   +'<label class="f">Not wanted in this clip\'s thumbnail <small>(optional; added to your general list on the Look and branding page)</small></label>'
   +'<input type="text" name="t_cover_avoid" maxlength="500" oninput="coverAvoid.set(\''+k+'\',this.value)" value="'+esc(coverAvoid.has(k)?coverAvoid.get(k):(c.cover_avoid||''))+'" placeholder="For example: no blood, do not show the other player\'s name, no red arrows">'
   +'<div class="btns"><button class="btn" onclick="makeCover(\''+k+'\',\'prompt\',this)">Make it from my description</button>'
   +(cv.made?'<a class="btn" href="/media/'+k+'/cover.jpg" download="thumbnail.jpg">Download</a><button class="btn quiet" onclick="makeCover(\''+k+'\',\'none\',this)">Remove thumbnail</button>':'')+'</div></details>'
   +(c.exported?'<details'+P('posted')+'><summary>Posted <small>log where you posted it; keeps views, payouts and rejections under Posted and earnings</small></summary>'
     +'<div class="three"><div><label class="f">Where</label><select name="t_platform"><option>YouTube Shorts</option><option>TikTok</option><option>Instagram Reels</option><option>Facebook Reels</option><option>Other</option></select></div>'
     +'<div><label class="f">Version</label><select name="t_version"><option>A</option><option>B</option><option>Explained A</option><option>Explained B</option></select></div>'
     +'<div><label class="f">Link to the post</label><input type="text" name="t_url" placeholder="https://..."></div></div>'
     +'<label class="f">Campaign (leave empty if none)</label><input type="text" name="t_campaign" value="'+esc(c.campaign)+'">'
     +'<div class="btns"><button class="btn" data-act="log" data-key="'+esc(k)+'">Add to Posted and earnings</button></div></details>':'')
   +'<div class="acts"><div class="tools"><button class="btn" onclick="save(\''+k+'\')">Save changes</button>'
   +'<button class="btn" onclick="rewrite(\''+k+'\',this)">Write text with AI</button>'
   +'<span class="saved"></span><button class="btn quiet" title="Remove this clip and its files forever" onclick="delClip(\''+k+'\')">Delete</button></div></div></div>';
}
function domId(k){return 'c-'+k.replace('/','--')}
function busy(el){return el.dataset.dirty==='1'||[...el.querySelectorAll('video')].some(v=>!v.paused)||el.contains(document.activeElement)}
function drawFilters(clips,shownList){
  const n={candidate:0,approved:0,pending:0,done:0,rejected:0,bin:0};for(const c of clips){const p=place(c);n[p]=(n[p]||0)+1}
  $('filters').innerHTML=[['candidate','To review'],['approved','Approved'],['pending','Pending'],['done','Done'],['rejected','Rejected'],['all','All']].concat(n.bin||filter==='bin'?[['bin','Bin']]:[]).map(f=>
    '<button class="chipbtn'+(filter===f[0]?' on':'')+(['pending','rejected','all','bin'].includes(f[0])?' minor':'')+'" onclick="setFilter(\''+f[0]+'\')">'+f[1]+'<i>'+(f[0]==='all'?clips.length-n.bin:(n[f[0]]||0))+'</i></button>').join('');
  if(filter==='rejected'&&n.rejected)$('filters').insertAdjacentHTML('beforeend','<button class="chipbtn act" style="color:var(--bad)" onclick="delRejected('+n.rejected+')">Move all rejected to the bin</button>');
  if(filter==='bin'&&n.bin)$('filters').insertAdjacentHTML('beforeend','<button class="chipbtn act" data-yours style="color:var(--bad)" title="Removes every clip in the bin for good, now" onclick="cleanNow(\'bin\')">Empty the bin now</button>');
  if(filter==='approved'&&n.approved)$('filters').insertAdjacentHTML('beforeend','<button class="chipbtn act" data-yours title="Takes every approved clip out of Make videos. They wait in the Pending list, not mixed into To review" onclick="holdApproved('+n.approved+')">Move all '+n.approved+' to Pending</button>');
  $('n-count').textContent=n.candidate||'';$('n-count').dataset.n=n.candidate;drawVideos(clips);
  $('p-count').textContent=n.candidate+' to review · '+n.approved+' approved · '+(n.pending?n.pending+' pending · ':'')+n.done+' done';
  if(HAS_PANEL)$('b-finish').textContent=n.approved?'Make videos ('+n.approved+')':'Make videos';
  const shown=shownList.filter(c=>filter==='all'?place(c)!=='bin':place(c)===filter).length, em=$('empty');
  em.hidden=shown>0;
  em.innerHTML=(clips.length&&shownList.length<clips.length&&(filter==='all'?clips.length:n[filter])>0)?'<b>No clip matches the filters.</b>Press Sort and filter, then Clear filters.':clips.length?'<b>Nothing here.</b>No clips are '+({candidate:'waiting for review',approved:'approved and waiting for their videos',pending:'in Pending. A clip you take back from Approved with "Move to Pending" waits here',done:'finished yet. Approve a clip and press Make videos',rejected:'rejected'}[filter]||'in this list')+'.'
    :'<b>No clips yet.</b>'+(HAS_PANEL?'Paste a finished stream under New clips, or start watching under Live channels.':'Run find first.');
}
function setFilter(f){filter=f;$('clips').dataset.filter=f;for(const o of document.querySelectorAll('#clips .clip:not(.open) video'))o.pause();loadClips()}
let known=[];
/* What a card was drawn from: when any of this changes, the card is drawn again. */
function sig(c){return [c.mtime,c.status,c.made,c.points,c.campaign,(c.campaign_issues||[]).length,(c.videos||[]).join(),c.held,c.simple,(c.files||[]).join(),(c.cover||{}).mtime,(c.cover||{}).error].join('|')}
let loadTurn=0, clipsWhy='';
/* ---------- order and filters ---------- */
function isNew(c){return c.status==='candidate'&&!c.held&&c.made_at&&Date.now()/1000-c.made_at<3600}
function madeText(t){if(!t)return '';const d=new Date(t*1000), age=(Date.now()-d)/60000, hm=String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');
  if(age<1)return 'made just now';if(age<60)return 'made '+Math.round(age)+' min ago';
  const day=x=>new Date(x.getFullYear(),x.getMonth(),x.getDate()).getTime(), diff=Math.round((day(new Date())-day(d))/864e5);
  if(diff===0)return 'made today '+hm;if(diff===1)return 'made yesterday '+hm;
  return 'made '+d.getDate()+' '+['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()]+' '+hm}
let onlyVid='';   /* set by a click on one of "Your videos": only that video's clips, until Clear filters */
function arrangeNow(){return {sort:val('s-sort'),who:val('s-who'),how:val('s-how'),when:val('s-when'),find:val('s-find').trim().toLowerCase(),vid:onlyVid}}
function setArrange(){try{const a=arrangeNow();delete a.vid;localStorage.setItem('arrange',JSON.stringify(a))}catch(e){} loadClips()}
function clearArrange(){onlyVid='';$('s-who').value='';$('s-how').value='';$('s-when').value='';$('s-find').value='';setArrange()}
function fits(c,a){
  if(a.vid&&String(c.key).split('/')[0]!==a.vid)return false;
  if(a.who&&(c.channel||c.stream_title||'Your video')!==a.who)return false;
  if(a.how&&!(String(c.trigger||'chat').split('+').includes(a.how)||(a.how==='ai'&&c.ai_pick)||(a.how==='said'&&c.said>=5)))return false;
  if(a.when&&!(c.made_at&&Date.now()/1000-c.made_at<a.when*3600))return false;
  if(a.find&&![c.transcript,c.hook,c.hook_b,c.title,c.channel,c.stream_title,c.notes].some(t=>String(t||'').toLowerCase().includes(a.find)))return false;
  return true}
/* Clips in the order chosen. Parts of one video you cut yourself stay together and in their own order. */
function arrange(clips,a){
  const out=clips.slice();
  if(a.sort==='score')out.sort((x,y)=>y.points-x.points||y.made_at-x.made_at);
  else{const dir=a.sort==='old'?1:-1;out.sort((x,y)=>{
    if(x.parts&&y.parts&&x.key.split('/')[0]===y.key.split('/')[0]&&Math.abs(x.made_at-y.made_at)<600)return x.part-y.part;
    return dir*(x.made_at-y.made_at)||y.points-x.points})}
  return out}
async function loadClips(){
  /* Answers can arrive out of order: a refresh that started before you approved a clip may come back after
     the newer one. Only the answer to the most recent request is used; older ones are dropped. */
  const turn=++loadTurn;
  let clips;try{const r=await fetch('/api/clips');
    if(!r.ok){const why=await r.text();if(why!==clipsWhy){clipsWhy=why;toast('The clip list could not be loaded: '+why,true)}return}
    clips=await r.json();clipsWhy='';if(!HAS_PANEL)setErr('')}
  catch(e){if(!HAS_PANEL)setErr('THE PROGRAM IS NOT ANSWERING. It has stopped or crashed: look at the terminal window (the text window that opened with the program); the reason is printed there.');return}  /* with the panel, the status check reports it */
  if(turn!==loadTurn)return;
  const a=arrangeNow();
  clips=arrange(clips,a);
  known=clips;
  /* the streamers there are, for the filter */
  const names=[...new Set(clips.map(c=>c.channel||c.stream_title||'Your video'))].sort(), who=$('s-who');
  if(who.dataset.sig!==names.join('|')){who.dataset.sig=names.join('|');const keep=who.value;
    who.innerHTML='<option value="">All</option>'+names.map(n=>'<option value="'+esc(n)+'">'+esc(n)+'</option>').join('');who.value=names.includes(keep)?keep:''}
  const box=$('clips'), want=new Set(clips.map(c=>domId(c.key)));
  for(const el of [...box.children])if(!want.has(el.id)){if(el.classList.contains('open')){closeClip(true);toast('The clip you had open is no longer there.',true)}el.remove()}
  let prev=null;
  for(const c of clips){
    let el=$(domId(c.key));
    if(!el){el=document.createElement('div');el.className='card clip';el.id=domId(c.key);el.dataset.status=place(c);el.dataset.m=sig(c);el._c=c;el.innerHTML=cardHtml(c);
      el.addEventListener('input',e=>{if(String(e.target.name||'').startsWith('t_'))return;
        if(e.target.closest&&e.target.closest('.words'))el.dataset.wdirty='1';el.dataset.dirty='1';const s=el.querySelector('.acts .saved');s.textContent='Unsaved changes';s.className='saved warn'});
      if(prev)prev.after(el);else box.prepend(el)}
    else if(sig(c)!==el.dataset.m&&!busy(el)){el.dataset.status=place(c);el.dataset.m=sig(c);el._c=c;el.innerHTML=cardHtml(c)}
    /* the clock on the card moves on without redrawing the card (a redraw would close fold-outs and rewind the video) */
    const w2=el.querySelector('.when2'), nt=el.querySelector('.newtag');if(w2)w2.textContent=madeText(c.made_at);if(nt)nt.hidden=!isNew(c);
    el.hidden=!fits(c,a)&&!busy(el);   /* a card you are typing in or watching is never hidden under you */
    prev=el;
  }
  /* Put the cards in the chosen order. A card that is playing or being typed in is not moved under you: next time. */
  const order=clips.map(c=>domId(c.key)), have=[...box.children].map(e=>e.id);
  if(order.join()!==have.join()&&![...box.children].some(busy))for(const id of order)box.appendChild($(id));
  if(a.vid&&!clips.some(c=>String(c.key).split('/')[0]===a.vid)){onlyVid='';a.vid=''}   /* that video's clips are all gone */
  const shownNow=clips.filter(c=>fits(c,a)), filtering=!!(a.who||a.how||a.when||a.find||a.vid);
  const one=a.vid&&clips.find(c=>String(c.key).split('/')[0]===a.vid);
  $('s-clear').hidden=!filtering;if(a.who||a.how||a.when||a.vid)$('sortrow').hidden=false;
  $('s-count').textContent=filtering?(one?'Only one video ('+String(one.stream_title||one.channel||'your video').slice(0,40)+'): ':'')+shownNow.length+' of '+clips.length+' clips'+(one?'. Clear filters shows all.':' match'):'';
  drawFilters(clips,shownNow);
}
async function save(key,extra){
  const el=$(domId(key)), g=n=>el.querySelector('[name='+n+']');
  if(!el){toast('That clip is not on the page any more.',true);return false}
  const all={hook:g('hook').value,hook_b:g('hook_b').value,intro:g('intro').value,intro_b:g('intro_b').value,explainer:g('explainer').checked,simple:g('simple').checked,ab:g('ab').checked,cover_avoid:g('t_cover_avoid').value.trim(),
    title:g('title').value,title_b:g('title_b').value,description:g('description').value,tags:g('tags').value,
    layout:g('layout').value,disclosure:g('disclosure').checked,cut_start_s:parseFloat(g('cut_start_s').value),cut_end_s:parseFloat(g('cut_end_s').value)};
  /* What the card showed when it was drawn. A field you did not touch is not sent: the card may be older than the clip
     (text written by AI since), and sending it all would put the old text back as if you had typed it. */
  const c=el._c||{}, was={hook:c.hook||'',hook_b:c.hook_b||'',intro:c.intro||'',intro_b:c.intro_b||'',explainer:!!c.explainer,simple:c.simple!==false,ab:c.ab!==false,cover_avoid:c.cover_avoid||'',title:c.title||'',title_b:c.title_b||'',
    description:c.description||'',tags:(c.tags||[]).join(' '),layout:c.layout||'',disclosure:!!c.disclosure,cut_start_s:Number(c.cut_start_s??c.start_s),cut_end_s:Number(c.cut_end_s??c.end_s)};
  const body={};for(const k in all)if(all[k]!==was[k])body[k]=all[k];
  if(('cut_start_s' in body)!==('cut_end_s' in body)){body.cut_start_s=all.cut_start_s;body.cut_end_s=all.cut_end_s}
  Object.assign(body,extra||{});
  if(el.dataset.wdirty==='1'&&!(await sendWords(key)))return false;  /* caption words you edited are saved with the rest */
  loadTurn++;
  let r;try{r=await fetch('/api/clip/'+key,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}catch(e){toast('The program is not answering: '+e,true);return false}
  if(!r.ok){toast('Not saved: '+await r.text(),true);return false}
  const s=await r.json();
  el.dataset.dirty='';el.dataset.wdirty='';el.dataset.m='';  /* redraw from the saved state on the next load */
  if(document.activeElement)document.activeElement.blur();
  const v=el.querySelector('video');if(v)v.pause();
  if(extra&&extra.status&&el.classList.contains('open'))closeClip(true);   /* decided: back to the list */
  toast(extra&&extra.held?'Moved to Pending':(extra&&extra.status?({approved:'Approved',rejected:'Rejected',candidate:'Moved back to review'}[s.status]):'Saved'));
  loadClips();
  return true;
}
async function rewrite(key,btn){
  const el=$(domId(key));
  if(el.dataset.dirty==='1'&&!confirm('You have unsaved changes on this clip. Text you typed and saved is kept; unsaved typing is lost. Continue?'))return;
  btn.disabled=true;btn.textContent='Writing…';
  let r;try{r=await fetch('/api/clip/'+key+'/rewrite',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})}catch(e){toast('The program is not answering: '+e,true);btn.disabled=false;btn.textContent='Write text with AI';return}
  if(!r.ok){toast(await r.text(),true);btn.disabled=false;btn.textContent='Write text with AI';return}
  el.dataset.dirty='';el.dataset.m='';if(document.activeElement)document.activeElement.blur();const v=el.querySelector('video');if(v)v.pause();
  toast('Text written by AI');loadClips();
}
async function post(url){
  try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(!r.ok){toast(await r.text(),true);return null} return await r.json()}
  catch(e){toast('The program is not answering: '+e,true);return null}
}
async function delClip(key,forever){
  const c=known.find(x=>x.key===key)||{}, gone=!!(forever||c.broken||c.binned);
  /* a clip goes to the bin first, where it waits a few days and can be brought back; only from there (or when its record is damaged) is it deleted for good */
  if(gone&&!confirm('Delete this clip'+(c.channel?' of '+c.channel:'')+' forever?'+NL+NL+'Its draft and downloaded video are removed and cannot be brought back. Finished videos you already made stay in the ready folder.'))return;
  const r=await send('/api/clip/'+key+'/delete',{forever:gone}); if(r.error){toast(r.error,true);return}
  const el=$(domId(key)); if(el){if(openKey===key)closeClip(true);if(r.binned)el.dataset.m='';else el.remove()}
  toast(r.binned?'Moved to the bin. You can bring it back for '+r.days+' days: My clips, Bin.':'Clip deleted for good'+(r.mb?' ('+r.mb+' MB freed)':'')); loadClips();
}
async function restoreClip(key){
  const r=await send('/api/clip/'+key+'/restore',{}); if(r.error){toast(r.error,true);return}
  const el=$(domId(key)); if(el)el.dataset.m='';
  toast('Brought back from the bin.'); loadClips();
}
async function delRejected(n){
  if(!confirm('Move all '+n+' rejected clip'+(n>1?'s':'')+' to the bin?'+NL+NL+'They wait there for a few days and can be brought back (My clips, Bin). After that they are removed for good.'))return;
  const r=await post('/api/clips/delete-rejected'); if(!r)return;
  toast(r.deleted+' clip'+(r.deleted===1?'':'s')+' deleted ('+r.mb+' MB freed)'+(r.busy?'. '+r.busy+' could not be deleted because a job is still using them; try again when it has ended.':''),r.busy>0); loadClips();
}
/* The list of things the person never wants in a ChatGPT thumbnail: saved with its own button, never lost to a redraw while typing. */
let avoidDirty=false;
document.addEventListener('input',e=>{if(e.target&&e.target.id==='o-avoid')avoidDirty=true});
async function saveAvoid(){
  if(await act('/api/options',{thumbnail_avoid:$('o-avoid').value})){avoidDirty=false;toast($('o-avoid').value.trim()?'Saved. ChatGPT thumbnails made from now on leave these out.':'Saved. The list is empty now.')}
}
/* A thumbnail for one clip: from the clip (free), by ChatGPT on its own, from your description, or taken off again. */
const coverAvoid=new Map();   /* what is typed in a clip's not-wanted box, kept while the card is redrawn */
async function makeCover(key,how,btn){
  const el=$(domId(key)); if(!el)return;
  const text=el.querySelector('[name=t_cover]').value.trim();
  if(how==='prompt'&&!text){toast('Describe the picture first, or press "Let ChatGPT make it".',true);return}
  if(how==='none'&&!confirm('Remove this clip\'s thumbnail? A copy already beside a finished video stays there.'))return;
  const box=btn.closest('details'), was=btn.textContent;
  for(const b of box.querySelectorAll('button'))b.disabled=true;
  btn.textContent=how==='frame'?'Making…':(how==='none'?'Removing…':'ChatGPT is drawing… one to three minutes');
  let r;try{r=await fetch('/api/clip/'+key+'/cover',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({how:how,prompt:text,avoid:el.querySelector('[name=t_cover_avoid]').value})})}
  catch(e){toast('The program is not answering: '+e,true);for(const b of box.querySelectorAll('button'))b.disabled=false;btn.textContent=was;return}
  const bad=r.ok?'':await r.text();
  coverOpen.add(key); if(document.activeElement)document.activeElement.blur(); el.dataset.m='';
  for(const b of box.querySelectorAll('button'))b.disabled=false; btn.textContent=was;   /* the card may not be redrawn at once (unsaved typing, a playing video) */
  if(bad)toast('The thumbnail was not made: '+bad,true);
  else{toast(how==='none'?'Thumbnail removed':'Thumbnail made');if(how!=='prompt')coverText.delete(key)}
  loadClips();
}
async function holdApproved(n){
  if(!confirm('Move all '+n+' approved clip'+(n>1?'s':'')+' to Pending?'+NL+NL+'They are not made into videos until you approve them again. Nothing is deleted, and they are kept apart from To review.'))return;
  const r=await post('/api/clips/hold-approved'); if(!r)return;
  for(const el of $('clips').children)el.dataset.m='';
  const stuck=r.stuck||[];
  toast(r.moved+' clip'+(r.moved===1?'':'s')+' moved to Pending'+(stuck.length?'. '+stuck.length+' could not be moved: '+stuck[0]:''),stuck.length>0); loadClips();
}
/* The videos of one approved clip, without the others. */
async function makeOne(key,again){
  const el=$(domId(key));
  if(again&&!confirm('Make this finished clip\'s videos again?'+NL+NL+'Its files in the videos folder are replaced. If you changed the caption look or the opening effects since, it is made again with the new ones.'))return;
  if(el&&el.dataset.dirty==='1'){toast('This clip has unsaved changes. Press Save changes first, then make its videos.',true);return}
  if(document.activeElement)document.activeElement.blur();   /* a focused button would keep the card from being redrawn when it is done */
  const inEditor=openKey===key;
  if(await act('/api/finish',{only:key})&&inEditor&&openKey===key){closeClip();toast('Making this clip\'s videos. The progress is at the top of My clips.')}
}
/* Stop making videos: the person chooses between not stopping, after this video, and now. */
function stopText(s){const fp=s.finish_progress||{};
  $('stop-what').textContent=(fp.total?'Video '+Math.min(fp.total,fp.done+1)+' of '+fp.total+' is being made'+(fp.now?': '+fp.now:'')+'. '+Math.max(0,fp.total-fp.done-1)+' more wait after it.':'The first video is being started.')
    +(s.finish_stopping==='after'?' It is already set to stop after this video.':'');
  $('stop-after').disabled=s.finish_stopping==='after'}
function stopAsk(open){if(open){if(!last.finishing){toast('No videos are being made right now.',true);return}stopText(last)}$('stopbox').hidden=!open;if(open)$('stop-cancel').focus()}
async function finishStop(how){$('stopbox').hidden=true;
  if(await act('/api/finish/stop',{how:how}))toast(how==='now'?'Stopping now. Videos already made are kept.':'It stops after the video being made.')}
async function openReady(){const r=await send('/api/ready/open');if(r.error)toast(r.error,true)}
/* What Esc closes is decided before any handler runs, so one press never closes two things. */
let escTop='';
window.addEventListener('keydown',e=>{if(e.key==='Escape')escTop=!$('stopbox').hidden?'stop':!$('boxes').hidden?'boxes':!$('spot').hidden?'spot':!$('helpbox').hidden?'help':!$('drawer').hidden?'drawer':openKey?'clip':''},true);
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&escTop==='stop')stopAsk(false)});
async function clearErr(key){if(await post('/api/clip/'+key+'/clear-error')){const el=$(domId(key));if(document.activeElement)document.activeElement.blur();if(el)el.dataset.m='';loadClips()}}
function showCover(key,btn){const el=$(domId(key)),v=el.querySelector('.vid video'),c=known.find(x=>x.key===key)||{};
  v.pause();v.removeAttribute('src');v.poster='/media/'+key+'/cover.jpg?v='+((c.cover||{}).mtime||0);v.load();
  for(const b of btn.parentElement.children)b.classList.toggle('on',b===btn)}
function showVid(key,name,btn){const el=$(domId(key)),v=el.querySelector('.vid video');const c=known.find(x=>x.key===key)||{};
  v.removeAttribute('poster');v.src='/media/'+key+'/'+name+'?v='+c.mtime;for(const b of btn.parentElement.children)b.classList.toggle('on',b===btn);v.play().catch(()=>{})}
async function makeB(key,btn){btn.disabled=true;btn.textContent='Making B…';
  if(await post('/api/clip/'+key+'/render-b')){const el=$(domId(key));if(document.activeElement)document.activeElement.blur();if(el)el.dataset.m='';toast('Version B is ready');loadClips()}else{btn.disabled=false;btn.textContent='Make B preview'}}

/* Clips with a long request running (key -> what is being done). It is drawn on the card, so the note and the
   switched-off buttons survive the card being redrawn, and a second press on the same clip is refused. */
const WORK=new Map();
function workStart(key,text){
  if(WORK.has(key)){toast('Wait a moment: '+WORK.get(key),true);return false}
  WORK.set(key,text);
  const el=$(domId(key));if(el){for(const b of el.querySelectorAll('.frow button,.wrow button'))b.disabled=true;for(const n of el.querySelectorAll('.frow span,.wrow span'))n.textContent=text}
  return true}
/* The request is over. What was typed on the card meanwhile is saved, not thrown away; otherwise the card is drawn again. */
async function workEnd(key){
  WORK.delete(key);
  const el=$(domId(key));
  if(el&&el.dataset.dirty==='1'&&await save(key))return;
  if(el){if(el.dataset.dirty!=='1'){if(el.contains(document.activeElement))document.activeElement.blur();el.dataset.m=''}
    else for(const b of el.querySelectorAll('.frow button,.wrow button'))b.disabled=false}   /* the save was refused: the card stays as typed, usable again */
  await loadClips()}
/* The picture of one clip, in one click: automatic framing, or the whole stream picture on a blurred copy of itself. */
async function quickFrame(key,mode,btn){
  if(btn.classList.contains('on')&&mode==='whole')return;
  if(WORK.has(key)){toast('Wait a moment: '+WORK.get(key),true);return}
  const card=$(domId(key));
  if(card&&card.dataset.dirty==='1'&&!(await save(key)))return;   /* what was typed is saved first */
  if(!workStart(key,'Making the draft again… up to a minute'))return;
  for(const o of document.querySelectorAll('#clips video'))o.pause();   /* its video file is about to be replaced */
  /* keep: the boxes remembered for this streamer stay; only this clip goes back to what the program chooses */
  const r=await send('/api/clip/'+key+'/framing',mode==='whole'?{layout:'blur',game:[0,0,1,1]}:{auto:true,keep:true});
  await workEnd(key);
  if(r.error)toast((/^the boxes are saved/i.test(r.error)?'':'The picture was not changed: ')+r.error,true);
  else toast(mode==='whole'?'This clip now shows the whole picture, with blur above and below. The draft is made again.':'Back to the automatic picture. The draft is made again.')}
/* More of the stream in front of or behind one clip, so its start or end can be dragged further out. */
async function widen(key,before,after,btn){
  if(WORK.has(key)){toast('Wait a moment: '+WORK.get(key),true);return}
  const card=$(domId(key));
  if(card&&card.dataset.dirty==='1'&&!(await save(key)))return;
  if(!workStart(key,'Downloading more of the stream… a minute or two'))return;
  /* Windows cannot replace a video file that a player still has open */
  {const el=$(domId(key));if(el)for(const v of el.querySelectorAll('video')){v.pause();v.removeAttribute('src');v.load()}}
  const r=await send('/api/clip/'+key+'/widen',{before:before,after:after});
  await workEnd(key);
  if(r.error)toast((/^the seconds were added/i.test(r.error)?'':'No more of the stream was added: ')+r.error,true);
  else toast('Added. The clip itself did not move: drag its start or end into the new part, then press Save changes.')}
/* ---------- trimmer: drag the start and end over the sound wave ---------- */
async function initTrim(key){
  const el=$(domId(key)), box=el.querySelector('.trim'), c=known.find(x=>x.key===key);
  if(!box||box.dataset.ready||!c||!(c.videos||[]).includes('source.mp4'))return;
  box.dataset.ready='1'; box.innerHTML='<p class="help">Loading the sound wave…</p>';
  let w;try{const r=await fetch('/api/clip/'+key+'/wave');if(!r.ok){box.innerHTML='<p class="help">The bar for start and end cannot be shown: '+esc(await r.text())+'</p>';return}w=await r.json()}
  catch(e){box.innerHTML='<p class="help">The bar for start and end could not load ('+esc(e)+').</p>';return}
  box.innerHTML='<video controls preload="metadata" src="/media/'+key+'/source.mp4?v='+c.mtime+'"></video>'
    +'<div class="tl"><canvas></canvas><div class="sel"></div><div class="ph"></div><div class="h hs" title="Drag: where the clip starts"></div><div class="h he" title="Drag: where the clip ends"></div></div>'
    +'<div class="btns" style="margin-top:8px"><button class="btn" data-a="play">Play the chosen part</button><span class="tinfo" style="font-size:12.5px;color:var(--muted)"></span></div>'
    +'<p class="help" style="margin-top:6px">Drag the two handles to set where the clip starts and ends, or click the sound wave to listen from there. This is the video as it was downloaded, without captions. Press Save changes when it is right.</p>';
  const total=w.seconds, base=Number(c.fetch_start_s)||0, tl=box.querySelector('.tl'), cv=tl.querySelector('canvas'), v=box.querySelector('video');
  const iS=el.querySelector('[name=cut_start_s]'), iE=el.querySelector('[name=cut_end_s]');
  const get=()=>[Math.max(0,parseFloat(iS.value)-base),Math.min(total,parseFloat(iE.value)-base)];
  function draw(){const [a,b]=get(), W=tl.clientWidth, H=tl.clientHeight, dpr=window.devicePixelRatio||1;
    cv.width=W*dpr;cv.height=H*dpr;const g=cv.getContext('2d');g.scale(dpr,dpr);g.clearRect(0,0,W,H);
    const css=getComputedStyle(document.documentElement), on=css.getPropertyValue('--accent').trim(), off=css.getPropertyValue('--muted').trim();
    const n=w.peaks.length, bw=W/n;for(let i=0;i<n;i++){const t=i/n*total, h=Math.max(1,w.peaks[i]*(H-8));g.fillStyle=(t>=a&&t<=b)?on:off;g.globalAlpha=(t>=a&&t<=b)?1:.45;g.fillRect(i*bw,(H-h)/2,Math.max(1,bw-.6),h)}
    const sel=tl.querySelector('.sel');sel.style.left=(a/total*100)+'%';sel.style.width=((b-a)/total*100)+'%';
    tl.querySelector('.hs').style.left=(a/total*100)+'%';tl.querySelector('.he').style.left=(b/total*100)+'%';
    box.querySelector('.tinfo').textContent='Clip length: '+(b-a).toFixed(1)+' s (downloaded: '+total.toFixed(0)+' s)'}
  function setVal(inp,t){inp.value=(Math.round((base+t)*2)/2).toFixed(1);inp.dispatchEvent(new Event('input',{bubbles:true}))}
  function drag(which){return e=>{e.preventDefault();e.stopPropagation();
    const move=ev=>{const r=tl.getBoundingClientRect(), t=Math.max(0,Math.min(total,(ev.clientX-r.left)/r.width*total)), [a,b]=get();
      if(which==='s')setVal(iS,Math.min(t,b-3));else setVal(iE,Math.max(t,a+3));draw()};
    const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up)};
    window.addEventListener('pointermove',move);window.addEventListener('pointerup',up)}}
  tl.querySelector('.hs').addEventListener('pointerdown',drag('s'));tl.querySelector('.he').addEventListener('pointerdown',drag('e'));
  tl.addEventListener('click',e=>{if(e.target.classList.contains('h'))return;const r=tl.getBoundingClientRect();v.currentTime=(e.clientX-r.left)/r.width*total;v.play().catch(()=>{})});
  let stopAt=null;const ph=tl.querySelector('.ph');
  v.addEventListener('timeupdate',()=>{ph.style.display='block';ph.style.left=(v.currentTime/total*100)+'%';if(stopAt!=null&&v.currentTime>=stopAt){v.pause();stopAt=null}});
  box.querySelector('[data-a=play]').addEventListener('click',()=>{const [a,b]=get();v.currentTime=a;stopAt=b;v.play().catch(()=>{})});
  v.addEventListener('pause',()=>{stopAt=null});
  iS.addEventListener('input',draw);iE.addEventListener('input',draw);
  const onSize=()=>{if(document.body.contains(tl))draw();else window.removeEventListener('resize',onSize)};window.addEventListener('resize',onSize);draw();
}

/* ---------- captions: edit the words ---------- */
async function initWords(key){
  const el=$(domId(key)), box=el.querySelector('.words'), c=known.find(x=>x.key===key);
  if(!box||box.dataset.ready||!c)return; box.dataset.ready='1';
  let words;try{const r=await fetch('/api/clip/'+key+'/words');if(!r.ok){box.textContent='The captions could not be read: '+await r.text();return}words=await r.json()}catch(e){box.textContent='The captions could not be read ('+e+').';return}
  el._words=words; const base=Number(c.fetch_start_s)||0, a=(c.cut_start_s??c.start_s)-base-0.5, b=(c.cut_end_s??c.end_s)-base+0.5;
  box.innerHTML=words.map((w,i)=>(w.start>=a&&w.start<=b)?'<input type="text" data-i="'+i+'" value="'+esc(w.word)+'" size="'+Math.max(2,String(w.word).length)+'" aria-label="caption word">':'').join('')
    ||'<span class="help">No words were heard in this part of the clip.</span>';
}
async function subFile(inp,key){
  const f=inp.files[0];inp.value='';if(!f)return;
  if(!confirm('Replace this clip\'s captions with the subtitles in '+f.name+'?'))return;
  let r;try{r=await fetch('/api/clip/'+key+'/captions-import',{method:'POST',body:await subText(f)})}catch(e){toast('The program is not answering: '+e,true);return}
  if(!r.ok){toast('The subtitle file was not used: '+await r.text(),true);return}
  const d=await r.json(), el=$(domId(key));if(document.activeElement)document.activeElement.blur();if(el){el.dataset.dirty='';el.dataset.wdirty='';el.dataset.m='';el._words=null}
  toast(d.lines+' subtitle lines are now this clip\'s captions. The draft is made again when you press Make videos.');loadClips();
}
/* A subtitle file's text, whatever it was saved as: "Unicode" (UTF-16) from older Notepad, UTF-8, or the old Windows letters. */
async function subText(f){
  const b=new Uint8Array(await f.arrayBuffer());
  if(b[0]===0xFF&&b[1]===0xFE)return new TextDecoder('utf-16le').decode(b.subarray(2));
  if(b[0]===0xFE&&b[1]===0xFF)return new TextDecoder('utf-16be').decode(b.subarray(2));
  try{return new TextDecoder('utf-8',{fatal:true}).decode(b)}catch(e){return new TextDecoder('windows-1252').decode(b)}
}
async function sendWords(key){
  const el=$(domId(key)); if(!el||!el._words)return true;
  for(const inp of el.querySelectorAll('.words input'))el._words[+inp.dataset.i].word=inp.value;
  let r;try{r=await fetch('/api/clip/'+key+'/words',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({words:el._words})})}catch(e){toast('The program is not answering: '+e,true);return false}
  if(!r.ok){toast('Captions not saved: '+await r.text(),true);return false}
  el.dataset.wdirty='';return true;
}
async function saveWords(key){
  const el=$(domId(key)); if(!el._words){toast('Open the captions first.',true);return}
  el.dataset.wdirty='1';
  if(await save(key))  /* saves the caption words and anything else you typed on this card, so nothing is thrown away */
    toast('Captions saved. The draft is made again when you press Make videos, or use "Make B preview".');
}

/* ---------- template, opening text, moment mix, safe zones ---------- */
function drawFrame(s){
  const f=s.frame||{}, tp=$('t-tpl'), bg=$('t-bg');
  if(!tp.options.length&&f.templates){tp.innerHTML=Object.entries(f.templates).map(([k,t])=>'<option value="'+k+'">'+esc(t)+'</option>').join('');
    bg.innerHTML=Object.entries(f.backgrounds).map(([k,t])=>'<option value="'+k+'">'+esc(t)+'</option>').join('');
    $('r-mix').innerHTML=(s.moment_mixes||[]).map(k=>'<option value="'+k+'">'+({balanced:'Balanced: a bit of everything',action:'Action: loud, fast, shouted',dialogue:'Dialogue: stories, jokes, what is said'}[k]||k)+'</option>').join('')}
  if(!$('tplbox').contains(document.activeElement)){tp.value=f.template||'none';bg.value=f.card_bg||'blur';$('t-c1').value=hex(f.card_color||'151225');$('t-c2').value=hex(f.card_color2||'FF3D8B');
    $('t-name').value=f.post_name||'';$('t-handle').value=f.post_handle||'';$('t-hook').value=s.hook_seconds||3;$('t-hookv').textContent=Math.round(s.hook_seconds||3)}
  const t=tp.value, b=bg.value;
  $('t-card').hidden=t!=='card';$('t-post').hidden=!t.startsWith('xpost');$('t-cols').hidden=!(b==='color'||b==='gradient');$('t-c2w').hidden=b!=='gradient';$('t-img').hidden=b!=='image';
  $('t-imgname').textContent=f.card_image_name?'In use: '+f.card_image_name:'No image chosen';
  if(document.activeElement!==$('r-mix'))$('r-mix').value=s.moment_mix||'balanced';
  const b2=s.brand||{};if(!$('brandbox').contains(document.activeElement)){$('g-rise').value=Math.round(b2.music_rise_s||0);$('g-risev').textContent=Math.round(b2.music_rise_s||0)}
}
function saveFrame(){if(document.activeElement)document.activeElement.blur();
  act('/api/options',{template:val('t-tpl'),card_bg:val('t-bg'),card_color:val('t-c1').slice(1),card_color2:val('t-c2').slice(1),post_name:val('t-name'),post_handle:val('t-handle')})}
/* Where each app covers the video with its own buttons and text. Shown over the previews only; never in the video. */
const ZONES={tiktok:{top:8,bottom:22,right:15},reels:{top:10,bottom:24,right:14},shorts:{top:8,bottom:20,right:13}};
function setZones(){const z=val('z-pick');try{localStorage.setItem('zones',z)}catch(e){}
  const c=$('clips');c.dataset.zones=z;const k=ZONES[z];
  if(k){c.style.setProperty('--zt',k.top+'%');c.style.setProperty('--zb',k.bottom+'%');c.style.setProperty('--zr',k.right+'%')}}
async function remakeAll(key){
  if(!confirm('Make the drafts of every unfinished clip of this video again, with the caption style, template, logo and music chosen now?'+NL+NL+'This takes about half a minute per clip. Their text, start and end, and boxes are kept.'))return;
  const r=await send('/api/clip/'+key+'/remake-all'); if(r.error){toast(r.error,true);return}
  toast(r.clips+' draft'+(r.clips===1?' is':'s are')+' being made again. Each card updates when its draft is ready.');
}

/* ---------- caption looks and branding ---------- */
let lookSeen='';
function hex(c){return '#'+String(c||'FFFFFF').replace('#','')}
function drawLooks(s){
  const pre=s.caption_presets||{}, mine=s.caption_profiles||{}, now=s.caption_now||{}, sel=$('o-cap');
  const sig=JSON.stringify([Object.keys(pre),Object.keys(mine)]);
  if(sig!==sel.dataset.sig){sel.dataset.sig=sig;
    sel.innerHTML=Object.entries(pre).map(([k,p])=>'<option value="'+k+'">'+esc(p.label)+'</option>').join('')
      +Object.keys(mine).map(k=>'<option value="mine:'+esc(k)+'">Your saved style: '+esc(k)+'</option>').join('')+'<option value="">Your own style (set below)</option>';
    $('o-anim').innerHTML=Object.entries(s.caption_animations||{}).map(([k,t])=>'<option value="'+k+'">'+esc(t)+'</option>').join('')}
  const cur=s.caption_preset in pre?s.caption_preset:(s.caption_preset in mine?'mine:'+s.caption_preset:'');
  if(document.activeElement!==sel)sel.value=cur;
  if(document.activeElement!==$('o-anim'))$('o-anim').value=now.animation||'highlight';
  $('k-del').hidden=!(s.caption_preset in mine);
  /* The form follows the style in use, unless you are in the middle of changing it. */
  const key=JSON.stringify(now);
  if(key!==lookSeen&&!$('lookbox').contains(document.activeElement)){lookSeen=key;
    $('k-font').value=now.font||'';fontPick(s,now.font);$('k-size').value=now.font_size||88;$('k-outline').value=now.outline??6;$('k-words').value=now.max_words||3;
    $('k-text').value=hex(now.text_color);$('k-active').value=hex(now.active_color);$('k-upper').checked=!!now.uppercase}
  const cp=$('capprev'), t=now.uppercase?['NO WAY','HE','DID THAT']:['No way','he','did that'];
  const bf=fontOf(s,now.font);if(bf)fontLoad(bf);cp.style.fontFamily='"'+(now.font||'Arial Black')+'",Arial,sans-serif';cp.style.fontWeight=bf&&bf.weight<600?'normal':900;cp.style.webkitTextStroke=Math.max(1,(now.outline||0)/4)+'px #000';cp.style.paintOrder='stroke fill';
  cp.innerHTML='<span style="color:'+hex(now.text_color)+'">'+t[0]+' </span><span style="color:'+hex(now.active_color)+';font-size:112%">'+t[1]+'</span><span style="color:'+hex(now.text_color)+'"> '+t[2]+'</span>';
  const b=s.brand||{};
  if(!$('brandbox').contains(document.activeElement)){$('g-corner').value=b.logo_corner||'top-left';$('g-size').value=Math.round((b.logo_size||.16)*100);$('g-op').value=Math.round((b.logo_opacity||.9)*100);
    $('g-outro').value=b.outro_text||'';$('g-vol').value=Math.round((b.music_volume||0)*100)}
  $('g-logoname').textContent=b.logo_name?'In use: '+b.logo_name:'No logo';$('g-logooff').hidden=!b.logo_name;
  $('g-musicname').textContent=b.music_name?'In use: '+b.music_name:'No music';$('g-musicoff').hidden=!b.music_name;
  posDraw(s);voiceDraw(s);keysDraw(s);modelDraw(s);
}
/* ---------- the fonts that come with the program ---------- */
const FL=new Set();
function fontOf(s,name){const n=String(name||'').toLowerCase();return ((s&&s.fonts)||[]).find(f=>f.family.toLowerCase()===n||f.label.toLowerCase()===n)}
/* the caption sample on the page is drawn in the font itself: it is fetched from the program, once */
function fontLoad(f){if(FL.has(f.family))return;FL.add(f.family);
  try{new FontFace(f.family,'url(/fonts/'+encodeURIComponent(f.file)+')').load().then(x=>document.fonts.add(x)).catch(e=>console.warn('font not shown on the page',f.family,e))}catch(e){}}
function fontPick(s,name){const sel=$('k-pick'),KIND={sans:'Bold and clean',condensed:'Tall and narrow',rounded:'Round and friendly',comic:'Comic',marker:'Hand-written',display:'Showy'};
  if(!sel.options.length){const fs=(s&&s.fonts)||[];
    sel.innerHTML='<option value="">'+(fs.length?'Choose one of the '+fs.length+' fonts…':'No fonts came with this copy')+'</option>'
      +fs.map(f=>'<option value="'+esc(f.family)+'">'+esc(f.label)+(KIND[f.kind]?' · '+KIND[f.kind]:'')+'</option>').join('')}
  const f=fontOf(s,name);sel.value=f?f.family:''}
function pickFont(){const v=val('k-pick');if(!v)return;$('k-font').value=v;useLook()}
/* ---------- where the captions sit ---------- */
function posText(){const up=+val('p-up'),side=+val('p-side');
  $('p-upv').textContent=Math.round(up/1920*100)+'% up from the bottom';
  $('p-sidev').textContent=side===0?'in the middle':Math.round(Math.abs(side)/1080*100)+'% to the '+(side<0?'left':'right')}
function posDraw(s){const p=s.caption_position;if(!p||document.activeElement===$('p-up')||document.activeElement===$('p-side'))return;
  $('p-up').value=p.margin_v;$('p-side').value=p.offset_x||0;posText()}
function savePos(){if(document.activeElement)document.activeElement.blur();act('/api/options',{caption_position:{margin_v:+val('p-up'),offset_x:+val('p-side')}})}
/* ---------- Settings: which speech model writes the captions ---------- */
function modelDraw(s){const sel=$('l-model');if(!s.speech_model)return;
  if(document.activeElement!==sel){if(![...sel.options].some(o=>o.value===s.speech_model))sel.insertAdjacentHTML('beforeend','<option value="'+esc(s.speech_model)+'">'+esc(s.speech_model)+' (set in the settings file)</option>');sel.value=s.speech_model}
  $('l-modelnote').innerHTML=s.speech_model_there?'This speech model is on this computer.'
    :'<b style="color:var(--warn)">This speech model is not on this computer yet.</b> Open <b>Set up</b>, go to the Captions step and press <b>Download and try it</b>. Until then no clips can be made.'}
function saveModel(){if(document.activeElement)document.activeElement.blur();act('/api/options',{speech_model:val('l-model')})}
/* ---------- Settings: keys of paid services, and disk space ---------- */
function keysDraw(s){const k=s.keys||{};
  for(const n of ['claude','elevenlabs','groq']){const st=$('keyst-'+n);st.textContent=k[n]?'· a key is saved':'· no key';st.style.color=k[n]?'var(--ok)':'';$('keyrm-'+n).hidden=!k[n]}
  const c=s.cleanup||{};if(document.activeElement!==$('cl-rej'))$('cl-rej').value=String(c.rejected_days??14);if(document.activeElement!==$('cl-fin'))$('cl-fin').value=String(c.finished_days||0)}
/* the key goes straight to the program on this computer; the box is emptied at once and the key is never drawn on the page again */
async function saveKey(n,remove){const inp=$('key-'+n), v=remove?'':inp.value.trim();
  if(!remove&&!v){toast('Paste the key into the box first.',true);inp.focus();return}
  if(remove&&!confirm('Remove this key from this computer?'))return;
  if(document.activeElement)document.activeElement.blur();
  const ok=await act('/api/keys/save',{name:n,value:v});inp.value='';
  if(ok)toast(remove?'The key was removed from this computer.':'Key saved on this computer. '+(n==='elevenlabs'?'Press Check ElevenLabs under Narrator voice to try it.':n==='claude'?'Press Check again under AI to try it.':''))}
function mbText(mb){return mb>=1000?(mb/1000).toFixed(1)+' GB':Math.round(mb)+' MB'}
async function diskDraw(){let d;try{const r=await fetch('/api/disk');if(!r.ok){$('disk-now').textContent='The space could not be counted: '+await r.text();return}d=await r.json()}catch(e){$('disk-now').textContent='The space could not be counted: '+e;return}
  $('disk-now').textContent='Downloads and drafts take '+mbText(d.work)+' on this computer, finished videos '+mbText(d.ready)+'. Clearing rejected clips would free '+mbText(d.rejected)
    +(d.finished?', clearing finished clips\' downloads '+mbText(d.finished):'')+'. The bin holds '+mbText(d.bin)+' (kept '+d.bin_days+' days).'}
async function cleanNow(what){
  if(what==='bin'&&!confirm('Empty the bin now?'+NL+NL+'Every clip in it is removed for good.'))return;
  if(what==='rejected'&&!confirm('Clear the downloads and drafts of every rejected clip now?'+NL+NL+'The clips stay in the Rejected list, but they cannot be played or brought back to review any more.'))return;
  const r=await send('/api/clean',{what:what});if(r.error){toast(r.error,true);return}
  show(r);toast(r.cleaned||'Done.');if(curPage==='settings')diskDraw();for(const el of document.querySelectorAll('#clips .clip'))el.dataset.m='';loadClips()}
/* ---------- the narrator of explainer videos ---------- */
const VOICE={audio:null,seen:''};
function voiceDraw(s){const v=s.voice;if(!v)return;const at=document.activeElement, c=v.check;
  /* the free model has other voices for Hindi and Urdu than for English: the list follows what the narrator speaks */
  if(VOICE.lang!==v.language||!$('v-kokoro').options.length){VOICE.lang=v.language;$('v-kokoro').innerHTML=v.kokoro_voices.map(x=>'<option value="'+esc(x[0])+'">'+esc(x[1])+'</option>').join('')}
  if(at!==$('v-lang'))$('v-lang').value=v.language;
  const ln=$('v-langnote');ln.hidden=v.language==='English';
  if(v.language!=='English')ln.innerHTML='The narrator reads '+(v.language==='Hindi'?'Hindi letters':'Urdu letters')+' only. With AI switched on, the narrator\'s lines of new clips are written that way by themselves; '
    +'for a clip you already have, press <b>Write again with AI</b> or type the line yourself (clip, Versions, What the narrator says). A line in English letters is refused with a message, never spoken wrongly. '
    +'<b>Press Listen</b> to hear whether you like the voice: it was checked by a speech model, not yet by a person.'
    +(v.language==='Urdu'?' Urdu writing leaves out short vowels, so an English word written in Urdu letters can be said a little wrong; Hindi is read more reliably.':'')
    +(v.backend==='elevenlabs'&&v.english_only?' <b style="color:var(--bad)">The ElevenLabs model chosen speaks English only: choose Multilingual v2 below.</b>':'');
  if(at!==$('v-back'))$('v-back').value=v.backend;
  if(at!==$('v-kokoro'))$('v-kokoro').value=v.kokoro_voice;
  if(at!==$('v-speed')){$('v-speed').value=v.speed;$('v-speedv').textContent=v.speed+'×'}
  $('v-local').hidden=v.backend!=='kokoro';$('v-eleven').hidden=v.backend!=='elevenlabs';
  const key=JSON.stringify([c&&c.at,c&&c.ok,v.eleven_model,v.eleven_voice]);
  if(key!==VOICE.seen&&at!==$('v-emodel')&&at!==$('v-evoice')){VOICE.seen=key;
    const ms=(c&&c.ok&&c.models.length?c.models.slice():[]);if(!ms.some(m=>m.id===v.eleven_model))ms.push({id:v.eleven_model,name:v.eleven_model});
    $('v-emodel').innerHTML=ms.map(m=>'<option value="'+esc(m.id)+'">'+esc(m.name)+(c&&c.ok&&m.id===c.best_model?' · recommended':'')+'</option>').join('');$('v-emodel').value=v.eleven_model;
    const vs=(c&&c.ok&&c.voices.length?c.voices.slice():[]);if(!vs.some(x=>x.id===v.eleven_voice))vs.unshift({id:v.eleven_voice,name:'The voice in your settings',about:''});
    $('v-evoice').innerHTML=vs.map(x=>'<option value="'+esc(x.id)+'">'+esc(x.name)+(x.about?' ('+esc(x.about.replace(/_/g,' '))+')':'')+'</option>').join('');$('v-evoice').value=v.eleven_voice}
  $('v-state').innerHTML=!v.key?'<b>No ElevenLabs key is set.</b> Paste your key under Keys on this page and press Save, then press Check ElevenLabs.'
    :!c?'A key is set. Press <b>Check ElevenLabs</b> to see whether it works and to fill the two lists.'
    :c.ok?'<b style="color:var(--ok)">ElevenLabs answers</b> (asked at '+esc(c.at)+').'+(c.left!=null?' Characters left on your plan: '+Number(c.left).toLocaleString()+'.':'')+(c.note?' '+esc(c.note):'')+(c.changed?' '+esc(c.changed):'')
    :'<b style="color:var(--bad)">ElevenLabs cannot be used:</b> '+esc(c.why);
  $('v-help').textContent=v.backend==='elevenlabs'?'Each explainer video uses characters of your ElevenLabs plan, about one for every letter spoken. Listen uses about 60.'+(v.language==='English'?' Speech is in English.':' Speech is in '+v.language+', with a many-language model such as Multilingual v2.')
    :'The free voice costs nothing and works without the internet. Speech is in '+v.language+'.'}
async function voiceCheck(use){if(document.activeElement)document.activeElement.blur();$('b-vcheck').disabled=true;
  const ok=await act('/api/voice/check',{use:!!use});$('b-vcheck').disabled=false;
  if(!ok)poll1();else if(use)toast('The narrator now speaks with ElevenLabs, with the best model it offers. You can change the model and the voice below.')}
function voiceLang(){if(document.activeElement)document.activeElement.blur();act('/api/options',{voice_language:val('v-lang')})}
function voiceBack(){if(val('v-back')==='elevenlabs')voiceCheck(true);else{if(document.activeElement)document.activeElement.blur();act('/api/options',{voice_backend:'kokoro'})}}
async function voiceListen(btn){
  if(last&&last.voice&&last.voice.backend==='elevenlabs'&&!confirm('Listen to the ElevenLabs voice?'+NL+NL+'It uses about 60 characters of your ElevenLabs plan.'))return;
  const was=btn.textContent;btn.disabled=true;btn.textContent='Making the sample…';
  try{const r=await fetch('/api/voice/sample',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(!r.ok)toast('The voice could not be played: '+await r.text(),true);
    else{const u=URL.createObjectURL(await r.blob());if(VOICE.audio)VOICE.audio.pause();VOICE.audio=new Audio(u);VOICE.audio.onended=()=>URL.revokeObjectURL(u);await VOICE.audio.play()}}
  catch(e){toast('The voice could not be played: '+e,true)}
  btn.disabled=false;btn.textContent=was}
function lookForm(){return {font:val('k-font').trim(),font_size:+val('k-size'),outline:+val('k-outline'),max_words:+val('k-words'),text_color:val('k-text').slice(1),active_color:val('k-active').slice(1),
  uppercase:$('k-upper').checked,animation:val('o-anim')}}
function pickLook(){const v=val('o-cap');
  if(v.startsWith('mine:')){const name=v.slice(5),p=(last.caption_profiles||{})[name];if(p)act('/api/options',{caption_look:Object.assign({},p,{name:name})});return}
  if(v)act('/api/options',{caption_preset:v});else{$('lookbox').open=true;$('k-font').focus()}}
function useLook(asProfile){
  const look=lookForm();
  if(asProfile){const name=(prompt('A name for this caption style:',last.caption_preset in (last.caption_profiles||{})?last.caption_preset:'')||'').trim();if(!name)return;
    look.name=name;if(document.activeElement)document.activeElement.blur();act('/api/options',{caption_profile_save:look});return}
  if(document.activeElement)document.activeElement.blur();act('/api/options',{caption_look:look});
}
function delLook(){const name=last.caption_preset;if(!confirm('Delete your saved caption style "'+name+'"? Clips already made keep their look.'))return;
  act('/api/options',{caption_profile_delete:name,caption_preset:'bold'})}
function saveBrand(){if(document.activeElement)document.activeElement.blur();
  act('/api/options',{brand_logo_corner:val('g-corner'),brand_logo_size:val('g-size')/100,brand_logo_opacity:val('g-op')/100,brand_outro_text:val('g-outro'),brand_music_volume:val('g-vol')/100,brand_music_rise_s:+val('g-rise')})}
async function brandFile(inp,kind){
  const f=inp.files[0];inp.value='';if(!f)return;
  try{const r=await fetch('/api/brand/upload?kind='+kind+'&name='+encodeURIComponent(f.name),{method:'POST',body:f});
    if(!r.ok){toast('The file could not be copied: '+await r.text(),true);return}
    show(await r.json());if(curPage==='look')lookSoon();toast(kind==='logo'?'Logo set. It is added to drafts and videos made from now on.':'Music set. It is added to drafts and videos made from now on.')}
  catch(e){toast('The program is not answering: '+e,true)}
}

/* ---------- face box and video box ---------- */
let BX=null;
function openBoxes(key){
  if(!$('boxes').hidden)return;
  if(document.activeElement)document.activeElement.blur();
  const c=known.find(x=>x.key===key);
  if(!c||!c.framing||!(c.videos||[]).includes('source.mp4')){toast('This clip is not downloaded yet, so its boxes cannot be set.',true);return}
  const f=c.framing, now=f.boxes[f.layout]||{};
  /* One face box and one video box. Each takes the shape the chosen layout shows it in, so what is inside is what is seen. */
  BX={key:key,layout:f.layout,sw:f.source[0],sh:f.source[1],shapes:f.shapes,top:f.split_top,corner:f.face_corner,size:f.face_size,
    face:(now.face||f.boxes.split.face||[0,0,.25,.25]).slice(),video:(now.video||f.boxes.crop.video).slice()};
  $('bx-layout').innerHTML=Object.entries(f.names).map(([k,t])=>'<option value="'+k+'">'+esc(t)+'</option>').join('');
  $('bx-corner').innerHTML=f.corners.map(k=>'<option value="'+k+'">'+k.replace('-',' ')+'</option>').join('');
  $('bx-layout').value=BX.layout;$('bx-corner').value=BX.corner;$('bx-fsize').value=Math.round(BX.size*100);
  $('bx-others').checked=false;$('bx-streamer').checked=false;$('bx-note').textContent='';
  $('bx-strow').hidden=!c.channel;$('bx-stlabel').textContent='Remember these boxes for new clips of '+(c.channel||'');
  const v=$('bx-video');v.src='/media/'+key+'/source.mp4?v='+c.mtime;
  v.onloadedmetadata=()=>{v.currentTime=Math.max(0,((c.cut_start_s??c.start_s)-(Number(c.fetch_start_s)||0))+1)};v.onended=()=>{$('bx-play').textContent='Play'};
  $('bx-stage').style.aspectRatio=BX.sw+' / '+BX.sh;$('bx-play').textContent='Play';
  for(const b of ['bx-save','bx-auto','bx-cancel'])$(b).disabled=false;
  $('boxes').hidden=false;bxLayout();bxLoop();
}
function bxHasFace(){return !!BX.shapes[BX.layout].face}
function bxAsp(which){return BX.shapes[BX.layout][which]}
/* Height of a box (as a share of the picture) that goes with its width, for the shape it is shown in. */
function bxH(w,asp){return w*BX.sw/(BX.sh*asp)}
function bxFit(b,asp){let w=b[2],h=bxH(w,asp);if(h>1){h=1;w=asp*BX.sh/BX.sw}if(w>1){w=1;h=bxH(1,asp)}
  const cx=b[0]+b[2]/2,cy=b[1]+b[3]/2;return [Math.min(Math.max(0,cx-w/2),1-w),Math.min(Math.max(0,cy-h/2),1-h),w,h]}
/* The layout changed: bring both boxes to the shapes it shows them in. */
function bxLayout(){
  if(BX.layout==='blur'){if(!BX.kept)BX.kept=BX.video.slice();BX.video=bxFit([0,0,1,1],bxAsp('video'))}
  else{if(BX.kept){BX.video=BX.kept;BX.kept=null}BX.video=bxFit(BX.video,bxAsp('video'))}
  if(bxHasFace())BX.face=bxFit(BX.face,bxAsp('face'));
  $('bx-small').hidden=!(BX.layout==='pip'||BX.layout==='circle');
  bxDraw();
}
function bxDraw(){
  const put=(el,b)=>{el.style.left=(b[0]*100)+'%';el.style.top=(b[1]*100)+'%';el.style.width=(b[2]*100)+'%';el.style.height=(b[3]*100)+'%'};
  put($('bx-v'),BX.video);put($('bx-f'),BX.face);$('bx-f').hidden=!bxHasFace();$('bx-f').style.borderRadius=BX.layout==='circle'?'50%':'0';
  $('bx-size').textContent='Video box: '+Math.round(BX.video[2]*BX.sw)+' × '+Math.round(BX.video[3]*BX.sh)+' of '+BX.sw+' × '+BX.sh
    +(bxHasFace()?' · Face box: '+Math.round(BX.face[2]*BX.sw)+' × '+Math.round(BX.face[3]*BX.sh):'');
}
/* The small picture on the right: the same arrangement the finished clip gets. */
function bxLoop(){
  if(!BX)return;const v=$('bx-video'),cv=$('bx-prev'),g=cv.getContext('2d'),W=cv.width,H=cv.height;
  if(v.readyState>=2){const vw=v.videoWidth,vh=v.videoHeight,L=BX.layout,f=BX.face,b=BX.video;
    const part=(box,x,y,w,h)=>g.drawImage(v,box[0]*vw,box[1]*vh,box[2]*vw,box[3]*vh,x,y,w,h);
    try{g.filter='none';g.clearRect(0,0,W,H);
      if(L==='split'||L==='split_under'){const th=Math.round(H*BX.top);if(L==='split'){part(f,0,0,W,th);part(b,0,th,W,H-th)}else{part(b,0,0,W,H-th);part(f,0,H-th,W,th)}}
      else if(L==='blur'){const fh=Math.min(H,W*(b[3]*vh)/(b[2]*vw));g.filter='blur(10px) brightness(.8)';g.drawImage(v,vw/2-vh*W/H/2,0,vh*W/H,vh,-12,-12,W+24,H+24);g.filter='none';part(b,0,(H-fh)/2,W,fh)}
      else{part(b,0,0,W,H);
        if(L==='pip'||L==='circle'){const sw=W*BX.size,sh=L==='circle'?sw:sw*3/4,m=W*36/1080,x=BX.corner.endsWith('left')?m:W-sw-m,y=BX.corner.startsWith('top')?H*360/1920:H-sh-H*150/1920;
          g.save();g.beginPath();if(L==='circle')g.arc(x+sw/2,y+sh/2,sw/2,0,7);else{g.fillStyle='#fff';g.fillRect(x-2,y-2,sw+4,sh+4);g.rect(x,y,sw,sh)}g.clip();part(f,x,y,sw,sh);g.restore()}}
    }catch(e){}
    if(!v.paused&&v.duration)$('bx-seek').value=Math.round(v.currentTime/v.duration*1000)}
  requestAnimationFrame(bxLoop);
}
function bxDrag(which,resize){return e=>{
  if(!BX||(!resize&&e.target.tagName==='I'))return;e.preventDefault();e.stopPropagation();
  const asp=bxAsp(which),st=$('bx-stage').getBoundingClientRect(),b0=BX[which].slice(),x0=e.clientX,y0=e.clientY;
  const move=ev=>{const dx=(ev.clientX-x0)/st.width,dy=(ev.clientY-y0)/st.height,b=BX[which];
    if(resize){let w=Math.max(.06,b0[2]+dx);w=Math.min(w,1-b0[0]);if(b0[1]+bxH(w,asp)>1)w=(1-b0[1])*BX.sh*asp/BX.sw;b[2]=w;b[3]=bxH(w,asp)}
    else{b[0]=Math.min(Math.max(0,b0[0]+dx),1-b0[2]);b[1]=Math.min(Math.max(0,b0[1]+dy),1-b0[3])}
    bxDraw()};
  const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up)};
  window.addEventListener('pointermove',move);window.addEventListener('pointerup',up)}}
function closeBoxes(){const v=$('bx-video');v.pause();v.removeAttribute('src');v.load();$('boxes').hidden=true;BX=null}
async function saveBoxes(auto){
  const key=BX.key,body=auto?{auto:true}:{layout:BX.layout,game:BX.video,facecam:BX.face,face_corner:BX.corner,face_size:BX.size,others:$('bx-others').checked,streamer:$('bx-streamer').checked};
  for(const b of ['bx-save','bx-auto','bx-cancel'])$(b).disabled=true;
  const card=$(domId(key));
  /* what was typed on the card is saved first; if that is refused the boxes are not saved either, so nothing typed is thrown away */
  if(card&&card.dataset.dirty==='1'&&!(await save(key))){for(const b of ['bx-save','bx-auto','bx-cancel'])$(b).disabled=false;$('bx-note').textContent='';return}
  $('bx-note').className='saved';$('bx-note').textContent='Making the draft again… this takes up to a minute.';
  const r=await send('/api/clip/'+key+'/framing',body);
  if(r.error){for(const b of ['bx-save','bx-auto','bx-cancel'])$(b).disabled=false;$('bx-note').textContent='';toast((/^the boxes are saved/i.test(r.error)?'':'The boxes were not saved: ')+r.error,true);if(/^the boxes are saved/i.test(r.error)){closeBoxes();loadClips()}return}
  closeBoxes();const el=$(domId(key));if(el){el.dataset.dirty='';el.dataset.m=''}
  toast(auto?'Back to the automatic picture. The draft is made again.':'Boxes saved. The draft is made again'+(r.others?'; '+r.others+' other clip'+(r.others>1?'s are':' is')+' being made again too.':'.')
    +((r.skipped||[]).length?' Not applied to '+r.skipped.length+' clip(s) that could not take them: '+r.skipped.join(', ')+'.':''),(r.skipped||[]).length>0);loadClips();
}
function bxInit(){
  $('bx-v').addEventListener('pointerdown',bxDrag('video',false));$('bx-f').addEventListener('pointerdown',bxDrag('face',false));
  $('bx-v').querySelector('i').addEventListener('pointerdown',bxDrag('video',true));$('bx-f').querySelector('i').addEventListener('pointerdown',bxDrag('face',true));
  $('bx-layout').addEventListener('change',()=>{BX.layout=val('bx-layout');bxLayout()});
  $('bx-corner').addEventListener('change',()=>{BX.corner=val('bx-corner')});
  $('bx-fsize').addEventListener('input',()=>{BX.size=val('bx-fsize')/100});
  $('bx-seek').addEventListener('input',()=>{const v=$('bx-video');if(v.duration)v.currentTime=val('bx-seek')/1000*v.duration});
  $('bx-play').addEventListener('click',()=>{const v=$('bx-video');if(v.paused){v.play().catch(()=>{});$('bx-play').textContent='Pause'}else{v.pause();$('bx-play').textContent='Play'}});
  $('bx-save').addEventListener('click',()=>saveBoxes(false));$('bx-auto').addEventListener('click',()=>saveBoxes(true));$('bx-cancel').addEventListener('click',closeBoxes);
}
bxInit();

/* ---------- cutting board: mark cuts on a downloaded part ---------- */
let CB=null, cbJob='', cbRunning=false, cbWasRunning=false, cbStopAt=null;
/* Stopped by you: the job's end must not be announced as "downloaded" or "ready". */
function stopRun(){cbJob='';act('/api/run/stop',{})}
async function getRange(){
  const body={source:val('m-src').trim(),start:hms('m-s'),end:hms('m-e'),to_end:$('m-toend').checked};
  if(!body.source){toast('Paste a video link, or choose a file, first.',true);return}
  const r=await send('/api/range',body); if(r.error){toast(r.error,true);return}
  cbJob='range';cbWasRunning=true;show(r);
}
async function loadBoard(pick){
  let rs;try{const r=await fetch('/api/ranges');if(!r.ok){toast('The cutting board could not load: '+await r.text(),true);return}rs=await r.json()}catch(e){toast('The program is not answering: '+e,true);return}
  $('cb-empty').hidden=rs.length>0;$('cb-body').hidden=!rs.length;
  if(!rs.length){CB=null;$('cb-list').innerHTML='';return}
  const key=pick||(CB&&rs.some(r=>r.key===CB.key)?CB.key:rs[0].key);
  $('cb-list').innerHTML=rs.length>1?rs.map(r=>'<button class="chipbtn'+(r.key===key?' on':'')+'" data-k="'+esc(r.key)+'">'+esc(String(r.title||r.channel||'Video').slice(0,28))+' · '+clock(r.start_s)+' to '+clock(r.end_s)+'</button>').join(''):'';
  const r=rs.find(x=>x.key===key);
  if(!CB||CB.key!==key||CB.r.mtime!==r.mtime)await openBoard(r);else{CB.r=r;cbRows()}
}
async function openBoard(r){
  CB={key:r.key,r:r,total:Number(r.duration_s),cuts:[],skip:[false],sel:-1,wave:null};
  try{const s=JSON.parse(localStorage.getItem('cb:'+r.key)||'null');
    if(s&&s.mtime===r.mtime&&Array.isArray(s.cuts)&&s.skip.length===s.cuts.length+1){CB.cuts=s.cuts;CB.skip=s.skip}}catch(e){}
  $('cb-title').textContent=(r.title||r.channel||'Your video')+' · from '+clock(r.start_s)+' to '+clock(r.end_s)+' of the video · '+r.mb+' MB on this PC'+(r.clips?' · '+r.clips+' clip'+(r.clips>1?'s':'')+' already made from it':'');
  $('cb-video').src='/media/'+r.key+'/range.mp4?v='+r.mtime;
  $('cb-ruler').innerHTML=[0,.25,.5,.75,1].map(f=>'<span>'+clock(r.start_s+f*CB.total)+'</span>').join('');
  cbDraw();
  try{const w=await fetch('/api/range/'+r.key+'/wave');if(w.ok){const d=await w.json();if(CB&&CB.key===r.key){CB.wave=d;cbDraw()}}
    else toast('The sound wave could not be drawn: '+await w.text()+' You can still cut by watching.',true)}catch(e){}
}
function cbSegs(){const e=[0].concat(CB.cuts,[CB.total]);return e.slice(0,-1).map((a,i)=>({a:a,b:e[i+1],i:i,on:!CB.skip[i]}))}
function cbKeep(){try{localStorage.setItem('cb:'+CB.key,JSON.stringify({mtime:CB.r.mtime,cuts:CB.cuts,skip:CB.skip}))}catch(e){}}
function cbDraw(){
  if(!CB)return;const tl=$('cb-tl'),cv=tl.querySelector('canvas'),W=tl.clientWidth,H=tl.clientHeight,dpr=window.devicePixelRatio||1,segs=cbSegs(),T=CB.total;
  if(W){cv.width=W*dpr;cv.height=H*dpr;const g=cv.getContext('2d');g.scale(dpr,dpr);g.clearRect(0,0,W,H);
    const css=getComputedStyle(document.documentElement),on=css.getPropertyValue('--accent').trim(),off=css.getPropertyValue('--muted').trim();
    for(const s of segs){g.globalAlpha=s.on?(s.i%2?.10:.18):0;g.fillStyle=on;g.fillRect(s.a/T*W,0,(s.b-s.a)/T*W,H)}
    const pk=CB.wave?CB.wave.peaks:null,n=pk?pk.length:0,bw=n?W/n:0;let k=0;
    for(let i=0;i<n;i++){const t=(i+.5)/n*T;while(k<segs.length-1&&t>=segs[k].b)k++;const h=Math.max(1,pk[i]*(H-22));
      g.fillStyle=segs[k].on?on:off;g.globalAlpha=segs[k].on?1:.4;g.fillRect(i*bw,(H-14-h)/2,Math.max(1,bw-.4),h)}
    g.globalAlpha=1}
  let num=0;
  $('cb-marks').innerHTML=CB.cuts.map((t,i)=>'<div class="cutm'+(i===CB.sel?' picked':'')+'" data-i="'+i+'" style="left:'+(t/T*100)+'%" title="Drag to move this cut"></div>').join('')
    +segs.map(s=>'<span class="segn'+(s.on?'':' off')+'" style="left:'+((s.a+s.b)/2/T*100)+'%">'+(s.on?++num:'skip')+'</span>').join('');
  cbRows();cbKeep();
}
function cbProblem(s){const len=s.b-s.a;return len<7.95?'too short (under 8 s)':(len>300.05?'too long (over 5 minutes)':'')}
function cbRows(){
  if(!CB)return;const segs=cbSegs(),on=segs.filter(s=>s.on),bad=on.filter(cbProblem);let num=0;
  $('cb-rows').innerHTML=segs.map(s=>{const len=s.b-s.a,p=s.on?cbProblem(s):'';
    return '<div class="cbrow'+(s.on?'':' off')+'"><b>'+(s.on?'Clip '+(++num):'Skipped')+'</b><span>'+clock(CB.r.start_s+s.a)+' to '+clock(CB.r.start_s+s.b)+' of the video</span>'
      +'<span class="'+(p?'badt':(s.on&&len>90?'warnt':''))+'">'+len.toFixed(1)+' s'+(p?' · '+p:(s.on&&len>90?' · long for a short video':''))+'</span>'
      +'<label><input type="checkbox" data-seg="'+s.i+'"'+(s.on?' checked':'')+'> make this one</label><button class="btn" data-play="'+s.i+'">Play</button></div>'}).join('');
  const sum=on.reduce((x,s)=>x+s.b-s.a,0);
  $('cb-count').textContent=on.length?(on.length+' clip'+(on.length>1?'s':'')+' will be made · '+Math.round(sum)+' seconds in all'+(bad.length?' · fix the piece'+(bad.length>1?'s':'')+' marked in red first':'')):'No piece is ticked, so nothing will be made';
  $('cb-make').textContent=on.length>1?'Make these '+on.length+' clips':'Make this clip';
  $('cb-make').disabled=cbRunning||!on.length||bad.length>0;$('cb-drop').disabled=cbRunning;$('cb-del').disabled=CB.sel<0;$('cb-clear').disabled=!CB.cuts.length;
  $('cb-label').parentElement.hidden=on.length<2;
}
function cbCut(t){
  if(!CB)return;t=Math.round(t*10)/10;
  if(t<1||t>CB.total-1||CB.cuts.some(c=>Math.abs(c-t)<1)){toast('A cut needs at least a second between it and the next cut or the edge.',true);return}
  let p=CB.cuts.findIndex(c=>c>t);if(p<0)p=CB.cuts.length;
  CB.cuts.splice(p,0,t);CB.skip.splice(p+1,0,CB.skip[p]);CB.sel=p;cbDraw();
}
function cbRemove(i){if(!CB||i<0||i>=CB.cuts.length)return;CB.cuts.splice(i,1);CB.skip.splice(i,2,CB.skip[i]&&CB.skip[i+1]);CB.sel=-1;cbDraw()}
function cbSet(cuts){CB.cuts=cuts.map(t=>Math.round(t*10)/10);CB.skip=new Array(cuts.length+1).fill(false);CB.sel=-1;cbDraw()}
function cbEqual(){const n=Math.round(+val('cb-n'));if(!(n>=2&&n<=60)){toast('Give a number of parts from 2 to 60.',true);return}
  if(CB.total/n<8){toast('That would make parts shorter than 8 seconds. Use fewer parts.',true);return}
  cbSet(Array.from({length:n-1},(_,i)=>(i+1)*CB.total/n))}
function cbEvery(){const s=+val('cb-s');if(!(s>=8&&s<=300)){toast('Give a length from 8 to 300 seconds.',true);return}
  const n=Math.max(1,Math.floor(CB.total/s)),rest=CB.total-n*s,cuts=[];for(let i=1;i<n;i++)cuts.push(i*s);if(rest>=8&&n*s<CB.total)cuts.push(n*s);cbSet(cuts)}
async function cbMake(){
  const pieces=cbSegs().filter(s=>s.on).map(s=>[s.a,s.b]);
  const r=await send('/api/range/cut',{key:CB.key,pieces:pieces,part_label:$('cb-label').checked});
  if(r.error){toast(r.error,true);return}
  cbJob='pieces';cbWasRunning=true;show(r);toast('Making '+pieces.length+' clip'+(pieces.length>1?'s':'')+'. They appear under My clips as each one is ready.');
}
async function cbDrop(){
  if(!CB||!confirm('Delete this downloaded part ('+CB.r.mb+' MB)?'+NL+NL+'Clips already made from it are kept. To cut it again you would have to download it again.'))return;
  const key=CB.key, v=$('cb-video'), src=v.getAttribute('src');
  v.pause();v.removeAttribute('src');v.load();  /* let go of the file first, or Windows will not delete it */
  const r=await send('/api/range/'+key+'/delete'); if(r.error){if(src){v.src=src}toast(r.error,true);return}
  try{localStorage.removeItem('cb:'+key)}catch(e){}
  CB=null;toast('Deleted ('+r.mb+' MB freed)');loadBoard();
}
function cbInit(){
  const v=$('cb-video'),tl=$('cb-tl');
  v.addEventListener('timeupdate',()=>{if(!CB)return;const ph=$('cb-ph');ph.style.display='block';ph.style.left=(v.currentTime/CB.total*100)+'%';
    if(cbStopAt!=null&&v.currentTime>=cbStopAt){v.pause();cbStopAt=null}});
  v.addEventListener('pause',()=>{cbStopAt=null});
  tl.addEventListener('pointerdown',e=>{
    if(!CB)return;const m=e.target.closest('.cutm'),rect=()=>tl.getBoundingClientRect(),at=ev=>Math.max(0,Math.min(CB.total,(ev.clientX-rect().left)/rect().width*CB.total));
    if(!m){v.currentTime=at(e);cbStopAt=null;return}
    e.preventDefault();const i=+m.dataset.i;CB.sel=i;cbDraw();
    const move=ev=>{const lo=(i?CB.cuts[i-1]:0)+1,hi=(i<CB.cuts.length-1?CB.cuts[i+1]:CB.total)-1;
      CB.cuts[i]=Math.round(Math.max(lo,Math.min(hi,at(ev)))*10)/10;v.currentTime=CB.cuts[i];cbDraw()};
    const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up)};
    window.addEventListener('pointermove',move);window.addEventListener('pointerup',up)});
  $('cb-cut').addEventListener('click',()=>cbCut(v.currentTime));$('cb-del').addEventListener('click',()=>cbRemove(CB?CB.sel:-1));
  $('cb-clear').addEventListener('click',()=>{if(CB)cbSet([])});$('cb-eq').addEventListener('click',cbEqual);$('cb-ev').addEventListener('click',cbEvery);
  $('cb-make').addEventListener('click',cbMake);$('cb-drop').addEventListener('click',cbDrop);
  $('cb-list').addEventListener('click',e=>{const b=e.target.closest('[data-k]');if(b)loadBoard(b.dataset.k)});
  $('cb-rows').addEventListener('change',e=>{const i=e.target.dataset.seg;if(i!=null){CB.skip[+i]=!e.target.checked;cbDraw()}});
  $('cb-rows').addEventListener('click',e=>{const b=e.target.closest('[data-play]');if(!b)return;const s=cbSegs()[+b.dataset.play];v.currentTime=s.a;cbStopAt=s.b;v.play().catch(()=>{})});
  window.addEventListener('resize',cbDraw);
  document.addEventListener('keydown',e=>{
    if(curPage!=='new'||curSub!=='board'||openKey||!$('drawer').hidden||!$('stopbox').hidden||!CB||!$('helpbox').hidden||!$('spot').hidden||HELP.doing||e.ctrlKey||e.metaKey||e.altKey||(e.target.closest&&e.target.closest('input,textarea,select,button,summary')))return;
    const k=e.key.toLowerCase(),step=d=>{v.currentTime=Math.max(0,Math.min(CB.total,v.currentTime+d))};
    if(k===' '){v.paused?v.play().catch(()=>{}):v.pause()}else if(k==='arrowleft')step(e.shiftKey?-5:-1);else if(k==='arrowright')step(e.shiftKey?5:1)
    else if(k===',')step(-.1);else if(k==='.')step(.1);else if(k==='c')cbCut(v.currentTime);else if(k==='delete'||k==='backspace')cbRemove(CB.sel);else return;
    e.preventDefault()});
}
cbInit();

/* ---------- transcript: search the words, pick stretches, chatbot parts ---------- */
let TW=null;
async function twGet(){
  const url=val('tw-url').trim(); if(!url){toast('Paste a video link first.',true);return}
  $('tw-get').disabled=true;$('tw-note').innerHTML='<b>Getting the words…</b> a few seconds for a YouTube video.';
  const r=await send('/api/words',{url:url}); $('tw-get').disabled=false;
  if(r.error){$('tw-note').textContent='Not loaded.';toast(r.error,true);return}
  TW={url:r.url,title:r.title,dur:r.duration_s,lines:r.lines,parts:r.parts,a:null,b:null,picks:[]};
  $('tw-note').innerHTML='<b>'+esc(r.title||'Video')+'</b> · '+r.lines.length+' lines from '+esc(r.from)+' · '+clock(r.duration_s)+' long';
  $('tw-body').hidden=false;$('tw-q').value='';
  $('tw-parts').innerHTML=r.parts.map(p=>'<button class="btn" data-part="'+p.n+'">Copy part '+p.n+' <small style="font-weight:400;color:var(--muted)">'+clock(p.start_s)+' to '+clock(p.end_s)+'</small></button>').join('');
  twDraw();
}
function twDraw(){
  if(!TW)return;const q=val('tw-q').trim().toLowerCase(), box=$('tw-lines');
  const hits=[];for(let i=0;i<TW.lines.length&&hits.length<400;i++){const l=TW.lines[i];if(!q||l.text.toLowerCase().includes(q))hits.push(i)}
  const total=q?TW.lines.filter(l=>l.text.toLowerCase().includes(q)).length:TW.lines.length;
  $('tw-count').textContent=q?(total+' line'+(total===1?'':'s')+' with "'+val('tw-q').trim()+'"'+(total>hits.length?', the first '+hits.length+' shown':'')):(total>hits.length?'The first '+hits.length+' of '+total+' lines. Search to narrow it down.':total+' lines.');
  const lo=TW.a, hi=TW.b;
  box.innerHTML=hits.map(i=>{const l=TW.lines[i], inside=lo!=null&&(hi!=null?l.t>=lo&&l.t<hi:l.t===lo);
    let text=esc(l.text);if(q){const at=l.text.toLowerCase().indexOf(q);if(at>=0)text=esc(l.text.slice(0,at))+'<mark>'+esc(l.text.slice(at,at+q.length))+'</mark>'+esc(l.text.slice(at+q.length))}
    return '<div class="twl'+(inside?' on':'')+'" data-i="'+i+'"><b>'+clock(l.t)+'</b><span>'+text+'</span></div>'}).join('')||'<p class="help">Nothing found.</p>';
  const len=lo!=null&&hi!=null?hi-lo:0, bad=len&&(len<8||len>300);
  $('tw-sel').innerHTML=lo==null?'Click a line to mark where a clip starts, then a later line for where it ends.'
    :(hi==null?'Starts at <b>'+clock(lo)+'</b>. Now click the line where it should end.'
      :'From <b>'+clock(lo)+'</b> to <b>'+clock(hi)+'</b> · '+Math.round(len)+' seconds'+(bad?' · <span style="color:var(--bad)">a clip must be 8 seconds to 5 minutes long</span>':''));
  $('tw-add').disabled=!(len&&!bad);
  $('tw-picks').innerHTML=TW.picks.map((p,i)=>'<div class="cbrow"><b>Clip '+(i+1)+'</b><span>'+clock(p[0])+' to '+clock(p[1])+(p[2]?' · '+esc(p[2]):'')+'</span><span>'+Math.round(p[1]-p[0])+' s</span><span></span><button class="btn" data-drop="'+i+'">Remove</button></div>').join('');
  $('tw-make').disabled=!TW.picks.length||cbRunning;$('tw-make').textContent=TW.picks.length>1?'Make these '+TW.picks.length+' clips':'Make this clip';
}
function twAdd(){if(TW.a==null||TW.b==null)return;
  if(TW.picks.some(p=>TW.a<p[1]&&TW.b>p[0])){toast('That part overlaps one you already picked.',true);return}
  TW.picks.push([TW.a,TW.b,'']);TW.picks.sort((x,y)=>x[0]-y[0]);TW.a=TW.b=null;twDraw()}
async function twMake(){
  const r=await send('/api/cut',{source:TW.url,ranges:TW.picks.map(p=>[p[0],p[1]])}); if(r.error){toast(r.error,true);return}
  cbJob='pieces';cbWasRunning=true;show(r);toast('Making '+TW.picks.length+' clip'+(TW.picks.length>1?'s':'')+'. They appear under My clips when they are ready; the Activity log shows the progress.');TW.picks=[];twDraw();
}
async function twAnswers(){
  const r=await send('/api/words/answers',{text:val('tw-ans'),duration_s:TW.dur}); if(r.error){toast(r.error,true);return}
  let added=0;for(const m of r.moments){if(!TW.picks.some(p=>m.start_s<p[1]&&m.end_s>p[0])){TW.picks.push([m.start_s,m.end_s,m.title]);added++}}
  TW.picks.sort((x,y)=>x[0]-y[0]);twDraw();
  toast(added+' moment'+(added===1?'':'s')+' added to your picks.'+(r.skipped.length?' Left out: '+r.skipped.join('; ')+'.':'')+(!r.moments.length&&!r.skipped.length?' No lines like 1:02:30-1:03:15 | title were found.':''),!added);
}
function twInit(){
  $('tw-lines').addEventListener('click',e=>{const row=e.target.closest('.twl');if(!row||!TW)return;const l=TW.lines[+row.dataset.i];
    if(TW.a==null||TW.b!=null||l.e<=TW.a){TW.a=l.t;TW.b=null}else TW.b=l.e;twDraw()});
  $('tw-picks').addEventListener('click',e=>{const b=e.target.closest('[data-drop]');if(b){TW.picks.splice(+b.dataset.drop,1);twDraw()}});
  $('tw-parts').addEventListener('click',async e=>{const b=e.target.closest('[data-part]');if(!b)return;const p=TW.parts.find(x=>x.n===+b.dataset.part);
    try{await navigator.clipboard.writeText(p.prompt);toast('Part '+p.n+' copied ('+p.lines+' lines). Paste it into the chatbot.')}
    catch(err){$('tw-ans').value=p.prompt;toast('Copying is blocked in this browser, so part '+p.n+' was put in the box below: select all of it and copy it from there.',true)}});
}
twInit();

/* ---------- the help assistant: ask, and be shown where ---------- */
let HELP={turns:[],map:new Map(),busy:false,tour:null,at:0,el:null,chat:0,said:''};
const HELP_STARTERS=['What does this program do?','How do I make my first clip?','How do I clip a live stream?','What do the scores and small labels on a clip mean?','How do I change how the captions look?','Why is it not working?','What can you do for me?'];
function helpToggle(){const b=$('helpbox');b.hidden=!b.hidden;if(!b.hidden){if(!$('helpmsgs').children.length)helpClear();$('helpq').focus()}}
function helpClear(){HELP.turns=[];HELP.chat++;HELP.busy=false;$('helpsend').disabled=false;$('helpmsgs').innerHTML='<div class="ha">Ask me anything about this program, in English or Roman Urdu. I answer in simple words and can show you where things are. You can also tell me to do something, like "start watching lacy" or "make the captions yellow": I list the steps, and one press on Do it for me does them on the page while you watch. Approving, rejecting and deleting clips are always done by you.</div>'
  +'<div class="hsug">'+HELP_STARTERS.map(q=>'<button type="button" data-q="'+esc(q)+'">'+esc(q)+'</button>').join('')+'</div>'}
/* Where a thing sits, in words: the view, the step-1 tab, the box and any fold-out it is inside. */
function helpWhere(el){
  const out=[], page=el.closest('.page'), part=el.closest('#tab-vod,#tab-own,#view-board,#view-words'), card=el.closest('.card'), clip=el.closest('.clip'), modal=el.closest('.modal');
  if(el.closest('#side'))out.push('menu on the left');
  if(page)out.push('page "'+(PAGE_NAME[Object.keys(PAGES).find(k=>PAGES[k]===page.id)]||page.id)+'"');
  if(part)out.push('tab "'+SUB_NAME[Object.keys(SUBS).find(k=>SUBS[k]===part.id)]+'"');
  if(clip)out.push('on each clip\'s card (this is the first clip\'s)'+(el.closest('.info,.edhead,.frow,.vtabs')?', in its editor (press Edit on the clip)':''));
  if(card&&!clip){let h=card.querySelector('h2');if(!h){let p=card.previousElementSibling;while(p&&!p.matches('.sect'))p=p.previousElementSibling;h=p}if(h&&h.textContent.trim())out.push(h.textContent.trim().replace(/\s+/g,' '))}
  for(let d=el.closest('details');d;d=d.parentElement&&d.parentElement.closest('details')){const s=d.querySelector('summary');if(s&&s!==el&&!s.hidden)out.push((d.classList.contains('pane')?'under the tab "':'inside the fold-out "')+s.textContent.trim().replace(/\s+/g,' ').slice(0,50)+'"')}
  if(modal)out.push(modal.id==='stopbox'?'in the "Stop making videos?" window':'in the box editor window');
  if(el.closest('#drawer'))out.push('in the Activity log panel');
  return out.join(' > ')||'on the page'}
function helpLabel(el){
  let t='';const tag=el.tagName;
  if(tag==='INPUT'||tag==='SELECT'||tag==='TEXTAREA'){
    const lab=(el.id&&document.querySelector('label[for="'+el.id+'"]'))||el.closest('label');
    /* a label that wraps its own control: only the label's words, not the choices inside the control */
    let lt='';if(lab){const c=lab.cloneNode(true);for(const x of c.querySelectorAll('select,input,textarea,button'))x.remove();lt=c.textContent}
    t=lt.trim()||el.getAttribute('aria-label')||el.placeholder||el.name||'';
    if(el.closest('.clip')&&el.previousElementSibling&&el.previousElementSibling.tagName==='LABEL')t=el.previousElementSibling.textContent}
  else t=el.innerText||el.textContent||el.title||el.getAttribute('aria-label')||'';   /* innerText keeps the gaps between a label's parts */
  return t.trim().replace(/\s+/g,' ').slice(0,90)}
/* Everything on the page that can be pressed, chosen or typed into, right now. Built fresh for every question, so new features are covered by themselves. */
function helpTargets(){
  HELP.map=new Map();HELP.meta={};const out=[];let n=0;const firstClip=(openKey&&$(domId(openKey)))||[...document.querySelectorAll('#clips .clip')].find(e=>e.offsetParent!==null)||document.querySelector('#clips .clip');
  const kinds={BUTTON:'button',SELECT:'choice',TEXTAREA:'box',SUMMARY:'fold-out',A:'link',LABEL:'option'};
  for(const el of document.querySelectorAll('button,select,input,textarea,summary,a.btn,label.mode,.pill')){
    if(el.closest('#helpbox,#spot,#toast,#helpbtn')||el.type==='hidden'||el.type==='file')continue;
    const clip=el.closest('.clip');if(clip&&clip!==firstClip)continue;   /* one clip's card stands for all */
    /* likewise one row of a long table stands for all, so long lists cannot push everything else off the list */
    const row=el.closest('#t-rows tr,#cb-rows .cbrow,#tw-picks .cbrow,#tw-parts button,#cb-list button');if(row&&row.previousElementSibling)continue;
    if(el.classList.contains('x')&&!el.title)continue;
    if(el.tagName==='INPUT'&&el.closest('label.mode'))continue;   /* the option around it is listed; listing both would only confuse */
    const label=helpLabel(el);if(!label)continue;
    const key=(el.id&&!clip)?el.id:'k'+(++n);
    if(HELP.map.has(key))continue;
    HELP.map.set(key,el);HELP.meta[key]={label:label,where:helpWhere(el)};
    /* what it holds now and, for a list, its choices: needed to do a step, not only to point at it */
    let now='',choices='';
    if(el.tagName==='SELECT'){now=(el.selectedOptions[0]||{}).textContent||'';choices=[...el.options].filter(o=>!o.disabled).map(o=>o.textContent.trim()).join('; ')}
    else if(el.tagName==='INPUT'&&(el.type==='checkbox'||el.type==='radio'))now=el.checked?'ticked':'not ticked';
    else if(el.tagName==='INPUT'&&(el.type==='range'||el.type==='number')){now=el.value;if(el.min!==''||el.max!=='')choices='a number from '+(el.min||'any')+' to '+(el.max||'any')}
    else if(el.tagName==='INPUT'&&el.type==='color'){now=el.value;choices='a colour written as #rrggbb'}
    else if((el.tagName==='INPUT'&&el.type!=='password')||el.tagName==='TEXTAREA')now=(el.value||'(empty)').slice(0,80);
    else if(el.tagName==='LABEL'&&el.querySelector('input[type=radio]'))now=el.querySelector('input').checked?'chosen':'';
    if(el.disabled)now=(now?now+', ':'')+'switched off right now';
    out.push({key:key,kind:el.tagName==='INPUT'?(el.type==='checkbox'||el.type==='radio'?'tick box':(el.type==='range'?'slider':'box')):(kinds[el.tagName]||'item'),label:label,where:helpWhere(el),now:now,choices:choices,yours:helpYours(el)});
    if(out.length>=400)break}
  return out}
function helpState(){const s=last||{},h=s.health||{};
  return {view:PAGE_NAME[curPage]+(curPage==='new'?' > '+SUB_NAME[curSub]:'')+(openKey?' > one clip open in the editor':''),tab:curTab,watching:s.watching?('yes: '+(s.channels||'')):false,running:!!s.running,finishing:!!s.finishing,
    progress:s.run_progress?(Math.floor(s.run_progress.percent)+'% '+s.run_progress.stage+' '+(s.run_progress.text||'')):'',setup:h.summary?h.summary.text:'',
    ai:s.ai?(s.ai.problem?('not available: '+s.ai.problem):'working')+((s.ai.local||{}).ready?', free local AI ready':''):'',clips:$('p-count').textContent,errors:errShown}}
/* An older answer points at things by the keys it was given. Bring those keys back; a thing whose card was redrawn since
   is found again by its label and place. */
function helpUse(box){
  if(!box._map)return;const fresh=helpTargets(), now=HELP.map, old=new Map(box._map);
  for(const [k,el] of old){if(document.body.contains(el))continue;const m=(box._meta||{})[k];
    const same=m&&fresh.find(t=>t.label===m.label&&t.where===m.where);if(same)old.set(k,now.get(same.key))}
  HELP.map=old}
function helpAdd(cls,html){const d=document.createElement('div');d.className=cls;d.innerHTML=html;$('helpmsgs').appendChild(d);$('helpmsgs').scrollTop=1e9;return d}
async function helpAsk(text){
  const q=(text||val('helpq')).trim();if(!q||HELP.busy)return;
  $('helpq').value='';for(const s of document.querySelectorAll('.hsug'))s.remove();
  helpAdd('hq',esc(q));const wait=helpAdd('ha','Thinking…');HELP.busy=true;$('helpsend').disabled=true;
  const chat=HELP.chat, listed=helpTargets(), map=HELP.map, meta=HELP.meta;
  const r=await send('/api/help',{question:q,history:HELP.turns.slice(-6),targets:listed,state:helpState()});
  if(chat!==HELP.chat)return;   /* "New chat" was pressed while this was on its way: it is not shown and nothing is done */
  HELP.busy=false;$('helpsend').disabled=false;wait.remove();HELP.map=map;HELP.meta=meta;
  if(r.error){helpAdd('ha','<b style="color:var(--bad)">I could not answer:</b> '+esc(r.error));return}
  HELP.turns.push({q:q,a:r.answer+' '+r.steps.map(s=>s.text).join(' ')});
  const id=HELP.turns.length-1, pts=r.steps.filter(s=>s.target&&HELP.map.has(s.target)).length, acts=r.steps.filter(s=>s.act&&HELP.map.has(s.target)).length;
  const box=helpAdd('ha',esc(r.answer)+(r.steps.length?'<ol>'+r.steps.map((s,i)=>'<li data-li="'+i+'">'+esc(s.text)+(s.yours?'<span class="mine">you press this yourself</span>':'')+(s.target&&HELP.map.has(s.target)?'<button class="show" data-step="'+i+'">Show me</button>':'')+'</li>').join('')+'</ol>':'')
    +((pts>1||acts)?'<div class="tour">'+(acts?'<button class="btn primary" data-do="1">Do it for me</button>':'')+(pts>1?'<button class="btn'+(acts?'':' primary')+'" data-tour="1">Show me step by step</button>':'')+'</div>':'')+(r.note?'<small>'+esc(r.note)+'</small>':''));
  box._steps=r.steps;box._map=HELP.map;box._meta=HELP.meta;box._turn=id;
  if((r.follow_ups||[]).length)helpAdd('hsug',r.follow_ups.map(f=>'<button type="button" data-q="'+esc(f)+'">'+esc(f)+'</button>').join(''));
  $('helpmsgs').scrollTop=1e9;$('helpq').focus();
  /* done by itself only while the user is still looking at this chat; otherwise the button is there for them */
  if(r.run&&acts&&!$('helpbox').hidden&&!HELP.el)helpDo(box);
}
/* ---- doing the steps for the user: one at a time, in front of them, with a way to stop ---- */
const pause=ms=>new Promise(r=>setTimeout(r,ms));
/* Presses that are never done for the user, whatever an answer says (the server takes them out as well). */
const HELP_YOURS=/\b(approve|approves|approving|reject|rejects|rejecting|delet\w*|remov\w*|eras\w*|discard\w*|clear\w*|wipe\w*|forget\w*|reset\w*|updat\w*|upgrad\w*|install|installs|installing|reinstall\w*|restart\w*|repair\w*|back to review|back to automatic|take it off)\b/i;
function helpYours(el){const l=helpLabel(el);return !!(el.closest('.clip,#boxes,#stopbox,[data-yours]')||HELP_YOURS.test(l)||/^(×|x|close)$/i.test(l.trim()))}
function helpColour(v){   /* a colour name or code as #rrggbb, or '' when it is not a colour */
  v=String(v).trim();if(/^#[0-9a-f]{6}$/i.test(v))return v.toLowerCase();
  const c=document.createElement('canvas').getContext('2d');c.fillStyle='#000001';c.fillStyle=v;const a=c.fillStyle;c.fillStyle='#000002';c.fillStyle=v;
  return (a===c.fillStyle&&/^#[0-9a-f]{6}$/i.test(a))?a:''}
function helpSet(el,value){   /* put a value into a box, a list or a slider the way a person would: the page's own handlers run */
  if(el.tagName==='SELECT'){
    const want=String(value).trim().toLowerCase(), opts=[...el.options].filter(o=>!o.disabled), text=o=>o.textContent.trim().toLowerCase();
    if(!want)return 'no choice was named';
    /* the exact choice; failing that a start or a part of one, but only when a single choice fits ("5" must not become "15 seconds") */
    let fit=opts.filter(o=>text(o)===want||o.value.toLowerCase()===want);
    if(!fit.length)fit=opts.filter(o=>text(o).startsWith(want));
    if(!fit.length)fit=opts.filter(o=>text(o).includes(want));
    if(!fit.length)return 'that list has no choice called "'+value+'"';
    if(fit.length>1)return '"'+value+'" fits more than one choice of that list ('+fit.slice(0,3).map(o=>o.textContent.trim()).join(', ')+')';
    el.value=fit[0].value}
  else if(el.type==='range'||el.type==='number'){
    const t=String(value).trim();if(!/^-?\d+(\.\d+)?$/.test(t))return '"'+value+'" is not a plain number';
    const n=parseFloat(t);
    if((el.min!==''&&n<+el.min)||(el.max!==''&&n>+el.max))return n+' is outside what that box takes ('+(el.min||'any')+' to '+(el.max||'any')+')';
    el.value=n}
  else if(el.type==='color'){const c=helpColour(value);if(!c)return '"'+value+'" is not a colour I can set';el.value=c}
  else el.value=String(value);
  el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return ''}
function helpAct(step,el){   /* returns '' when done, or why it could not be done */
  if(el.disabled)return 'it is switched off right now';
  if(step.act==='type'){if(!/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)||el.type==='checkbox'||el.type==='radio'||el.type==='password'||el.type==='file')return 'nothing can be typed there';return helpSet(el,step.value)}
  if(step.act==='tick'||step.act==='untick'){
    const box=el.tagName==='INPUT'?el:el.querySelector('input[type=checkbox],input[type=radio]');
    if(!box)return 'it is not a tick box';
    if(box.checked!==(step.act==='tick'))box.click();return ''}
  if(step.act==='press'){
    if(el.tagName==='SUMMARY'&&el.parentElement&&el.parentElement.open)return '';   /* a fold-out that is already open stays open */
    el.click();return ''}
  return 'I do not know how to do that'}
async function helpDo(box){
  if(HELP.doing)return;
  HELP.doing=true;HELP.stop=false;HELP.tour=null;
  const steps=box._steps, done=[];let why='', stoppedAt=-1, yours=-1, waitAt=-1;
  const mark=(i,cls)=>{const li=box.querySelector('[data-li="'+i+'"]');if(li)li.className=cls};
  const before=errShown.join(' || ');
  try{
    helpUse(box);
    for(const b of box.querySelectorAll('[data-do]'))b.remove();
    const can=i=>steps[i].act&&HELP.map.has(steps[i].target), todo=steps.map((s,i)=>i).filter(can);
    for(let n=0;n<steps.length;n++){
      const s=steps[n];
      if(s.yours&&s.target&&HELP.map.has(s.target)){yours=n;break}   /* a decision: from here on it is the user's turn */
      if(!can(n)){
        /* a step that is not an action, in the middle of the actions: something has to happen first (a job to finish, a choice of
           the user's). Carrying on past it would use the page before it is ready, so the run ends here and says so. */
        if(done.length&&todo.some(i=>i>n)){waitAt=n;break}
        continue}
      if(HELP.stop){stoppedAt=n;break}
      const real=HELP.map.get(s.target), shown=helpReveal(real);
      if(!shown||shown!==real){why='it is not on the page right now';stoppedAt=n;break}
      /* the same rule as the server, checked again here on the real thing: decisions and deleting are never done for the user */
      if(helpYours(real)){yours=n;break}
      HELP.el=real;$('spot').hidden=false;$('helpbox').hidden=true;$('spotnext').hidden=true;$('spotdone').textContent='Stop';
      $('spottext').textContent='Doing it for you ('+(todo.indexOf(n)+1)+' of '+todo.length+'): '+s.text;
      real.scrollIntoView({block:'center',behavior:'smooth'});spotPlace();await pause(400);spotPlace();await pause(650);
      if(HELP.stop){stoppedAt=n;break}
      if(!document.body.contains(real)){why='the page redrew that part just now; ask again';stoppedAt=n;break}
      HELP.said='';
      why=helpAct(s,real);
      if(why){stoppedAt=n;break}
      await pause(900);   /* long enough for the page to answer the press */
      if(HELP.said){why='the page answered: '+HELP.said;stoppedAt=n;break}   /* a press the page refused is not a step done */
      mark(n,'did');done.push(s.text);
    }
  }catch(e){why='the page hit a fault of its own ('+e+')';if(stoppedAt<0)stoppedAt=Math.max(0,done.length)}
  finally{$('spot').hidden=true;HELP.el=null;$('spotdone').textContent='Done';$('helpbox').hidden=false}
  try{
    await pause(600);   /* let the page show what the last press led to */
    const after=errShown.join(' || '), bad=stoppedAt>=0||(after&&after!==before);
    let msg=done.length?('Done: '+done.length+' step'+(done.length>1?'s':'')+'.'):'';
    if(stoppedAt>=0){mark(stoppedAt,'fail');msg+=(msg?' ':'')+(HELP.stop&&!why?'Stopped, as you asked. Nothing after that was done.':'I stopped at "'+steps[stoppedAt].text+'" because '+why+'. Nothing after that was done.')}
    if(waitAt>=0)msg+=(msg?' ':'')+'I stopped before "'+steps[waitAt].text+'": that has to happen first, so the steps after it are left for you (or ask me again when it is done).';
    if(after&&after!==before)msg+=' The page now shows a problem: '+errShown[0];
    if(HELP.turns[box._turn])HELP.turns[box._turn].a+=' ['+(msg||'nothing was done')+']';
    if(msg)helpAdd('ha',(bad?'<b style="color:var(--bad)">':'<b>')+esc(msg)+'</b>');
  }finally{HELP.doing=false;$('helpq').focus()}
  if(yours>=0){helpAdd('ha','You must press the next step yourself, so I only show it: '+esc(steps[yours].text));HELP.tour=steps;HELP.at=yours;
    spotShow(steps[yours],steps.slice(yours+1).some(s=>s.target&&HELP.map.has(s.target)))}
}
/* Bring a thing into view: open its view, its tab and every fold-out around it. Returns the thing to highlight (the nearest visible one). */
function helpReveal(el){
  if(!document.body.contains(el))return null;
  const page=el.closest('.page');if(page&&page.hidden)go(Object.keys(PAGES).find(k=>PAGES[k]===page.id));
  const part=el.closest('#tab-vod,#tab-own,#view-board,#view-words');if(part&&part.hidden)sub(Object.keys(SUBS).find(k=>SUBS[k]===part.id));
  if(el.closest('#sortrow'))$('sortrow').hidden=false;
  if(el.closest('#drawer'))actToggle(true);
  const clip=el.closest('.clip');if(clip&&!clip.classList.contains('open')&&clip.offsetParent===null&&filter!=='all')setFilter('all');
  if(clip&&el.closest('.info,.edhead,.frow,.vtabs')&&!clip.classList.contains('open')&&clip._c){const pane=el.closest('details.pane');openClip(clip._c.key,pane?pane.dataset.tab:'text')}
  else if(clip&&clip.classList.contains('open')){const pane=el.closest('details.pane');if(pane&&!pane.open)edGo(clip._c.key,pane.dataset.tab)}
  for(let d=el.closest('details');d;d=d.parentElement&&d.parentElement.closest('details'))if(!d.open&&!d.classList.contains('pane')&&d.querySelector('summary')!==el)d.open=true;
  let show=el;while(show&&show.offsetParent===null&&show.parentElement)show=show.parentElement;   /* hidden right now: point at what holds it */
  return (show===document.body||show===document.documentElement||show.id==='boxes')?null:show}
function view_(n){view(n)}
function spotPlace(){
  const el=HELP.el;if(!el||!document.body.contains(el)){if(!HELP.doing)spotEnd();return}   /* during a run the runner itself notices and says why */
  const r=el.getBoundingClientRect(),p=6,W=innerWidth,H=innerHeight,x=Math.max(0,r.left-p),y=Math.max(0,r.top-p),x2=Math.min(W,r.right+p),y2=Math.min(H,r.bottom+p);
  const set=(id,l,t,w,h)=>{const s=$(id).style;s.left=l+'px';s.top=t+'px';s.width=Math.max(0,w)+'px';s.height=Math.max(0,h)+'px'};
  set('sp-t',0,0,W,y);set('sp-b',0,y2,W,H-y2);set('sp-l',0,y,x,y2-y);set('sp-r',x2,y,W-x2,y2-y);set('spotring',x,y,x2-x,y2-y);
  const tip=$('spottip'),tw=Math.min(340,W-24),below=H-y2>170||y<170;tip.style.width=tw+'px';tip.style.left=Math.max(12,Math.min(W-tw-12,x))+'px';
  tip.style.top=below?(y2+12)+'px':'';tip.style.bottom=below?'':(H-y+12)+'px'}
function spotShow(step,more){
  const el=helpReveal(HELP.map.get(step.target));
  if(!el){toast('That is not on the page right now. It may only appear after an earlier step.',true);if(more)spotNext();return}
  HELP.el=el;$('spot').hidden=false;$('helpbox').hidden=true;
  $('spottext').textContent=step.text+(el!==HELP.map.get(step.target)?' (It is not showing right now, so the area it belongs to is highlighted.)':'');
  $('spotnext').hidden=!more;el.scrollIntoView({block:'center',behavior:'smooth'});
  spotPlace();setTimeout(spotPlace,350);setTimeout(spotPlace,800)}
function spotNext(){const t=HELP.tour;if(!t){spotEnd();return}
  HELP.at++;while(HELP.at<t.length&&!(t[HELP.at].target&&HELP.map.has(t[HELP.at].target)))HELP.at++;
  if(HELP.at>=t.length){spotEnd();return}
  spotShow(t[HELP.at],t.slice(HELP.at+1).some(s=>s.target&&HELP.map.has(s.target)))}
function spotEnd(){if(HELP.doing){HELP.stop=true;return}$('spot').hidden=true;HELP.el=null;HELP.tour=null;$('helpbox').hidden=false;$('helpq').focus()}
function helpInit(){
  $('helpmsgs').addEventListener('click',e=>{
    const q=e.target.closest('[data-q]');if(q){helpAsk(q.dataset.q);return}
    const box=e.target.closest('.ha');if(!box||!box._steps||HELP.doing||HELP.busy)return;
    if(e.target.closest('[data-do]')){helpDo(box);return}
    helpUse(box);
    const one=e.target.closest('[data-step]');if(one){HELP.tour=null;spotShow(box._steps[+one.dataset.step],false);return}
    if(e.target.closest('[data-tour]')){HELP.tour=box._steps;HELP.at=-1;spotNext()}});
  $('spotnext').addEventListener('click',spotNext);$('spotdone').addEventListener('click',spotEnd);
  for(const id of ['sp-t','sp-b','sp-l','sp-r'])$(id).addEventListener('click',spotEnd);
  window.addEventListener('resize',()=>{if(HELP.el)spotPlace()});window.addEventListener('scroll',()=>{if(HELP.el)spotPlace()},true);
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&(escTop==='spot'||escTop==='help')){if(HELP.el)spotEnd();else if(!$('helpbox').hidden)helpToggle()}});
}
helpInit();

/* ---------- tracker ---------- */
function view(n){if(n==='clips'||n==='track')go(n);else{go('new');sub(n)}}
async function send(url,body){
  try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
    if(!r.ok)return {error:await r.text()}; return await r.json()}
  catch(e){return {error:'The program is not answering: '+e}}
}
let trackerData=null;
async function loadTracker(){
  let d;try{const r=await fetch('/api/tracker');if(!r.ok){toast(await r.text(),true);return}d=await r.json()}catch(e){toast('The program is not answering: '+e,true);return}
  trackerData=d; const t=d.totals, money=n=>'$'+Number(n||0).toFixed(2);
  $('t-totals').innerHTML=[['Posts',t.posts],['Views',Number(t.views).toLocaleString()],['Paid',money(t.payout)],['Rejected',t.rejected]].map(x=>'<div class="card tot"><b>'+x[1]+'</b><span>'+x[0]+'</span></div>').join('')
    +Object.entries(t.campaigns).filter(([n])=>n!=='(no campaign)').map(([n,c])=>'<div class="card tot"><b>'+money(c.payout)+'</b><span>'+esc(n)+': '+c.posts+' post(s), '+Number(c.views).toLocaleString()+' views'+(c.rejected?', '+c.rejected+' rejected':'')+'</span></div>').join('');
  const sel=(list,v)=>list.map(o=>'<option'+(o===v?' selected':'')+'>'+esc(o)+'</option>').join('');
  $('t-rows').innerHTML=d.rows.slice().reverse().map(r=>'<tr data-id="'+r.id+'"><td><b>'+esc(r.channel||'')+'</b><br><span style="color:var(--muted)">'+esc((r.clip||'').split('/').pop())+'<br>'+esc(r.posted_at||'')+'</span></td>'
    +'<td><input data-f="version" value="'+esc(r.version)+'" style="width:86px"></td>'
    +'<td><input data-f="campaign" value="'+esc(r.campaign)+'"></td>'
    +'<td><select data-f="platform">'+sel(d.platforms,r.platform)+'</select></td>'
    +'<td class="link"><input data-f="url" value="'+esc(r.url)+'" placeholder="https://...">'+(r.url?'<br><a href="'+esc(r.url)+'" target="_blank" rel="noopener" style="color:var(--accent);font-size:12px">open</a>':'')+'</td>'
    +'<td><select data-f="status">'+sel(d.statuses,r.status)+'</select></td>'
    +'<td class="num"><input data-f="views" type="number" min="0" value="'+(r.views??'')+'"></td>'
    +'<td class="num"><input data-f="payout" type="number" min="0" step="0.01" value="'+(r.payout??'')+'"></td>'
    +'<td><input data-f="reason" value="'+esc(r.reason||r.notes||'')+'" placeholder="why it was rejected, or a note"></td>'
    +'<td><button class="btn quiet" data-act="trdel" title="Remove this line from Posted and earnings">Remove</button></td></tr>').join('');
  $('t-empty').hidden=d.rows.length>0;
}
document.addEventListener('change',async e=>{
  const f=e.target.dataset&&e.target.dataset.f, tr=e.target.closest&&e.target.closest('#t-rows tr'); if(!f||!tr)return;
  const r=await send('/api/tracker/update',{id:tr.dataset.id,changes:{[f]:e.target.value}});
  if(r.error){toast('Not saved: '+r.error,true);loadTracker();return} toast('Saved');
  /* Redrawing the table drops whatever is being typed in the next box (Tab from Views to Paid): wait until the table is left. */
  const at=document.activeElement;
  if(at&&at!==e.target&&at.closest&&at.closest('#t-rows'))trackerLater=true;else loadTracker();
});
let trackerLater=false;
document.addEventListener('focusout',e=>{
  if(!trackerLater||!e.target.closest||!e.target.closest('#t-rows'))return;
  setTimeout(()=>{const at=document.activeElement;if(trackerLater&&!(at&&at.closest&&at.closest('#t-rows'))){trackerLater=false;loadTracker()}},400);
});
document.addEventListener('click',async e=>{
  const b=e.target.closest&&e.target.closest('[data-act]'); if(!b)return;
  if(b.dataset.act==='trdel'){if(!confirm('Remove this line from Posted and earnings? The post itself is not changed.'))return;
    const r=await send('/api/tracker/delete',{id:b.closest('tr').dataset.id}); if(r.error)toast(r.error,true); loadTracker(); return}
  if(b.dataset.act==='log'){const key=b.dataset.key, el=$(domId(key)), c=known.find(x=>x.key===key)||{}, g=n=>el.querySelector('[name='+n+']').value;
    const body={clip:key,channel:c.channel||'',title:c.title||'',platform:g('t_platform'),version:g('t_version'),url:g('t_url'),campaign:g('t_campaign')};
    let r=await send('/api/tracker/add',body);
    if(r.error&&/already logged/.test(r.error)){if(!confirm(r.error+String.fromCharCode(10,10)+'Add it anyway?'))return; r=await send('/api/tracker/add',Object.assign(body,{allow_duplicate:true}))}
    if(r.error){toast('Not added: '+r.error,true);return}
    el.querySelector('[name=t_url]').value=''; toast('Added. See it under Posted and earnings.')}
});
async function refreshViews(){const b=$('b-views');b.disabled=true;b.textContent='Reading views…';
  const r=await send('/api/tracker/refresh');b.disabled=false;b.textContent='Refresh views';
  if(r.error){toast(r.error,true);return}
  toast(r.updated+' view count(s) updated'+(r.problems.length?'. Could not read '+r.problems.length+': '+r.problems[0]:''),r.problems.length>0&&r.updated===0);loadTracker()}

/* ---------- keyboard ---------- */
let cur=null;
function shownCards(){return [...$('clips').children].filter(e=>e.offsetParent!==null)}
function setCur(el){if(cur)cur.classList.remove('cur');cur=el||null;if(cur){cur.classList.add('cur');cur.scrollIntoView({block:'center',behavior:'smooth'})}}
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){if(escTop==='drawer')actToggle(false);else if(escTop==='clip'&&openKey){closeClip();e.preventDefault()}return}
  if($('view-clips').hidden||!$('boxes').hidden||!$('stopbox').hidden||!$('spot').hidden||!$('helpbox').hidden||HELP.doing)return;   /* words meant for the chat must never approve or reject */
  if(e.ctrlKey||e.metaKey||e.altKey||(e.target.closest&&e.target.closest('input,textarea,select,button,summary,[contenteditable]')))return;
  if(openKey)return;   /* in the editor the keys belong to what is being edited */
  const cards=shownCards(); if(cur&&!cards.includes(cur)){cur.classList.remove('cur');cur=null}
  const i=cards.indexOf(cur), k=e.key.toLowerCase();
  if(k==='j'||k==='arrowdown'){setCur(cards[Math.min(cards.length-1,i+1)]);e.preventDefault()}
  else if(k==='k'||k==='arrowup'){setCur(cards[Math.max(0,i<0?0:i-1)]);e.preventDefault()}
  else if(cur&&(k==='a'||k==='r')){if(e.repeat){e.preventDefault();return}   /* a key held down decides one clip, not every clip after it */
    const key=(cur._c&&cur._c.key)||cur.id.slice(2).replace('--','/'), next=cards[i+1]||cards[i-1]||null;
    if(cur._c&&cur._c.broken){toast('This clip is damaged and cannot be approved or rejected. Press Delete on its tile to remove it.',true);e.preventDefault();return}
    save(key,{status:k==='a'?'approved':'rejected'});setCur(next);e.preventDefault()}
  else if(cur&&(k==='enter'||k==='e')&&cur._c&&!cur._c.broken){openClip(cur._c.key);e.preventDefault()}
  else if(cur&&k===' '){const v=cur.querySelector('.vid video');if(v)playTile(v);e.preventDefault()}
});
async function clipLoop(){try{await loadClips()}catch(e){setErr('THE PAGE HIT A FAULT OF ITS OWN while drawing the clips ('+e+'). The program is still running. Reload the page; if this stays, report this line.')}
  setTimeout(clipLoop,8000)}

/* ---------- pages: one at a time, chosen in the menu on the left ---------- */
const ICON={};for(const n of ['check','x','edit','back'])ICON[n]='<svg class="i" aria-hidden="true"><use href="#i-'+n+'"/></svg>';
const PAGES={new:'page-new',live:'page-live',clips:'view-clips',look:'page-look',track:'view-track',setup:'page-setup',settings:'page-settings'};
const PAGE_NAME={new:'New clips',live:'Live channels',clips:'My clips',look:'Look and branding',track:'Posted and earnings',setup:'Set up',settings:'Settings'};
const SUBS={vod:'tab-vod',own:'tab-own',board:'view-board',words:'view-words'};
const SUB_NAME={vod:'Find the best moments',own:'Pick the part myself',board:'Cutting board',words:'Search what was said'};
let curPage='clips', curSub='vod';
function go(p){
  if(!PAGES[p]||(!HAS_PANEL&&p!=='clips'))p=HAS_PANEL?'new':'clips';
  if(openKey)closeClip();
  for(const o of document.querySelectorAll('#clips video'))o.pause();
  curPage=p;for(const k in PAGES){$(PAGES[k]).hidden=k!==p;const b=$('n-'+k);if(b)b.classList.toggle('on',k===p)}
  if(p!=='new')$('cb-video').pause();if(p==='track')loadTracker();if(p==='look')lookSoon();if(p==='settings')diskDraw();if(p==='new'&&curSub==='board'){loadBoard();redrawSoon()}if(p==='setup'&&last)suDraw(last);
  try{localStorage.setItem('page',p)}catch(e){}
  window.scrollTo(0,0)}
function sub(n){
  if(!SUBS[n])n='vod';curSub=n;if(n==='vod'||n==='own')curTab=n;
  for(const k in SUBS){$(SUBS[k]).hidden=k!==n;$((k==='board'||k==='words'?'v-':'t-')+k).className=k===n?'on':''}
  $('videos-wrap').style.display=(n==='vod'||n==='own')?'':'none';
  if(n!=='board')$('cb-video').pause();if(n==='board'){loadBoard();redrawSoon()}}
function actToggle(open){const d=$('drawer'), was=d.hidden;d.hidden=open===undefined?!d.hidden:!open;
  if(was&&!d.hidden)for(const p of d.querySelectorAll('pre.log'))p.scrollTop=p.scrollHeight}
/* The videos there are clips of, newest first, on the first page: one click shows that video's clips. */
function drawVideos(clips){
  const by=new Map();
  for(const c of clips){const id=String(c.key).split('/')[0], g=by.get(id)||{who:c.channel||c.stream_title||'Your video',title:c.channel?(c.stream_title||''):'',n:{},t:0};
    const p=place(c);g.n[p]=(g.n[p]||0)+1;g.id=id;g.t=Math.max(g.t,Number(c.made_at)||0);by.set(id,g)}
  const list=[...by.values()].sort((x,y)=>y.t-x.t).slice(0,12);
  const html=list.map(g=>'<button data-vid="'+esc(g.id)+'" onclick="pickVideo(this.dataset.vid)"><b>'+esc(g.who)+'</b><span>'+esc(g.title||'\u00a0')+'</span>'
    +(g.n.candidate?'<i class="tag new">'+g.n.candidate+' to review</i>':'')+(g.n.approved?'<i class="tag">'+g.n.approved+' approved</i>':'')+(g.n.done?'<i class="tag ok">'+g.n.done+' done</i>':'')+'</button>').join('');
  const box=$('videos');if(box._h!==html){box._h=html;box.innerHTML=html}
  $('videos-wrap').hidden=!list.length}
function pickVideo(id){
  const mine=known.filter(c=>String(c.key).split('/')[0]===id), has=p=>mine.some(c=>place(c)===p);
  go('clips');onlyVid=id;$('s-who').value='';$('s-how').value='';$('s-when').value='';$('s-find').value='';
  setFilter(has('candidate')?'candidate':has('approved')?'approved':'all')}
/* ---------- the setup guide: what this computer still needs, one step per screen ---------- */
const SU={at:-1,skipped:new Set(),fastUntil:0,timer:0,wait:''};
function suDraw(s){
  const g=(s&&s.setup)||{}, steps=g.steps||[], flag=$('n-setup-flag'), left=(g.required_left||[]).length;
  /* The guide never opens by itself, and nothing is downloaded until a step's own button is pressed (Ahmad, 2026-11-27).
     While something the program needs is missing, the menu entry turns into a warning that says how many steps are left;
     with only optional steps left it shows their number quietly. */
  const nb=$('n-setup'), names=(g.required_left||[]).map(id=>(steps.find(x=>x.id===id)||{}).title||id).join(', ');
  const opt=g.finished?0:steps.filter(x=>x.optional&&x.state==='todo'&&!(g.skipped||[]).includes(x.id)).length;
  nb.classList.toggle('attn',left>0);nb.querySelector('use').setAttribute('href',left?'#i-warn':'#i-check');
  flag.textContent=left?left+' to do':(opt?String(opt):'');flag.dataset.n=left||opt;
  nb.title=left?'Needed before clips can be made: '+names+'. Open this page and press the button of each step. Nothing is downloaded before you press.'
    :(opt?opt+' optional step'+(opt>1?'s':'')+' not done yet. ':'')+'Install what the program needs and sign in, one step at a time';
  if(curPage!=='setup')return;
  const box=$('su-body');
  /* before the program has answered there is nothing to say yet: never "This computer is ready" on no evidence */
  if(!steps.length){const w='<div class="card"><div class="body"><h2 class="sutitle">Checking this computer…</h2><p class="suwhat">'+(g.problem?'The setup steps could not be checked: '+esc(g.problem):'This takes a few seconds.')+'</p></div></div>';
    if(box._h!==w){box._h=w;box.innerHTML=w}return}
  if(SU.wait){const w=steps.find(x=>x.id===SU.wait);if(!w||w.state==='done'){SU.wait='';SU.fastUntil=0}}   /* what was waited for has happened: no more asking every few seconds */
  const n=steps.length; if(SU.at<0)SU.at=steps.findIndex(x=>x.state!=='done');if(SU.at<0||SU.at>n)SU.at=n;
  const at=SU.at, m=(s.health||{}).maint||{}, running=!!m.running;
  let h='<div class="suprog" aria-label="Steps">'+steps.map((x,i)=>'<button class="'+(i===at?'on ':'')+(x.state==='done'?'ok':'')+'" onclick="suGo('+i+')" title="'+esc(x.title)+(x.state==='done'?': done':'')+'">'+(i+1)+'</button>').join('')
    +'<button class="'+(at===n?'on':'')+'" onclick="suGo('+n+')">Finish</button></div>';
  if(g.problem)h+='<div class="cliperr" style="white-space:normal">The setup steps could not be checked: '+esc(g.problem)+'</div>';
  if(at<n){
    const x=steps[at], done=x.state==='done', signin=x.actions.some(a=>a.do==='signin');
    h+='<div class="card"><div class="body"><div class="sukick">Step '+(at+1)+' of '+n+(x.optional?' · optional, you can skip it':'')+'</div><h2 class="sutitle">'+esc(x.title)+'</h2><p class="suwhat">'+esc(x.what)+'</p>'
      +'<div class="sustate '+x.state+'"><span class="dot"></span><span>'+esc(x.detail)+'</span></div>'
      +(x.actions.length?'<div class="btns">'+x.actions.map(a=>'<button class="btn primary big" data-yours'+(running?' disabled':'')+' onclick="suAct(\''+a.do+'\',\''+a.what+'\')">'+esc(a.label)+'</button>').join('')
         +(signin?'':'<span class="saved">Download: '+esc(x.size)+'</span>')+'</div>':'')
      +(signin?'<p class="help" style="margin-top:10px">A window opens and then your browser. Sign in there with your own account. This program never sees your password. When you are done, come back here: the step turns green by itself.</p>':'')
      +(running?'<div class="bar indet"><i></i></div><div class="pct supct"></div><pre class="log sulog"></pre>':'')
      +'</div></div>'
      +'<div class="btns sunav"><button class="btn"'+(at===0?' disabled':'')+' onclick="suGo('+(at-1)+')">Back</button><span class="sp"></span>'
      +(!done&&x.optional?'<button class="btn" onclick="suSkip(\''+x.id+'\')">Skip this</button>':'')
      +'<button class="btn'+(done?' primary':'')+'"'+(!done&&!x.optional?' disabled title="This step is needed before clips can be made"':'')+' onclick="suGo('+(at+1)+')">Next</button></div>';
  }else{
    h+='<div class="card"><div class="body"><div class="sukick">Finish</div><h2 class="sutitle">'+(left?'Almost there':'This computer is ready')+'</h2>'
      +'<p class="suwhat">'+(left?'The steps marked below are still needed before clips can be made.':'You can change any of this later: Set up stays in the menu.')+'</p><ul class="sulist">'
      +steps.map((x,i)=>'<li><span class="dot" style="background:'+(x.state==='done'?'var(--ok)':(x.optional?'var(--faint)':'var(--bad)'))+'"></span><span><b>'+esc(x.title)+'</b><br><small>'+esc(x.state==='done'?x.detail:(x.optional?'Skipped. '+x.detail:'Still needed. '+x.detail))+'</small></span>'
        +(x.state==='done'?'':'<button class="btn" onclick="suGo('+i+')">Open</button>')+'</li>').join('')+'</ul>'
      +'<div class="btns"><button class="btn primary big" onclick="suFinish()">Start using Stream Clipper</button></div></div></div>'
      +'<div class="btns sunav"><button class="btn" onclick="suGo('+(n-1)+')">Back</button></div>';
  }
  if(box._h!==h){box._h=h;box.innerHTML=h}
  /* the lines of a running install change every second: only their text is replaced, so buttons keep their focus and
     text you are selecting to copy stays selected */
  const lg=box.querySelector('.sulog'), pc=box.querySelector('.supct');
  if(pc){const t=(m.name||'Working')+' · '+((m.step||[])[2]||'working');if(pc.textContent!==t)pc.textContent=t}
  if(lg){const t=(m.lines||[]).slice(-7).join(NL), sel=window.getSelection&&window.getSelection();
    if(lg.textContent!==t&&!(sel&&!sel.isCollapsed&&lg.contains(sel.anchorNode)))lg.textContent=t}
  /* while a sign-in is awaited the tools are asked more often, so the step turns green soon after */
  if(Date.now()<SU.fastUntil&&!SU.timer)SU.timer=setTimeout(async()=>{SU.timer=0;if(curPage==='setup'&&Date.now()<SU.fastUntil){const r=await send('/api/setup/refresh',{});if(!r.error)show(r)}},6000);
}
function suGo(i){SU.at=i;if(last)suDraw(last);window.scrollTo(0,0)}
function suSkip(id){SU.skipped.add(id);suGo(SU.at+1)}
async function suAct(what,thing){
  const step=((last&&last.setup&&last.setup.steps)||[])[SU.at];
  const ok=await act('/api/setup/'+(what==='signin'?'signin':'install'),{what:thing});
  if(ok&&what==='signin'){SU.fastUntil=Date.now()+5*60*1000;toast('A sign-in window opened. Sign in there, then come back here.')}
  if(ok&&what==='install')SU.fastUntil=Date.now()+60*1000;
  if(ok&&step)SU.wait=step.id;
  if(last)suDraw(last)}
async function suRefresh(){const b=$('su-again');b.disabled=true;const r=await send('/api/setup/refresh',{});b.disabled=false;if(r.error){toast(r.error,true);return}show(r);toast('Checked again')}
async function suFinish(){if(await act('/api/setup/finish',{skipped:[...SU.skipped]}))go('new')}   /* a failure has said why, and the guide stays open */
/* ---------- one clip opened as an editor: the same card, shown over the page with its sections as tabs ---------- */
let openKey='';const edTab=new Map();
function edGo(key,name){
  const el=$(domId(key));if(!el)return;const panes=[...el.querySelectorAll('details.pane')];
  if(!panes.some(d=>d.dataset.tab===name))name='text';edTab.set(key,name);
  for(const d of panes){if(d.dataset.tab!==name)for(const o of d.querySelectorAll('video'))o.pause();d.open=d.dataset.tab===name}
  for(const b of el.querySelectorAll('.edtabs button'))b.classList.toggle('on',b.dataset.tab===name);
  if(name==='trim')initTrim(key);if(name==='captions')initWords(key);
  if(name==='trim')redrawSoon()}
/* A sound wave drawn while its box was hidden has no width. Drawing again once it shows puts it there. */
function redrawSoon(){requestAnimationFrame(()=>window.dispatchEvent(new Event('resize')))}
function openClip(key,tabName){
  const el=$(domId(key));if(!el||(el._c&&el._c.broken))return;
  if(el._c&&el._c.binned){toast('This clip is in the bin. Press Bring back to use it again.');return}
  if(curPage!=='clips')go('clips');if(openKey&&openKey!==key)closeClip();
  openKey=key;el.classList.add('open');document.body.classList.add('editing');el.scrollTop=0;
  const v=el.querySelector('.vid video');if(v){v.setAttribute('controls','');if(!v.dataset.begun){v.dataset.begun='1';v.currentTime=0}}
  for(const o of document.querySelectorAll('#clips video'))if(o!==v)o.pause();
  edGo(key,tabName||edTab.get(key)||'text')}
function closeClip(decided){
  const key=openKey, el=key&&$(domId(key));openKey='';document.body.classList.remove('editing');if(!el)return;
  el.classList.remove('open');
  for(const o of el.querySelectorAll('video'))o.pause();
  const v=el.querySelector('.vid video');if(v)v.removeAttribute('controls');
  for(const d of el.querySelectorAll('details.pane'))d.open=false;
  if(!decided&&el.dataset.dirty==='1')save(key);   /* what was typed is not lost by going back */
  if(cur)cur.classList.remove('cur');cur=el;el.classList.add('cur')}
$('clips').addEventListener('click',e=>{
  const v=e.target.closest&&e.target.closest('.vid video');if(!v)return;const el=v.closest('.clip');if(!el||el.classList.contains('open'))return;
  playTile(v);
  if(cur)cur.classList.remove('cur');cur=el;el.classList.add('cur')});
/* A tile shows a frame from 1.5 s in so it is not black; the first play still starts at the beginning. One clip plays at a time. */
function playTile(v){
  for(const o of document.querySelectorAll('#clips video'))if(o!==v)o.pause();
  if(v.paused){if(!v.dataset.begun){v.dataset.begun='1';v.currentTime=0}v.play().catch(()=>{})}else v.pause()}
$('clips').addEventListener('dblclick',e=>{const el=e.target.closest&&e.target.closest('.clip');if(el&&el._c&&!el.classList.contains('open')&&e.target.closest('.vid'))openClip(el._c.key)});
setInterval(()=>{const t=$('lang-sum').textContent;for(const m of document.querySelectorAll('.lang-mirror'))if(m.textContent!==t)m.textContent=t},1500);
$('scope').textContent=__TITLE__;
if(HAS_PANEL){for(const id of ['m-sh','m-sm','m-ss','m-eh','m-em','m-es'])$(id).addEventListener('input',ownHint);ownHint()}
if(HAS_PANEL)poll();else document.body.classList.add('nopanel');
try{$('z-pick').value=localStorage.getItem('zones')||''}catch(e){}
try{const a=JSON.parse(localStorage.getItem('arrange')||'{}');if(['new','score','old'].includes(a.sort))$('s-sort').value=a.sort;if(a.how)$('s-how').value=a.how;if(a.when)$('s-when').value=a.when}catch(e){}
setZones();
/* ---------- select several clips and do one thing to all of them ---------- */
const SEL=new Set(), BULK={busy:false,stop:false};let SELECTING=false;
function selDraw(){const have=new Set(known.map(c=>c.key));for(const k of [...SEL])if(!have.has(k))SEL.delete(k);
  $('bulk-n').textContent=SEL.size+' selected';$('bulkbar').hidden=!SELECTING;$('clips').classList.toggle('selecting',SELECTING);
  $('b-select').classList.toggle('on',SELECTING);$('b-select').textContent=SELECTING?'Selecting…':'Select several'}
function selMode(on){if(BULK.busy)return;SELECTING=on===undefined?!SELECTING:!!on;if(!SELECTING)selNone();
  if(SELECTING)$('bulk-note').textContent='Press a clip to tick it. Then choose what to do with the ticked clips.';selDraw()}
function selSet(key,on){if(on)SEL.add(key);else SEL.delete(key);selDraw()}
function selBoxes(){for(const el of document.querySelectorAll('#clips .clip')){const b=el.querySelector('.pk input');if(b&&el._c)b.checked=SEL.has(el._c.key)}}
function selAll(){for(const el of document.querySelectorAll('#clips .clip'))if(el.offsetParent!==null&&el._c&&!el._c.broken)SEL.add(el._c.key);selBoxes();selDraw()}
function selNone(){SEL.clear();selBoxes();selDraw()}
/* in selecting mode a press anywhere on a tile's picture ticks it, instead of playing the video */
$('clips').addEventListener('click',e=>{
  if(!SELECTING)return;const card=e.target.closest('.clip');
  if(!card||card.classList.contains('open')||!card._c||card._c.broken||e.target.closest('.pk,.tile'))return;
  e.preventDefault();e.stopPropagation();selSet(card._c.key,!SEL.has(card._c.key));selBoxes()},true);
function bulkMore(){const v=val('bulk-more');if(!v){toast('Choose what to do in the list beside this button first.',true);return}bulk(v).then(()=>{$('bulk-more').value=''})}
async function bulk(what){
  if(BULK.busy)return;
  const keys=[...SEL].filter(k=>known.some(c=>c.key===k&&!c.broken));
  if(!keys.length){toast('Tick the clips first: press a clip to tick it.',true);return}
  const A={approve:{say:'approved',body:{status:'approved'},leaves:1},reject:{say:'rejected',body:{status:'rejected'},leaves:1},
    review:{say:'put back under To review',body:{status:'candidate',held:false},leaves:1},pending:{say:'moved to Pending',body:{status:'candidate',held:true},leaves:1},
    b_on:{say:'set to get a version B',body:{ab:true}},b_off:{say:'set to get no version B',body:{ab:false}},
    x_on:{say:'set to get an explainer version',body:{explainer:true}},x_off:{say:'set to get no explainer version',body:{explainer:false}},
    whole:{say:'changed to show the whole picture',url:'/framing',body:{layout:'blur',game:[0,0,1,1]},slow:1},
    auto:{say:'set back to the automatic picture',url:'/framing',body:{auto:true,keep:true},slow:1},
    del:{say:'moved to the bin',url:'/delete',body:{},leaves:1}}[what];
  if(!A)return;const n=keys.length, cl=n===1?'clip':'clips';
  if(what==='del'&&!confirm('Move '+(n===1?'this clip':'these '+n+' clips')+' to the bin?'+NL+NL+'They wait there for a few days and can be brought back (My clips, Bin). After that they are removed for good.'))return;
  if(A.slow&&!confirm('Change the picture of '+n+' '+cl+'?'+NL+NL+'Each draft is made again. That takes up to a minute for every clip.'))return;
  BULK.busy=true;BULK.stop=false;$('bulk-stop').hidden=n<2;$('bulk-stop').disabled=false;let ok=0;const bad=[];
  for(const b of $('bulkbar').querySelectorAll('button:not(#bulk-stop),select'))b.disabled=true;
  for(let i=0;i<n;i++){
    if(BULK.stop)break;
    $('bulk-note').textContent='Working on clip '+(i+1)+' of '+n+'…'+(A.slow?' (each one takes up to a minute)':'');
    const r=await send('/api/clip/'+keys[i]+(A.url||''),A.body);
    if(r.error)bad.push(r.error);else{ok++;if(A.leaves)SEL.delete(keys[i])}
  }
  const left=n-ok-bad.length;
  BULK.busy=false;$('bulk-stop').hidden=true;for(const b of $('bulkbar').querySelectorAll('button,select'))b.disabled=false;
  for(const el of document.querySelectorAll('#clips .clip'))el.dataset.m='';
  await loadClips();selBoxes();selDraw();
  const msg=ok+' '+(ok===1?'clip':'clips')+' '+A.say+'.'+(left?' Stopped: '+left+' not done.':'')+(bad.length?' '+bad.length+' could not be done. The first reason: '+bad[0]:'');
  $('bulk-note').textContent=msg;toast(msg,bad.length>0)}
/* The program opens where you were last, but never on Set up: that page is only opened by you. */
{let first='clips';try{first=localStorage.getItem('page')||'new'}catch(e){}if(first==='setup')first='new';go(HAS_PANEL?first:'clips');sub('vod')}
clipLoop();
