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
module.exports={monitorZones};
if(require.main===module){let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>{
 try{const payload=JSON.parse(input),stocks=engine.analyseImport(payload,{maxBases:5});
 process.stdout.write(JSON.stringify(stocks.map(s=>({symbol:s.symbol,cmp:s.cmp,asof:s.asof,
 zone_breaks:monitorZones(payload),zones:s.zones.filter(z=>['Monthly','Quarterly','Half Yearly','Yearly'].includes(z.tf)&&z.currentInside&&!z.broken&&!z.freshnessUnspecified).map(z=>({tf:z.tf,date:z.date,p:z.p,d:z.d,firstTouch:z.firstTouch,latestTouch:z.latestTouch,testEpisodes:z.testEpisodes,firstReturn:!!z.firstReturn,contextOnly:!!z.contextOnly,achievement:z.achievement,closingStrong:z.closingStrong,quality:engine.selection(z).label}))}))));
 }catch(e){process.stderr.write(e.message);process.exitCode=1;}
});}
