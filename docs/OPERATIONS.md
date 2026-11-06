<!-- current-nvr-start -->
# Door peephole go2rtc

HA now uses the shared `nvr-camera-card` for room, cat-eye and lock cameras. Cat-eye opens idle and calls `cateye_prepare` only on an explicit live click. Playback, picture browsing/download, page exit and backgrounding do not wake it. Closing a manual viewer preserves an independent real-event collection session. Media remains LAN-only; the existing on-demand Xiaomi cloud wake/key path is still needed. Reliable pure-LAN cold wake is not established.

`live_server.py` is the maintained copy of the router's on-demand server. Its `start_engine` also registers the lock's loopback MPEG-TS source without opening it. `nvr_bridge.py` adds fixed read-only history routes and a lock MSE relay for the existing NVR proxy; it cannot dispatch an arbitrary URL. Requests remain behind the existing HA authenticated proxy. The lock-media endpoint is accepted only from NVR `192.168.1.100`.

To replace only the viewer, stop the `viewer` procd instance and explicitly add/start that same instance again; a `service signal` stop clears its respawn settings on this router. Leave the `events` and `observer` instances running. Runtime support files (`video_probe.py`, wake adapters, event observer and private credentials) remain on HA. Do not replace them from old experiments.

The running cateye binary is go2rtc v1.9.15 (`7c05066`) plus `go2rtc-1.9.15-cold-start.patch`.
It listens on `127.0.0.1:11984` inside the Home Assistant container and is started by `cateye-local` instance `viewer` (`run_viewer.sh`). The Frigate go2rtc process is separate. Do not restart it, and do not restart Home Assistant for this binary.

Rebuild from a clean `7c05066` tree:

```
patch -p1 < go2rtc-1.9.15-cold-start.patch
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags "-s -w" -o go2rtc .
go test ./pkg/xiaomi/miss/
```

Installed binary sha256 `391a0e9c80155d806747e8c849d8646cceb1c249160969581e3c1edf0f0cf81c`.
The previous binary is kept beside it as `bin/go2rtc.bak-params-20261101` (`fee8f2e1e1d4fdbd1a159cafcad4a3abe7c466d939923b8090f86e993813b5ab`). Replace the file, then signal only the viewer instance. `live_server.py` stays on the router and is not in this repo.

The patch gives the cold-start deadlines and phase log, replays the parameter-set access unit, and returns from the probe once HEVC VPS+SPS are present (PPS is kept when it is already in those units). It does not wait for a later audio packet. Discovery still waits for the peephole to answer. Do not shorten that timeout, and do not hold the CS2 session open to skip it.

<!-- current-nvr-end -->

## Prior operating and research notes

# 门口猫眼 HA / NVR 接入

## 使用方式

入口：http://192.168.1.1:8123/security-nvr/overview 。门口猫眼在客厅监控下方，默认展开并显示空闲提示；只有点击“实时画面”才唤醒和连接。展开、查看人脸状态、进入“录像回放”均不主动取流。收起、切到回放、离开页面、切到后台和暂停实时播放都会结束当前手动查看。

页面不自动反复唤醒或重连。连接失败、没有首帧、播放停帧会结束会话并显示重试提示，防止一直停在“正在唤醒”。用户可再点击“实时画面”重试。

## 实时传输与省电

设备 midr.cateye.ph300 / 1234567890，局域网地址 192.168.1.210。设备到路由器为本地 H265/Opus；支持 HEVC 的浏览器优先原流，其他浏览器走本地 H264 兼容路径。视频不经小米云端转发。本地唤醒失败时，只有用户主动查看才允许已有米家 OAuth 云端唤醒回退。

当前主页面使用原生 video + MediaSource，通过 HA 已有 Frigate 集成的签名 WebSocket 同源接收分片 MP4。HTTPS 页面自动使用 WSS，不再使用私网 iframe 或要求外网浏览器连私网 ICE 地址。默认静音、仅视频；“开启声音”按需接入音轨，不重复唤醒。旧 18766 LAN WebRTC 入口保留用于诊断，不是主页面播放路径。

播放始终为正常速度，积压时直接追到靠近实时的位置，不用慢放追帧。客厅目标播放器缓冲：LAN 约 0.45 秒、公网约 0.8 秒。猫眼先积累 LAN 约 1 秒 / 公网约 1.5 秒缓冲再播放，耗尽时补充缓冲；超过约 2.5 / 3 秒积压时才追帧，追帧至少间隔 5 秒，减少抖动下反复跳转。另有队列限制和历史缓冲清理。这是播放器缓冲，并不等同于设备曝光到显示的总延迟。

每次查看使用独立随机会话。取消会终止准备任务和子进程，迟到连接与心跳被拒绝。准备超时、首帧超时与播放心跳超时分开。最后一个手动会话结束且没有独立真实事件时，立即关闭本地媒体引擎连接，不等待 NVR 延迟退出。真实按铃/PIR采集仍可独立完成。

自动采集仅使用 --no-wake，不为了取片唤醒休眠设备。云端仅用于鉴权、已授权的唤醒回退与轻量事件通知。

## 客厅与回放性能

客厅预览复用原有隐私过滤后的流畅/高清源，经同源媒体通道播放；没有绕过隐私过滤。隐私模型继续 fail-closed。默认流畅档减少带宽，高清按需切换。

客厅与猫眼使用同一个 nvr-playback-card 回放组件。总览两台相机都有“实时画面 / 录像回放”；连续、事件、媒体库三个页签也在当前页面嵌入同一组件，可直接切换摄像头，不再跳到 HA 媒体浏览器。

统一提供摄像头、日期、时段筛选，按时间定位，上一段/下一段，前后十秒，0.5/1/1.5/2 倍速及关闭回放。时间按 HA 时区显示，不依赖浏览器所在时区。事件片段沿用 Frigate 人员/人名标签；无录像、无事件、加载失败和超时分别明确提示。

只读取所选小时的录像索引；列表每页 12 条，按需索引缓存 30 秒且最多 30 项。可见页每 5 秒更新当前时段索引、约 30 秒更新日期摘要；隐藏页面不刷新，自动更新不会停止当前播放器，数据未变时不重建列表按钮。默认跟随最新时段，手动选择日期/时段后保留筛选。连续录像按约五分钟窗口组织，猫眼保留真实休眠空档，不再把几秒短片显示成一小时录像。关键帧对齐可能使视频实际时长略长于请求窗口。事件查询限制为每小时最新 100 条，达到上限时界面明示；可通过时间定位读取更早的原始录像。

只有点击片段或定位播放才签名并加载视频，使用 HA 原生 HLS 播放器与既有 Frigate 同源代理，分片接收，不下载整小时后播放。切换摄像头、日期、时段、回放模式、关闭、离页或后台时停止旧播放器；迟到的索引和签名结果无法重新挂载旧视频。录像本体、索引、保留规则仍由原 Frigate 管理。

Frigate nginx 的 VOD 目标切片为 2 秒，并保留关键帧对齐。旧客厅录像关键帧间隔约 10 秒，实际 HLS 切片仍可能接近 10 秒；修改目标切片不能补造历史关键帧。未重编码历史录像，也未声称所有回放已成为 2 秒切片。

## 冷启动与省电边界

当前策略是按需云端唤醒、局域网视频。此设备当前固件下，已测试本地 action 2/3、wakeup_host、wakeup、参数变体、Start P2P Stream 12/1，以及不发任何唤醒而只用缓存凭据建立本地 CS2，均未成功；证据为 local_wake_research.json 和 local_cs2_only_probe.json。结论仅限这些已测入口，不能泛化为所有本地技术都不可能。用户已授权在这些入口不可靠时直接云唤醒，故不再每次白等本地尝试。

私有配置 /config/.storage/cateye_local/wake_policy.json 的 mode=cloud_on_demand 控制这条策略；需要经新证据恢复本地优先时可改为 local_first。主路径复用服务器已导入的小米 HA SDK，在进程内发送一次云端 MIoT action 2/3，不再为每次唤醒启动独立 Python 进程。此处云端是控制，不是视频中转。

常驻仅预加载 SDK、视频接入代码和已有事件订阅，不连接、探测或轮询休眠摄像头，不定时取视频钥匙、发唤醒或拉流。缓存凭据是本地数据，不代表设备在线；Mico OAuth 刷新也推迟到确实需要取新钥匙时。已有真实活动的自动采集保持 --no-wake。

点击后优先复用已保存的视频凭据；鉴权或建链失败，在同一会话中仅重新取钥一次（force_fresh=True，no_wake=True），不重复唤醒，再失败就结束。准备中取消会取消异步任务，迟到重取钥请求返回410。

媒体路径：HA 签名 WebSocket → 原隐私网关的精确猫眼路由 → 路由器的有效会话媒体桥 → 本地 go2rtc。去掉两层内部 RTSP 转发。桥只接收有效会话的 MSE 协商，客厅保护路径、录像、人脸配置不变。旧客户端没有会话参数时保留旧路径。当前资源版本20261030-15，刷新页面载入。

猫眼专用 go2rtc 基于官方 v1.9.15（7c05066）定制，现行完整补丁和测试为 go2rtc-cold-start.patch；原二进制保留在 bin/go2rtc.stock-1.9.15。现行修改包括：转交编码探测期间收到的首关键帧；识别 AUD/SEI 后的参数集；ACK通知不再使用可被迟到ACK重启的纳秒ticker；鉴权响应只在鉴权请求期间可作隐式送达确认；搜索与后续协商各有独立的有界期限。初始媒体暂存上限4MiB。

搜索最多5秒，第一次回应后协商另给3秒，避免低功耗设备约4秒后才回应时只剩不到1秒而被误判失败。客户端初次建链/缓冲上限15秒，已有画面的补缓冲上限10秒；这些是失败上限，不是固定等待。仍保留 LAN 约1秒、公网约1.5秒播放缓冲。加快探测和强制TCP的试验没有稳定收益或建链失败，已撤回，保留自动选择TCP/UDP。未降低画质或通过常在线换速度。

服务关闭会结束自己的媒体连接；启动时只清理本目录 go2rtc 残留，避免旧实例占端口。stop_worker.py 只匹配本目录指定脚本，TERM后有界等待再清理。不要广泛终止其他 go2rtc、HA 或 Frigate。

当前最重要证据是 continued_cold_optimization_browser.json：真实 Chrome 首帧8291ms（客户端计时8282ms），准备763ms，后段约7528ms；31个呈现帧，1个丢帧，0次等待，约1秒缓冲，测试后35ms停止响应。此前端到端样本10641ms，更早12892ms。这是单次冷启动样本，不是每次必达或长播放零卡顿的保证。

分阶段协议证据为 detailed_startup_probe.json、final_source_probe.json、separated_deadline_probe.json、captured_handshake_probe.json；它们是收到视频/解码计时，不是浏览器首帧。一次抓包只保存控制报文类型、长度、时间，首4秒没有收到有效搜索回应，随后TCP建立仅约3ms；TCP鉴权响应约2.7秒，UDP鉴权样本约1.2秒。当前主要等待在设备CS2服务回应与鉴权，不能把这些阶段都算作HA或缓冲。

保留失败试验：fast_handshake_probe.json、tcp_handshake_probe.json、ack_handshake_probe.json、cloud_rpc_candidate.json。RPC wakeup_host未成功，未采用。停止/失效凭据证据仍见 cold_cancel_acceptance.json、cold_rekey_acceptance.json。当前模式无需以云中转视频作为回退。


### 本地动态端口实验

2026-10-31 的实验汇总在 research/dynamic_port_experiments.json；单轮回执同目录保存。放宽只读请求时限后 miIO.info 成功解密，已有 miIO 密钥有效；本地属性和唤醒 action 2/3 返回 -30012 busy。初始握手成功不等于视频主服务已可用。

两次有界云唤醒对照均未请求视频或刷新视频密钥。一次云请求742ms，设备首次向CS2服务器注册在4557ms。预挂探测的一次云请求1109ms，注册出现后立即向实际局域网动态端口发送握手，在6827ms收到P2P就绪；最终动态端口出现至就绪578ms。客户端没有请求外部CS2协调。这些是控制协议计时，不是解码或浏览器首帧，不替代此前8291ms的实际Chrome样本。

随后无设备请求闲置132.4秒，再读业务属性仍为busy，对已学端口和32108发局域网搜索/PUNCH，6021ms无回应。缓存动态端口本身没有实现本地冷唤醒；闲置时长和busy不是电气休眠状态测量。另一轮仅向设备实际使用的三台CS2服务器请求介绍，不发MIoT唤醒，首回应25ms，但均为fe000000非成功状态，未返回设备地址；未查明该状态的精确含义，不能据此区分注册、身份或策略问题。

现有视频授权vendor_params中的协调地址解码后均为127.*回环地址，核对原始加密授权后相同；这些参数不能直接用于公网协调，不影响独立字段中的本地视频鉴权材料。现有OAuth访问旧miss_get_vendor接口返回404，未替换生产凭据或硬编码另一设备的服务器池。

本轮未验证出可替换现行按需云唤醒的纯本地冷启动路线，未改播放、唤醒或保活配置。实验完成后上游producer/consumer为零，viewer_active=false。

### 其他接入路线的实测结果

完整候选清单与回执见 research/route_exhaustion_results.json 和 research/route_matrix.json。当前固件为5.3.1_0347，hw_ver=BL616_ECO，miio_ver=0.0.9，与同型号上游报告一致。记录区分真实解码、局部握手、源码同路径以及外部条件阻塞，不声称所有未来或私有方案均不可能。

两条替代媒体路线已出帧：官方原生miot_camera_lite v0.1.7在主机网络、专用UID媒体规则约束下成功解码15帧640×480；SDK开始到首解码5493ms，从现有prepare开始约9279ms。go2rtc PR#2631传输实现配合已有OAuth唤醒/取钥适配、仅接受猫眼LAN peer，成功解码9帧；整体9736ms，媒体连接到首解码3155ms。PR试验没有走原生米家账号登录，且将不可用的127.*协调参数替换为该设备实际注册所用的服务器；不能写成未修改源码的原样账号流程已验收。新计时均非浏览器呈现首帧，也不是长播放稳定性结论。

原生SDK只启动而不显式唤醒时未出帧（start=-1）。同一SDK实例在一次成功观看后停止视频，保留服务器内存90秒，期间专用UID所有非回环流量被阻断；设备目标丢弃计数为0。随后仅放通猫眼LAN、继续禁止云访问重启，start=-2且无帧。单纯保存软件状态没有实现离线冷启动。

本地miIO唤醒2/3、点亮屏幕2/2均返回-30012 busy；本地Start P2P、厂商wake方法变体、空闲后缓存端口，以及标准WoWLAN魔术包试验均未完成本地冷唤醒。参数与限制见单轮回执。缓存PR凭据的一次试验在鉴权处失败（凭据年龄2441秒），不能据此推定统一TTL。已有HA一次重取钥恢复已另行验收：首轮建链超时，重取钥1286ms后成功解码15帧，首解码9423ms，停止响应7ms，viewer_active=false。

标准RTSP/ONVIF/HomeKit发现未找到猫眼服务；活跃窗口测试的80、443、554、8554、8899、2020、8000、8080 TCP端口均未开放，此结论不覆盖所有未公开端口。hass-xiaomi-miot没有该型号的独立唤醒翻译，通用local action与已测报文相同；micam/Miloco使用同一原生SDK，上层默认cateye过滤及持续桥接需另做按需适配，未重复部署整个应用作为独立唤醒方案。

已授权账号清单（含路由器候选）及_miot-central._tcp发现没有找到可用小米中枢；现有Aqara M1S不构成该中枢测试。BLE被动扫描8秒接收305条广播但未识别猫眼，规格中GATT访问操作数为0；没有找到可执行的同型号BLE唤醒协议，不能据此否定所有私有BLE能力。适配器原先及恢复后均为关闭状态。OpenIPC/Thingino公开树有通用芯片支持，但未找到ph300/MJMY01BY命名的板级配置；未拆机、未复位、未刷机，仍缺精确板级信息、备份恢复手段和固件变更授权。

唤醒相关的实际下行控制数据确认为完整TLS记录，未获得可本地重发的明文唤醒指令；之后的空负载包是新连接握手，未作为唤醒信号重放。原生账号入口还需要现有OAuth之外的米家会话，未执行原样登录流；这不影响已出帧的OAuth适配传输结果。

日常HA后端未替换，仍为按需云唤醒、LAN视频和已有一次重取钥恢复。未新增摄像头保活或常驻原生SDK服务。实验容器及专用UID防火墙已移除，NVR上临时访问令牌副本已删，蓝牙状态已恢复；实验后生产producer/consumer均为0。

## 录像、容量和人脸

Frigate 摄像头 ID 为 cateye。录像位于 nvr-host /opt/frigate-poc/media/recordings（容器 /media/frigate/recordings），与客厅共用同一约 916GiB 文件系统及空间清理机制，无猫眼单独 2GiB 限额。普通录像保留 90 天，事件录像 30 天；只在视频活跃期间启用猫眼采集，不代表持续取流。

早期两个独立 MP4 测试片归档在共享盘 exports/cateye-legacy，没有伪造写入 Frigate 数据库。

猫眼使用固定相机配置、运行时 MQTT enabled 开关和临时取流入口，空闲时关闭。不要用 config/set 热增删 cateye：该路径曾导致录像进程 KeyError 与索引异常。retry_interval 为 1 秒，固定相机名保留，避免清理器误认为相机已删除。

人脸复用现有本地 Frigate large 模型、库和阈值，仅视频活跃时识别。HA template 保存最近一次实际识别成功的人名；休眠 unavailable 不覆盖人名。已有库样本接口匹配验证成功（分数 0.99），不是门口真人识别准确率。真实陌生人与门口真人识别效果尚未现场验收。

## 活跃采集触发

1. 手动实时查看确认产生播放帧后，开启 NVR 采集。
2. 被动观察已有 CS2 媒体流，排除心跳、控制包和本机自身采集，避免自触发。
3. 已有米家接口订阅按铃、经过、逗留事件，只有非保留新事件开启有界窗口；同一事件只试一次，不反复唤醒。

设备事件收到后写入 events.sqlite3（仅本机，保留 90 天），与 Frigate 的人/物识别事件分别保存。事件页合并显示真实设备事件和识别事件，并将设备事件与时间相邻的原生录像关联；没有录像时显示等待落盘，不伪造 Frigate 检测记录。首次实际收到手动查看的视频时记录“实时查看”，未出画面的取消请求不记成视频事件。已从旧单事件状态保留读回的真实 16:34 停留事件，不推测补造更早事件。

手动采集的心跳有效窗口由 3 秒改为 8 秒，以容忍 2.5 秒心跳及网络抖动；显式收起/切回放仍立即将 active 置为 false，不等待这 8 秒。

已收到并验证一条真实“有人停留”事件及对应本地录像；按铃、经过及同 AP 媒体可见性仍需实际触发验证。省电策略无法补齐本地连接建立前的画面。

## 其他能力调研

已有云端缓存读回支持电量/充电、SD状态/余量、强拆开关、勿扰时段、移动侦测参数、自动回复、屏幕息屏。适合后续通过属性推送接入提醒与控制，避免轮询唤醒。具体缓存见 capabilities_readback.json，不能当成实时读数。

设备声明强拆、过温、PIR误唤醒事件，但仍需真实事件验证。双向 Opus 通道存在，对讲和麦克风权限尚未验收。设备通用规格中的原生人脸/车辆属性本机返回不支持，人脸继续走 Frigate。

## 运维入口

- /etc/init.d/cateye-local：viewer / observer / events 三个实例，不重启 HA 或客厅录像。
- live_server.py：会话准备、停止、心跳、受会话约束的 MSE 媒体桥及旧 LAN WebRTC；18766 仅 LAN 控制与诊断。
- packages/cateye_same_origin.yaml：HA rest_command 转发猫眼 prepare / heartbeat / stop / events / rekey。
- event_history.py：真实设备事件与已出画面查看活动的持久记录，不写入 Frigate 人/物事件数据库。
- www/cateye-expand-card.js：猫眼展开卡与客厅同源低延迟播放器，加载共用回放组件。
- www/nvr-playback-card.js：统一回放控件；只通过现有 HA Frigate WebSocket 索引和签名 HLS 访问已保存录像。
- activity_collector.py / nvr_control.py：被动活跃采集与 MQTT 控制，仅作用于 cateye。
- mijia_events.py：轻量米家事件订阅。
- video_probe.py / cloud_wake.py：按需鉴权及已授权唤醒回退。
- packages/cateye_face_history.yaml：最近识别人脸记录。
- nvr-host 用户服务 nvr-privacy.service：客厅保护网关，安全流白名单包含 cateye_ha；不要把白名单泛化。
- nvr-host /opt/frigate-poc/patches/nginx.conf：已挂载的 VOD nginx 配置，变更后 nginx -t 和平滑 reload，无需重启 NVR。

凭据在 /config/.storage/cateye_local（0700，文件0600）。不得输出视频签名 URL、SDP、令牌和私钥。临时 OAuth 页面正常运行时关闭。

## 当前验收与边界

本轮证据：playback_alignment_acceptance.json、same_origin_acceptance.json、ha_playback_latency.json、playback_after.json、monitor_latency_acceptance.json。历史协议与人脸验证保留在 acceptance.json、lifecycle_acceptance.json。

- 主页面默认展开但不连接；本地上游 active producer / consumer 均为零。
- 实际 Chrome 猫眼同源 HEVC 首帧约 10.2 秒，记录过 206 帧，播放器缓冲样本 46 毫秒；客厅缓冲样本约 0.2–0.7 秒。冷启动包含唤醒和取钥，不是瞬时出画面。
- 独立同源接收测试：HEVC 首媒体约 11.3 秒，83 帧；H264 约 16.9 秒，68 帧。协议测试不替代浏览器兼容性及真实人脸效果验收。
- 实际 Chrome 猫眼回放、客厅当天原生回放已出画面；切回放后猫眼实时连接归零。
- 唤醒准备约 4.9 秒时收起，任务取消，上游连接归零；重新展开保持空闲，没有迟到重连。
- HA LAN 回放首段样本：111663 字节，首字节 238 毫秒、总计 240 毫秒。公网域名从路由器访问：首字节248毫秒、总计254毫秒。后者只验证该入口，不等于真实异地公网测速。
- 停止控制协议响应样本约9毫秒；不等同于所有网络环境的最终断流耗时。
- 客厅隐私模型 ready、既有保留天数和人脸库保留；本轮性能配置只平滑重载 nginx / 隐私网关，未再重启 Frigate。

回放统一组件 Chrome 验收：客厅窗口首屏 1205ms、猫眼短片 405ms、事件录像 1603ms；时间定位样本首屏403ms。已实际验证二倍速、快进十秒、日期切到前一天、摄像头切换、空事件提示和片段翻页。首次数据就绪计时是本机浏览器样本，不是异地公网承诺。纯逻辑检查覆盖上海时区、休眠空档、重叠索引不重复计时、播放窗口边界；本轮没有为了回放唤醒猫眼或重启 NVR。

最新事件/卡顿修复证据：event_hls_acceptance.json、stability_acceptance.json、event_refresh_acceptance.json。真实 16:34 停留事件已在浏览器显示并关联 11 秒录像。该段经 HA 签名 HLS 输出完整解码163帧、10.8秒，最大帧间隔0.067秒。一次有界取流检查解码333帧、22.067秒，最大媒体分片到达间隔0.454秒；这些是协议/解码证据，不代表用户播放端已实测零卡顿。猫眼已在验收后断流。
