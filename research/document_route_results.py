import os
from pathlib import Path

path = Path('/config/cateye_local/README.md')
text = path.read_text()
marker = '### 其他接入路线的实测结果'
anchor = '\n## 录像、容量和人脸'
addition = '''
### 其他接入路线的实测结果

完整候选清单与回执见 research/route_exhaustion_results.json 和 research/route_matrix.json。当前固件为5.3.1_0347，hw_ver=BL616_ECO，miio_ver=0.0.9，与同型号上游报告一致。记录区分真实解码、局部握手、源码同路径以及外部条件阻塞，不声称所有未来或私有方案均不可能。

两条替代媒体路线已出帧：官方原生miot_camera_lite v0.1.7在主机网络、专用UID媒体规则约束下成功解码15帧640×480；SDK开始到首解码5493ms，从现有prepare开始约9279ms。go2rtc PR#2631传输实现配合已有OAuth唤醒/取钥适配、仅接受猫眼LAN peer，成功解码9帧；整体9736ms，媒体连接到首解码3155ms。PR试验没有走原生米家账号登录，且将不可用的127.*协调参数替换为该设备实际注册所用的服务器；不能写成未修改源码的原样账号流程已验收。新计时均非浏览器呈现首帧，也不是长播放稳定性结论。

原生SDK只启动而不显式唤醒时未出帧（start=-1）。同一SDK实例在一次成功观看后停止视频，保留服务器内存90秒，期间专用UID所有非回环流量被阻断；设备目标丢弃计数为0。随后仅放通猫眼LAN、继续禁止云访问重启，start=-2且无帧。单纯保存软件状态没有实现离线冷启动。

本地miIO唤醒2/3、点亮屏幕2/2均返回-30012 busy；本地Start P2P、厂商wake方法变体、空闲后缓存端口，以及标准WoWLAN魔术包试验均未完成本地冷唤醒。参数与限制见单轮回执。缓存PR凭据的一次试验在鉴权处失败（凭据年龄2441秒），不能据此推定统一TTL。已有HA一次重取钥恢复已另行验收：首轮建链超时，重取钥1286ms后成功解码15帧，首解码9423ms，停止响应7ms，viewer_active=false。

标准RTSP/ONVIF/HomeKit发现未找到猫眼服务；活跃窗口测试的80、443、554、8554、8899、2020、8000、8080 TCP端口均未开放，此结论不覆盖所有未公开端口。hass-xiaomi-miot没有该型号的独立唤醒翻译，通用local action与已测报文相同；micam/Miloco使用同一原生SDK，上层默认cateye过滤及持续桥接需另做按需适配，未重复部署整个应用作为独立唤醒方案。

已授权账号清单（含路由器候选）及_miot-central._tcp发现没有找到可用小米中枢；现有Aqara M1S不构成该中枢测试。BLE被动扫描8秒接收305条广播但未识别猫眼，规格中GATT访问操作数为0；没有找到可执行的同型号BLE唤醒协议，不能据此否定所有私有BLE能力。适配器原先及恢复后均为关闭状态。OpenIPC/Thingino公开树有通用芯片支持，但未找到ph300/MJMY01BY命名的板级配置；未拆机、未复位、未刷机，仍缺精确板级信息、备份恢复手段和固件变更授权。

唤醒相关的实际下行控制数据确认为完整TLS记录，未获得可本地重发的明文唤醒指令；之后的空负载包是新连接握手，未作为唤醒信号重放。原生账号入口还需要现有OAuth之外的米家会话，未执行原样登录流；这不影响已出帧的OAuth适配传输结果。

日常HA后端未替换，仍为按需云唤醒、LAN视频和已有一次重取钥恢复。未新增摄像头保活或常驻原生SDK服务。实验容器及专用UID防火墙已移除，NVR上临时访问令牌副本已删，蓝牙状态已恢复；实验后生产producer/consumer均为0。
'''
if marker in text:
    start = text.index('\n' + marker)
    stop = text.index(anchor, start)
    text = text[:start] + '\n' + addition.strip() + '\n' + text[stop:]
else:
    assert text.count(anchor) == 1
    text = text.replace(anchor, '\n' + addition.strip() + '\n' + anchor, 1)
temp = path.with_name('README.md.routes.tmp')
temp.write_text(text)
os.chmod(temp, path.stat().st_mode & 0o777)
os.replace(temp, path)
print('route_results_documented')
