import os,select,struct,time,json,ipaddress
from pathlib import Path
deadline=time.monotonic()+30
def read_exact(n):
 data=b''
 while len(data)<n:
  remaining=deadline-time.monotonic()
  if remaining<=0 or not select.select([0],[],[],remaining)[0]:return None
  part=os.read(0,n-len(data))
  if not part:return None
  data+=part
 return data
rows=[];header=read_exact(24)
if header:
 endian='<'if header[:4]in[b'\xd4\xc3\xb2\xa1',b'\x4d\x3c\xb2\xa1']else'>'
 while time.monotonic()<deadline:
  h=read_exact(16)
  if not h:break
  sec,usec,length,wire=struct.unpack(endian+'IIII',h)
  if length>65536:break
  p=read_exact(length)
  if not p or len(p)<42:continue
  offset=18 if p[12:14]==b'\x81\x00'else 14
  ip=p[offset:]
  if len(ip)<28 or ip[9]!=17:continue
  ihl=(ip[0]&15)*4
  if len(ip)<ihl+12:continue
  src=str(ipaddress.ip_address(ip[12:16]));dst=str(ipaddress.ip_address(ip[16:20]))
  udp=ip[ihl:];size=int.from_bytes(udp[4:6],'big')-8;data=udp[8:]
  if data[0]!=0xf1 or data[1]not in[0x30,0x41,0x42,0x43]:continue
  if len(rows)<120:rows.append({'at':sec+usec/1000000,'direction':'camera_to_router'if src=='192.168.1.210'else'router_to_camera','kind':hex(data[1]),'udp_payload_length':size,'declared_length':int.from_bytes(data[2:4],'big')})
p=Path('/config/cateye_local/camera_handshake_packets.json');p.write_text(json.dumps(rows,indent=2));os.chmod(p,0o600)
print(json.dumps({'packet_count':len(rows),'first_control_packets':rows[:18]}))
