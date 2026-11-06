import {lockLensView} from '/local/nvr-lens.js?v=20261106-nvr12';
import {nvrQuery} from '/local/nvr-api.js?v=20261106-nvr12';
const INST='frigate', PAGE=12;
const cache=new Map();
const escapeHTML=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const timeout=(p,ms=12000)=>new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('请求超时，请重试')),ms);p.then(v=>{clearTimeout(t);resolve(v);},e=>{clearTimeout(t);reject(e);});});
async function query(hass,args,fresh=false){
  const key=JSON.stringify(args),old=cache.get(key);if(!fresh&&old&&Date.now()-old.at<30000)return old.value;
  let value;
  if(args.camera==='haier_lock'||args.cameras?.[0]==='haier_lock'){
    const kind=args.type.endsWith('/summary')?'summary':args.type==='frigate/events/get'?'events':'recordings';
    value=await nvrQuery(hass,`lock/${kind}?after=${args.after||0}&before=${args.before||Math.floor(Date.now()/1000)}`);
  }else value=await timeout(hass.callWS({instance_id:INST,...args}));if(typeof value==='string')value=JSON.parse(value);
  cache.set(key,{value,at:Date.now()});if(cache.size>30)cache.delete(cache.keys().next().value);return value;
}
function wallEpoch(day,hour,zone){
  const [y,m,d]=day.split('-').map(Number),[h,min=0,sec=0]=String(hour).split(':').map(Number);const target=Date.UTC(y,m-1,d,h,min,sec);let value=target;
  const fmt=new Intl.DateTimeFormat('en-GB',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
  for(let i=0;i<2;i++){const p=Object.fromEntries(fmt.formatToParts(new Date(value)).map(x=>[x.type,x.value]));value+=target-Date.UTC(+p.year,+p.month-1,+p.day,+p.hour,+p.minute,+p.second);}
  return Math.floor(value/1000);
}
// Join a normal file boundary (0s, about 2s, about 3s). Keep a real pause (14s and longer) split, and keep each manifest within five minutes.
function recordingRanges(segments,after,before){
  const sorted=segments.map(s=>({start:Math.max(after,s.start_time),end:Math.min(before,s.end_time),duration:s.duration})).filter(s=>s.end>s.start).sort((a,b)=>a.start-b.start),groups=[];
  for(const s of sorted){let last=groups.at(-1);if(last&&s.start-last.end<=4&&s.end-last.start<=300){last.recorded+=Math.min(s.duration||s.end-s.start,Math.max(0,s.end-Math.max(last.end,s.start)));last.end=Math.max(last.end,s.end);}else groups.push({...s,recorded:Math.min(s.duration||s.end-s.start,s.end-s.start)});}
  return groups.reverse();
}
let loader;
async function deviceEvents(hass,after,before){
  const r=await timeout(hass.callWS({type:'call_service',domain:'rest_command',service:'cateye_events',service_data:{after,before},return_response:true}));
  if(r.response?.status!==200)throw Error('device_events_unavailable');
  let data=r.response.content;if(typeof data==='string')data=JSON.parse(data);return data.events||[];
}
function formatDuration(seconds){
  const s=Number(seconds);
  if(!Number.isFinite(s)||s<1)return '不足 1 秒';
  if(s<10)return `${Math.round(s*10)/10} 秒`;
  return `${Math.round(s)} 秒`;
}
function deviceRows(events,segments,now=Date.now()/1000){
  return events.map(e=>{const ranges=recordingRanges(segments,e.at-2,e.at+35);const pending=!ranges.length;return {key:e.id,start:ranges.length?Math.min(...ranges.map(r=>r.start)):e.at,end:ranges.length?Math.max(...ranges.map(r=>r.end)):e.at,recorded:ranges.reduce((n,r)=>n+r.recorded,0),label:`${e.source==='ha_viewer'?'查看':'米家'} · ${e.label}`,pending,stale:pending&&now-Number(e.at)>90,source:'device'};});
}
async function loadPlayer(){
  if(customElements.get('ha-hls-player'))return;
  if(!loader)loader=(async()=>{const h=await window.loadCardHelpers();await h.createCardElement({type:'picture-glance',entities:[],camera_image:'dummy-to-load-editor-components'});await customElements.whenDefined('hui-picture-glance-card');await customElements.get('hui-picture-glance-card').getConfigElement();await timeout(customElements.whenDefined('ha-hls-player'),8000);})();
  try{await loader;}catch(e){loader=null;throw e;}
}
const CSS=`:host{display:block}ha-card{overflow:hidden}.head{display:flex;align-items:center;gap:8px;padding:14px 14px 6px}.head ha-icon{--mdc-icon-size:20px;color:var(--primary-color)}h3{font-size:15px;margin:0;font-weight:500}.form{display:flex;flex-wrap:wrap;align-items:end;gap:8px;padding:8px 14px}.field{display:grid;gap:4px;flex:1;min-width:110px}.field label{font-size:11px;color:var(--secondary-text-color)}select,input{box-sizing:border-box;min-height:38px;border:1px solid var(--divider-color);border-radius:7px;background:var(--card-background-color);color:var(--primary-text-color);padding:6px 8px;font:inherit;font-size:13px;width:100%;color-scheme:light dark}button{min-height:36px;border:0;border-radius:7px;background:var(--secondary-background-color);color:var(--primary-text-color);font:inherit;font-size:12px;padding:6px 11px;cursor:pointer;white-space:nowrap;flex:0 0 auto}button:disabled{opacity:.4;cursor:default}button:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}.modes{display:flex;gap:6px;margin-left:auto}.selected{background:var(--primary-color);color:var(--text-primary-color,#fff)}.note,.status{font-size:12px;line-height:1.5;color:var(--secondary-text-color);padding:6px 14px}.status[role=alert]{color:var(--error-color)}.jump{display:flex;align-items:end;gap:8px;padding:0 14px 8px}.jump .field{flex:0 1 150px}.viewer:empty{display:none}.viewer{background:#0b1115}.viewer ha-hls-player{display:block;max-height:60vh;--video-max-height:60vh}.playbar{display:flex;flex-wrap:wrap;align-items:center;gap:7px;padding:8px 14px}.playbar:empty{display:none}.playbar>span{font-size:12px;flex:1 1 100%;min-width:0}.playbar small,.clock{white-space:nowrap}.playbar select{width:auto;min-width:72px;min-height:32px}.list{display:grid;padding:4px 14px;gap:5px}.clip{display:flex;align-items:center;gap:10px;text-align:left;width:100%;background:transparent;border:1px solid var(--divider-color)}.clip.active{border-color:var(--primary-color);background:var(--secondary-background-color)}.clip ha-icon{--mdc-icon-size:19px;color:var(--primary-color)}.clip .time{font-size:13px;display:block}.clip small{display:block;font-size:11px;color:var(--secondary-text-color);margin-top:3px}.clip .length{margin-left:auto;white-space:nowrap;font-size:11px;color:var(--secondary-text-color)}.pages{display:flex;align-items:center;justify-content:center;gap:10px;padding:8px 14px 14px;font-size:12px;color:var(--secondary-text-color)}@media(max-width:430px){.form{gap:6px}.field{min-width:90px}.head{flex-wrap:wrap}.playbar{flex-wrap:wrap}}`;

class NvrPlaybackCard extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});this.epoch=0;this.dayEpoch=0;this.playEpoch=0;this.page=0;this.rows=[];this.mode='recordings';this.followLatest=true;this.preferRecorded=false;this.pinClock=false;this._visibilityHandler=()=>{if(document.hidden)this.stop('页面已切到后台，点击片段继续');else this.refreshVisible();};}
  setConfig(c){const key=JSON.stringify(c);if(key===this.key)return;this.key=key;this.config=c;this.mode=c.mode||'recordings';this.cameras=c.cameras||[{id:'ezviz_bb8083087',title:'客厅'},{id:'cateye',title:'门口猫眼'}];this.camera=this.cameras[0].id;this.render();if(this._hass)this.loadSummary();}
  set hass(h){this._hass=h;if(this.player)this.player.hass=h;if(!this.initialized&&this.config&&this.isConnected){this.initialized=true;this.loadSummary();}}
  connectedCallback(){document.addEventListener('visibilitychange',this._visibilityHandler);clearInterval(this.poll);this.poll=setInterval(()=>this.refreshVisible(),5000);if(this._hass&&!this.initialized){this.initialized=true;this.loadSummary();}}
  disconnectedCallback(){document.removeEventListener('visibilitychange',this._visibilityHandler);queueMicrotask(()=>{if(!this.isConnected){clearInterval(this.poll);++this.epoch;this.stop();this.initialized=false;}});}
  getCardSize(){return this.active?9:5;}
  get zone(){return this._hass?.config.time_zone||'Asia/Shanghai';}
  time(t){return new Intl.DateTimeFormat('zh-CN',{timeZone:this.zone,hourCycle:'h23',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(t*1000);}
  hourOf(t){return new Intl.DateTimeFormat('en-GB',{timeZone:this.zone,hour:'2-digit',hourCycle:'h23'}).format(t*1000);}
  rangeText(start,end,pending){const a=this.time(start);if(pending)return a;const b=this.time(end);return a===b?a:`${a} – ${b}`;}
  dayNow(){const f=new Intl.DateTimeFormat('en-CA',{timeZone:this.zone,year:'numeric',month:'2-digit',day:'2-digit'});const p=Object.fromEntries(f.formatToParts(new Date()).map(x=>[x.type,x.value]));return `${p.year}-${p.month}-${p.day}`;}
  hourNow(){return new Intl.DateTimeFormat('en-GB',{timeZone:this.zone,hour:'2-digit',hourCycle:'h23'}).format(new Date());}
  clockNow(){const p=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:this.zone,hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date()).map(x=>[x.type,x.value]));return `${p.hour}:${p.minute}:${p.second}`;}
  async cachedEvents(after,before){const key=`${this.camera}:${after}:${before}`;if(this.eventCache?.key===key&&Date.now()-this.eventCache.at<30000)return this.eventCache.value;const value=await deviceEvents(this._hass,after,before);this.eventCache={key,value,at:Date.now()};return value;}
  render(){
    this.shadowRoot.innerHTML=`<style>${CSS}</style><ha-card><div class="head"><ha-icon icon="mdi:play-box-outline"></ha-icon><h3>${escapeHTML(this.config?.title||'录像回放')}</h3><div class="modes"><button data-mode="recordings">时段录像</button><button data-mode="events">事件片段</button></div></div><div class="form"><div class="field"><label for="camera">摄像头</label><select id="camera">${this.cameras.map(c=>`<option value="${escapeHTML(c.id)}">${escapeHTML(c.title)}</option>`).join('')}</select></div><div class="field"><label for="day">日期</label><input id="day" type="date" aria-label="录像日期"></div><div class="field"><label for="hour">时段</label><select id="hour" aria-label="录像时段"></select></div><button class="refresh" aria-label="刷新录像索引">刷新</button></div><div class="jump"><div class="field"><label for="at">定位到时间</label><input id="at" type="time" step="1" value="00:00:00" aria-label="定位到时间"></div><button class="locate">定位播放</button><button class="latest">最近有录像</button></div><div class="note"></div><div class="viewer"></div><div class="playbar"></div><div class="status" role="status" aria-live="polite"></div><div class="list"></div><div class="pages"></div></ha-card>`;
    const q=s=>this.shadowRoot.querySelector(s);
    q('#camera').value=this.camera;q('#camera').onchange=e=>{this.followLatest=true;this.preferRecorded=false;this.pinClock=true;this.camera=e.target.value;this.day=null;this.hour=null;this.loadSummary();};
    q('#day').onchange=e=>{this.followLatest=false;this.preferRecorded=false;this.pinClock=false;this.day=e.target.value;this.hour=null;this.changeDay();};q('#hour').onchange=e=>{this.followLatest=false;this.preferRecorded=false;this.pinClock=false;this.hour=e.target.value;this.loadRows();};
    q('.refresh').onclick=()=>this.loadSummary(true);q('.latest').onclick=()=>{this.preferRecorded=true;this.followLatest=false;this.pinClock=false;this.day=null;this.hour=null;this.loadSummary(true);};q('.locate').onclick=()=>this.locate();
    this.shadowRoot.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{this.mode=b.dataset.mode;this.loadRows();});this.syncModes();
  }
  syncModes(){this.shadowRoot.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('selected',b.dataset.mode===this.mode));}
  message(text,error=false){const s=this.shadowRoot.querySelector('.status');s.textContent=text;s.setAttribute('role',error?'alert':'status');}
  async loadSummary(fresh=false){
    const epoch=++this.epoch;this.stop();this.message('正在读取录像日期…');this.rows=[];this.paintRows();
    try{const data=await query(this._hass,{type:'frigate/recordings/summary',camera:this.camera,timezone:this.zone},fresh);if(epoch!==this.epoch||!this.isConnected)return;
      this.summary=Array.isArray(data)?data:[];this.summaryAt=Date.now();const latest=[...this.summary].sort((a,b)=>String(b.day).localeCompare(String(a.day)))[0];this.day=this.day||(this.preferRecorded&&latest?.day)||this.dayNow();this.shadowRoot.querySelector('#day').value=this.day;await this.changeDay(fresh);
    }catch(e){if(epoch===this.epoch)this.message('录像索引读取失败，请刷新重试',true);}
  }
  paintHours(events){
    const recorded=this.summary?.find(x=>x.day===this.day)?.hours||[];const byHour=new Map();
    for(const h of recorded){const hour=String(h.hour).padStart(2,'0');byHour.set(hour,{...h,hour,device:0});}
    for(const e of events||[]){const hour=this.hourOf(e.at);const row=byHour.get(hour)||{hour,duration:0,events:0,device:0};row.device+=1;byHour.set(hour,row);}
    const onToday=this.day===this.dayNow();const nowHour=onToday?this.hourNow():null;
    const followNow=this.hour==null&&!this.preferRecorded&&onToday;
    if(onToday&&!this.preferRecorded&&nowHour&&!byHour.has(nowHour))byHour.set(nowHour,{hour:nowHour,duration:0,events:0,device:0});
    const hours=[...byHour.values()].sort((a,b)=>String(b.hour).localeCompare(String(a.hour)));
    this.hour=String(followNow?nowHour:(this.hour??hours[0]?.hour??nowHour??'00')).padStart(2,'0');if(followNow)this.pinClock=true;const select=this.shadowRoot.querySelector('#hour');
    const selectedHours=hours.length?hours:[{hour:this.hour,duration:0,events:0,device:0}];
    if(!selectedHours.some(h=>String(h.hour)===this.hour))this.hour=String(selectedHours[0].hour).padStart(2,'0');
    select.innerHTML=selectedHours.map(h=>`<option value="${escapeHTML(h.hour)}">${h.hour}:00 · ${Math.round(h.duration||0)} 秒 · ${h.events||0} 识别${h.device?` · ${h.device} 门口事件`:''}</option>`).join('');select.value=this.hour;
    this.shadowRoot.querySelector('.note').textContent=`时间按 ${this.zone} 显示。每 5 秒更新索引，不唤醒摄像头。`;
  }
  async changeDay(fresh=false){
    const token=++this.dayEpoch;this.stop();
    this.dayEvents=[];this.paintHours([]);
    if(this.camera==='cateye'&&this.day){
      const start=wallEpoch(this.day,'0',this.zone);
      try{this.dayEvents=await this.cachedEvents(start,start+86399);}catch(e){this.dayEvents=[];}
      if(token!==this.dayEpoch||!this.isConnected)return;
      this.paintHours(this.dayEvents);
    }
    this.loadRows(fresh);
  }
  async loadRows(fresh=false,preserve=false){
    const epoch=++this.epoch;if(!preserve){this.stop();this.syncModes();this.page=0;this.rows=[];this.paintRows();this.message('正在读取所选时段…');}
    if(!this.day||this.hour==null)return;
    const start=wallEpoch(this.day,this.hour,this.zone);this.hourStart=start;if(!preserve){const at=this.shadowRoot.querySelector('#at');const sameHour=String(at.value||'').slice(0,2)===String(this.hour).padStart(2,'0');if(this.pinClock){this.pinClock=false;at.value=this.clockNow();}else if(!(sameHour&&!this.preferRecorded&&this.day===this.dayNow()))at.value=`${String(this.hour).padStart(2,'0')}:00:00`;}
    try{
      const isEvent=this.mode==='events';const data=await query(this._hass,isEvent?{type:'frigate/events/get',cameras:[this.camera],after:start,before:start+3600,has_clip:true,limit:100}:{type:'frigate/recordings/get',camera:this.camera,after:start,before:start+3600},fresh);
      if(epoch!==this.epoch||!this.isConnected)return;
      this.segments=isEvent?null:data;
      let rows=isEvent?data.map(e=>({start:e.start_time,end:e.end_time||Date.now()/1000,id:e.id,key:e.id,pending:!e.end_time,label:(e.sub_label||({person:'人员',car:'车辆',dog:'狗',cat:'猫'}[e.label]||e.label))+(!e.end_time?' · 进行中':''),recorded:(e.end_time||Date.now()/1000)-e.start_time})):recordingRanges(data,start,start+3600);
      if(this.camera==='haier_lock')rows=data.map(e=>({start:e.start_time,end:e.end_time,id:e.id,key:e.id,label:e.label||'门锁活跃录像',recorded:e.duration}));
      if(this.camera==='cateye'){
        const [events,segs]=await Promise.all([this.cachedEvents(start,start+3600),query(this._hass,{type:'frigate/recordings/get',camera:this.camera,after:start-2,before:start+3635},fresh)]);
        if(epoch!==this.epoch||!this.isConnected)return;const device=deviceRows(events,segs);rows=rows.concat(isEvent?device:device.filter(r=>r.pending)).sort((a,b)=>b.start-a.start);
      }
      this.rows=rows;this.page=Math.min(this.page,Math.max(0,Math.ceil(rows.length/PAGE)-1));
      const missed=this.rows.filter(r=>r.pending&&r.source==='device').length,playable=this.rows.length-missed;
      if(!this.active)this.message(playable&&missed?`${playable} 个${isEvent?'事件片段':'录像区间'}，${missed} 条门口事件没有录像，点击已保存的片段播放`:playable?`${playable} 个${isEvent?'事件片段':'录像区间'}${isEvent&&data.length===100?'（含最新 100 条识别事件）':''}，点击后播放`:missed?`${missed} 条门口事件没有录像`:(isEvent?'该时段没有事件；设备事件与识别事件每 5 秒更新':'该时段没有录像，请选择其他日期或时段'));
      if(!this.active&&!playable&&!missed&&this.camera==='cateye'&&this.day===this.dayNow()){const others=(this.dayEvents||[]).filter(e=>this.hourOf(e.at)!==this.hour).sort((a,b)=>b.at-a.at);if(others.length)this.message(`这个时段没有录像。今天还有 ${others.length} 条门口事件，最近一条 ${this.time(others[0].at)}`);}
      this.shadowRoot.querySelector('.note').textContent=`时间按 ${this.zone} 显示。索引更新 ${this.time(Date.now()/1000)}，每 5 秒刷新，不唤醒摄像头。`;
      this.paintRows();this.noteTodayGap(epoch);
    }catch(e){if(epoch===this.epoch&&!this.active)this.message('事件或录像索引读取失败，请刷新重试',true);}
  }
  async refreshVisible(){
    if(document.hidden||!this.isConnected||!this._hass||!this.summary||this.refreshing)return;this.refreshing=true;const epoch=this.epoch;
    try{
      if(Date.now()-(this.summaryAt||0)>30000){
        const data=await query(this._hass,{type:'frigate/recordings/summary',camera:this.camera,timezone:this.zone},true);if(epoch!==this.epoch)return;this.summary=data;this.summaryAt=Date.now();
        if(this.followLatest&&!this.preferRecorded&&(!this.active||this.video()?.ended)){const today=this.dayNow(),hour=this.hourNow();if(this.day!==today||this.hour!==hour){this.day=today;this.hour=null;this.pinClock=true;this.shadowRoot.querySelector('#day').value=this.day;await this.changeDay(true);return;}}
      }
      if(epoch===this.epoch)await this.loadRows(true,true);
    }catch(e){if(!this.active)this.shadowRoot.querySelector('.note').textContent='自动更新暂时失败，请点击刷新重试。';}finally{this.refreshing=false;}
  }
  async noteTodayGap(epoch){
    if(this.camera!=='cateye'||this.day===this.dayNow())return;
    const start=wallEpoch(this.dayNow(),'0',this.zone);let events=[];
    try{events=await this.cachedEvents(start,start+86399);}catch(e){return;}
    if(epoch!==this.epoch||!this.isConnected||!events.length)return;
    let segs=[];try{segs=await query(this._hass,{type:'frigate/recordings/get',camera:'cateye',after:start-2,before:start+86400});}catch(e){segs=[];}
    if(epoch!==this.epoch||!this.isConnected)return;
    const misses=deviceRows(events,segs).filter(r=>r.stale);if(!misses.length)return;
    const sample=misses.slice().sort((a,b)=>a.start-b.start).slice(0,3).map(r=>`${this.time(r.start)} ${r.label}`).join('；');
    const note=this.shadowRoot.querySelector('.note');if(note)note.textContent=`今天有 ${misses.length} 条门口事件没有录像（${sample}）。当前显示最近有录像的日期。索引每 5 秒刷新，不唤醒摄像头。`;
  }
  paintRows(){
    const total=Math.ceil(this.rows.length/PAGE),start=this.page*PAGE;
    const list=this.shadowRoot.querySelector('.list');const html=this.rows.slice(start,start+PAGE).map((r,i)=>`<button class="clip${this.active&&(this.active.key||this.active.start)===(r.key||r.start)?' active':''}" data-row="${start+i}" ${r.pending?'disabled':''}><ha-icon icon="mdi:play-circle-outline"></ha-icon><span><span class="time">${this.rangeText(r.start,r.end,r.pending)}</span><small>${escapeHTML(r.label||'已保存录像')}</small></span><span class="length">${r.stale?'没有对应录像':r.pending?'等待录像落盘':formatDuration(r.recorded)}</span></button>`).join('');
    const signature=`${this.page}/${total}/${html}`;if(this.listRoot===list&&this.listSignature===signature)return;this.listRoot=list;this.listSignature=signature;list.innerHTML=html;
    list.querySelectorAll('[data-row]').forEach(b=>b.onclick=()=>this.play(this.rows[+b.dataset.row]));
    const p=this.shadowRoot.querySelector('.pages');p.innerHTML=total>1?`<button class="prev" ${this.page===0?'disabled':''}>上一页</button><span>${this.page+1} / ${total}</span><button class="next" ${this.page+1>=total?'disabled':''}>下一页</button>`:'';
    if(total>1){p.querySelector('.prev').onclick=()=>{this.page--;this.paintRows();};p.querySelector('.next').onclick=()=>{this.page++;this.paintRows();};}
  }
  async locate(){
    const input=this.shadowRoot.querySelector('#at');if(!this.day||!input.value)return;
    const epoch=++this.epoch;this.stop();this.message('正在定位已保存录像…');
    const at=wallEpoch(this.day,input.value,this.zone);
    try{const segs=await query(this._hass,{type:'frigate/recordings/get',camera:this.camera,after:at-15,before:at+300});if(epoch!==this.epoch||!this.isConnected)return;
      segs.sort((a,b)=>a.start_time-b.start_time);const next=segs.find(s=>s.end_time>at);if(!next){this.message('该时间后五分钟内没有录像，请选择有录像的时段');return;}
      if(this.camera==='haier_lock'){await this.play({start:next.start_time,end:next.end_time,id:next.id,key:next.id,label:next.label,recorded:next.duration,seek:Math.max(0,at-next.start_time)});return;}
      const start=Math.max(at,next.start_time),ranges=recordingRanges(segs,start,start+300).reverse(),r=ranges[0];if(!r){this.message('该时间没有可播放录像');return;}
      if(start-at>1)this.message(`所选时间无录像，跳到 ${this.time(start)}`);await this.play(r);
    }catch(e){if(epoch===this.epoch)this.message('定位失败，请刷新后重试',true);}
  }
  async play(row){
    if(row.pending)return;
    this.stop();this.active=row;this.paintRows();const epoch=++this.playEpoch;this.message('正在加载录像…');const began=performance.now();
    try{
      const lock=this.camera==='haier_lock';
      const clipId=row.id||this.segments?.find(s=>s.start_time<=row.start+1&&s.end_time>row.start)?.id;
      if(lock&&!clipId)throw Error('clip_not_found');
      const path=lock?`/api/frigate/${INST}/go2rtc/nvr/lock/clip/${encodeURIComponent(clipId)}`:row.id?`/api/frigate/${INST}/vod/event/${encodeURIComponent(row.id)}/index.m3u8`:`/api/frigate/${INST}/vod/${encodeURIComponent(this.camera)}/start/${Math.floor(row.start)}/end/${Math.ceil(row.end)}/index.m3u8`;
      const [signed]=await Promise.all([timeout(this._hass.callWS({type:'auth/sign_path',path,expires:7200})),lock?Promise.resolve():loadPlayer()]);if(epoch!==this.playEpoch||!this.isConnected||document.hidden)return;
      const player=document.createElement(lock?'video':'ha-hls-player');this.player=player;player.hass=this._hass;player.autoplay=true;player.controls=true;player.muted=true;player.playsInline=true;player.allowExoPlayer=false;if(lock){player.src=signed.path;player.style.cssText='display:block;width:100%;max-height:60vh;object-fit:contain';}else player.url=signed.path;
      if(lock){this.lockLens=lockLensView(player,{initial:this.config.lock_lens||'front',playback:true});this.shadowRoot.querySelector('.viewer').append(this.lockLens);}else this.shadowRoot.querySelector('.viewer').append(player);
      if(lock&&row.seek)player.addEventListener('loadedmetadata',()=>{if(epoch===this.playEpoch)player.currentTime=Math.min(row.seek,player.duration||row.seek);},{once:true});
      const stepTo=(from,dir)=>{for(let i=from;i>=0&&i<this.rows.length;i+=dir){if(!this.rows[i].pending)return i;}return -1;};
      const index=this.rows.findIndex(r=>(r.key||r.start)===(row.key||row.start));const older=stepTo(index+1,1),newer=stepTo(index-1,-1);
      const bar=this.shadowRoot.querySelector('.playbar');bar.innerHTML=`<span><span>${this.rangeText(row.start,row.end,row.pending)}</span><br><small class="clock"></small></span><button class="older" ${older<0?'disabled':''}>上一段</button><button class="newer" ${newer<0?'disabled':''}>下一段</button><button data-seek="-10">退 10 秒</button><button data-seek="10">进 10 秒</button><select aria-label="回放速度"><option value="0.5">0.5 倍</option><option value="1" selected>1 倍</option><option value="1.5">1.5 倍</option><option value="2">2 倍</option></select><button class="close">关闭回放</button>`;
      const neighbor=target=>{if(target>=0)this.play(this.rows[target]);};bar.querySelector('.older').onclick=()=>neighbor(older);bar.querySelector('.newer').onclick=()=>neighbor(newer);
      bar.querySelector('.close').onclick=()=>this.stop('回放已关闭');bar.querySelectorAll('[data-seek]').forEach(b=>b.onclick=()=>{const v=this.video();if(v)v.currentTime=Math.max(0,Math.min(Number.isFinite(v.duration)?v.duration:Infinity,v.currentTime+Number(b.dataset.seek)));});bar.querySelector('select').onchange=e=>{const v=this.video();if(v)v.playbackRate=Number(e.target.value);};
      let ready=false,last=0,lastAdvance=performance.now();
      const stamp=s=>`${String(Math.floor(s/60)).padStart(2,'0')}:${String(Math.floor(s%60)).padStart(2,'0')}`;
      const tick=()=>{
        if(epoch!==this.playEpoch)return false;const v=this.video();if(!v)return false;
        if(v.error){this.stop('该录像无法播放，请选择其他片段或刷新重试',true);return true;}
        if(!ready){const pending=v.play();if(v.readyState>=2){ready=true;clearTimeout(this.first);this.message(`录像已就绪 · 首屏 ${Math.round(performance.now()-began)} ms`);pending.catch(()=>this.message('录像已就绪，点击播放按钮开始'));}else pending.catch(()=>{});}
        const clock=bar.querySelector('.clock');if(clock)clock.textContent=`已播 ${stamp(v.currentTime)} / ${Number.isFinite(v.duration)?stamp(v.duration):'--:--'} · ${v.playbackRate} 倍${v.ended?' · 已结束':v.paused?' · 已暂停':''}`;
        if(v.currentTime!==last){last=v.currentTime;lastAdvance=performance.now();}
        else if(ready&&!v.paused&&!v.ended&&performance.now()-lastAdvance>12000){this.stop('回放接收停滞，请点击片段重试',true);return true;}
        return ready;
      };
      const watch=()=>{if(epoch!==this.playEpoch||tick())return;requestAnimationFrame(watch);};
      requestAnimationFrame(watch);this.check=setInterval(tick,250);
      this.first=setTimeout(()=>{if(epoch===this.playEpoch&&!ready)this.stop('录像加载超时，请点击片段重试',true);},16000);
    }catch(e){if(epoch===this.playEpoch)this.stop('录像加载失败，请点击片段重试',true);}
  }
  video(){const search=root=>{if(!root)return null;const v=root.querySelector('video');if(v)return v;for(const el of root.querySelectorAll('*'))if(el.shadowRoot){const v=search(el.shadowRoot);if(v)return v;}return null;};return this.player?.tagName==='VIDEO'?this.player:search(this.player?.shadowRoot);}
  stop(message,error=false){
    ++this.playEpoch;clearInterval(this.check);clearTimeout(this.first);
    const v=this.video();if(v){v.pause();v.removeAttribute('src');v.load();}this.player?.remove();this.player=null;this.active=null;
    this.shadowRoot.querySelector('.viewer')?.replaceChildren();this.shadowRoot.querySelector('.playbar')?.replaceChildren();if(message)this.message(message,error);
    this.shadowRoot.querySelectorAll('.clip.active').forEach(e=>e.classList.remove('active'));
  }
}
NvrPlaybackCard.recordingRanges=recordingRanges;
if(!customElements.get('nvr-playback-card'))customElements.define('nvr-playback-card',NvrPlaybackCard);
