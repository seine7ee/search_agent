import json,re,sys,datetime
from pathlib import Path
from collections import Counter
BASE=Path(__file__).resolve().parent;ROOT=BASE.parents[2];sys.path.insert(0,str(ROOT))
from traj_web_extractor.sentence_range_quotes import build_sentence_range_messages
D=json.load(open(BASE/'measurements.json'))
def norm(s):return re.sub(r'\s+','',s)
def getblocks(s):return {wid:body.strip() for wid,body in re.findall(r'^\[(\d+)\](.*?)(?=^\[\d+\]|\Z)',s,re.M|re.S)}
for g in D:
 t=json.load(open(g['trajectory_path']))['trajectory'];rows=[json.loads(l) for l in open(g['quotes_path'])];qm={(r['search_goal_id'],str(r['web']['web_id'])):r for r in rows};seen=[];rounddata=[];snapshots=[];ret=[];current_savings=[];oldout=0
 for r in t['rounds']:
  oldout+=len(json.dumps(r.get('evidence_extractor_call',{}).get('response',{}),ensure_ascii=False)) if 'evidence_extractor_call' in r else 0
  if r['kind']=='init':continue
  u=next(m['content'] for m in r['evidence_extractor_call']['messages'] if m['role']=='user');b=re.search(r'<webs>(.*?)</webs>',u,re.S).group(1);blocks=getblocks(b);goal=r['target_goal_id'];pairs=[(goal,i) for i in blocks]
  def pack(p):
   x=qm[p]
   return f"[{p[1]}] goal={p[0]} url={x['web']['url']}\ntitle={x['web']['title']} date={x['web'].get('date','')}\n"+'\n'.join(x['quotes']) if x['quotes'] else ''
  oldm=next(z for z in g['rounds'] if z['round_id']==r['round_id']);curpack='\n\n'.join(pack(p) for p in pairs if pack(p));histpack='\n\n'.join(pack(p) for p in seen if p not in pairs and pack(p))
  overhead=oldm['old_planner_chars']-len(re.search(r'<turn_webs>(.*?)</turn_webs>',next(m['content'] for m in r['planner_call']['messages'] if m['role']=='user'),re.S).group(1))-oldm['history_statement_chars']
  rounddata.append({'round_id':r['round_id'],'goal':goal,'current_source_pack_chars':len(curpack),'previous_source_pack_chars':len(histpack),'hypothetical_2k_history_chars':overhead+len(curpack)+min(2000,len(histpack)),'hypothetical_4k_history_chars':overhead+len(curpack)+min(4000,len(histpack)),'actual_current_web_chars':sum(len(v) for v in blocks.values())})
  for p in pairs:
   if p not in seen:seen.append(p)
   x=qm[p];actual=blocks[p[1]]
   snapshots.append({'round_id':r['round_id'],'goal':goal,'web_id':p[1],'same_normalized_body':norm(actual)==norm(x['web']['web_content']),'quotes_available_in_original_round':all(norm(q) in norm(actual) for q in x['quotes']),'empty_quote':not x['quotes'],'actual_chars':len(actual),'extraction_snapshot_chars':len(x['web']['web_content'])})
  for e in r.get('evidences',[]):
   for qu in e.get('quotes',[]):
    wid=str(qu['web_id']);raw=norm(blocks.get(wid,''));extr=norm('\n'.join(qm.get((goal,wid),{}).get('quotes',[])))
    for q in qu['quotes']:ret.append({'round_id':r['round_id'],'evidence_id':e['evidence_id'],'web_id':wid,'quote':q,'in_own_original_round_web':norm(q) in raw,'in_extraction_snapshot':norm(q) in norm(qm.get((goal,wid),{}).get('web',{}).get('web_content','')),'retained':norm(q) in extr})
 ext_input=0;ext_output=0
 ts=int(re.search(r'__(\d+)_',g['quotes_path']).group(1))/1000
 now=datetime.datetime.fromtimestamp(ts,datetime.timezone(datetime.timedelta(hours=8)))
 for x in rows:
  msgs=build_sentence_range_messages(g['query'],x['search_goal'],x['web']['web_content'],now=now)
  ext_input+=sum(len(m['content']) for m in msgs);ext_output+=len('|'.join(json.dumps(v) for v in x['sentence_ranges']) or '[]')
 orig=g['summary']['old_planner_chars'];init=g['rounds'][0]['old_planner_chars']
 g['diagnostics']={'snapshot_comparison':snapshots,'own_source_quote_checks':ret,'memory_scenarios_by_round':rounddata,'summary':{'round_web_exposures':len(snapshots),'snapshot_mismatches':sum(not x['same_normalized_body'] for x in snapshots),'nonempty_extracted_records_not_fully_present_in_original_round':sum(not x['quotes_available_in_original_round'] for x in snapshots),'quote_count':len(ret),'quote_verbatim_in_own_original_web':sum(x['in_own_original_round_web'] for x in ret),'quote_verbatim_in_extraction_input':sum(x['in_extraction_snapshot'] for x in ret),'retained_quotes':sum(x['retained'] for x in ret),'hypothetical_2k_history_reduction_pct':100*(1-(init+sum(x['hypothetical_2k_history_chars'] for x in rounddata))/orig),'hypothetical_4k_history_reduction_pct':100*(1-(init+sum(x['hypothetical_4k_history_chars'] for x in rounddata))/orig),'new_extractor_input_chars_reconstructed_from_current_prompt':ext_input,'new_extractor_range_output_chars':ext_output,'old_extractor_json_output_chars':oldout}}
 print(g['group'],g['diagnostics']['summary'])
(BASE/'measurements.json').write_text(json.dumps(D,ensure_ascii=False,indent=2)+'\n')
