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
function entryReferences(stock,payload){
 const raw=engine.validateRows(payload.rows).filter(b=>b.date.slice(0,10)<=payload.asof),result=[];
 for(const parent of stock.zones.filter(z=>HTF.includes(z.tf)&&z.currentInside&&!z.broken&&!z.freshnessUnspecified)){
  const [trendTF,entryTF]=ENTRY_MAP[parent.tf],touch=(parent.latestTouch||parent.firstTouch||'').slice(0,10);
  const candidates=stock.zones.filter(z=>z.tf===entryTF&&!z.broken&&!z.freshnessUnspecified&&!z.contextOnly&&z.d>=parent.d&&z.p<=parent.p&&z.d>0&&z.p>z.d)
   .sort((a,b)=>Number(b.currentInside)-Number(a.currentInside)||Number(engine.selection(b).best)-Number(engine.selection(a).best)||Math.abs(stock.cmp-a.p)-Math.abs(stock.cmp-b.p)||b.date.localeCompare(a.date));
  const z=candidates[0],base={parentTF:parent.tf,parentDate:parent.date,parentD:parent.d,parentP:parent.p,trendTF,trend:stock.trends[trendTF]?.state||'Unknown',entryTF,asof:stock.asof,ready:false,status:'WAIT_ENTRY_ZONE',basis:'Unbuffered reference levels; no order, quantity or Entry Ready approval'};
  if(!z){result.push(base);continue;}
  let bars=entryTF==='Daily'?raw.filter(b=>b.complete!==false):engine.aggregate(raw,entryTF).filter(b=>b.date<engine.aggregate([{date:payload.asof}],entryTF)[0].date&&b.complete!==false);
  if(payload.dropFirstAggregate&&entryTF!=='Daily'&&bars.length)bars=bars.slice(1);
  const supplies=engine.supplies(bars,payload.asof,{maxBases:5}).filter(q=>q.bodyIn>=.7-1e-8&&q.bodyOut>=.7-1e-8);
  const closes=supplies.flatMap(q=>{const b=bars.find(b=>!b.invalid&&b.date>q.date&&b.c>q.d);return b&&touch&&(b.end||b.date).slice(0,10)>=touch?[{supplyDate:q.date,distal:q.d,date:b.end||b.date,close:b.c}]:[];}).sort((a,b)=>b.date.localeCompare(a.date));
  const supply=closes[0]||null,retest=supply?raw.find(b=>!b.invalid&&b.complete!==false&&b.date.slice(0,10)>supply.date.slice(0,10)&&b.date>(z.departureEnd||z.end||z.date)&&b.l<=z.p&&b.h>=z.d):null;
  const risk=z.p-z.d,plan=engine.entryPlan(stock,z,stock.cmp,{}, {purpose:parent.tf,confirmation:true,historyCurrent:!(payload.missingDates||[]).length});
  result.push({...base,status:!supply?'WAIT_SUPPLY_CLOSE':!retest||!z.currentInside?'WAIT_RETEST':'REVIEW_REQUIRED',zoneDate:z.date,proximal:z.p,distal:z.d,entryReference:z.p,stopReference:z.d,targets:[2,3,4].map(rr=>({rr,price:z.p+rr*risk})),supplyClose:supply,retestDate:retest?.date||null,quality:engine.selection(z).label,reviewIssues:plan.issues});
 }
 return result;
}
module.exports={monitorZones,entryReferences};
if(require.main===module){let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>{
 try{const payload=JSON.parse(input),stocks=engine.analyseImport(payload,{maxBases:5});
 process.stdout.write(JSON.stringify(stocks.map(s=>({symbol:s.symbol,cmp:s.cmp,asof:s.asof,
 entry_references:entryReferences(s,payload),zone_breaks:monitorZones(payload),zones:s.zones.filter(z=>['Monthly','Quarterly','Half Yearly','Yearly'].includes(z.tf)&&z.currentInside&&!z.broken&&!z.freshnessUnspecified).map(z=>({tf:z.tf,date:z.date,p:z.p,d:z.d,firstTouch:z.firstTouch,latestTouch:z.latestTouch,testEpisodes:z.testEpisodes,firstReturn:!!z.firstReturn,contextOnly:!!z.contextOnly,achievement:z.achievement,closingStrong:z.closingStrong,quality:engine.selection(z).label}))}))));
 }catch(e){process.stderr.write(e.message);process.exitCode=1;}
});}
