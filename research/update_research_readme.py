import os
from pathlib import Path

path = Path('/config/cateye_local/README.md')
text = path.read_text()
marker = '### 本地动态端口实验'
anchor = '\n## 录像、容量和人脸'
addition = '''
### 本地动态端口实验

2026-10-31 的实验汇总在 research/dynamic_port_experiments.json；单轮回执同目录保存。放宽只读请求时限后 miIO.info 成功解密，已有 miIO 密钥有效；本地属性和唤醒 action 2/3 返回 -30012 busy。初始握手成功不等于视频主服务已可用。

两次有界云唤醒对照均未请求视频或刷新视频密钥。一次云请求742ms，设备首次向CS2服务器注册在4557ms。预挂探测的一次云请求1109ms，注册出现后立即向实际局域网动态端口发送握手，在6827ms收到P2P就绪；最终动态端口出现至就绪578ms。客户端没有请求外部CS2协调。这些是控制协议计时，不是解码或浏览器首帧，不替代此前8291ms的实际Chrome样本。

随后无设备请求闲置132.4秒，再读业务属性仍为busy，对已学端口和32108发局域网搜索/PUNCH，6021ms无回应。缓存动态端口本身没有实现本地冷唤醒；闲置时长和busy不是电气休眠状态测量。另一轮仅向设备实际使用的三台CS2服务器请求介绍，不发MIoT唤醒，首回应25ms，但均为fe000000非成功状态，未返回设备地址；未查明该状态的精确含义，不能据此区分注册、身份或策略问题。

现有视频授权vendor_params中的协调地址解码后均为127.*回环地址，核对原始加密授权后相同；这些参数不能直接用于公网协调，不影响独立字段中的本地视频鉴权材料。现有OAuth访问旧miss_get_vendor接口返回404，未替换生产凭据或硬编码另一设备的服务器池。

本轮未验证出可替换现行按需云唤醒的纯本地冷启动路线，未改播放、唤醒或保活配置。实验完成后上游producer/consumer为零，viewer_active=false。
'''
if marker in text:
    print('already_present')
elif text.count(anchor) == 1:
    temp = path.with_name('README.md.dynamic-research.tmp')
    temp.write_text(text.replace(anchor, '\n' + addition + anchor, 1))
    os.chmod(temp, path.stat().st_mode & 0o777)
    os.replace(temp, path)
    print('readme_research_updated')
else:
    raise RuntimeError('readme_anchor_not_unique')
