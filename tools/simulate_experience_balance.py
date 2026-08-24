#!/usr/bin/env python3
"""Phase 2A：只用正式引擎分析体验与评价反馈，不读写生产存档。"""
from __future__ import annotations
import argparse, random, statistics, sys
from collections import Counter
from pathlib import Path
from typing import Any
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from camping_plaza.game_engine import CampingPlazaEngine
from balance_sim_v1 import day_end_actions, turn_actions
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
def main():
 p=argparse.ArgumentParser();p.add_argument('--seed',type=int,default=20260824);p.add_argument('--runs',type=int,default=500);p.add_argument('--days',type=int,default=20);p.add_argument('--strategy',default='balanced',choices=('growth_priority','balanced','quality_priority'));a=p.parse_args()
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
