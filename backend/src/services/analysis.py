import json,hashlib
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
FEATURES=['weekly_reading','completion','night_share']
SEED=3107
def cluster(rows,features,k):
 if len(features)<2 or len(set(features))!=len(features) or not set(features)<=set(FEATURES):raise ValueError('选择2–3个不同的有效特征')
 if k<2 or k>6:raise ValueError('分群数仅支持2–6')
 if len(rows)<=k:raise ValueError('样本数必须大于分群数，补充会员档案或降低k')
 x=np.array([[r[f] for f in features] for r in rows],dtype=float)
 if not np.isfinite(x).all():raise ValueError('特征包含缺失或非有限值，请先修订档案')
 if np.any(np.ptp(x,axis=0)<=1e-12):raise ValueError('所选特征中存在常量列，请移除该特征或补充有差异的样本')
 if len(np.unique(x,axis=0))<k:raise ValueError('不同特征点少于k，不能形成指定数量的群组')
 scaler=StandardScaler();z=scaler.fit_transform(x);model=KMeans(n_clusters=k,random_state=SEED,n_init=20,algorithm='lloyd');labels=model.fit_predict(z)
 if len(set(labels))!=k:raise ValueError('实际群数小于k，请检查重复点或降低k')
 centers=scaler.inverse_transform(model.cluster_centers_)
 order=sorted(range(k),key=lambda i:tuple(centers[i]));mapping={old:new for new,old in enumerate(order)}
 memberships=[dict(id=r['id'],code=r['code'],cluster_id=mapping[int(label)],**{f:r[f] for f in FEATURES},x=float(point[0]),y=float(point[1])) for r,label,point in zip(rows,labels,z)]
 profiles=[]
 for old in order:
  members=[r for r in memberships if r['cluster_id']==mapping[old]]
  profiles.append(dict(cluster_id=mapping[old],size=len(members),portrait={f:float(np.mean([r[f] for r in members])) for f in FEATURES},center_inverse={f:float(v) for f,v in zip(features,centers[old])},member_codes=[r['code'] for r in members]))
 return dict(n=len(rows),k=k,features=features,profiles=profiles,members=memberships,silhouette=float(silhouette_score(z,labels)),inertia=float(model.inertia_),scaler_mean=scaler.mean_.tolist(),scaler_scale=scaler.scale_.tolist(),seed=SEED,checksum=hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),method='StandardScaler总体均值/标准差→KMeans欧氏距离，n_init=20、seed=3107；群号按原始中心排序，便于同一版本复核；轮廓系数用标准化空间。分群是行为相似性，不是会员价值或因果判断。')
def recalc_after_removal(res,code):
 '''会员数据清除后的原地重算：保留其余会员群号与训练期scaler/中心，仅移除该会员并重算画像、轮廓系数与checksum；群数或样本不足时明确标为不可分析。幂等，可安全重试。'''
 had=any(m.get('code')==code for m in res.get('members') or []) or any(r.get('code')==code for r in res.get('snapshot') or [])
 members=[m for m in res.get('members') or [] if m.get('code')!=code]
 snapshot=[r for r in res.get('snapshot') or [] if r.get('code')!=code]
 features=res['features'];k=res['k']
 profiles=[]
 for p in res.get('profiles') or []:
  ms=[m for m in members if m['cluster_id']==p['cluster_id']]
  profiles.append({**p,'size':len(ms),'portrait':{f:float(np.mean([m[f] for m in ms])) for f in FEATURES} if ms else None,'member_codes':[m['code'] for m in ms]})
 out={**res,'members':members,'snapshot':snapshot,'n':len(members),'profiles':profiles}
 out['purged_count']=int(res.get('purged_count') or 0)+(1 if had else 0)
 reasons=[]
 if len(members)<=k:reasons.append('剩余样本数不大于群数k，无法维持原有分群结构')
 if sum(1 for p in profiles if p['size']>0)<k:reasons.append('存在没有成员的群组，实际群数不足k')
 x=np.array([[m[f] for f in features] for m in members],dtype=float) if members else np.zeros((0,len(features)))
 if len(members) and len(np.unique(x,axis=0))<k:reasons.append('不同特征点少于k')
 if reasons:
  out['analyzable']=False;out['reason']='；'.join(reasons);out['silhouette']=None
 else:
  mean=np.array(res['scaler_mean'],dtype=float);scale=np.array(res['scaler_scale'],dtype=float)
  z=(x-mean)/scale;labels=np.array([m['cluster_id'] for m in members])
  centers={p['cluster_id']:np.array([p['center_inverse'][f] for f in features],dtype=float) for p in res['profiles']}
  cz=np.stack([(centers[c]-mean)/scale for c in range(k)])
  out['silhouette']=float(silhouette_score(z,labels));out['inertia']=float(((z-cz[labels])**2).sum())
  out['analyzable']=True;out.pop('reason',None)
 out['checksum']=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
 return out
