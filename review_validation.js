/* Re-evaluate each warning against the field it describes, never the whole row blindly. */
(() => {
  const key=v=>String(v??'').normalize('NFKC').toLowerCase().replace(/\s/g,'');
  function issues(original, values, names, kind, before, after){
    const validName=values.name?.trim().length>=2&&/\p{L}/u.test(values.name);
    const known=names.some(n=>key(n)===key(values.name));
    const changed=field=>String(values[field]??'').trim()!==String(original[field]??'').trim();
    const stat=kind==='vs'?'points':original.tab||(['power','kills','donations','power_millions'].find(f=>original[f]!=null)||'power');
    const numberValid=field=>values[field]!==''&&values[field]!=null&&Number.isFinite(Number(values[field]))&&Number(values[field])>=0&&(field==='power_millions'||Number.isSafeInteger(Number(values[field])));
    const nameFixed=validName&&(known||changed('name'));
    const statFixed=numberValid(stat)&&changed(stat);
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
    if(!validName)reasons.push('Enter a valid player name');
    if(kind==='vs'&&!known&&!changed('name'))reasons.push('Match name to Player List or correct the player name');
    if(kind!=='vs'&&!rankFixed)reasons.push('Choose a rank from R1 to R5');
    if(!numberValid(stat))reasons.push('Enter valid '+stat.replace('_',' ')+' for this row');
    for(const field of ['power','kills','donations','power_millions','hq_level']){
      if(values[field]!==undefined&&values[field]!==''&&values[field]!=null&&(!numberValid(field)||(field==='hq_level'&&(Number(values[field])<1||Number(values[field])>99))||(field==='power_millions'&&Number(values[field])>1000000)))reasons.push('Check '+field.replace('_',' '));
    }
    if(kind==='vs'&&numberValid('points')){
      if(before!==undefined&&before!==''&&before!=null&&Number(values.points)>Number(before))reasons.push('Points exceed the row above');
      if(after!==undefined&&after!==''&&after!=null&&Number(values.points)<Number(after))reasons.push('Next row has higher points; verify this value');
    }
    return [...new Set(reasons)];
  }
  globalThis.reviewIssues=issues;
  if(typeof module!=='undefined')module.exports=issues;
})();
