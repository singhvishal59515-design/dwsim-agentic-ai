import React from "react";
import{SafetyWarning}from"../types";
interface Props{status?:string;warnings:SafetyWarning[];dark?:boolean;}
export default function SafetyBadge({status,warnings}:Props){
  if(!status||status==="UNKNOWN") return null;
  const passed=status==="PASSED";
  const col=passed?"#86efac":"#f87171";
  const bg=passed?"#052e16":"#3b1f1f";
  const brd=passed?"#166534":"#7f1d1d";
  return(<div style={{padding:"4px 8px"}}><div style={{background:bg,border:"1px solid "+brd,borderRadius:6,padding:"6px 10px"}}><div style={{color:col,fontWeight:700,fontSize:11,marginBottom:warnings.length?4:0}}>{passed?"✓ Safety: PASSED":"⚠ Safety: "+status+" ("+warnings.length+" violation"+(warnings.length!==1?"s":"")+")"}</div>{warnings.map((w,i)=>(<div key={i} style={{color:w.severity==="SILENT"?"#fca5a5":"#fbbf24",fontSize:11,marginBottom:2}}><code style={{background:"#7f1d1d",padding:"1px 4px",borderRadius:3,marginRight:5}}>{w.code}</code>{w.description}</div>))}</div></div>);
}
