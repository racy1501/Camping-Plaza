#!/usr/bin/env python3
"""Phase 2A：只用正式引擎分析体验与评价反馈，不读写生产存档。"""
from __future__ import annotations
import argparse, math, random, statistics, sys
from collections import Counter
from pathlib import Path
from typing import Any
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from camping_plaza.game_engine import CampingPlazaEngine
from balance_sim_v1 import PROJECT_IDS, debt_repayment_decision, day_end_actions, turn_actions
LAMBDAS=(1,1.25,1.5,1.75,2); WINDOWS=(10,20,30,50)
def avg(v): return statistics.fmean(v) if v else 0.0
def rating(e, scores):
 c=Counter(e._calculate_rating(x) for x in scores); n=len(scores)
 return [c[x]/n if n else 0 for x in range(1,6)]
def win(v,n=None): return avg(v[-n:] if n else v)
def simulate(seed, days, strategy):
 random.seed(seed); e=CampingPlazaEngine(':memory:'); rec=[]; reviews=[]; neg=[]; profiles={}; seen=set(); stack=[]
 apply=e.apply_satisfaction_delta; review=e._try_leave_review
 def traced(npc, delta):
  actual=apply(npc,delta)
  if actual<0: neg.append((stack[-1] if stack else 'unattributed',f'{seed}:{npc.id}',-actual))
  return actual
 def wrap(name, source):
  original=getattr(e,name)
  def f(*a,**k):
   stack.append(source)
   try: return original(*a,**k)
   finally: stack.pop()
  setattr(e,name,f)
 def depart(npc,result):
  review(npc,result)
  if npc.id in seen:return
  seen.add(npc.id); entry=e._find_arrival_plan_entry(npc_id=npc.id) or {}
  action=next((a for a in entry.get('planned_actions',[]) if a.get('action')=='dining'),None)
  unresolved=bool(npc.had_food_shortage and action and action.get('status')!='completed')
  menu=e.DINING_SET_MENUS[action['menu_key']] if unresolved else None
  rec.append({'id':f'{seed}:{npc.id}','p':float(npc.positive_experience_total),'n':float(npc.negative_experience_total),'final':float(npc.total_satisfaction),'events':sum(x[1]==f'{seed}:{npc.id}' for x in neg),'food':bool(npc.had_food_shortage),'unresolved':unresolved,'lost_income':menu['price_per_person']*npc.group_size if menu else 0,'lost_positive':menu['satisfaction_gain'] if menu else 0})
  if npc.review_left: reviews.append(npc.review_rating)
 e.apply_satisfaction_delta=traced; wrap('_apply_temporary_conflict_event','temporary_conflict'); wrap('_apply_broken_penalty','broken_tent'); e._try_leave_review=depart
 while e.state.day<=days:
  while e.state.turn<=5:
   if e.get_current_temporary_conflict_event(): e.resolve_current_temporary_conflict('verbal')
   if e.state.turn in (2,3,4,5):
    free,actions=turn_actions(e,strategy); assert e.submit_turn_plan(free,actions)['success']
   e.advance_turn()
  if e.state.day in (1,10,20): profiles[e.state.day]=(e.facilities['dining'].level,e.facilities['entertainment'].level,e.facilities['greenery'].greenery_satisfaction,e._calculate_development_degree())
  assert e.submit_day_end_actions(day_end_actions(e,strategy))['success']
  if e.state.day==days:break
  assert e.start_next_day()['success']
 return rec,reviews,neg,profiles
def shock(base):
 history=base*10; rows=[]
 for label,extra in [('3x2',[2]*3),('5x2',[2]*5),('8x2',[2]*8),('5x3',[3]*5),('10x3',[3]*10),('5x2+5x4',[2]*5+[4]*5),('5x2+10x4',[2]*5+[4]*10),('5x2+20x4',[2]*5+[4]*20)]:
  after=history+extra; rows.append((label,[win(after,n)-win(history,n) for n in (None,*WINDOWS)]))
 return rows

# Phase 2C 只在模拟实例上注入候选参数；正式引擎的需求、预约和结算代码不改动。
PHASE2C_CANDIDATES={
 'Baseline':(0.25,0.0),'A1':(0.35,0.0),'A2':(0.40,0.0),'A3':(0.50,0.0),
 'B1':(0.25,0.025),'B2':(0.25,0.050),'B3':(0.25,0.075),
 'C1':(0.35,0.025),'C2':(0.35,0.050),
}
PHASE2C_PERIODS=((1,10),(11,25),(26,45))
PHASE2C_BINS=(('<3.0',lambda x:x<3.0),('3.0-3.49',lambda x:3.0<=x<3.5),('3.5-3.99',lambda x:3.5<=x<4.0),('4.0-4.49',lambda x:4.0<=x<4.5),('>=4.5',lambda x:x>=4.5))
def clamp(v,lo,hi): return max(lo,min(hi,v))
def qstats(values):
 if not values:return {'mean':None,'median':None,'p10':None,'p90':None,'max':None}
 values=sorted(values); pick=lambda p:values[round((len(values)-1)*p)]
 return {'mean':avg(values),'median':pick(.5),'p10':pick(.1),'p90':pick(.9),'max':values[-1]}
def candidate_reservation_probability(engine,slope):
 rating=engine.get_average_rating()
 rating=3.0 if rating is None else rating
 return clamp(.15+(rating-3.0)*slope,.05,.30)
def install_phase2c_overlay(engine,weight,slope,reservations):
 """复用正式方法；仅替换候选的评分权重和预约概率判定。"""
 if weight!=.25:
  def management_quality():
   greenery=engine.facilities['greenery']
   other=((engine.facilities['dining'].level+1)/3+(engine.facilities['entertainment'].level+1)/3+greenery.greenery_satisfaction/10)/3
   return weight*engine._get_average_rating_ratio()+(1-weight)*other
  engine._calculate_management_quality=management_quality
 original=engine._generate_daily_reservation
 def generate_reservations():
  probability=candidate_reservation_probability(engine,slope) if slope else .15
  before=len(engine.state.reservations); original_random=random.random
  if slope:
   # 正式函数只将 random.random() 与 0.15 比较；这个单次映射仅改变该阈值的命中率。
   random.random=lambda:original_random()*.15/probability
  try: original()
  finally: random.random=original_random
  reservations[engine.state.day]=(len(engine.state.reservations)-before,probability)
 engine._generate_daily_reservation=generate_reservations
def phase2c_repay(engine):
 """沿用既有 balanced 成长储备逻辑，但通过正式 repay_debt action 执行。"""
 debt=type('DebtView',(),{'debt_remaining':engine.state.debt_remaining,'repayment_deadline_day':engine.state.repayment_deadline_day})()
 amount=debt_repayment_decision(engine,debt,'balanced')['amount']
 if amount: assert engine.repay_debt(amount)['success']
def simulate_phase2c(seed,days,name,weight,slope,capture_states=False):
 random.seed(seed); engine=CampingPlazaEngine(':memory:'); reservations={}; install_phase2c_overlay(engine,weight,slope,reservations)
 purchases={project:None for project in PROJECT_IDS}; daily=[]; states={}
 while engine.state.day<=days:
  while engine.state.turn<=5:
   if engine.get_current_temporary_conflict_event(): assert engine.resolve_current_temporary_conflict('verbal')['success']
   if engine.state.turn in (2,3,4,5):
    free,actions=turn_actions(engine,'balanced'); assert engine.submit_turn_plan(free,actions)['success']
   engine.advance_turn()
  result=engine.submit_day_end_actions(day_end_actions(engine,'balanced')); assert result['success']
  for item in result.get('results',[]):
   if item.get('success') and item.get('action')=='purchase_growth_project':
    project=(item.get('params') or {}).get('project_id')
    if project in purchases and purchases[project] is None:purchases[project]=engine.state.day
  phase2c_repay(engine)
  arrived=[x for x in engine.state.today_arrival_plan if x.get('arrival_status')=='arrived']
  profile=engine.state.daily_demand_profile or {}; generated,probability=reservations.get(engine.state.day,(0,.15))
  daily.append({'day':engine.state.day,'rating':engine.get_average_rating(),'natural_day':profile.get('natural_day_group_demand',0),'natural_overnight':profile.get('natural_overnight_group_demand',0),'day_served':sum(x.get('visit_type')=='day' for x in arrived),'overnight_served':sum(x.get('visit_type')=='overnight' for x in arrived),'reservation_served':sum(x.get('source')=='reservation' for x in arrived),'reservation_generated':generated,'reservation_probability':probability,'income':sum(engine.state.today_income.values()),'balance':engine.state.balance,'debt':engine.state.debt_remaining})
  if capture_states and engine.state.day in (1,20,45):
   greenery=engine.facilities['greenery'];states[engine.state.day]=((engine.facilities['dining'].level+1)/3,(engine.facilities['entertainment'].level+1)/3,greenery.greenery_satisfaction/10,engine._calculate_development_degree(),len(engine._get_unlocked_tents()))
  if engine.state.day==days:break
  assert engine.start_next_day()['success']
 normal=[purchases[x] for x in PROJECT_IDS if x!='hot_spring']
 return {'daily':daily,'purchases':purchases,'normal_complete':all(x is not None for x in normal),'normal_complete_day':max((x for x in normal if x is not None),default=None),'hot_day':purchases['hot_spring'],'states':states}
def phase2c_summary(runs):
 rows=[row for run in runs for row in run['daily']]; result={'periods':{},'balances':{},'rating_bins':{}}
 for first,last in PHASE2C_PERIODS:
  period=[x for x in rows if first<=x['day']<=last]
  result['periods'][f'{first}-{last}']={'day':avg([x['day_served'] for x in period]),'overnight':avg([x['overnight_served'] for x in period]),'total':avg([x['day_served']+x['overnight_served'] for x in period])}
 result['total_served_mean']=avg([sum(x['day_served']+x['overnight_served'] for x in run['daily']) for run in runs])
 result['reservation_daily_mean']=avg([x['reservation_generated'] for x in rows]);result['reservation_total_mean']=avg([sum(x['reservation_generated'] for x in run['daily']) for run in runs])
 served=sum(x['day_served']+x['overnight_served'] for x in rows);result['reservation_share']=sum(x['reservation_served'] for x in rows)/served if served else 0
 for day in (10,20,25,30,45):result['balances'][day]=qstats([x['balance'] for x in rows if x['day']==day])
 result['paid_by_25']=sum(next(x['debt'] for x in run['daily'] if x['day']==25)==0 for run in runs)/len(runs)
 paid=[next((x['day'] for x in run['daily'] if x['debt']==0),None) for run in runs];result['paid_day']=qstats([x for x in paid if x is not None]);result['unpaid_day45']=sum(run['daily'][-1]['debt']>0 for run in runs)/len(runs)
 result['normal_complete']=sum(run['normal_complete'] for run in runs)/len(runs);result['normal_day']=qstats([run['normal_complete_day'] for run in runs if run['normal_complete_day'] is not None]);result['hot_complete']=sum(run['hot_day'] is not None for run in runs)/len(runs);result['hot_day']=qstats([run['hot_day'] for run in runs if run['hot_day'] is not None])
 for label,predicate in PHASE2C_BINS:
  pairs=[]
  for run in runs:
   for now,nxt in zip(run['daily'],run['daily'][1:]):
    if now['rating'] is not None and predicate(now['rating']):pairs.append(nxt)
  result['rating_bins'][label]={'n':len(pairs),'natural_day':avg([x['natural_day'] for x in pairs]),'natural_overnight':avg([x['natural_overnight'] for x in pairs]),'reservations':avg([x['reservation_generated'] for x in pairs]),'income':avg([x['income'] for x in pairs])}
 return result
def phase2c_shock(states):
 rows=[]
 for stage,values in states.items():
  dining,entertainment,greenery,development,tents=values;other=(dining+entertainment+greenery)/3
  for name,(weight,slope) in PHASE2C_CANDIDATES.items():
   for rating in (2.5,3,3.5,4,4.5,5):
    quality=weight*(rating/5)+(1-weight)*other;day=quality*development*10;overnight=quality*development*6;reservation=(6+tents)*clamp(.15+(rating-3)*slope,.05,.30)
    rows.append((stage,name,rating,day,overnight,reservation,day+overnight+reservation))
 return rows
def print_phase2c(results,shock_rows):
 print('PHASE2C core candidate total_served reservation_daily reservation_share paid25 normal11 hot')
 for name,summary in results.items():print(name,f"{summary['total_served_mean']:.2f}",f"{summary['reservation_daily_mean']:.3f}",f"{summary['reservation_share']:.2%}",f"{summary['paid_by_25']:.2%}",f"{summary['normal_complete']:.2%}",f"{summary['hot_complete']:.2%}")
 print('PHASE2C periods candidate period day_served overnight_served total_served')
 for name,s in results.items():
  for period,v in s['periods'].items():print(name,period,f"{v['day']:.3f}",f"{v['overnight']:.3f}",f"{v['total']:.3f}")
 print('PHASE2C balances candidate day mean median p10 p90')
 for name,s in results.items():
  for day,v in s['balances'].items():print(name,day,*(f"{v[k]:.2f}" for k in ('mean','median','p10','p90')))
 print('PHASE2C timing candidate paid_median paid_p90 hot_median hot_p90 unpaid45')
 for name,s in results.items():print(name,s['paid_day']['median'],s['paid_day']['p90'],s['hot_day']['median'],s['hot_day']['p90'],f"{s['unpaid_day45']:.2%}")
 print('PHASE2C normal_timing candidate normal_median normal_p90')
 for name,s in results.items():print(name,s['normal_day']['median'],s['normal_day']['p90'])
 print('PHASE2C rating_bin candidate bin n day overnight reservation income')
 for name,s in results.items():
  for label,v in s['rating_bins'].items():print(name,label,v['n'],f"{v['natural_day']:.3f}",f"{v['natural_overnight']:.3f}",f"{v['reservations']:.3f}",f"{v['income']:.2f}")
 print('PHASE2C shock stage candidate rating day overnight reservation total')
 for row in shock_rows:print(row[0],row[1],row[2],*(f'{x:.3f}' for x in row[3:]))
def run_phase2c(args):
 results={};states={}
 names=args.phase2c_candidates.split(',') if args.phase2c_candidates else list(PHASE2C_CANDIDATES)
 unknown=set(names)-set(PHASE2C_CANDIDATES)
 if unknown:raise ValueError(f'unknown Phase 2C candidates: {sorted(unknown)}')
 for name in names:
  weight,slope=PHASE2C_CANDIDATES[name]
  runs=[simulate_phase2c(args.seed+i,args.days,name,weight,slope,capture_states=(name=='Baseline' and i==0)) for i in range(args.runs)]
  results[name]=phase2c_summary(runs)
  if name=='Baseline':states=runs[0]['states']
 shock_rows=phase2c_shock({{1:'early',20:'middle',45:'late'}[day]:value for day,value in states.items()}) if states else []
 print_phase2c(results,shock_rows)
 return results
def phase2d_drive_day(engine, reservations):
 while engine.state.turn<=5:
  if engine.get_current_temporary_conflict_event():assert engine.resolve_current_temporary_conflict('verbal')['success']
  if engine.state.turn in (2,3,4,5):
   free,actions=turn_actions(engine,'balanced');assert engine.submit_turn_plan(free,actions)['success']
  engine.advance_turn()
 result=engine.submit_day_end_actions(day_end_actions(engine,'balanced'));assert result['success'];phase2c_repay(engine)
 arrived=[x for x in engine.state.today_arrival_plan if x.get('arrival_status')=='arrived'];profile=engine.state.daily_demand_profile or {};generated,p=reservations.get(engine.state.day,(0,.15))
 return {'day':engine.state.day,'natural_day':profile.get('natural_day_group_demand',0),'natural_overnight':profile.get('natural_overnight_group_demand',0),'reservation':generated,'total':len(arrived),'income':sum(engine.state.today_income.values()),'probability':p}
def phase2d_shock(k,anchor,target):
 """从同一 baseline 状态出发，实例级强制 rating，真实推进后三天。"""
 random.seed(910000+anchor);engine=CampingPlazaEngine(':memory:');reservations={};install_phase2c_overlay(engine,.25,0,reservations)
 while engine.state.day<=anchor:
  phase2d_drive_day(engine,reservations)
  if engine.state.day==anchor:break
  assert engine.start_next_day()['success']
 install_phase2c_overlay(engine,.25,k,reservations);engine.get_average_rating=lambda:target
 rows=[]
 for _ in range(3):
  assert engine.start_next_day()['success'];rows.append(phase2d_drive_day(engine,reservations))
 return rows
def phase2d_probability_stats(runs):
 values=[x['reservation_probability'] for r in runs for x in r['daily']]
 stats=qstats(values);stats['p25']=sorted(values)[round((len(values)-1)*.25)];stats['p75']=sorted(values)[round((len(values)-1)*.75)]
 bins=Counter('<3.0' if x['rating'] is not None and x['rating']<3 else '3.0-3.49' if x['rating'] is not None and x['rating']<3.5 else '3.5-3.99' if x['rating'] is not None and x['rating']<4 else '4.0-4.49' if x['rating'] is not None and x['rating']<4.5 else '>=4.5' for r in runs for x in r['daily'])
 return stats,bins
def phase2d_grid(args):
 slopes=[round(x*.005,3) for x in range(21)];rows=[]
 for k in slopes:
  runs=[simulate_phase2c(args.seed+i,args.days,f'k={k}',.25,k) for i in range(args.runs)]
  stats,bins=phase2d_probability_stats(runs);base=[phase2d_shock(k,anchor,4.0) for anchor in (1,20,45)];low=[phase2d_shock(k,anchor,3.5) for anchor in (1,20,45)]
  diffs=[]
  for b,l in zip(base,low):diffs.append({'d1_guests':b[0]['total']-l[0]['total'],'d1_reservations':b[0]['reservation']-l[0]['reservation'],'d1_income':b[0]['income']-l[0]['income'],'d3_guests':sum(x['total'] for x in b)-sum(x['total'] for x in l),'d3_reservations':sum(x['reservation'] for x in b)-sum(x['reservation'] for x in l),'d3_income':sum(x['income'] for x in b)-sum(x['income'] for x in l)})
  rows.append({'k':k,'prob':stats,'bins':bins,'middle':diffs[1],'late':diffs[2]})
 print('PHASE2D_GRID k p35 p40 p45 middle_d1_arrivals middle_d1_reservations middle_d1_income middle_d3_arrivals middle_d3_reservations middle_d3_income late_d1_arrivals late_d1_reservations late_d1_income late_d3_arrivals late_d3_reservations late_d3_income')
 for x in rows:
  k=x['k'];p=lambda r:clamp(.15+(r-3)*k,.05,.30);m=x['middle'];l=x['late'];print(k,f'{p(3.5):.3f}',f'{p(4):.3f}',f'{p(4.5):.3f}',m['d1_guests'],m['d1_reservations'],m['d1_income'],m['d3_guests'],m['d3_reservations'],m['d3_income'],l['d1_guests'],l['d1_reservations'],l['d1_income'],l['d3_guests'],l['d3_reservations'],l['d3_income'])
 return rows
def main():
 p=argparse.ArgumentParser();p.add_argument('--seed',type=int,default=20260824);p.add_argument('--runs',type=int,default=500);p.add_argument('--days',type=int,default=20);p.add_argument('--strategy',default='balanced',choices=('growth_priority','balanced','quality_priority'));p.add_argument('--phase2c',action='store_true');p.add_argument('--phase2c-candidates');p.add_argument('--phase2d-grid',action='store_true');a=p.parse_args()
 if a.phase2d_grid:
  if a.days<45:raise ValueError('Phase 2D grid requires --days >= 45')
  phase2d_grid(a);return
 if a.phase2c:
  if a.days<45:raise ValueError('Phase 2C requires --days >= 45')
  run_phase2c(a);return
 runs=[simulate(a.seed+i,a.days,a.strategy) for i in range(a.runs)]; rec=[x for r in runs for x in r[0]]; reviews=[x for r in runs for x in r[1]]; events=[x for r in runs for x in r[2]]; e=CampingPlazaEngine(':memory:'); total=sum(x[2] for x in events)
 print(f'Phase2A runs={a.runs} days={a.days} groups={len(rec)} reviews={len(reviews)}')
 print('NEGATIVE_SOURCES source events affected_ratio avg_loss share')
 for src in ('broken_tent','temporary_conflict','unattributed'):
  x=[v for v in events if v[0]==src]; ids={v[1] for v in x}; loss=sum(v[2] for v in x);print(src,len(x),f'{len(ids)/len(rec):.2%}',f'{loss/len(ids) if ids else 0:.2f}',f'{loss/total if total else 0:.2%}')
 c=Counter(x['events'] for x in rec);print('NEG_EVENT_COUNTS',f'0={c[0]/len(rec):.2%}',f'1={c[1]/len(rec):.2%}',f'2={c[2]/len(rec):.2%}',f'3+={sum(v for k,v in c.items() if k>=3)/len(rec):.2%}')
 food=[x for x in rec if x['food']]; unresolved=[x for x in rec if x['unresolved']];print('FOOD_SHORTAGE',f'affected={len(food)/len(rec):.2%}',f'unresolved={len(unresolved)/len(rec):.2%}',f'lost_income={sum(x["lost_income"] for x in unresolved):.0f}',f'lost_positive={sum(x["lost_positive"] for x in unresolved):.0f}')
 print('LAMBDA bucket lambda avg_score <=2 3 4 5')
 for label,fn in [('N=0',lambda n:n==0),('0<N<5',lambda n:0<n<5),('5<=N<10',lambda n:5<=n<10),('N>=10',lambda n:n>=10)]:
  x=[v for v in rec if fn(v['n'])]
  for l in LAMBDAS:
   scores=[60+v['p']-l*v['n'] for v in x]; stars=rating(e,scores);print(label,l,len(x),f'{avg(scores):.2f}',*(f'{z:.2%}' for z in (stars[0]+stars[1],stars[2],stars[3],stars[4])))
 print('REVIEW_WINDOWS',' '.join(f'{n or "all"}={avg([win(r[1],n) for r in runs if r[1]]):.3f}' for n in (None,*WINDOWS)))
 candidates=[r[1][-50:] for r in runs if len(r[1])>=50] or [reviews[-50:]]
 base=min(candidates,key=lambda x:abs(win(x)-4));print('SHOCK_BASE',f'{win(base):.3f}')
 print('SHOCK all w10 w20 w30 w50');[print(name,*[f'{x:+.3f}' for x in vals]) for name,vals in shock(base)]
 print('DEMAND stage lv_d lv_e greenery D rating S day overnight')
 for name,day in [('early',1),('middle',10),('late',20)]:
  q=[r[3][day] for r in runs if day in r[3]]; dl,el,g,d=(avg([x[i] for x in q]) for i in range(4))
  for r in (2.5,3,3.5,4,4.5,5):
   s=(r/5+(dl+1)/3+(el+1)/3+g/10)/4;print(name,f'{dl:.2f}',f'{el:.2f}',f'{g:.2f}',f'{d:.3f}',r,f'{s:.3f}',f'{s*d*10:.2f}',f'{s*d*6:.2f}')
 print('CONFLICT base daily days_per 2day 3day 4day 5day 6day')
 for b in (.15,.20,.25,.30,.35,.40):
  q=b*(.36*.95+.64*1.05);print(f'{b:.0%}',f'{q:.2%}',f'{1/q:.2f}',*(f'{q**n:.3%}' for n in range(2,7)))
if __name__=='__main__':main()
