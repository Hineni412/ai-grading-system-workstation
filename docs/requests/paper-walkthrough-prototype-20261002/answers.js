'use strict';
/* ===== 手写体答卷占位图 =====
   单一事实来源 = 达成步骤掩码 mask（每步整点有无，题分=达成步骤分值之和）：
   手写行、逐行批改记号、步骤摘要、AI 判分理由全部从同一 mask 派生。
   文本用手写字体族渲染，每行带轻微位移与旋转抖动。 */
const INK_FONT="KaiTi,STKaiti,'Ink Free','Segoe Print','Segoe Script',cursive";
const INK='#1f3465';
const RED='#c0392b';
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

/* 解答题作答模板：lines 与步骤一一对应
   ok=该步正确写法；any=无具体错法时的通用错写法；
   bad={错法:出错步写法}；carry={错法:被带错的后续写法}。
   alt=参考答案以外的解法（同样每步一行）；wrong=开头即错；blank=空白。 */
const ANSWER_TPL={
  '9':{lines:[
      {ok:'解：设每件降价 x 元'},
      {ok:'y=(20−x)(30+2x)',any:'y=(20+x)(30−2x)'},
      {ok:'配方：y=−2(x−5)²+650',any:'y=−2(x+5)²+650'},
      {ok:'∴降价 5 元，最大利润 650 元',any:'∴降价 3 元'}],
    alt:['列表试算：','x=4 时 y=616','x=5 时 y=650，x=6 时 y=648','∴降价 5 元时利润最大'],
    wrong:['y=(20+x)(30−2x)','（算不下去了）'],blank:['不会']},
  '10':{lines:[
      {ok:'解：x²−4x−5=0'},
      {ok:'(x−5)(x+1)=0',any:'(x−5)(x+5)=0',bad:{'符号处理错误':'(x+5)(x−1)=0'}},
      {ok:'x₁=5，x₂=−1',any:'x=5',bad:{'漏写负根':'x=5'},
        carry:{'符号处理错误':'x₁=−5，x₂=1'}},
      {ok:'经检验，均合题意'}],
    wrong:['x²−4x−5=0','x²=4x+5','x=2'],blank:['（空）']},
  '11(1)':{lines:[
      {ok:'∵DE∥BC'},
      {ok:'∴△ADE∽△ABC',any:'∴△ADE≌△ABC'},
      {ok:'AD/AB = DE/BC',any:'AD/DB = DE/BC'},
      {ok:'∴DE = 6',any:'∴DE = 3'}],
    alt:['用平行线分线段成比例：','AD:DB = AE:EC','DE:BC = AD:AB','∴DE = 6'],
    wrong:['∵DE∥BC','∴AD/BD = DE/BC','DE = 3'],blank:['不会']},
  '11(2)':{lines:[
      {ok:'作 DF⊥BC 于 F'},
      {ok:'证 △DBF≌△ECF（AAS）',bad:{'公式套用错误':'用勾股：EF²=BD²+BF²'}},
      {ok:'∴DF=EF，再由面积求出',bad:{'漏乘系数':'S△=DF·BC=18'},
        carry:{'公式套用错误':'∴EF=5'}},
      {ok:'∴EF = 3',any:'∴EF = 6',carry:{'漏乘系数':'∴EF = 6'}}],
    alt:['以 B 为原点建立坐标系','设 C(8,0)，D(4,6)','由中点坐标公式求 E、F','算得 EF = 3'],
    wrong:['连接 BE','∵……','（写不下去了）'],blank:['不会']},
  '12(1)':{lines:[
      {ok:'解：设每盒售价 x 元'},
      {ok:'(x−12)(40−x)=320',any:'(x+12)(40−x)=320'},
      {ok:'解得 x₁=20，x₂=32',any:'解得 x=16'},
      {ok:'x=20（x=32 不合题意，舍去）',any:'∴售价 32 元'}],
    wrong:['(x+12)(40−x)=320','x=16'],blank:['不会']},
  '12(2)':{lines:[
      {ok:'∵AB=AD，∠B=∠D=90°'},
      {ok:'连接 AC',any:'连接 BD',bad:{'辅助线构造不当':'连接 BD'}},
      {ok:'∴Rt△ABC≌Rt△ADC（HL）',any:'∴△ABC∽△ADC',
        carry:{'辅助线构造不当':'∴BD 垂直平分 AC'}},
      {ok:'∴BC=DC=5',bad:{'分类讨论遗漏':'只算 F 在 BC 上：BF=3'},
        carry:{'辅助线构造不当':'∴BC=DC=5'}}],
    wrong:['∵AB=AD','∴△ABC 是等腰三角形','……'],blank:['不会']},
  '12(3)':{lines:[
      {ok:'由(2)知 BC=5'},
      {ok:'设时间 t：S=t²−4t+10',any:'S=t²+4'},
      {ok:'配方：S=(t−2)²+6',any:'S=−(t−2)²+6'},
      {ok:'∴t=2 时 S 最小，为 6',any:'t=0 时最小'}],
    alt:['画出 S 关于 t 的图象','开口向上的抛物线','顶点横坐标 t=2','∴S 最小 = 6'],
    wrong:['S=t²+4','t=0 时最小'],blank:['不会']},
};

function causeOf(s,qid){
  for(const g of (CAUSES[qid]||[]))
    if(g.idx[s.cls]&&g.idx[s.cls].includes(s.idx))return g.name;
  return null;
}

/* 该生这题的作答计划 = mask + 逐行手写 {t,mark,cross}
   客观题返回 {kind:'obj',chosen,crossed,mask:[bool]}。 */
function planFor(s,qid){
  const q=QMAP[qid],sc=s.scores[qid];
  if(q.kind==='o'){
    const chosen=sc>=q.max?q.ans:'ABCD'.replace(q.ans,'')[(s.idx+qid.length)%3];
    const crossed=sc<q.max&&(hashStr(s.id+'|x|'+qid)%4===0)?q.ans:null;
    return{kind:'obj',chosen,crossed,mask:[sc>=q.max]};
  }
  const tpl=ANSWER_TPL[qid]||{lines:[{ok:'解：'}],blank:['不会']};
  const L=tpl.lines,alt=!!(s.alt.has(qid)&&tpl.alt);
  const base=i=>alt?(tpl.alt[i]||L[i].ok):L[i].ok;
  const cause=causeOf(s,qid);
  const cf=cause&&(CAUSE_FAIL[qid]||{})[cause];
  let mask;
  if(sc>=q.max)mask=L.map(()=>true);
  else if(cf)mask=causeMask(qid,cause);
  else if(sc===0){
    const blank=mulberry32(hashStr(s.id+'|z|'+qid))()<0.45;
    const arr=blank?(tpl.blank||['不会']):(tpl.wrong||['（空）']);
    return{kind:'sol',variant:blank?'blank':'zero',mask:L.map(()=>false),
      lines:arr.map(t=>({t,mark:'n'}))};
  }
  else mask=achieveSteps(STEP_PTS[qid],sc);
  /* 逐行：达成→正确行 ✓；未达成→错法行/带错行/通用错行/划去的行 ✗。
     显式掩码（跳步错法）：未达成步不写。at 型错法：出错步之后只写带错行。 */
  const lines=[];
  L.forEach((st,i)=>{
    if(mask[i]){lines.push({t:base(i),mark:'y'});return}
    if(cf&&cf.mask)return;
    if(cf&&cf.at!=null&&i>cf.at){
      const c=st.carry&&st.carry[cause];
      if(c)lines.push({t:c,mark:'n'});
      return;
    }
    const badLine=(st.bad&&st.bad[cause])||(alt?null:st.any);
    lines.push(badLine?{t:badLine,mark:'n'}:{t:base(i),mark:'n',cross:true});
  });
  return{kind:'sol',variant:cause?'cause':alt?'alt':'partial',cause,mask,lines};
}

function cornerStamp(card,w){
  const sc=card.s.scores[card.qid],m=QMAP[card.qid].max;
  const pen=`<g transform="translate(${w-64},28)" stroke="${RED}" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">`;
  if(sc>=m)return pen+`<path d="M-18,-2 L-8,10 L16,-14"/></g>`;
  const score=`<text x="${w-104}" y="78" font-size="20" fill="${RED}" font-weight="700">${sc} / ${m}</text>`;
  if(sc===0)return pen+`<path d="M-13,-13 L13,13 M-13,13 L13,-13"/></g>`+score;
  return pen+`<path d="M-15,1 L-7,9 L12,-10"/><line x1="-18" y1="14" x2="17" y2="-17"/></g>`+score;
}

function paperFrame(w,h,label){
  return `<rect width="${w}" height="${h}" fill="#fffef8"/>
  <text x="16" y="18" font-size="12" fill="#9aa4aa">${esc(label)}</text>
  <path d="M6,22 v-12 h12 M${w-6},22 v-12 h-12 M6,${h-22} v12 h12 M${w-6},${h-22} v12 h-12" stroke="#c9cfd0" stroke-width="1.5" fill="none"/>`;
}

function inkLine(t,x,y,fs,r,cross){
  const rot=(r()*2.4-1.2).toFixed(2);
  const w=Math.max(t.length,2)*fs*0.62;
  const s=`<text x="${x.toFixed(0)}" y="${y.toFixed(0)}" font-family="${INK_FONT}" font-size="${fs}" fill="${INK}" transform="rotate(${rot} ${x.toFixed(0)} ${y.toFixed(0)})">${esc(t)}</text>`;
  return cross?s+`<line x1="${(x-4).toFixed(0)}" y1="${(y-fs*0.55).toFixed(0)}" x2="${(x+w).toFixed(0)}" y2="${(y-fs*0.2).toFixed(0)}" stroke="${INK}" stroke-width="2.2"/>`:s;
}

/* 单题答题区截图 */
function cropSvg(card,big){
  const w=big?980:680,h=big?330:250;
  const r=mulberry32(hashStr(card.s.id+'|'+card.qid+'|j'));
  const plan=planFor(card.s,card.qid);
  let body='';
  if(plan.kind==='obj'){
    const opts='ABCD'.split(''),cy=h/2,bw=big?110:88;
    const x0=w/2-bw*2;
    body=opts.map((o,i)=>{
      const cx=x0+i*bw+bw/2;
      let s=`<rect x="${x0+i*bw+8}" y="${cy-26}" width="${bw-16}" height="52" rx="8" fill="none" stroke="#dfe4e1"/>
        <text x="${cx-7}" y="${cy+8}" font-size="20" fill="#8a95a0" font-family="var(--mono)">${o}</text>`;
      if(o===plan.chosen)s+=`<ellipse cx="${cx}" cy="${cy+2}" rx="24" ry="20" fill="none" stroke="${INK}" stroke-width="2.6"/>`;
      if(o===plan.crossed)s+=`<ellipse cx="${cx}" cy="${cy+2}" rx="24" ry="20" fill="none" stroke="${INK}" stroke-width="2.2" opacity=".55"/>
        <line x1="${cx-24}" y1="${cy+18}" x2="${cx+24}" y2="${cy-16}" stroke="${INK}" stroke-width="2.4"/>`;
      return s;
    }).join('');
    body+=`<text x="26" y="${cy+8}" font-family="${INK_FONT}" font-size="${big?22:17}" fill="${INK}">答：</text>`;
  }else{
    const fs=big?21:16,lh=big?46:40,y0=56;
    body=plan.lines.map((l,i)=>{
      const y=y0+i*lh;
      const mk=card._rev?`<text x="${w-52}" y="${y}" font-size="17" fill="${RED}">${l.mark==='y'?'✓':'✗'}</text>`:'';
      return inkLine(l.t,30+r()*14,y,fs,r,l.cross)+mk;
    }).join('');
  }
  const stamp=card._rev?cornerStamp(card,w):'';
  return `<svg viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="答卷截取（占位图）">
    ${paperFrame(w,h,'第 '+card.qid+' 题 作答区')}
    ${[...Array(5)].map((_,i)=>`<line x1="14" y1="${96+i*38}" x2="${w-14}" y2="${96+i*38}" stroke="#eef0ee" stroke-width="1"/>`).join('')}
    ${body}${stamp}
  </svg>`;
}

/* 整卷两页：每区显示该题的首行手写内容 */
function paperSvg(card,big){
  const w=big?430:300,h=big?600:440;
  const mk=side=>{
    const qs=side===0?QUESTIONS.slice(0,8):QUESTIONS.slice(8);
    const rh=(h-58)/qs.length,fs=big?13:10.5;
    let reg='';
    qs.forEach((q,i)=>{
      const y=52+i*rh,plan=planFor(card.s,q.id);
      const txt=plan.kind==='obj'?'答：'+plan.chosen:(plan.lines[0]?plan.lines[0].t:'');
      const rr=mulberry32(hashStr(card.s.id+'|p'+side+'|'+i));
      reg+=`<text x="20" y="${y-7}" font-size="10" fill="#9aa4aa">第 ${q.id} 题</text>
        <rect x="16" y="${y-3}" width="${w-32}" height="${rh-8}" fill="none" stroke="#e3e7e4" rx="4"/>
        ${inkLine(txt.slice(0,Math.ceil((w-70)/(fs*0.62))),26,y+rh/2-8,fs,rr)}`;
    });
    return `<svg viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg">
      ${paperFrame(w,h,'第 5 次单元测（合成）· '+card.s.name+' · '+card.s.cls)}
      <text x="${w-70}" y="29" font-size="11" fill="#c0c6c8">${side===0?'第 1 页':'第 2 页'}</text>
      ${reg}</svg>`;
  };
  return [mk(0),mk(1)];
}
