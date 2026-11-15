'''会员数据永久清除的端到端逻辑验证（本地无docker时用SQLite+fakeredis跑真实服务/路由/worker代码）。

用法：cd backend && python3 -m scripts.verify_purge
覆盖：基本清除与对账、暂停查看/导出、重复请求、计算中清除（并发写回防护）、
写文件中断恢复、重启恢复、失败重试不重复扣减、群数/样本不足标不可分析、权限。
'''
import os,sys,json,shutil
os.environ.setdefault('DATABASE_URL','sqlite:////tmp/purge_verify.db')
os.environ.setdefault('REDIS_URL','redis://localhost:6379/0')
os.environ.setdefault('JWT_SECRET','verify-secret')
os.environ.setdefault('ARTIFACTS_DIR','/tmp/purge_verify_artifacts')
import fakeredis,redis
fake=fakeredis.FakeRedis()
redis.Redis.from_url=lambda *a,**k:fake
for p in ('/tmp/purge_verify.db','/tmp/purge_verify_artifacts'):
 if os.path.isdir(p):shutil.rmtree(p)
 elif os.path.exists(p):os.remove(p)
from fastapi.testclient import TestClient
from src.config.database import Base,engine,Session
from src.models.entities import User,Reader,Segment,Outreach,Purge
from src.utils.security import hash_password
from src.services.purge import artifact_path
from src.main import app
import src.worker as worker
Base.metadata.create_all(engine)
PASS=[0]
def ok(cond,msg):
 assert cond,'FAIL: '+msg
 PASS[0]+=1;print('  ✓',msg)
def seed():
 with Session() as s:
  for role in ('admin','analyst','viewer'):s.add(User(email=role+'@demo.local',role=role,password=hash_password('Demo123!')))
  import random;rng=random.Random(7)
  for i in range(30):
   g=i%3
   s.add(Reader(code=f'T-{i+1:03}',weekly_reading=round([5,20,40][g]+rng.uniform(-1,1),2),completion=round([.3,.7,.9][g]+rng.uniform(-.02,.02),4),night_share=round([.2,.5,.8][g]+rng.uniform(-.02,.02),4),note=''))
  s.commit()
seed()
client=TestClient(app)
def login(email):
 r=client.post('/auth/login',json={'email':email,'password':'Demo123!'});assert r.status_code==200,r.text
 return {'Authorization':'Bearer '+r.json()['token']}
H=login('analyst@demo.local');HA=login('admin@demo.local');HV=login('viewer@demo.local')
def drain():
 '''模拟worker消费两个队列直至清空。'''
 while True:
  item=fake.blpop(['reader:segments','reader:purges'],timeout=1)
  if not item:return
  key=item[0].decode() if isinstance(item[0],bytes) else item[0]
  if key=='reader:segments':worker.process_segment(int(item[1]))
  else:worker.process_purge(int(item[1]))
def seg(sid):return client.get(f'/segments/{sid}',headers=H).json()
print('== 1. 基本清除流程：快照/成员/名单/档案全清，重算并对账 ==')
r=client.post('/segments',json={'name':'11月分群','k':3,'features':['weekly_reading','completion','night_share']},headers=H)
assert r.status_code==200,r.text
sid=r.json()['id'];drain()
d=seg(sid);assert d['state']=='done',d.get('error')
n0=d['result']['n'];sil0=d['result']['silhouette']
target=[m for m in d['result']['members'] if m['cluster_id']==0][0]['code']
size0=[p['size'] for p in d['result']['profiles'] if p['cluster_id']==0][0]
r=client.post('/outreaches',json={'segment_id':sid,'cluster_id':0,'title':'低频试点','note':'每周一次短篇精选推送'},headers=H)
assert r.status_code==200,r.text
oid=r.json()['id'];om0=len(r.json()['members'])
ok(target in r.json()['members'],f'触达名单含目标会员 {target}')
r=client.post('/purges',json={'code':target},headers=H)
assert r.status_code==200,r.text
pid=r.json()['id']
ok(r.json()['impact']['segments']==[sid] and r.json()['impact']['outreaches']==[oid],'影响范围含分群版本与触达方案')
ok(r.json()['state']=='pending','任务初始为待清理pending')
d=seg(sid)
ok(d.get('purging')==True and d['result'] is None,'任务完成前受影响版本暂停查看（结果遮蔽）')
r=client.get(f'/segments/{sid}/export',headers=H);ok(r.status_code==409,'受影响版本暂停导出(409)')
r=client.get(f'/segments',headers=H);ok(all(x.get('purging') for x in r.json() if x['id']==sid),'版本列表标记清除中')
r=client.get(f'/outreaches/{oid}/export',headers=H);ok(r.status_code==409,'受影响方案暂停导出(409)')
r=client.post(f'/outreaches/{oid}/approve',json={},headers=HA);ok(r.status_code==409,'受影响方案暂停签核(409)')
r=client.get('/outreaches',headers=H);ok(r.json()[0].get('purging')==True and r.json()[0]['members']==[],'方案名单暂停查看（遮蔽）')
r=client.post('/segments',json={'name':'清除中分群','k':2,'features':['weekly_reading','completion']},headers=H);ok(r.status_code==409,'清除进行中禁止新建分群(409)')
r=client.post('/outreaches',json={'segment_id':sid,'cluster_id':1,'title':'干扰方案','note':'12345678'},headers=H);ok(r.status_code==409,'清除进行中禁止基于受影响版本起草(409)')
with Session() as s:rid=s.query(Reader).filter_by(code=target).first().id
r=client.put(f'/readers/{rid}',json={'code':target,'weekly_reading':1,'completion':.5,'night_share':.5,'note':''},headers=H);ok(r.status_code==409,'清除进行中禁止修订该会员档案(409)')
drain()
p=client.get(f'/purges/{pid}',headers=H).json()
ok(p['state']=='done','清除任务完成')
ok(p['report']['consistent']==True and p['report']['reader_deleted']==True,'对账一致且档案已删除')
ok(p['progress']['done']==p['progress']['total'],'进度走满')
d=seg(sid)
ok(d.get('purging') is None and d['result'] is not None,'完成后版本恢复可查看')
r=d['result']
ok(r['n']==n0-1 and len(r['members'])==n0-1 and target not in json.dumps(r,ensure_ascii=False),'版本人数减一且无任何目标会员痕迹')
ok(r['purged_count']==1,'版本记录清除次数用于溯源（不含会员编号）')
ok(r['silhouette'] is not None and abs(r['silhouette']-sil0)>1e-9,'轮廓系数已按剩余成员重算')
ok([x['size'] for x in r['profiles'] if x['cluster_id']==0][0]==size0-1,'群画像人数重算')
ok(sum(x['size'] for x in r['profiles'])==r['n'],'各群人数之和与总数对账一致')
disk=json.loads(artifact_path(sid).read_text())
ok(disk['n']==n0-1 and target not in json.dumps(disk,ensure_ascii=False),'产物文件与库内一致且不含目标会员')
o=[x for x in client.get('/outreaches',headers=H).json() if x['id']==oid][0]
ok(len(o['members'])==om0-1 and target not in o['members'],'触达冻结名单移除目标会员且人数对账')
ok(all(x['code']!=target for x in client.get('/readers',headers=H).json()),'阅读档案已删除')
print('== 2. 重复请求与权限 ==')
r=client.post('/purges',json={'code':target},headers=H);ok(r.status_code==404,'已删会员再次发起返回404')
r=client.post('/segments',json={'name':'二次分群','k':3,'features':['weekly_reading','completion','night_share']},headers=H)
sid2=r.json()['id'];drain()
t2=seg(sid2)['result']['members'][0]['code']
r=client.post('/purges',json={'code':t2},headers=H);pid2=r.json()['id']
r=client.post('/purges',json={'code':t2},headers=H);ok(r.status_code==409,'同一会员重复发起被拒绝(409)')
from sqlalchemy.exc import IntegrityError
with Session() as s:
 s.add(Purge(reader_code=t2,state='pending',impact={},progress={}))
 try:s.commit();ok(False,'数据库唯一索引应拒绝第二个进行中任务')
 except IntegrityError:ok(True,'部分唯一索引保证并发下同一会员仅一个进行中任务')
r=client.post('/purges',json={'code':'T-030'},headers=HV);ok(r.status_code==403,'观察员不能发起清除(403)')
drain()
ok(client.get(f'/purges/{pid2}',headers=H).json()['state']=='done','清除任务执行完成')
print('== 3. 计算中清除：并发分群不得把已删数据写回 ==')
r=client.post('/segments',json={'name':'并发分群','k':3,'features':['weekly_reading','completion','night_share']},headers=H)
sid3=r.json()['id']
with Session() as s: # 模拟worker已取任务置running、正在计算
 row=s.get(Segment,sid3);row.state='running';s.commit()
with Session() as s:t3=s.get(Segment,sid3).result['snapshot'][0]['code']
r=client.post('/purges',json={'code':t3},headers=H)
ok(sid3 in r.json()['impact']['segments'],'影响范围包含计算中的版本')
pid3=r.json()['id'];drain()
d=seg(sid3)
ok(d['state']=='failed' and '作废' in d['error'],'计算中的版本被作废')
ok(t3 not in json.dumps(d['result'],ensure_ascii=False),'作废版本快照已清除目标会员')
with Session() as s: # 模拟迟到的worker写回：条件更新必须失效
 n=s.query(Segment).filter(Segment.id==sid3,Segment.state=='running').update({'state':'done','result':{'members':[{'code':t3}]}});s.commit()
 ok(n==0,'迟到的分群写回被条件更新挡下(0行)')
d=seg(sid3)
ok(d['state']=='failed' and t3 not in json.dumps(d['result'],ensure_ascii=False),'已删数据未被写回')
ok(not artifact_path(sid3).exists(),'作废版本无产物文件残留')
ok(client.get(f'/purges/{pid3}',headers=H).json()['report']['consistent']==True,'并发清除后对账一致')
print('== 4. 写文件中断：半截产物被原子重写恢复 ==')
r=client.post('/segments',json={'name':'中断恢复','k':3,'features':['weekly_reading','completion','night_share']},headers=H)
sid4=r.json()['id'];drain()
full=artifact_path(sid4).read_text()
artifact_path(sid4).write_text(full[:len(full)//2]) # 模拟写文件中断的半截JSON
open(str(artifact_path(sid4))+'.tmp','w').write('{"partial"') # 中断残留的临时文件
try:json.loads(artifact_path(sid4).read_text());ok(False,'前提：产物应为损坏状态')
except json.JSONDecodeError:ok(True,'前提：产物文件已损坏（半截JSON）')
with Session() as s:t4=s.get(Segment,sid4).result['snapshot'][1]['code']
r=client.post('/purges',json={'code':t4},headers=H);pid4=r.json()['id'];drain()
disk=json.loads(artifact_path(sid4).read_text())
ok(disk['n']==seg(sid4)['result']['n'] and t4 not in json.dumps(disk,ensure_ascii=False),'产物被原子重写为完整一致JSON')
ok(not os.path.exists(str(artifact_path(sid4))+'.tmp'),'中断临时文件已清理')
ok(client.get(f'/purges/{pid4}',headers=H).json()['report']['consistent']==True,'中断恢复后对账一致')
print('== 5. 重启恢复：running任务重新入队并幂等完成 ==')
r=client.post('/segments',json={'name':'重启恢复','k':3,'features':['weekly_reading','completion','night_share']},headers=H)
sid5=r.json()['id'];drain()
with Session() as s:t5=s.get(Segment,sid5).result['snapshot'][2]['code']
r=client.post('/purges',json={'code':t5},headers=H);pid5=r.json()['id']
fake.flushall() # 模拟队列与服务同时重启前队列丢失/任务卡在running
with Session() as s:
 row=s.get(Purge,pid5);row.state='running';s.commit()
 row=s.get(Segment,sid5);row.state='running';s.commit() # 分群也卡在running
worker.recover()
with Session() as s:
 ok(s.get(Purge,pid5).state=='pending','重启后卡住的清除任务重置为pending')
 ok(s.get(Segment,sid5).state=='queued','重启后卡住的分群重置为queued')
drain()
p=client.get(f'/purges/{pid5}',headers=H).json()
ok(p['state']=='done' and p['report']['consistent']==True,'重启后清除任务恢复并完成、对账一致')
ok(all(x['code']!=t5 for x in client.get('/readers',headers=H).json()),'重启恢复后档案删除生效')
print('== 6. 失败重试不重复扣减 ==')
r=client.post('/segments',json={'name':'重试幂等','k':3,'features':['weekly_reading','completion','night_share']},headers=H)
sid6=r.json()['id'];drain()
before=seg(sid6)['result']['n']
with Session() as s:t6=s.get(Segment,sid6).result['snapshot'][3]['code']
import src.services.purge as purgemod
real_write=purgemod.write_artifact;calls=[0]
def flaky(sid_,res):
 calls[0]+=1
 if calls[0]==1:raise OSError('模拟写文件失败')
 return real_write(sid_,res)
purgemod.write_artifact=flaky
worker.write_artifact=flaky
r=client.post('/purges',json={'code':t6},headers=H);pid6=r.json()['id'];drain()
purgemod.write_artifact=real_write;worker.write_artifact=real_write
p=client.get(f'/purges/{pid6}',headers=H).json()
ok(p['state']=='failed' and p['error'],'任务失败并记录失败原因')
r=client.post('/purges',json={'code':t6},headers=H)
ok(r.status_code==200,'失败后可重新发起')
pid6b=r.json()['id'];drain()
p=client.get(f'/purges/{pid6b}',headers=H).json()
ok(p['state']=='done' and p['report']['consistent']==True,'重试成功且对账一致')
ok(seg(sid6)['result']['n']==before-1,'重试不重复扣减（人数仅减一）')
print('== 7. 群数/样本不足标为不可分析 ==')
with Session() as s:
 for i,(w,c,nt) in enumerate([(1,.1,.1),(1.1,.1,.1),(.9,.1,.1),(50,.9,.9),(51,.9,.9),(49,.9,.9)]):
  s.add(Reader(code=f'X-{i+1:03}',weekly_reading=w,completion=c,night_share=nt,note=''))
 s.commit()
with Session() as s: # 直接构造只含这6位读者的版本（模拟当时库内仅有他们）
 snap=[{'id':r.id,'code':r.code,'weekly_reading':r.weekly_reading,'completion':r.completion,'night_share':r.night_share,'note':''} for r in s.query(Reader).filter(Reader.code.like('X-%')).all()]
 row=Segment(name='小样本',k=2,features=['weekly_reading','completion','night_share'],state='queued',result={'snapshot':snap});s.add(row);s.commit();sid7=row.id
worker.process_segment(sid7)
d=seg(sid7)
ok(d['state']=='done' and d['result']['n']==6,'小样本版本训练成功')
empty_cluster=[p['cluster_id'] for p in d['result']['profiles'] if any(m['code']=='X-001' for m in d['result']['members'] if m['cluster_id']==p['cluster_id'])][0]
victims=[m['code'] for m in d['result']['members'] if m['cluster_id']==empty_cluster]
ok(len(victims)==3,'前提：目标群3位成员')
for i,code in enumerate(victims):
 r=client.post('/purges',json={'code':code},headers=H);drain()
 d=seg(sid7)['result']
 if i<2:ok(d.get('analyzable',True)==True,f'清除{i+1}人后仍可分析')
d=seg(sid7)['result']
ok(d['analyzable']==False and '群' in d['reason'],'某群清空后标为不可分析并说明原因')
ok(d['silhouette'] is None,'不可分析时轮廓系数为空')
ok(all(p['portrait'] is not None for p in d['profiles'] if p['size']>0) and [p for p in d['profiles'] if p['size']==0][0]['portrait'] is None,'空群画像置空、其余群画像保留')
left=[m['code'] for m in d['members']]
for code in left[:1]:
 r=client.post('/purges',json={'code':code},headers=H);drain()
d=seg(sid7)['result']
ok(d['n']==2 and d['analyzable']==False and '样本' in d['reason'],'剩余样本数不大于k时标为不可分析')
print('== 8. API竞态兜底：绕过检查的新版本/方案仍被清除 ==')
with Session() as s:ok(s.query(Reader).filter_by(code='T-010').first() is not None,'前提：T-010 仍在库')
r=client.post('/purges',json={'code':'T-010'},headers=H)
assert r.status_code==200,r.text
pid8=r.json()['id']
with Session() as s: # 模拟与purge并发、绕过409检查插入的版本和方案
 snap=[{'id':x.id,'code':x.code,'weekly_reading':x.weekly_reading,'completion':x.completion,'night_share':x.night_share,'note':''} for x in s.query(Reader).limit(8).all()]
 s8=Segment(name='竞态版本',k=2,features=['weekly_reading','completion'],state='queued',result={'snapshot':snap});s.add(s8);s.commit();sid8=s8.id
 o8=Outreach(segment_id=sid8,cluster_id=0,title='竞态方案',note='并发插入的冻结名单',members=['T-010','T-011'],status='draft');s.add(o8);s.commit();oid8=o8.id
drain() # purge执行时合并影响范围，作废竞态版本并清理竞态方案
p=client.get(f'/purges/{pid8}',headers=H).json()
ok(sid8 in p['impact']['segments'] and oid8 in p['impact']['outreaches'],'执行时合并影响范围，兜住并发新建记录')
ok(p['state']=='done' and p['report']['consistent']==True,'竞态场景下清除完成且对账一致')
d=seg(sid8)
ok(d['state']=='failed' and 'T-010' not in json.dumps(d['result'],ensure_ascii=False),'竞态版本被作废且快照清理')
o=[x for x in client.get('/outreaches',headers=H).json() if x['id']==oid8][0]
ok('T-010' not in o['members'] and len(o['members'])==1,'竞态方案冻结名单被清理')
ok(all(x['code']!='T-010' for x in client.get('/readers',headers=H).json()),'竞态场景下档案删除生效')
with Session() as s: # worker兜底：活跃清除期间不得计算含被撤会员的快照
 snap=[{'id':x.id,'code':x.code,'weekly_reading':x.weekly_reading,'completion':x.completion,'night_share':x.night_share,'note':''} for x in s.query(Reader).limit(8).all()]
 s9=Segment(name='竞态版本2',k=2,features=['weekly_reading','completion'],state='queued',result={'snapshot':snap});s.add(s9);s.commit();sid9=s9.id
r=client.post('/purges',json={'code':'T-011'},headers=H);pid9=r.json()['id']
worker.process_segment(sid9)
d=seg(sid9)
ok(d['state']=='failed' and 'T-011' not in json.dumps(d['result'],ensure_ascii=False),'worker兜底作废竞态版本并清理快照')
drain()
ok(client.get(f'/purges/{pid9}',headers=H).json()['state']=='done','竞态清除任务完成')
print(f'\n全部 {PASS[0]} 项断言通过。')
