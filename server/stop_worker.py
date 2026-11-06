import pathlib,os,signal,sys,time
target=('/config/cateye_local/'+sys.argv[1]).encode()
def owned():
 result=[]
 for p in pathlib.Path('/proc').iterdir():
  if p.name.isdigit():
   try:
    a=(p/'cmdline').read_bytes().split(b'\0')
    if len(a)>1 and a[1]==target:result.append(int(p.name))
   except OSError:pass
 return result
for pid in owned():
 try:os.kill(pid,signal.SIGTERM)
 except ProcessLookupError:pass
for _ in range(15):
 if not owned():break
 time.sleep(.1)
for pid in owned():
 try:os.kill(pid,signal.SIGKILL)
 except ProcessLookupError:pass
