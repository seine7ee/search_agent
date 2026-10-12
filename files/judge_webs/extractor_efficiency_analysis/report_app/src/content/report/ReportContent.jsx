import React from 'react';
import {ReportSection,RichNarrative,DataTable,useDataApp} from '../../data-app-public.jsx';
import sections from './sections.json';
import './report.css';
function Body({s}) {
 const text=s.id==='report-files' ? '## 输入与可复现文件\n\n本报告配套目录包含 report.md、measurements.json、analyze.py 和 diagnose.py。完整输入路径及逐条测量保存在数据来源与配套文件中；本地文件链接请从 Markdown 报告打开。\n\n复现顺序：先运行 analyze.py，再运行 diagnose.py，无需网络或模型 API 调用。' : s.text;
 const parts=text.split(/(\n?\|[^\n]*\n\|[-:| ]+\|\n(?:\|[^\n]*\n?)+)/g).filter(Boolean);
 return parts.map((value,i)=>{
  if(!value.trim().startsWith('|')) return <RichNarrative key={i} id={s.id+':body:'+i} value={value}/>;
  const lines=value.trim().split('\n');
  const cells=line=>line.split('|').slice(1,-1).map(x=>x.trim());
  const labels=cells(lines[0]);
  const rows=lines.slice(2).map(line=>Object.fromEntries(cells(line).map((cell,j)=>['c'+j,cell])));
  const columns=labels.map((label,j)=>({field:'c'+j,label,renderCell:value=><span style={{whiteSpace:'normal',display:'block',minWidth:labels.length===3?'210px':'64px',lineHeight:1.7,textAlign:j&&labels.length!==3?'right':'left'}}>{value}</span>}));
  return <div key={i} data-reviewed-rows><DataTable rows={rows} columns={columns} searchable={false} caption={s.title}/></div>;
 });
}
export function ReportContent(){
 const {snapshot,appTitle,setAppTitle,canEdit,mode,visible}=useDataApp();
 const rows=snapshot.queries.comparison.rows;
 return <article className="report-content extraction-report" aria-label="网页抽取效率分析报告">
  <header className="report-hero">
   <h1 data-data-app-title contentEditable={canEdit&&mode==='edit'} suppressContentEditableWarning
    onBlur={canEdit&&mode==='edit' ? e=>setAppTitle(e.currentTarget.textContent.trim()||appTitle):undefined}>{appTitle}</h1>
   <RichNarrative id="report-description" value="三组轨迹的字符量实测、逐轮上下文重建与条件性提速估计" className="report-deck" />
  </header>
  {sections.map(s=>visible(s.id)&&<ReportSection key={s.id} id={s.id} title={s.title} queryId="comparison" sourceRows={rows.filter(r=>s.groups.includes(r.group))} showHeading={false}>
   <Body s={s}/>
  </ReportSection>)}
 </article>;
}
