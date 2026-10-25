// Exact Demand Lab v31 engine; no analyst overrides are inferred.
const engine=require('./zone_engine.cjs');
const HTF=['Monthly','Quarterly','Half Yearly','Yearly'];
// A quote below distal is not a confirmed break. Use completed candles of the
// alerted timeframe, with the same calendar boundaries as Demand Lab.
function monitorZones(payload){
 const raw=engine.validateRows(payload.rows).filter(b=>b.date.slice(0,10)<=payload.asof);
 return (payload.monitoredZones||[]).flatMap(w=>{
  if(!HTF.includes(w.tf)||!Number.isFinite(w.d)||!Number.isFinite(w.p)||w.d>=w.p)return [];
  if((payload.missingDates||[]).some(d=>d>w.registered_asof))return [];
  const bars=engine.aggregate(raw,w.tf),cutoff=engine.aggregate([{date:payload.asof}],w.tf)[0].date;
  const breach=bars.find((b,i)=>(!payload.dropFirstAggregate||i>0)&&b.date<cutoff&&b.complete!==false&&!b.invalid&&(b.end||b.date)>w.registered_asof&&b.c<w.d);
  return breach?[{id:w.id,tf:w.tf,date:w.date,d:w.d,p:w.p,breach_date:breach.end||breach.date,close:breach.c}]:[];
 });
}
// Reference levels only: zero buffer is a displayed band boundary, never an order plan.
const ENTRY_MAP={'Monthly':['Weekly','Daily'],'Quarterly':['Monthly','Weekly'],'Half Yearly':['Quarterly','Monthly'],'Yearly':['Half Yearly','Quarterly']};
function selectIncomeLevel(stock,parent,entryTF){
 const curve=stock.curves?.[parent.tf],location=curve?.location||'Unknown';
 const locationOK=['Low','Very Low'].includes(location)&&curve.demand?.date===parent.date;
 const levels=stock.zones.filter(z=>z.tf===entryTF&&!z.broken&&!z.freshnessUnspecified&&!z.contextOnly&&(z.fresh||z.firstReturn)&&z.d>=parent.d&&z.d<=stock.cmp&&z.d>0&&z.p>z.d);
 const evaluated=levels.map(z=>{
  const support=stock.zones.filter(h=>HTF.includes(h.tf)&&engine.TF.indexOf(h.tf)>engine.TF.indexOf(entryTF)&&!h.broken&&!h.freshnessUnspecified&&(h.fresh||h.firstReturn||h.currentInside)&&h.p>=z.d&&h.p<=z.p&&h.departureAchievement?.known&&h.departureAchievement.strong===true&&h.departureAchievement.closeConfirmed===true);
  const keypoints=support.map(h=>({tf:h.tf,date:h.date,proximal:h.p,grade:h.departureAchievement.grade,supplyBreakCount:h.departureAchievement.supplyBreakCount,through:h.departureAchievement.through}));
  const enriched={...z,matches:keypoints.map(h=>({...h,p:h.proximal,matchType:'Achievement proximal within entry range'}))};
  return {z:enriched,keypoints,quality:engine.selection(enriched),score:engine.score(enriched).total,components:engine.score(enriched).components,parentDistance:Math.max(0,z.d-parent.p,parent.d-z.p)};
 });
 const qualified=evaluated.filter(x=>x.keypoints.length&&x.quality.best).sort((a,b)=>a.parentDistance-b.parentDistance||Math.max(...b.keypoints.map(k=>k.grade))-Math.max(...a.keypoints.map(k=>k.grade))||Math.max(...b.keypoints.map(k=>k.supplyBreakCount||0))-Math.max(...a.keypoints.map(k=>k.supplyBreakCount||0))||b.z.achievement-a.z.achievement||b.score-a.score||(a.z.p-a.z.d)-(b.z.p-b.z.d)||b.z.date.localeCompare(a.z.date));
 return {location,locationOK,levelsChecked:levels.length,keypointLevels:evaluated.filter(x=>x.keypoints.length).length,qualifiedLevels:qualified.length,best:qualified[0]||null};
}
function entryReferences(stock,payload){
 const raw=engine.validateRows(payload.rows).filter(b=>b.date.slice(0,10)<=payload.asof),result=[];
 for(const parent of stock.zones.filter(z=>HTF.includes(z.tf)&&!z.broken&&!z.freshnessUnspecified&&(z.currentInside||(['Low','Very Low'].includes(stock.curves?.[z.tf]?.location)&&stock.curves[z.tf].demand?.date===z.date)))){
  const [trendTF,entryTF]=ENTRY_MAP[parent.tf],touch=(parent.latestTouch||parent.firstTouch||'').slice(0,10);
  const selection=selectIncomeLevel(stock,parent,entryTF),chosen=selection.best,z=chosen?.z;
  const base={parentTF:parent.tf,parentDate:parent.date,parentD:parent.d,parentP:parent.p,trendTF,trend:stock.trends[trendTF]?.state||'Unknown',entryTF,asof:stock.asof,ready:false,status:'WAIT_ENTRY_ZONE',location:selection.location,levelsChecked:selection.levelsChecked,keypointLevels:selection.keypointLevels,qualifiedLevels:selection.qualifiedLevels,basis:'Best qualified level; unbuffered reference only, no order or Entry Ready approval'};
  if(!selection.locationOK){result.push({...base,status:'WAIT_HTF_LOCATION'});continue;}
  if(base.trend!=='Up'){result.push({...base,status:'WAIT_TREND'});continue;}
  if(!z){result.push({...base,status:!selection.levelsChecked?'WAIT_ENTRY_ZONE':!selection.keypointLevels?'WAIT_ACHIEVEMENT_LEVEL':'WAIT_CORE_QUALITY'});continue;}
  let bars=entryTF==='Daily'?raw.filter(b=>b.complete!==false):engine.aggregate(raw,entryTF).filter(b=>b.date<engine.aggregate([{date:payload.asof}],entryTF)[0].date&&b.complete!==false);
  if(payload.dropFirstAggregate&&entryTF!=='Daily'&&bars.length)bars=bars.slice(1);
  const supplies=engine.supplies(bars,payload.asof,{maxBases:5}).filter(q=>q.bodyIn>=.7-1e-8&&q.bodyOut>=.7-1e-8);
  const closes=supplies.flatMap(q=>{const b=bars.find(b=>!b.invalid&&b.date>q.date&&b.c>q.d);return b&&touch&&(b.end||b.date).slice(0,10)>=touch?[{supplyDate:q.date,distal:q.d,date:b.end||b.date,close:b.c}]:[];}).sort((a,b)=>b.date.localeCompare(a.date));
  const supply=closes[0]||null,retest=supply?raw.find(b=>!b.invalid&&b.complete!==false&&b.date.slice(0,10)>supply.date.slice(0,10)&&b.date>(z.departureEnd||z.end||z.date)&&b.l<=z.p&&b.h>=z.d):null;
  const risk=z.p-z.d,plan=engine.entryPlan(stock,z,stock.cmp,{}, {purpose:parent.tf,confirmation:true,historyCurrent:!(payload.missingDates||[]).length});
  result.push({...base,status:!supply?'WAIT_SUPPLY_CLOSE':!retest||!z.currentInside?'WAIT_RETEST':'REVIEW_REQUIRED',zoneDate:z.date,proximal:z.p,distal:z.d,entryReference:z.p,stopReference:z.d,targets:[2,3,4].map(rr=>({rr,price:z.p+rr*risk})),supplyClose:supply,retestDate:retest?.date||null,quality:chosen.quality.label,parentDistance:chosen.parentDistance,scoreComponents:chosen.components,closing:z.closingStrong===true?'Strong':z.closingStrong===false?'Normal':'Review needed',achievement:z.achievement,achievementKeypoints:chosen.keypoints,reviewIssues:plan.issues});
 }
 return result;
}
module.exports={monitorZones,entryReferences,selectIncomeLevel};
if(require.main===module){let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>{
 try{const payload=JSON.parse(input),stocks=engine.analyseImport(payload,{maxBases:5});
 process.stdout.write(JSON.stringify(stocks.map(s=>({symbol:s.symbol,cmp:s.cmp,asof:s.asof,
 entry_references:entryReferences(s,payload),zone_breaks:monitorZones(payload),zones:s.zones.filter(z=>['Monthly','Quarterly','Half Yearly','Yearly'].includes(z.tf)&&z.currentInside&&!z.broken&&!z.freshnessUnspecified).map(z=>({tf:z.tf,date:z.date,p:z.p,d:z.d,firstTouch:z.firstTouch,latestTouch:z.latestTouch,testEpisodes:z.testEpisodes,firstReturn:!!z.firstReturn,contextOnly:!!z.contextOnly,achievement:z.achievement,closingStrong:z.closingStrong,quality:engine.selection(z).label}))}))));
 }catch(e){process.stderr.write(e.message);process.exitCode=1;}
});}
