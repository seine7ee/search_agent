import json,re,hashlib
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[3];OUT=Path(__file__).resolve().parent
QUERIES=['小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？','近三年动力电池技术有哪些重要突破，分别由哪些企业推动？','梳理生成式 AI 在搜索产品中的主要落地方式和代表性产品。']
def norm(s):return re.sub(r'\s+','',s)
def block(s,tag):return re.search('<'+tag+'>(.*?)</'+tag+'>',s,re.S).group(1)
def ids(s):return re.findall(r'^\[(\d+)\]',s,re.M)
results=[]
for gi,q in enumerate(QUERIES,1):
 tp=next((ROOT/'batch_trajectories/multi_agent').glob('traj_'+q+'_*'));qp=next((ROOT/'traj_web_extractor/webs_quotes').glob('sentence_ranges_ds_traj_'+q+'_*quotes.jsonl'))
 t=json.load(open(tp))['trajectory'];rows=[json.loads(l) for l in open(qp) if l.strip()]
 qm={(r['search_goal_id'],str(r['web']['web_id'])):r for r in rows};assert len(qm)==len(rows)
 pages=[]
 for line,r in enumerate(rows,1):
  raw=r['web']['web_content'];quotes='\n'.join(r['quotes']);normalized=norm(raw);assert all(norm(x) in normalized for x in r['quotes'])
  selected=sum(b-a+1 for a,b in r['sentence_ranges'])
  pages.append({'line':line,'goal':r['search_goal_id'],'web_id':str(r['web']['web_id']),'url':r['web']['url'],'title':r['web']['title'],'raw_chars':len(raw),'normalized_raw_chars':len(normalized),'quote_chars':len(quotes),'normalized_quote_chars':len(norm(quotes)),'sentence_count':len(r['sentences']),'selected_sentence_count':selected,'empty':not r['quotes'],'full_selected':selected==len(r['sentences']),'compression_pct':100*(1-len(norm(quotes))/len(normalized)) if normalized else 0})
 hist_pairs=[];rounds=[];retention=[]
 for r in t['rounds']:
  pm=r['planner_call']['messages'];orig=sum(len(m['content']) for m in pm)
  if r['kind']=='init':
   rounds.append(dict(round_id=r['round_id'],goal=None,old_planner_chars=orig,new_quote_only_chars=orig,new_with_sources_chars=orig,current_raw_chars=0,current_quote_chars=0,current_evidence_chars=0,history_statement_chars=0,cumulative_quote_chars=0,web_ids=[]));continue
  u=next(m['content'] for m in pm if m['role']=='user');webtxt=block(u,'turn_webs');wbody=webtxt.split('\nevidences:')[0];wids=ids(wbody);goal=r['target_goal_id'];pairs=[(goal,x) for x in wids];assert all(pair in qm for pair in pairs),(gi,r['round_id'],pairs)
  current_raw=sum(len(qm[p]['web']['web_content']) for p in pairs)
  current_quote=sum(sum(len(s) for s in qm[p]['quotes']) for p in pairs)
  h=block(u,'search_history');hs=sum(len(x) for x in re.findall(r'^- E\d+:.*(?:\n|$)',h,re.M))
  ce=len(webtxt)-len(wbody)
  for pair in pairs:
   if pair not in hist_pairs:hist_pairs.append(pair)
  cumulative='\n\n'.join('\n'.join(qm[p]['quotes']) for p in hist_pairs if qm[p]['quotes'])
  pack='\n\n'.join(f"[{p[1]}] goal={p[0]} url={qm[p]['web']['url']}\ntitle={qm[p]['web']['title']} date={qm[p]['web'].get('date','')}\n"+'\n'.join(qm[p]['quotes']) for p in hist_pairs if qm[p]['quotes'])
  rounds.append(dict(round_id=r['round_id'],goal=goal,old_planner_chars=orig,new_quote_only_chars=orig-len(webtxt)-hs+len(cumulative),new_with_sources_chars=orig-len(webtxt)-hs+len(pack),current_raw_chars=current_raw,current_quote_chars=current_quote,current_evidence_chars=ce,history_statement_chars=hs,cumulative_quote_chars=len(cumulative),cumulative_source_chars=len(pack),web_ids=wids,new_evidence_count=len(r.get('evidences',[])),action=r['action'],state_update=r.get('state_update',{})))
  for e in r.get('evidences',[]):
   for refs in e.get('quotes',[]):
    wid=str(refs['web_id']);ex=qm.get((goal,wid));allquotes=norm('\n'.join(ex['quotes'])) if ex else ''
    for quote in refs.get('quotes',[]):
     nq=norm(quote);raw=norm(ex['web']['web_content']) if ex else ''
     retention.append(dict(round_id=r['round_id'],goal=goal,evidence_id=e['evidence_id'],statement=e['statement'],web_id=wid,quote=quote,in_original=nq in raw,retained=nq in allquotes))
 old=sum(r['old_planner_chars'] for r in rounds);new=sum(r['new_with_sources_chars'] for r in rounds);lower=sum(r['new_quote_only_chars'] for r in rounds)
 raw=sum(p['normalized_raw_chars'] for p in pages);quoted=sum(p['normalized_quote_chars'] for p in pages)
 out=dict(group=gi,query=q,trajectory_path=str(tp),quotes_path=str(qp),input_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [tp,qp]},metrics=t['metrics'],stop_reason=t['stop_reason'],goals=t['goals'],pages=pages,rounds=rounds,evidence_retention=retention,summary=dict(goal_web_pairs=len(rows),unique_urls=len({p['url'] for p in pages}),empty_pages=sum(p['empty'] for p in pages),full_selected_pages=sum(p['full_selected'] for p in pages),raw_chars=raw,quote_chars=quoted,web_compression_pct=100*(1-quoted/raw),density_multiplier_if_relevance_preserved=raw/quoted,old_planner_chars=old,new_planner_quote_only_chars=lower,new_planner_source_chars=new,quote_only_planner_reduction_pct=100*(1-lower/old),source_planner_reduction_pct=100*(1-new/old),old_evidence_quote_count=len(retention),old_evidence_quotes_verbatim_in_input=sum(x['in_original'] for x in retention),old_evidence_quotes_retained=sum(x['retained'] for x in retention),extractor_input_chars=sum(sum(len(m['content']) for m in r.get('evidence_extractor_call',{}).get('messages',[])) for r in t['rounds'])))
 results.append(out)
(OUT/'measurements.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
for r in results:
 print(r['group'],json.dumps(r['summary'],ensure_ascii=False));print('ROUNDS',[(x['round_id'],x['old_planner_chars'],x['new_with_sources_chars'],x['current_raw_chars'],x['current_quote_chars'],x['history_statement_chars']) for x in r['rounds']])
