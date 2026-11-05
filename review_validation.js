/* Re-evaluate each warning against the field it describes, never the whole row blindly. */
(() => {
  const key=v=>String(v??'').normalize('NFKC').toLowerCase().replace(/\s/g,'');
  const stats=['power','kills','donations','power_millions','hq_level'];
  const numberValid=(value,field)=>value!==''&&value!=null&&Number.isFinite(Number(value))&&Number(value)>=0&&(field==='power_millions'||Number.isSafeInteger(Number(value)));
  function mediaInputIssues(original,values){
    const reasons=[];
    const name=values.name?.trim()||'';
    if(name.length<2||name.length>80||!/\p{L}/u.test(name))reasons.push('Enter a valid player name');
    if(!/^R[1-5]$/.test(values.rank||''))reasons.push('Choose a rank from R1 to R5');
    for(const field of stats){
      const value=values[field];
      if(value===''||value==null){
        if(original.tab===field)reasons.push('Enter '+field.replaceAll('_',' '));
        continue;
      }
      if(!numberValid(value,field)||(field==='hq_level'&&(Number(value)<1||Number(value)>99))||
         (field==='power_millions'&&Number(value)>1000000))reasons.push('Check '+field.replaceAll('_',' '));
    }
    return reasons;
  }
  function issues(original, values, names, kind, before, after){
    const validName=values.name?.trim().length>=2&&/\p{L}/u.test(values.name);
    const known=names.some(n=>key(n)===key(values.name));
    const changed=field=>String(values[field]??'').trim()!==String(original[field]??'').trim();
    const stat=kind==='vs'?'points':original.tab||(['power','kills','donations','power_millions'].find(f=>original[f]!=null)||'power');
    const nameFixed=validName&&(known||changed('name'));
    const statFixed=numberValid(values[stat],stat)&&changed(stat);
    const rankFixed=/^R[1-5]$/.test(values.rank||'');
    let reasons=(original.review_reasons||[]).filter(reason=>{
      if(/Adjacent frames did not resolve/.test(reason))return false;
      if(/row above|Next row has higher|out of descending/.test(reason))return false; // Recomputed below.
      if(/name/i.test(reason))return !nameFixed;
      if(/rank/i.test(reason))return !rankFixed;
      if(/point|stat|value|multi_view_difference/i.test(reason))return !statFixed;
      // Combined confidence or geometry warnings require a correction to both readings.
      return !(nameFixed&&statFixed);
    });
    if(kind==='vs'){
      if(!validName)reasons.push('Enter a valid player name');
      if(!known&&!changed('name'))reasons.push('Match name to Player List or correct the player name');
      if(!numberValid(values.points,'points'))reasons.push('Enter valid points for this row');
    }else{
      reasons.push(...mediaInputIssues(original,values));
      if(original.review_required&&!original.review_reasons?.length)reasons.push('Verify this row against the capture');
      if(!stats.some(field=>numberValid(values[field],field)))reasons.push('No statistics captured');
    }
    if(kind==='vs'&&numberValid(values.points,'points')){
      if(before!==undefined&&before!==''&&before!=null&&Number(values.points)>Number(before))reasons.push('Points exceed the row above');
      if(after!==undefined&&after!==''&&after!=null&&Number(values.points)<Number(after))reasons.push('Next row has higher points; verify this value');
    }
    return [...new Set(reasons)];
  }
  issues.nameKey=key;
  issues.scanNames=list=>{
    let roster=[];
    const used=new Map();
    function mark(job,ids){
      if(!used.has(job))used.set(job,new Set());
      ids.forEach(id=>used.get(job).add(id));
    }
    const eligible=job=>roster.filter(player=>!used.get(job)?.has(player.id));
    return {
      roster(players){roster=players;},
      names:()=>roster.map(player=>player.name),
      update(byJob){for(const [job,ids] of Object.entries(byJob||{}))mark(job,ids);},
      mark,
      match(job,name){
        const matches=roster.filter(player=>key(player.name)===key(name));
        return matches.length===1&&!used.get(job)?.has(matches[0].id)?matches[0]:null;
      },
      show(job){
        list.replaceChildren(...eligible(job).map(player=>{
          const option=document.createElement('option');
          option.value=player.name;
          return option;
        }));
      },
    };
  };
  issues.mediaInputIssues=mediaInputIssues;
  globalThis.reviewIssues=issues;
  if(typeof module!=='undefined')module.exports=issues;
})();
