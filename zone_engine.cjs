(function(root){
'use strict';
const TF=['Daily','Weekly','Monthly','Quarterly','Half Yearly','Yearly'];
const MAP=[['Hourly','75m','15m','5m'],['Daily','Daily','75m','15m'],['Weekly 75m','Weekly','Daily','75m'],['Weekly 125m','Weekly','Daily','125m'],['Monthly','Monthly','Weekly','Daily'],['Quarterly','Quarterly','Monthly','Weekly'],['Half Yearly','Half Yearly','Quarterly','Monthly'],['Yearly','Yearly','Half Yearly','Quarterly']];
const body=b=>b.h>b.l?Math.abs(b.c-b.o)/(b.h-b.l):0;
function historicalRally(z){return !z.achievementUnspecified&&Number.isFinite(z.p)&&z.p>0&&Number.isFinite(z.maxCloseSince)?100*(z.maxCloseSince-z.p)/z.p:null;}
function reviewedRally(z,review={}){return historicalRally(z)>0&&review.rallyAchievement===true&&!!review.exceptionEvidence?.trim();}
function selection(z,review={},settings={}){const sc=score(z,review,settings),q=z.firstReturn&&!z.broken?score({...z,fresh:true},review,settings):sc,h=!!z.matches?.length,a=sc.components.Achievement===1,valid=!z.broken&&!z.archived,rallyPct=historicalRally(z),best=valid&&h&&a&&q.core,exception=valid&&h&&!q.core&&(a||rallyPct>0),exceptionApproved=exception&&a&&rallyPct>0&&review.exceptionApproved===true&&!!review.exceptionEvidence?.trim();return {best,exception,exceptionApproved,rallyPct,core7:q.core,htf:h,achievement:a,label:best?'Core 7 · best candidate':exceptionApproved?'Exception · reviewed':exception?'Exception · review required':'Other research'};}
function score(z,review={},settings={}){
 const f=z.freshnessUnspecified?null:z.fresh?3:(z.firstReturn||z.testEpisodes===1?1.5:0),s=z.strengthUnspecified?null:z.doubleExciting||settings.gapStrength&&z.gapExciting?2:(z.bodyOut>=.7-1e-8?1:0),t=z.timeUnspecified?null:(z.n===0||z.n>=1&&z.n<=3)?2:z.n===4?1:z.n===5&&Number.isFinite(settings.fiveBaseTime)?settings.fiveBaseTime:z.n>5&&Number.isFinite(settings.moreBaseTime)?settings.moreBaseTime:null;
 const h=z.matches?.length?1:0,c=z.closingStrong===null?null:z.closingStrong===true?1:0,a=z.achievementUnspecified?null:z.achievement>=1||reviewedRally(z,review)?1:0;
 const sector=review.sector===true&&review.evidence?.trim()&&review.peers?.length?1:review.sector===false?0:review.autoSector?.checked&&[0,1].includes(review.autoSector.points)?review.autoSector.points:null;
 const components={Freshness:f,Strength:s,Time:t,HTF:h,Closing:c,Achievement:a,Sector:sector};
 return {components,total:Object.values(components).reduce((v,x)=>v+(x||0),0),pending:Object.entries(components).filter(([k,v])=>v===null).map(([k])=>k),core:f===3&&s===2&&t===2};
}
function priority(z){const h=!!z.matches?.length,a=!z.achievementUnspecified&&z.achievement>=1;return {eligible:!z.broken&&h&&a,htf:h,achievement:a,closing:z.closingStrong===true?'Strong':'Normal / review',reason:!h?'HTF support missing':!a?'Achievement missing / unknown':'HTF support + achievement'};}
function status(z,price){
 if(!Number.isFinite(price)||!Number.isFinite(z.p)||!Number.isFinite(z.d))return {key:'unknown',label:'Data needed',distance:null};
 const distance=100*(price-z.p)/z.p;
 if(z.broken)return {key:'below',label:'Closing breach recorded · review',distance};
 if(price<z.d)return {key:'below',label:'Below distal · review',distance};
 if(price<=z.p)return {key:'zone',label:'In the Zone',distance};
 if(z.firstTouch||z.tested||z.firstReturn){if(z.broken)return {key:'below',label:'Closing breach recorded · review',distance};return {key:'reaction',label:z.reactionConfirmed?'Reaction Started · close verified':'Above touched zone · reaction review',distance};}
 return {key:distance<=5+1e-9?'approaching':'waiting',label:distance<=5+1e-9?'Approaching Zone':'Waiting',distance};
}
function key(s,z){return [s.symbol,z.tf,z.date,z.p,z.d].join('|');}
function contexts(stock,z){
 if(z.contextOnly)return [];
 const result=[];
 for(const [purpose,loc,tr,entry] of MAP){
  if(entry!==z.tf)continue;
  const curve=stock.curves?.[loc],trend=stock.trends?.[tr];
  if(curve&&['Low','Very Low'].includes(curve.location)&&trend?.state==='Up'&&curve.demand&&z.p>=curve.demand.d&&z.p<=stock.cmp){result.push({purpose,loc,tr,curve:curve.location,trend:'Up',reviewRequired:trend.reviewRequired!==false,inside:z.d>=curve.demand.d&&z.p<=curve.demand.p});}
 }
 // Earlier intraday tests used SMA slope; retain as REVIEW rather than silently treating as advanced trend.
 for(const c of z.legacyContexts||[]){if(c.segment==='Income'&&!result.some(r=>r.purpose===c.purpose.replace(' Income',''))){result.push({purpose:c.purpose.replace(' Income',''),loc:c.locationTF,tr:c.trendTF,curve:c.curve,trend:'SMA proxy · review',reviewRequired:true,inside:c.insideLocationDZ});}}
 return result;
}
function bucket(date,tf){
 if(tf==='Daily')return date.slice(0,10);
 if(tf==='Weekly'){const d=new Date(date.slice(0,10)+'T00:00:00Z');d.setUTCDate(d.getUTCDate()-(d.getUTCDay()+6)%7);return d.toISOString().slice(0,10);}
 if(tf.endsWith('m')){const n=+tf.slice(0,-1),mins=+date.slice(11,13)*60+(+date.slice(14,16))-555;if(mins<0||mins>=375)throw Error('Intraday rows must be NSE session timestamps (IST)');const start=555+Math.floor(mins/n)*n;return date.slice(0,10)+'T'+String(Math.floor(start/60)).padStart(2,'0')+':'+String(start%60).padStart(2,'0');}
 const step={'Monthly':1,'Quarterly':3,'Half Yearly':6,'Yearly':12}[tf];return date.slice(0,4)+'-'+String(Math.floor((+date.slice(5,7)-1)/step)*step+1).padStart(2,'0')+'-01';
}
function aggregate(rows,tf){const out=[];for(const b of rows){const d=bucket(b.date,tf),last=out[out.length-1];if(last?.date===d){last.h=Math.max(last.h,b.h);last.l=Math.min(last.l,b.l);last.c=b.c;last.end=b.date;last.complete=last.complete!==false&&b.complete!==false;last.invalid=!!last.invalid||!!b.invalid;}else out.push({...b,date:d,end:b.date});}return out;}
function patterns(s,asof,{maxBases=5,extended=false}={}){
 const out=[];
 for(let i=1;i<s.length;i++){
  const b=s[i];if(b.invalid||b.complete===false||b.date>asof||b.c<=b.o||body(b)<=.5+1e-8)continue;
  for(let n=0;n<=maxBases;n++){
   const k=i-n-1;if(k<0)break;const a=s[k],base=s.slice(k+1,i),rev=a.c<a.o;
   if(base.some(x=>x.invalid||body(x)>.5+1e-8))break;if(a.invalid)continue;
   const regular=body(a)>.5+1e-8&&(n>0||rev);if(!regular)continue;
   if(n===0&&(body(a)<.7-1e-8||body(b)<.7-1e-8))continue;
   const p=n?Math.max(...base.map(x=>Math.max(x.o,x.c))):Math.max(Math.min(a.o,a.c),Math.min(b.o,b.c));
   const bd=n?Math.min(...base.map(x=>x.l)):Math.min(a.l,b.l),d=Math.min(bd,b.l,...(rev?[a.l]:[]));if(p<=d)continue;
   const nx=s[i+1],double=body(b)>=.7-1e-8&&nx&&!nx.invalid&&nx.complete!==false&&nx.date<=asof&&nx.c>nx.o&&body(nx)>=.7-1e-8;let departure=i;while(departure+1<s.length){const q=s[departure+1];if(q.invalid||q.complete===false||q.date>asof||q.c<=q.o||body(q)<.7-1e-8)break;departure++;}const departureEnd=s[departure].end||s[departure].date;
   const later=s.slice(departure+1),touch=later.map(x=>x.l<=p&&x.h>=d),first=touch.findIndex(Boolean);let ep=0;touch.forEach((v,j)=>{if(v&&(j===0||!touch[j-1]))ep++;});
   out.push({date:b.date,end:b.end||b.date,departureEnd,availableAfter:departureEnd,legIn:a.date,p,d,n,baseD:bd,type:!regular?'Base–Rally review':rev?'DBR':'RBR',mark:n===0?'Hidden':Math.abs(d-bd)>1e-8?'Exceptional':'Normal',bodyIn:body(a),bodyOut:body(b),closingStrong:regular?b.c>a.h:null,doubleExciting:!!double,gapExciting:n>0&&b.l>s[i-1].h, fresh:first<0&&!later.some(x=>x.invalid),freshnessUnspecified:later.some(x=>x.invalid),tested:first<0?null:later[first].date,firstTouch:first<0?null:later[first].date,testEpisodes:ep,broken:later.some(x=>x.c<d),matches:[],achievement:0,achievementReview:true,manualReview:!regular||n>5,kind:'Entry zone',formationRows:s.slice(k,departure+1).map(x=>[x.date,x.o,x.h,x.l,x.c])});
  }
 }
 return out;
}
function supplies(s,asof,opts){return patterns(s.map(b=>({...b,o:-b.o,h:-b.l,l:-b.h,c:-b.c})),asof,opts).map(z=>({...z,p:-z.p,d:-z.d}));}
function advanced(s,dz,sz){
 const ev=[];for(const [side,zones] of [['Supply',sz],['Demand',dz]])for(const z of zones){const b=s.find(b=>!b.invalid&&b.date>z.date&&(side==='Supply'?b.c>z.d:b.c<z.d));if(b)ev.push({date:b.end||b.date,direction:side,zoneDate:z.date,level:z.d});}
 ev.sort((a,b)=>a.date.localeCompare(b.date)||a.zoneDate.localeCompare(b.zoneDate));if(!ev.length)return {state:'Unknown',count:0,events:[],reviewRequired:true};const last=ev.at(-1),dirs=new Set(ev.filter(e=>e.date===last.date).map(e=>e.direction));if(dirs.size>1)return {state:'Review',count:0,events:ev.slice(-8),reviewRequired:true};const seen=new Set();for(let i=ev.length-1;i>=0&&ev[i].direction===last.direction;i--)seen.add(ev[i].zoneDate+'|'+ev[i].level);const n=seen.size;return {state:n>=2?(last.direction==='Supply'?'Up':'Down'):'Sideways',count:n,events:ev.slice(-8),reviewRequired:true,method:'Distinct first-close breach sequence; opposite breach resets'};
}
function achievementContext(s,sz){
 const n=s.length,index=new Map(),suffix=new Array(n+1).fill(-Infinity),prefix=new Array(n+1).fill(-Infinity),ph=new Array(n+1).fill(null);let last=null;
 for(let j=n-1;j>=0;j--)suffix[j]=Math.max(suffix[j+1],s[j].invalid?-Infinity:s[j].c);
 for(let k=0;k<=n;k++){if(k<n&&!index.has(s[k].date))index.set(s[k].date,k);if(k<n)prefix[k+1]=Math.max(prefix[k],s[k].invalid?-Infinity:s[k].h);const j=k-3;if(j>=2&&j+2<n&&!s.slice(j-2,j+3).some(b=>b.invalid)&&[j-2,j-1,j+1,j+2].every(q=>s[j].h>s[q].h))last=s[j].h;ph[k]=last;}
 return {index,suffix,prefix,ph,prior:[...sz].sort((a,b)=>b.date.localeCompare(a.date)),invalid:s.some(b=>b.invalid)};
}
function achievement(z,s,sz,ctx){const v=ctx||achievementContext(s,sz),k=v.index.get(z.legIn)??-1,i=v.index.get(z.date)??-1,prior=v.prior.find(q=>q.date<=z.legIn&&(q.end||q.date)<=(z.end||z.date)),maxc=i>=0?v.suffix[i]:Math.max(...s.slice(i).filter(b=>!b.invalid).map(b=>b.c)),ph=k>=0?v.ph[k]:null,ath=k>0?v.prefix[k]:null,a=!!prior&&maxc>prior.d,b=ph!==null&&maxc>ph,c=ath!==null&&maxc>ath;return {achievement:a&&b&&c?3:a&&b?2:a?1:0,previousHigh:ph,priorSupplyDistal:prior?.d??null,preFormationATH:ath,maxCloseSince:maxc,achievementReview:v.invalid,achievementUnspecified:v.invalid};}
function validateRows(rows,{quarantine=false}={}){
 if(!Array.isArray(rows)||rows.length<3)throw Error('At least 3 OHLC rows required');
 return rows.map((r,i)=>{const x=Array.isArray(r)?{date:r[0],o:+r[1],h:+r[2],l:+r[3],c:+r[4],complete:r[5]!==false}:{date:r.date||r.Date,o:+(r.o??r.open??r.Open),h:+(r.h??r.high??r.High),l:+(r.l??r.low??r.Low),c:+(r.c??r.close??r.Close),complete:r.complete!==false&&r.complete!=='false'};if(!/^\d{4}-\d{2}-\d{2}/.test(x.date)||![x.o,x.h,x.l,x.c].every(Number.isFinite))throw Error('Invalid OHLC row '+(i+1));if(x.l>Math.min(x.o,x.c)||x.h<Math.max(x.o,x.c)||x.l>x.h){if(!quarantine)throw Error('Invalid OHLC row '+(i+1));x.invalid=true;}return x;}).sort((a,b)=>a.date.localeCompare(b.date));
}
function analyseImport(payload,opts={}){
 const stocks=Array.isArray(payload)?payload:payload.stocks||[payload];
 return stocks.map(p=>{if(!p.symbol)throw Error('symbol is required');const input=validateRows(p.rows,{quarantine:!!opts.quarantine}),asof=p.asof||input.at(-1).date.slice(0,10),raw=input.filter(b=>b.date.slice(0,10)<=asof);if(raw.length<3)throw Error('At least 3 rows on or before asof required');const missingDates=(p.missingDates||[]).filter(d=>d.slice(0,10)<=asof);const baseTF=p.timeframe||'Daily';let frames=baseTF==='Daily'?TF:baseTF.endsWith('m')?[baseTF,...[15,75,125].filter(n=>n>+baseTF.slice(0,-1)&&n%(+baseTF.slice(0,-1))===0).map(n=>n+'m'),...TF]:[baseTF];const curves={},trends={},charts={},all=[],seriesByTF={},suppliesByTF={};
  for(const tf of frames){const s=tf===baseTF?raw:aggregate(raw,tf); // Exclude current open calendar bucket unless explicitly complete.
   const cutoff=tf===baseTF?null:tf.endsWith('m')?null:bucket(asof,tf);let closed=cutoff?s.filter(b=>b.date<cutoff):s.filter(b=>b.complete!==false);if(tf==='Weekly'&&new Date(asof+'T00:00:00Z').getUTCDay()>=5)closed=s.filter(b=>b.complete!==false);if(tf.endsWith('m')&&tf!==baseTF){const need=+tf.slice(0,-1)/(+baseTF.slice(0,-1));closed=s.filter(b=>raw.filter(v=>bucket(v.date,tf)===b.date&&v.complete!==false).length===need);}if(p.dropFirstAggregate&&tf!==baseTF&&s.length)closed=closed.filter(b=>b.date!==s[0].date);charts[tf]=s.filter(b=>!b.invalid).map(b=>[b.date,b.o,b.h,b.l,b.c]);const zs=patterns(closed,asof,opts).map(z=>({...z,tf}));
   for(const z of zs){const ref=(p.referenceZones||[]).find(r=>r.tf===tf&&r.date===z.date);if(ref&&Number.isFinite(ref.p)&&Number.isFinite(ref.d)&&ref.p>ref.d){z.computedBand={p:z.p,d:z.d};z.p=ref.p;z.d=ref.d;z.markingSource=ref.source||'User chart';}}const szs=supplies(closed,asof,opts).map(q=>({...q,tf}));for(const q of szs){const ref=(p.referenceSupplies||[]).find(r=>r.tf===tf&&r.date===q.date);if(ref&&Number.isFinite(ref.p)&&Number.isFinite(ref.d)&&ref.d>ref.p){q.computedBand={p:q.p,d:q.d};q.p=ref.p;q.d=ref.d;q.markingSource=ref.source||'User chart';}}seriesByTF[tf]=closed;suppliesByTF[tf]=szs;const ac=achievementContext(closed,szs);for(const z of zs)Object.assign(z,achievement(z,closed,szs,ac));all.push(...zs);trends[tf]=advanced(closed,zs,szs);

  }
  const cmp=Number.isFinite(p.quote?.price)&&p.quote.price>0?p.quote.price:raw.at(-1).c,first={};
  function visits(z,supply=false){const later=raw.filter(b=>b.date>(z.departureEnd||z.end||z.date)),flags=later.map(b=>supply?b.h>=z.p&&b.l<=z.d:b.l<=z.p&&b.h>=z.d);let ep=0;flags.forEach((v,i)=>{if(v&&(i===0||!flags[i-1]))ep++;});const ti=flags.findIndex(Boolean);z.freshnessUnspecified=later.some(b=>b.invalid)||missingDates.some(d=>d>(z.departureEnd||z.end||z.date));z.fresh=ti<0&&!z.freshnessUnspecified;z.testEpisodes=ep;z.latestTouch=ti<0?null:later[flags.map((v,i)=>v&&(i===0||!flags[i-1])?i:-1).filter(i=>i>=0).at(-1)]?.date;z.firstTouch=ti<0?null:later[ti].date;z.tested=z.firstTouch;z.broken=(seriesByTF[z.tf]||later).some(b=>(b.end||b.date)>(z.end||z.date)&&!b.invalid&&b.complete!==false&&(supply?b.c>z.d:b.c<z.d));z.dataAsOf=asof;}
  for(const z of all){if(['Monthly','Quarterly','Half Yearly','Yearly'].includes(z.tf))z.departureAchievement=departureAchievement(z,raw,suppliesByTF[z.tf]||[]);visits(z);z.achievementUnspecified=z.achievementUnspecified||missingDates.length>0;z.currentInside=z.d<=cmp&&cmp<=z.p;const last=raw.at(-1),touch=last.date>(z.departureEnd||z.end||z.date)&&last.l<=z.p&&last.h>=z.d;z.firstReturnEpisode=z.testEpisodes===1&&!z.broken&&!z.freshnessUnspecified;z.firstReturn=(z.currentInside||touch&&cmp>=z.d)&&z.firstReturnEpisode;z.reactionConfirmed=z.firstReturn&&last.complete!==false&&last.c>last.o&&last.c>z.p;z.activeHTF=TF.indexOf(z.tf)>=2&&z.currentInside&&!z.broken&&!z.freshnessUnspecified;z.contextOnly=z.activeHTF&&!z.fresh&&!z.firstReturn;z.kind=z.activeHTF||z.firstReturn&&TF.indexOf(z.tf)>=2?'HTF context':'Entry zone';}
  for(const qs of Object.values(suppliesByTF))for(const q of qs)visits(q,true);
  for(const tf of [...frames.filter(f=>f.endsWith('m')),...TF.slice(2)]){const z=all.filter(z=>z.tf===tf&&z.fresh&&!z.broken&&z.p<=cmp).sort((a,b)=>b.p-a.p)[0];if(z)first[tf]={p:z.p,d:z.d,date:z.date};}
  for(const z of all){z.matches=higherMatches(z,all,first);z.manualReview=z.manualReview||(['Daily','Weekly'].includes(z.tf)&&(z.bodyIn<.7-1e-8||z.bodyOut<.7-1e-8&&!(z.bodyOut>=.6-1e-8&&z.matches.length)));}
  for(const tf of frames){const eligible=all.filter(z=>z.tf===tf&&!z.broken&&(z.fresh||z.firstReturn||z.activeHTF)),inside=eligible.filter(z=>z.currentInside&&(z.firstReturn||z.activeHTF)).sort((a,b)=>a.p-b.p),fresh=eligible.filter(z=>z.fresh&&z.p<=cmp).sort((a,b)=>b.p-a.p),dz=inside[0]||fresh[0],su=(suppliesByTF[tf]||[]).filter(q=>!q.broken&&!q.freshnessUnspecified&&q.p>cmp),sf=su.filter(q=>q.fresh),sz=(sf.length?sf:su.filter(q=>q.testEpisodes===1)).sort((a,b)=>a.p-b.p)[0],pct=!inside.length&&dz&&sz&&sz.p>dz.p?100*(cmp-dz.p)/(sz.p-dz.p):null;curves[tf]={location:inside.length?'Very Low':pct===null?'Unknown':pct<=100/3?'Low':pct<200/3?'Middle':'High',pct,demand:dz?{p:dz.p,d:dz.d,date:dz.date,testEpisodes:dz.testEpisodes,contextOnly:!!dz.contextOnly}:null,supply:sz?{p:sz.p,d:sz.d,date:sz.date,fresh:sz.fresh,testEpisodes:sz.testEpisodes}:null};}
  const htfContexts=all.filter(z=>TF.indexOf(z.tf)>=2&&(z.firstReturnEpisode||z.activeHTF)).map(z=>({id:[z.tf,z.date,z.p,z.d].join('|'),tf:z.tf,date:z.date,p:z.p,d:z.d,firstTouch:z.firstTouch,contextTouch:z.latestTouch||z.firstTouch,testEpisodes:z.testEpisodes,contextOnly:!!z.contextOnly,currentInside:z.currentInside,departureEnd:z.departureEnd}));
const confirmation={};for(const tf of ['Daily','Weekly']){const ev=[];for(const parent of htfContexts){const touch=(parent.contextTouch||parent.firstTouch)?.slice(0,10);if(!touch)continue;for(const q of suppliesByTF[tf]||[]){if(q.bodyIn<.7-1e-8||q.bodyOut<.7-1e-8)continue;const b=(seriesByTF[tf]||[]).find(b=>!b.invalid&&b.date>q.date&&b.c>q.d);if(b&&(b.end||b.date).slice(0,10)<touch)continue;if(!b&&q.p<=cmp)continue;ev.push({parentID:parent.id,contextTouch:touch,supplyDate:q.date,p:q.p,d:q.d,closeDate:b?(b.end||b.date):null,close:b?.c??null,status:b?'Close above supply recorded':'Waiting for close above supply',properFallReview:true});}}confirmation[tf]=ev.sort((a,b)=>(b.closeDate||'').localeCompare(a.closeDate||'')||a.p-b.p).slice(0,20);}
 const supplyContext={};for(const tf of frames){const qs=(suppliesByTF[tf]||[]).filter(q=>!q.broken&&q.p>cmp).sort((a,b)=>a.p-b.p);if(qs[0]){const q=qs[0];supplyContext[tf]={date:q.date,p:q.p,d:q.d,testEpisodes:q.testEpisodes,markingSource:q.markingSource||'OHLC-derived',closingStrong:q.closingStrong};}}
 return {sectorBars:baseTF==='Daily'?sectorBars(raw,asof,!!p.dropFirstAggregate):{},symbol:p.symbol.toUpperCase(),name:p.name||p.symbol,industry:p.industry||'Imported',cmp,asof,quote:p.quote||null,lastCompletedSession:raw.filter(b=>!b.invalid&&b.complete!==false).at(-1)?.date||null,lastBar:raw.at(-1).date,zones:all.filter(z=>!z.broken&&(z.fresh||z.firstReturn||z.activeHTF||opts.retainPriorityTested&&z.matches.length&&z.achievement>=1||opts.retainNearbyTested&&!z.freshnessUnspecified&&z.testEpisodes>0&&cmp>=z.d&&cmp<=z.p*1.05+1e-8)),supplyContext,referenceZones:p.referenceZones||[],referenceSupplies:p.referenceSupplies||[],curves,trends,first,confirmation,htfContexts,charts,imported:true,dataQuality:{missingDates,quarantinedRows:raw.filter(b=>b.invalid).length,invalidDates:raw.filter(b=>b.invalid).map(b=>b.date)},dataError:raw.at(-1).invalid?'Latest OHLC row invalid':null};
 });
}
function entryPlan(stock,z,price,review={},settings={}){
 const cs=contexts(stock,z),purpose=settings.purpose,ctx=purpose&&purpose!=='all'&&purpose!=='confirmation'?cs.find(c=>purpose==='Weekly'?c.purpose.startsWith('Weekly'):c.purpose===purpose):cs[0],r=[];const confirmation=!!settings.confirmation||String(review.entry||'').startsWith('Confirmation')||['Wait supply close','Wait retest','Retest reviewed'].includes(review.entry);const parents=stock.htfContexts||[],parent=parents.find(p=>p.id===review.parentID)||parents.find(p=>z.d>=p.d&&z.p<=p.p);
 const stat=status(z,price),sc=score(z,review,settings);
 const check=(key,label,ok,detail,advisory=false)=>r.push({key,label,ok,detail,advisory});
 if(settings.historyCurrent===false)check('history','OHLC recheck after quote',false,'Price-only update does not verify freshness, closing or achievement; refresh OHLC');
 if(z.contextOnly)check('context','Tested HTF context only',false,'Retained for location; use a separately qualified Daily/Weekly confirmation entry');
 const chosen=selection(z,review,settings);check('quality','Core 7 best-trade quality / reviewed exception',chosen.best||chosen.exceptionApproved,chosen.best?'Fresh before return + double exciting + preferred base, with HTF support and achievement':chosen.exceptionApproved?'Documented historical-rally exception accepted by analyst':'Core 7 is preferred. Lower-core candidates need HTF support, verified achievement and a documented rally exception.');check('marking','Formation / origin marking',!z.manualReview||review.patternValidated===true,z.manualReview?'Practical exception needs analyst marking review':'Standard / hidden / exceptional marking');check('support','HTF support',!!z.matches?.length,'First-fresh proximal / eligible Weekly range match');check('closing','Closing quality · additional evidence',sc.components.Closing===1,'Strong closing earns one star; normal closing does not reject HTF + achievement.',true);check('achievement','Achievement',sc.components.Achievement===1,reviewedRally(z,review)?'Documented historical rally reviewed as achievement':'Closing-confirmed achievement before this decision');
 if(confirmation){check('location','HTF demand / reviewed nearby location',!!parent&&(z.d>=parent.d&&z.p<=parent.p||review.nearHTF===true),parent?parent.tf+' demand; first touch '+parent.firstTouch:'Choose the relevant HTF demand in review');}else{check('location','Location',!!ctx,ctx?ctx.loc+': '+ctx.curve:'No Low / Very Low income context for this zone');check('trend','Advanced trend',!!ctx&&ctx.trend==='Up'&&review.trend===true,ctx?ctx.tr+': '+ctx.trend+(review.trend?' · reviewed':' · chart review needed'):'Choose income purpose / review trend');}
 check('proximity','At planned zone',stat.key==='zone',stat.label);
 check('sector','Sector · additional evidence',sc.components.Sector===1,sc.components.Sector===null?'Pending':sc.components.Sector?'Verified':'No support',true);
 if(confirmation){check('supply','Proper supply close above distal',review.properFall===true&&review.supplyDistal!==''&&review.supplyClose!==''&&+review.supplyDistal>0&&Number.isFinite(+review.supplyDistal)&&Number.isFinite(+review.supplyClose)&&+review.supplyClose>+review.supplyDistal&&!!review.supplyDate&&!!parent&&review.supplyDate>=(parent.contextTouch||parent.firstTouch).slice(0,10),'Wick-only break does not qualify');check('retest','Demand retest',review.entry==='Retest reviewed'&&stat.key==='zone'&&!!review.retestDate&&review.retestDate>review.supplyDate&&review.retestDate<=(stock.asof||'9999-99-99'),'Retest of selected demand, not breakout-price entry');}
 const qtyRisk=+settings.riskAmount,capital=+settings.capital,eb=+settings.entryBuffer,sb=+settings.stopBuffer,configured=[settings.entryBuffer,settings.stopBuffer].every(v=>v!==''&&v!==null&&v!==undefined)&&eb>=0&&sb>=0;
 let order=null;if(configured){const entry=z.p+eb,stop=z.d-sb,risk=entry-stop;if(risk>0){let quantity=qtyRisk>0?Math.floor(qtyRisk/risk):null;if(capital>0)quantity=quantity===null?Math.floor(capital/entry):Math.min(quantity,Math.floor(capital/entry));order={entry,stop,risk,quantity,capitalRequired:quantity===null?null:quantity*entry,plannedLoss:quantity===null?null:quantity*risk,targets:[2,3,4].map(rr=>({rr,price:entry+rr*risk}))};}}
 check('risk','Explicit risk plan',configured&&qtyRisk>0&&capital>0&&order?.quantity>0,'Choose buffers, capital and planned risk amount; fees / slippage excluded');
 const issues=r.filter(c=>!c.ok&&!c.advisory).map(c=>c.label);return {checks:r,order,issues,confirmation,label:issues.length?'Review / wait':'Required checks met · analyst decision',configured};
}
function peerCloseIndex(stocks){if(!Array.isArray(stocks)||stocks.length<2||stocks.length>10)throw Error('Use 2–10 peer series');if(new Set(stocks.map(s=>s.symbol)).size!==stocks.length)throw Error('Duplicate peer symbol');const maps=stocks.map(s=>({symbol:s.symbol,map:new Map(s.rows.map(r=>[r.date,+(r.close??r.c)]))}));const dates=[...maps[0].map.keys()].filter(d=>maps.every(s=>Number.isFinite(s.map.get(d))&&s.map.get(d)>0)).sort();if(dates.length<2)throw Error('Not enough common observations');return dates.map(d=>({date:d,c:100*maps.reduce((v,s)=>v+s.map.get(d)/s.map.get(dates[0]),0)/maps.length}));}

function rank(tf){return tf.endsWith('m')?+tf.slice(0,-1)/10000:TF.indexOf(tf)+1;}
function higherMatches(z,zones,first){
 const out=Object.entries(first||{}).filter(([tf,h])=>rank(tf)>rank(z.tf)&&h.p>=z.d&&h.p<=z.p).map(([tf,h])=>({tf,...h,matchType:'First fresh proximal'}));
 out.push(...matchWeekly(z,zones.filter(q=>q.tf==='Weekly'&&!q.broken)));return out;
}
function supportRelations(z,zones,first){
 const higher=higherMatches(z,zones,first),lower=[],containment=[];
 for(const q of zones){if(q===z||q.broken||!(q.fresh||q.firstReturn)||rank(q.tf)>=rank(z.tf))continue;
 const reciprocal=higherMatches(q,zones,first).some(h=>h.tf===z.tf&&h.date===z.date&&Math.abs(h.p-z.p)<1e-8);
 if(reciprocal)lower.push({tf:q.tf,date:q.date,p:q.p,d:q.d,matchType:'Lower timeframe match · no HTF star'});
 else if(q.d>=z.d&&q.p<=z.p)containment.push({tf:q.tf,date:q.date,p:q.p,d:q.d,matchType:'Contained only · no proximal match'});
 }return {higher,lower,containment};
}
function matchWeekly(z,weekly){if(z.tf!=='Daily')return [];return weekly.filter(w=>w.fresh&&Math.max(z.d,w.d)<=Math.min(z.p,w.p)).map(w=>({tf:'Weekly',p:w.p,d:w.d,date:w.date,matchType:'Range match'}));}

// Compare completed candles only. No synthetic peer OHLC and no inferred order quantity.
function sectorBars(rows,asof,dropFirstAggregate=false){const raw=validateRows(rows,{quarantine:true}).filter(b=>!b.invalid&&b.complete!==false&&b.date.slice(0,10)<=asof),out={};for(const tf of TF){let bs=aggregate(raw,tf),cut=bucket(asof,tf);if(tf!=='Daily')bs=bs.filter(b=>b.date<cut||(tf==='Weekly'&&new Date(asof+'T00:00:00Z').getUTCDay()>=5));if(dropFirstAggregate&&tf!=='Daily')bs=bs.slice(1);out[tf]={count:bs.length,bar:bs.at(-1)||null};}return out;}
function zoneReaction(z,b){if(!z||!b||z.broken||z.freshnessUnspecified||b.complete===false||b.invalid||b.date<=(z.departureEnd||z.end||z.date))return false;const touched=b.l<=z.p&&b.h>=z.d||(z.latestTouch||'')>=b.date&&(z.latestTouch||'')<=(b.end||b.date);return touched&&b.c>b.o&&b.c>z.p;}
function sectorEvidence(stock,z,record){const base={checked:false,points:null,benchmark:record?.name||record?.symbol||null,asof:record?.asof||null,tf:z.tf,method:'Related sector demand reaction on the same completed timeframe / period. Stock entry timing is checked separately.'};const pending=reason=>({...base,state:'unavailable',reason});if(stock.instrumentType==='index')return pending('Broad-market index: no stock-sector star applied.');if(!record)return pending('No verified sector benchmark mapping. Use same-business peers; a close-only peer line does not prove an OHLC demand zone.');if(record.error||!record.stock)return pending(record.error||'Sector OHLC unavailable.');if(!TF.includes(z.tf))return pending('Intraday sector OHLC needed for '+z.tf+'. Daily candles are not substituted.');if(stock.dataQuality?.missingDates?.length||record.stock.dataQuality?.missingDates?.length||stock.dataQuality?.quarantinedRows||record.stock.dataQuality?.quarantinedRows)return pending('Missing OHLC dates: reaction needs a data-quality recheck.');if(stock.asof!==record.stock.asof)return pending('Stock / sector session mismatch: '+stock.asof+' vs '+record.stock.asof+'. Refresh both.');const a=stock.sectorBars?.[z.tf],b=record.stock.sectorBars?.[z.tf];if(!a?.bar||!b?.bar||a.count<3||b.count<3)return pending('Insufficient completed '+z.tf+' history.');if(a.bar.date!==b.bar.date||(a.bar.end||a.bar.date)!==(b.bar.end||b.bar.date))return pending('Completed candle periods do not match.');const zs=record.stock.zones.filter(q=>q.tf===z.tf&&!q.broken),sectorZone=zs.find(q=>zoneReaction(q,b.bar)),stockReaction=zoneReaction(z,a.bar),points=sectorZone&&!z.broken?1:0;return {...base,checked:true,points,state:points?'supported':'not-confirmed',stockReaction,sectorReaction:!!sectorZone,sectorZone:sectorZone?{p:sectorZone.p,d:sectorZone.d,date:sectorZone.date}:null,stockBar:a.bar,sectorBar:b.bar,source:record.source,reason:points?'Sector reacted from its own demand on the comparable completed candle. Stock entry / retest is a separate check.':'Sector reaction above demand proximal is not confirmed on the latest completed candle.'};}

function clusterDistance(z,price){if(!z||![z.d,z.p,price].every(Number.isFinite)||z.d<=0||z.p<z.d||price<=0)return {key:'unknown',label:'Data needed',distance:null};if(price<z.d)return {key:'below',label:'Below common zone',distance:null};if(price<=z.p)return {key:'inzone',label:'In the zone · सर्व TFच्या common rangeमध्ये',distance:0};const distance=100*(price-z.p)/z.p,key=distance<=5+1e-9?'within5':distance<=10+1e-9?'within10':distance<=20+1e-9?'within20':'beyond20',label={within5:'0–5% above zone',within10:'5–10% above zone',within20:'10–20% above zone',beyond20:'20% पेक्षा जास्त दूर'}[key];return {key,label,distance};}

// Achievement of this departure, measured before its first return; no score/supply gate.
function departureAchievement(z,rows,supplyZones=[]){
 const valid=b=>!b.invalid&&b.complete!==false&&[b.o,b.h,b.l,b.c].every(Number.isFinite)&&b.l<=Math.min(b.o,b.c)&&b.h>=Math.max(b.o,b.c);
 const bs=rows.map(b=>Array.isArray(b)?{date:b[0],o:b[1],h:b[2],l:b[3],c:b[4]}:b).filter(b=>b.date<=(z.dataAsOf||'9999'));
 const start=z.date,end=z.departureEnd||z.end||z.date,prior=bs.filter(b=>b.date<z.date),touch=bs.find(b=>b.date>end&&valid(b)&&b.l<=z.p&&b.h>=z.d)?.date||null;
 const run=bs.filter(b=>b.date>=start&&(!touch||b.date<touch));
 if(!prior.length||!run.length||bs[0]?.date>z.legIn||[...prior,...run].some(b=>!valid(b)))return {known:false,grade:0,reason:'Full valid pre-formation / departure history unavailable'};
 const ath=Math.max(...prior.map(b=>b.h)),ph=Number.isFinite(z.previousHigh)?z.previousHigh:null,peak=Math.max(...run.map(b=>b.h)),close=Math.max(...run.map(b=>b.c)),athBreak=peak>ath,highBreak=ph!==null&&peak>ph;
 const obstacles=supplyZones.filter(q=>(q.availableAfter||q.departureEnd||q.end||q.date)<=start&&q.d>z.p&&!bs.some(b=>valid(b)&&b.date>(q.availableAfter||q.departureEnd||q.end||q.date)&&b.date<start&&b.c>q.d));
 const breaks=obstacles.map(q=>{const hit=run.find(b=>b.c>q.d);return hit?{tf:q.tf||z.tf,date:q.date,p:q.p,d:q.d,breakDate:hit.date,breakClose:hit.c}:null;}).filter(Boolean),strong=breaks.length>0&&athBreak;
 return {known:true,grade:strong?3:breaks.length?2:athBreak||highBreak?1:0,strong,supplyBreakCount:breaks.length,supplyBreaks:breaks,obstacleCount:obstacles.length,athBreak,highBreak,closeConfirmed:athBreak?close>ath:highBreak&&close>ph,previousHigh:ph,preFormationATH:ath,peak,peakDate:run.find(b=>b.h===peak).date,maxClose:close,rallyPct:100*(peak-z.p)/z.p,firstReturn:touch,through:run.at(-1).date,historyStart:bs[0].date};
}
function supportAchievement(zones,price,group,selected=[],mode='any',kind='high'){
 const tfs=['Monthly','Quarterly','Half Yearly','Yearly'],nearest=nearestDemand(supportDemandLevels(zones,price),price);
 const entries=tfs.map(tf=>nearest[tf]).filter(z=>z&&Math.max(z.d,group.d)<=Math.min(z.p,group.p)).map(z=>({tf:z.tf,z,proof:z.departureAchievement||{known:false,grade:0}}));
 const pass=x=>x.proof.known&&x.proof.strong===true;
 const qualified=entries.filter(pass),rank=(a,b)=>b.proof.grade-a.proof.grade||(b.proof.supplyBreakCount||0)-(a.proof.supplyBreakCount||0)||Number(b.proof.closeConfirmed)-Number(a.proof.closeConfirmed)||(b.proof.rallyPct||0)-(a.proof.rallyPct||0);
 qualified.sort(rank);const best=qualified[0]||null,bestTFs=best?qualified.filter(x=>rank(x,best)===0).map(x=>x.tf):[];
 const chosen=selected.filter(tf=>tfs.includes(tf)),hits=qualified.filter(x=>chosen.includes(x.tf));
 const common=hits.length?Math.max(group.d,...hits.map(x=>x.z.d))<=Math.min(group.p,...hits.map(x=>x.z.p)):false;
 return {entries,qualified,best,bestTFs,pass:!chosen.length||(mode==='all'?hits.length===chosen.length&&common:hits.length>0)};
}

function selectedSupport(zones,price,timeframes=[]){const selected=TF.filter(tf=>timeframes.includes(tf));if(!selected.length)return supportClusters(zones,price,{nearestOnly:true});const nearest=nearestDemand(supportDemandLevels(zones,price),price),members=selected.map(tf=>nearest[tf]);if(members.some(z=>!z))return [];const d=Math.max(...members.map(z=>z.d)),p=Math.min(...members.map(z=>z.p));if(d>p)return [];const g={id:members.map(z=>z.tf+':'+z.date+':'+z.d+':'+z.p).join('|'),count:selected.length,tfs:selected,d,p,zones:members};return members.every(z=>z.fresh===true)||['inzone','within5'].includes(clusterDistance(g,price).key)?[g]:[];}
// Fresh at any distance; tested support is retained only inside / within 5% of the common band.
function supportDemandLevels(zones,price){return demandLevelsBelow(zones,price).filter(z=>!z.freshnessUnspecified&&(z.fresh===true||((z.testEpisodes||0)>0||z.firstTouch||z.tested)&&['inzone','within5'].includes(clusterDistance(z,price).key)));}
function supportClusters(zones,price,{nearestOnly=false}={}){return confluences(supportDemandLevels(zones,price),{price,nearestOnly}).filter(g=>g.zones.every(z=>z.fresh===true)||['inzone','within5'].includes(clusterDistance(g,price).key));}
function demandLevelsBelow(zones,price=null,{freshOnly=false,includeFirstReturn=false}={}){return (zones||[]).filter(z=>TF.includes(z.tf)&&!z.broken&&!z.archived&&Number.isFinite(z.d)&&Number.isFinite(z.p)&&z.d>0&&z.d<=z.p&&(!Number.isFinite(price)||z.d<=price)&&(!freshOnly||!z.freshnessUnspecified&&(z.fresh===true||includeFirstReturn&&z.testEpisodes===1&&(z.firstReturn||z.firstReturnEpisode)&&Number.isFinite(price)&&price>=z.d&&price<=z.p)));}
function nearestDemand(zones,price,{freshOnly=false,includeFirstReturn=false}={}){const valid=demandLevelsBelow(zones,price,{freshOnly,includeFirstReturn}),out={};for(const tf of TF)out[tf]=valid.filter(z=>z.tf===tf).sort((a,b)=>Math.max(0,price-a.p)-Math.max(0,price-b.p)||(a.p-a.d)-(b.p-b.d)||b.p-a.p)[0]||null;return out;}
// Closed interval intersection: every reported timeframe shares the same price area.
function confluences(zones,{freshOnly=false,price=null,nearestOnly=false,includeFirstReturn=false}={}){
 let valid=demandLevelsBelow(zones,price,{freshOnly,includeFirstReturn});if(nearestOnly)valid=Object.values(nearestDemand(zones,price,{freshOnly,includeFirstReturn})).filter(Boolean);
 const points=[...new Set(valid.flatMap(z=>[z.d,z.p]))].sort((a,b)=>a-b),sets=new Map();
 for(const point of points){const members=valid.filter(z=>z.d<=point&&z.p>=point),tfs=TF.filter(tf=>members.some(z=>z.tf===tf));if(tfs.length<2)continue;
 const ids=members.map(z=>TF.indexOf(z.tf)+':'+z.date+':'+z.d+':'+z.p).sort(),id=ids.join('|');
 if(!sets.has(id))sets.set(id,{id,count:tfs.length,tfs,d:Math.max(...members.map(z=>z.d)),p:Math.min(...members.map(z=>z.p)),zones:members});
 }
 const groups=[...sets.values()];return groups.filter(g=>!groups.some(h=>h!==g&&h.zones.length>g.zones.length&&g.zones.every(z=>h.zones.includes(z)))).sort((a,b)=>b.count-a.count||b.p-a.p);
}
// Wide-base origin is an analyst marking choice, never an automatic extra star.
function originCandidate(z){
 const rows=(z.formationRows||[]).slice(1,1+z.n).filter(r=>Array.isArray(r)&&r.length>=5&&r.slice(1,5).every(Number.isFinite));
 if(z.n<2||rows.length!==z.n)return null;
 const b=rows.reduce((a,b)=>b[3]<a[3]?b:a),d=b[3],p=Math.max(b[1],b[4]);if(p<=d)return null;
 return {date:b[0],p,d,sourceDate:z.date,tf:z.tf,originalBaseCount:z.n,requiresReview:true,reason:'Lowest base origin candidate. Confirm wide / spread base marking; original formation base count is retained.'};
}
function studySetup(stock,z,review={},settings={},purpose='all',price=stock.cmp){
 const choice=selection(z,review,settings),sc=score(z,review,settings),st=status(z,price),maps=MAP.filter(m=>m[3]===z.tf&&(purpose==='all'||m[0]===purpose)),contextsFound=contexts(stock,z),htf=[...new Set((z.matches||[]).map(h=>h.tf))];
 const context=maps.map(([name,loc,tr,entry])=>({purpose:name,loc,tr,entry,curve:stock.curves?.[loc]?.location||'Unknown',trend:stock.trends?.[tr]?.state||'Unknown',qualified:contextsFound.some(c=>c.purpose===name),reviewed:review.trend===true}));
 const parents=(stock.htfContexts||[]).filter(p=>TF.indexOf(p.tf)>TF.indexOf(z.tf)&&p.firstTouch&&p.firstTouch<z.date&&z.p>=p.d),followParents=parents.filter(p=>p.contextTouch&&p.contextTouch<=z.date&&z.d>=p.d);
 const confirmation=(stock.confirmation?.[z.tf]||[]).filter(c=>(stock.htfContexts||[]).some(p=>p.id===c.parentID&&z.d>=p.d&&z.p<=p.p));
 const supply=stock.supplyContext?.[z.tf],room=supply&&supply.p>z.p?{p:supply.p,d:supply.d,date:supply.date,rr:(supply.p-z.p)/(z.p-z.d)}:null;
 return {choice,sc,st,context,htf,followParents,followupReview:followParents.length>0,confirmation,origin:originCandidate(z),room,valid:!z.broken&&!z.archived,needsMarking:!!z.manualReview&&review.patternValidated!==true,qualityPending:sc.pending.length>0};
}
function universeContains(s,scope='combined'){if(s.instrumentType==='index')return true;if(s.inUniverse===false)return false;const nifty=s.nifty500===true||s.nifty500===undefined&&s.inUniverse===true&&!s.marketCapEligible,cap=s.marketCapEligible===true&&Number.isFinite(s.marketCapCr)&&s.marketCapCr>500;return scope==='nifty500'?nifty:scope==='mcap500'?cap:scope==='outside500'?!nifty&&cap:nifty||cap;}
root.DemandEngine={departureAchievement,supportAchievement,selectedSupport,supportDemandLevels,supportClusters,clusterDistance,demandLevelsBelow,nearestDemand,studySetup,originCandidate,confluences,selection,historicalRally,achievementContext,priority,universeContains,sectorBars,zoneReaction,sectorEvidence,TF,MAP,body,score,status,key,contexts,aggregate,patterns,supplies,advanced,achievement,validateRows,analyseImport,entryPlan,peerCloseIndex,matchWeekly,higherMatches,supportRelations};if(typeof module!=='undefined')module.exports=root.DemandEngine;
})(typeof window==='undefined'?globalThis:window);