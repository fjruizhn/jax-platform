import subprocess
from pathlib import Path
from cv_office_test_env import environment,JXP
code=r'''
import asyncio,hashlib,json,statistics,time
from pathlib import Path
from db.connection import get_pool
from db.transaccion import AISLAMIENTO_ADMIN,transaccion
from tests.identidades import crear_usuario,sql
from virtual_office.mfa import _insert_recovery_codes
async def run():
 user,_=await crear_usuario();out=[]
 try:
  for count in (8,16,32,64,128):
   elapsed=[];sizes=[]
   for repeat in range(12):
    start=time.perf_counter()
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
     await cur.execute('DELETE FROM office_mfa_recovery_code WHERE user_id=%s',(user,))
     codes=await _insert_recovery_codes(cur,user,0,count)
    encoded=json.dumps({'recovery_codes':codes},separators=(',',':')).encode()
    assert len(codes)==count and len(set(codes))==count
    elapsed.append(time.perf_counter()-start);sizes.append(len(encoded))
   samples=sorted(elapsed);position=(len(samples)-1)*.95;i=int(position);p95=samples[i]+(samples[min(i+1,len(samples)-1)]-samples[i])*(position-i)
   out.append(dict(count=count,repetitions=len(elapsed),median_s=statistics.median(elapsed),p95_s=p95,response_bytes=max(sizes),seconds=elapsed))
  receipt=dict(date='2026-10-20',by='Codex root',scope='synthetic disposable MariaDB33991 only; random generation/hash/executemany+commit+JSON serialization, NOT full MFA HTTP authentication/concurrency',results=out)
  Path('/home/fruiz/.local/share/codex-worktrees/jxp-virtual-office-20261020/.superpowers/sdd/2026-10-20-office-administration/recovery-code-measurement.json').write_text(json.dumps(receipt,indent=2)+'\n')
  print(json.dumps([{k:v for k,v in row.items() if k!='seconds'} for row in out]))
 finally:
  await sql('DELETE FROM office_mfa_recovery_code WHERE user_id=%s',(user,));await sql('DELETE FROM jax_users WHERE user_id=%s',(user,))
  pool=await get_pool();pool.close();await pool.wait_closed()
asyncio.run(run())
'''
env=environment();assert env['JAX_DB_PORT']=='33991'
raise SystemExit(subprocess.run([str(JXP/'backend/.venv/bin/python'),'-c',code],cwd=JXP/'backend',env=env).returncode)
