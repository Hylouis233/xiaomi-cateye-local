import '/local/nvr-playback-card.js?v=20261106-nvr12';
const isLan = /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(location.hostname);
const newSession = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2,'0')).join('');
const bounded = (promise, ms) => new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('request_timeout')),ms);promise.then(value=>{clearTimeout(timer);resolve(value);},error=>{clearTimeout(timer);reject(error);});});

async function command(hass, service, data) {
  const result = await bounded(hass.callWS({type:'call_service',domain:'rest_command',service,
    service_data:data,return_response:true}),['cateye_prepare','cateye_rekey'].includes(service)?36000:7000);
  const response = result.response;
  if (response?.status >= 400) throw new Error('control_failed');
  return response?.content ?? response;
}

class SameOriginLive {
  constructor(hass, video, status, {stream, cateye=false, lock=false, onStop, onReady}) {
    this.onStop=onStop;this.onReady=onReady;this.hass=hass; this.video=video; this.status=status; this.stream=stream;
    this.cateye=cateye; this.lock=lock; this.audio=false; this.running=false; this.epoch=0;
    this.video.autoplay=!cateye; this.video.muted=true; this.video.playsInline=true;
  }
  async start() {
    this.stop('restart');
    const epoch=++this.epoch;
    this.running=true; this.frames=0; this.started=performance.now();this.lastFrameCount=0;this.lastAdvance=performance.now();this.reportedFirst=false;this.waits=0;this.catchups=0;this.lastCatchup=0;this.hbFrames=0;this.hbAt=performance.now();
    this.status.textContent=this.cateye?'正在唤醒猫眼…':this.lock?'正在唤醒门锁…':'正在连接受保护画面…';
    try {
      if(this.cateye) {
        this.session=newSession();
        this.rekeyed=false;this.mediaRetried=false;this.authRejected=false;this.lastError='';
        const prepared=await command(this.hass,'cateye_prepare',{session:this.session});this.usedCached=prepared?.used_cached_session;
        if(epoch!==this.epoch)return;
        this.status.textContent='猫眼已就绪，正在接收画面…';
        this.hb=setInterval(()=>this.heartbeat(epoch),2500);
        this.heartbeat(epoch);
      }
      await this.connectMedia(epoch);
    } catch(error) {
      if(epoch!==this.epoch)return;
      this.stop('error'); this.status.textContent='连接未完成，请点击实时画面重试';
    }
  }
  async connectMedia(epoch) {
    const transport=++this.mediaEpoch;
    const valid=()=>epoch===this.epoch&&transport===this.mediaEpoch;
    const videoCodecs=this.compat?['avc1.640029','avc1.64002A','avc1.42E01F']:['hvc1.1.6.L153.B0','hev1.1.6.L153.B0','avc1.640029','avc1.64002A','avc1.42E01F'];
    const audioCodecs=this.audio?(this.compat?['mp4a.40.2']:['opus','mp4a.40.2']):[];
    const MS=window.ManagedMediaSource||window.MediaSource;
    if(!MS)throw new Error('media_source_unavailable');
    const codecs=[...videoCodecs,...audioCodecs].filter(c=>MS.isTypeSupported(`video/mp4; codecs="${c}"`));
    if(!codecs.length)throw new Error('codec_unavailable');
    // Stay on the go2rtc websocket. The privacy proxy remaps living-room sources only
    // under /api/go2rtc/, and the door session is accepted only on that same route.
    const path=`/api/frigate/frigate/go2rtc/ws/api/ws?src=${encodeURIComponent(this.stream)}${this.cateye?'&session='+encodeURIComponent(this.session):''}`;
    const signed=await bounded(this.hass.callWS({type:'auth/sign_path',path,expires:60}),8000);
    if(!valid())return;
    this.ms=new MS(); this.video.disableRemotePlayback=true;
    this.prebuffering=false;this.bufferAt=performance.now();
    this.objectUrl=URL.createObjectURL(this.ms); this.video.src=this.objectUrl;
    this.video.onerror=()=>{if(valid())this.fail(epoch);};
    this.video.onwaiting=()=>{if(valid()&&this.reportedFirst)this.waits++;};
    this.frameBase=this.video.getVideoPlaybackQuality?.().totalVideoFrames||this.video.webkitDecodedFrameCount||0;
    this.frames=0;this.lastFrameCount=0;this.lastAdvance=performance.now();
    if(this.video.requestVideoFrameCallback){
      const presented=()=>{if(!valid())return;this.frames++;this.lastAdvance=performance.now();this.frameCallback=this.video.requestVideoFrameCallback(presented);};
      this.frameCallback=this.video.requestVideoFrameCallback(presented);
    }
    this.queue=[];
    this.ms.addEventListener('sourceopen',()=>{
      if(!valid())return;
      const wsUrl=new URL(signed.path,location.origin);wsUrl.protocol=location.protocol==='https:'?'wss:':'ws:';
      this.ws=new WebSocket(wsUrl);this.ws.binaryType='arraybuffer';
      this.ws.onopen=()=>this.ws.send(JSON.stringify({type:'mse',value:codecs.join(',')}));
      this.ws.onmessage=e=>{
        if(!valid())return;
        if(typeof e.data==='string') {
          const m=JSON.parse(e.data);
          if(m.type==='mse') {
            try {
              this.mime=m.value;this.sb=this.ms.addSourceBuffer(m.value);
              this.sb.addEventListener('updateend',()=>{if(valid())this.flush(epoch);});
              this.sb.addEventListener('error',()=>{if(valid())this.fail(epoch);});
              this.flush(epoch);this.video.play().catch(()=>{this.video.muted=true;this.video.play().catch(()=>{});});
            }catch(error){this.fail(epoch);}
          } else if(m.type==='error'){this.lastError=String(m.value||'');if(/auth/i.test(this.lastError))this.authRejected=true;this.fail(epoch);}
        } else {
          this.queue.push(e.data);
          if(this.queue.reduce((n,b)=>n+b.byteLength,0)>4*1024*1024){this.fail(epoch);return;}
          this.flush(epoch);
        }
      };
      this.ws.onerror=()=>{if(valid())this.fail(epoch);};
      this.ws.onclose=()=>{if(valid()&&this.running)this.fail(epoch);};
    },{once:true});
    this.firstFrameTimer=setTimeout(()=>{if(valid()&&!this.frames)this.fail(epoch);},this.cateye?15000:this.lock?35000:10000);
    this.progress=setInterval(()=>this.progressTick(epoch),500);
  }
  flush(epoch) {
    if(epoch!==this.epoch||!this.sb||this.sb.updating)return;
    try {
      const ranges=this.video.buffered;
      if(ranges.length) {
        const start=ranges.start(0),end=ranges.end(ranges.length-1);
        if(this.cateye){
          if(this.video.currentTime<start)this.video.currentTime=start;
        }else{
          const target=isLan?.45:.8,limit=isLan?1.2:1.8;
          if(this.video.currentTime<start||(end-this.video.currentTime>limit&&performance.now()-this.lastCatchup>5000)){
            this.video.currentTime=Math.max(start,end-target);this.lastCatchup=performance.now();this.catchups++;
          }
        }
        this.video.playbackRate=1;
        if(start<end-20){this.sb.remove(start,end-15);return;}
      }
      if(this.queue.length)this.sb.appendBuffer(this.queue.shift());
    } catch(error) { this.fail(epoch); }
  }
  progressTick(epoch) {
    if(epoch!==this.epoch)return;
    if(this.prebuffering){if(performance.now()-this.bufferAt>(this.reportedFirst?10000:15000)){this.fail(epoch);return;}this.status.textContent=this.reportedFirst?'正在补充播放缓冲…':'正在缓冲猫眼画面…';return;}
    const quality=this.video.getVideoPlaybackQuality?.();
    if(!this.video.requestVideoFrameCallback)this.frames=Math.max(0,(quality?.totalVideoFrames||this.video.webkitDecodedFrameCount||0)-this.frameBase);
    if(this.frames) {
      clearTimeout(this.firstFrameTimer);
      if(this.frames>this.lastFrameCount){this.lastFrameCount=this.frames;this.lastAdvance=performance.now();}
      else if(performance.now()-this.lastAdvance>8000){this.fail(epoch);return;}
      if(this.video.paused){this.stop('paused');this.status.textContent='已暂停，点击画面重新连接';return;}
      if(!this.reportedFirst){this.reportedFirst=true;this.onReady?.();this.firstFrameMs=Math.round(performance.now()-this.started);if(this.cateye)this.heartbeat(epoch);}
      const buffer=this.video.buffered.length?this.video.buffered.end(this.video.buffered.length-1)-this.video.currentTime:0;
      this.status.textContent=`实时画面 · 缓冲 ${Math.round(Math.max(0,buffer)*1000)} ms`;
    }
  }
  async heartbeat(epoch) {
    if(epoch!==this.epoch||!this.session)return;
    const buffered=this.video.buffered.length?Math.max(0,this.video.buffered.end(this.video.buffered.length-1)-this.video.currentTime):0;
    const now=performance.now(),fps=Math.round(1000*((this.frames||0)-this.hbFrames)/Math.max(1,now-this.hbAt));this.hbFrames=this.frames||0;this.hbAt=now;
    try {await command(this.hass,'cateye_heartbeat',{session:this.session,metrics:{frames:this.frames||0,fps,codec:this.mime||null,first_frame_ms:this.firstFrameMs||null,buffer_ms:Math.round(buffered*1000),dropped_frames:this.video.getVideoPlaybackQuality?.().droppedVideoFrames||0,waits:this.waits,catchups:this.catchups}});}catch(error){}
  }
  fail(epoch) {
    if(epoch!==this.epoch)return;
    const auth=this.authRejected||/miss:\s*auth|auth(?:entication)?[_ ]?(?:fail|error|rejected)/i.test(String(this.lastError||''));
    if(this.cateye&&auth&&this.usedCached&&!this.rekeyed&&!this.reportedFirst){
      this.rekeyed=true;this.releaseMedia();this.frames=0;this.status.textContent='正在刷新视频授权…';
      command(this.hass,'cateye_rekey',{session:this.session}).then(result=>{if(epoch!==this.epoch)return;if(result?.skipped||result?.content?.skipped){this.rekeyed=false;this.status.textContent='正在重新连接画面…';}return this.connectMedia(epoch);}).catch(()=>{if(epoch===this.epoch){this.stop('rekey_failed');this.status.textContent='连接未完成，点击实时画面重试';}});return;
    }
    if(this.cateye&&!this.reportedFirst&&!this.mediaRetried){
      this.mediaRetried=true;this.releaseMedia();this.frames=0;this.status.textContent='正在重新连接画面…';
      this.connectMedia(epoch).catch(()=>{if(epoch===this.epoch){this.stop('media_error');this.status.textContent='连接未完成，点击画面重试';}});return;
    }
    if(!this.compat&&/hvc|hev/.test(this.mime||'')) {
      this.compat=true;this.releaseMedia();this.frames=0;
      this.status.textContent='正在切换兼容画面…';
      this.connectMedia(epoch).catch(()=>{this.stop('media_error');this.status.textContent='连接未完成，点击画面重试';});
      return;
    }
    this.stop('media_error');this.status.textContent='媒体连接已结束，点击画面重试';
  }
  setAudio(enabled) {
    this.audio=enabled;this.video.muted=!enabled;
    if(this.running){const epoch=this.epoch;this.releaseMedia();this.connectMedia(epoch).catch(()=>this.fail(epoch));}
  }
  stop(reason='stop') {
    ++this.epoch;this.running=false;
    clearInterval(this.hb);this.releaseMedia();
    this.onStop?.(reason);
    const session=this.session;this.session=null;
    if(session) {
      command(this.hass,'cateye_stop',{session,reason}).catch(()=>{});
      const token=this.hass.auth?.accessToken;
      if(token)fetch('/api/services/rest_command/cateye_stop',{method:'POST',headers:{'Authorization':`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify({session,reason}),keepalive:true}).catch(()=>{});
    }
  }
  releaseMedia() {
    this.mediaEpoch=(this.mediaEpoch||0)+1;
    clearInterval(this.progress);clearTimeout(this.firstFrameTimer);
    this.video.onerror=null;this.video.onwaiting=null;
    if(this.frameCallback&&this.video.cancelVideoFrameCallback)this.video.cancelVideoFrameCallback(this.frameCallback);
    this.frameCallback=null;
    if(this.ws){this.ws.onclose=null;this.ws.close();this.ws=null;}
    this.video.pause();this.video.removeAttribute('src');this.video.load();
    if(this.objectUrl){URL.revokeObjectURL(this.objectUrl);this.objectUrl=null;}
    this.queue=[];this.sb=null;this.ms=null;
  }
}

const STYLE=`:host{display:block}ha-card{overflow:hidden}.toggle{display:flex;align-items:center;gap:9px;width:100%;min-height:44px;padding:8px 14px;border:0;background:none;color:var(--primary-text-color);font:inherit;text-align:left;cursor:pointer}.toggle ha-icon{--mdc-icon-size:20px;color:var(--primary-color)}.title{font-size:14px;font-weight:500}.hint{margin-left:auto;font-size:12px;color:var(--secondary-text-color)}.tabs{display:flex;gap:4px;padding:0 10px 8px;align-items:center}.tabs button{border:0;border-radius:7px;padding:8px 11px;background:none;color:var(--secondary-text-color);font:inherit;font-size:13px;cursor:pointer}.tabs .active{color:var(--primary-color);background:var(--secondary-background-color)}.tabs .audio{margin-left:auto}video{display:block;width:100%;max-height:65vh;aspect-ratio:4/3;background:#0b1115;object-fit:contain}.status,.face,.idle{padding:8px 14px;color:var(--secondary-text-color);font-size:12px}.records{padding:0 6px 6px}`;

class CateyeExpandCard extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});this._open=true;this._tab='idle';this._hidden=()=>{if(document.hidden){this._stop('hidden');this._tab='idle';this.render();}};}
  setConfig(config){const signature=JSON.stringify(config);if(signature===this._signature)return;this._signature=signature;this._config=config;this.render();}
  set hass(hass){this._hass=hass;if(this._player)this._player.hass=hass;if(this._mediaCard)this._mediaCard.hass=hass;const face=this.shadowRoot.querySelector('.face');if(face){const v=hass.states[this._config.face_entity]?.state;face.textContent=v&&!['unknown','unavailable'].includes(v)?`最近识别成功：${v}`:'人脸识别与录像在视频活跃期间运行';}}
  connectedCallback(){document.addEventListener('visibilitychange',this._hidden);}
  disconnectedCallback(){document.removeEventListener('visibilitychange',this._hidden);queueMicrotask(()=>{if(!this.isConnected)this._stop('disconnected');});}
  getCardSize(){return this._open?(this._tab==='idle'?2:7):1;}
  _stop(reason){if(this._player)this._player.stop(reason);this._player=null;this._mediaCard=null;}
  render(){
    if(!this._config)return;
    this._stop('render');
    this.shadowRoot.innerHTML=`<style>${STYLE}</style><ha-card><button class="toggle"><ha-icon icon="mdi:doorbell-video"></ha-icon><span class="title">门口猫眼</span><span class="hint">${this._open?'收起':'展开'}</span></button>${this._open?'<div class="tabs"><button data-tab="live">实时画面</button><button data-tab="records">录像回放</button><button class="audio" hidden>开启声音</button></div><div class="content"></div><div class="face"></div>':''}</ha-card>`;
    this.shadowRoot.querySelector('.toggle').onclick=()=>{this._open=!this._open;this._tab='idle';this.render();};
    if(!this._open)return;
    this.shadowRoot.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===this._tab);b.onclick=()=>{if(this._tab===b.dataset.tab&&this._player?.running)return;this._tab=b.dataset.tab;this.render();};});
    const content=this.shadowRoot.querySelector('.content');
    if(this._tab==='live'){
      content.innerHTML='<video controls autoplay playsinline muted></video><div class="status">正在准备连接…</div>';
      const video=content.querySelector('video'),status=content.querySelector('.status');
      this._player=new SameOriginLive(this._hass,video,status,{stream:'cateye_ha',cateye:true});
      this._player.start();status.onclick=()=>{if(!this._player.running)this._player.start();};
      const audio=this.shadowRoot.querySelector('.audio');audio.hidden=false;audio.onclick=()=>{const enabled=!this._player.audio;audio.textContent=enabled?'关闭声音':'开启声音';this._player.setAudio(enabled);};
    } else if(this._tab==='records'){
      content.classList.add('records');window.loadCardHelpers().then(h=>{if(!this._open||this._tab!=='records'||!content.isConnected)return;this._mediaCard=h.createCardElement(this._config.media_card);this._mediaCard.hass=this._hass;content.appendChild(this._mediaCard);});
    }else{content.classList.add('idle');content.textContent='点击实时画面后才连接，当前不取流。';}
    if(this._hass)this.hass=this._hass;
  }
}

class NvrLowLatencyCard extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});this._index=0;this._tab='live';this._visibility=()=>{if(document.hidden)this.stop();else this.start();};}
  setConfig(config){const signature=JSON.stringify(config);if(signature===this._signature)return;this._signature=signature;this._config=config;this.render();}
  set hass(hass){this._hass=hass;if(this._mediaCard)this._mediaCard.hass=hass;if(this._player)this._player.hass=hass;else if(this.isConnected&&!document.hidden)this.start();}
  connectedCallback(){document.addEventListener('visibilitychange',this._visibility);if(this._hass)this.start();}
  disconnectedCallback(){document.removeEventListener('visibilitychange',this._visibility);queueMicrotask(()=>{if(!this.isConnected)this.stop();});}
  getCardSize(){return 5;}
  stop(){if(this._player)this._player.stop('room_hidden');this._player=null;}
  start(){if(this._tab!=='live'||!this._hass||!this._config||document.hidden||this._player?.running)return;const streams=this._config.streams;this._player=new SameOriginLive(this._hass,this.shadowRoot.querySelector('video'),this.shadowRoot.querySelector('.status'),{stream:streams[this._index].stream});this._player.start();}
  render(){if(!this._config)return;this.stop();this._mediaCard=null;this.shadowRoot.innerHTML=`<style>${STYLE}video{aspect-ratio:16/9}</style><ha-card><div class="tabs"><span class="title">客厅监控</span><button data-tab="live" class="${this._tab==='live'?'active':''}">实时画面</button><button data-tab="records" class="${this._tab==='records'?'active':''}">录像回放</button></div>${this._tab==='live'?`<div class="tabs">${this._config.streams.map((s,i)=>`<button data-index="${i}" class="${i===this._index?'active':''}">${s.title}</button>`).join('')}<button class="audio">开启声音</button></div><video controls autoplay playsinline muted></video><div class="status">正在准备受保护画面…</div>`:'<div class="records"></div>'}</ha-card>`;
    this.shadowRoot.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{if(this._tab===b.dataset.tab)return;this._tab=b.dataset.tab;this.render();});
    if(this._tab==='records'){this._mediaCard=document.createElement('nvr-playback-card');this._mediaCard.setConfig({title:'客厅录像',cameras:[{id:'ezviz_bb8083087',title:'客厅'}]});if(this._hass)this._mediaCard.hass=this._hass;this.shadowRoot.querySelector('.records').append(this._mediaCard);return;}
    this.shadowRoot.querySelectorAll('[data-index]').forEach(b=>b.onclick=()=>{this._index=Number(b.dataset.index);this.render();});
    this.shadowRoot.querySelector('.audio').onclick=()=>{if(!this._player)return;const value=!this._player.audio;this.shadowRoot.querySelector('.audio').textContent=value?'关闭声音':'开启声音';this._player.setAudio(value);};
    this.shadowRoot.querySelector('.status').onclick=()=>{if(!this._player?.running)this.start();};if(this._hass)this.start();}
}

if(!customElements.get('cateye-expand-card'))customElements.define('cateye-expand-card',CateyeExpandCard);
if(!customElements.get('nvr-low-latency-card'))customElements.define('nvr-low-latency-card',NvrLowLatencyCard);
export { SameOriginLive };
