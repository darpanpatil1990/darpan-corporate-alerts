// Exact Demand Lab v31 engine; no analyst overrides are inferred.
const engine=require('./zone_engine.cjs');
let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>{
 try{const stocks=engine.analyseImport(JSON.parse(input),{maxBases:5});
 process.stdout.write(JSON.stringify(stocks.map(s=>({symbol:s.symbol,cmp:s.cmp,asof:s.asof,
 zones:s.zones.filter(z=>['Monthly','Quarterly','Half Yearly','Yearly'].includes(z.tf)&&z.currentInside&&!z.broken&&!z.freshnessUnspecified).map(z=>({tf:z.tf,date:z.date,p:z.p,d:z.d,firstTouch:z.firstTouch,latestTouch:z.latestTouch,testEpisodes:z.testEpisodes,firstReturn:!!z.firstReturn,contextOnly:!!z.contextOnly,achievement:z.achievement,closingStrong:z.closingStrong,quality:engine.selection(z).label}))}))));
 }catch(e){process.stderr.write(e.message);process.exitCode=1;}
});
