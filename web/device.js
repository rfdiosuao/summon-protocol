'use strict';
const $=id=>document.getElementById(id);
let preview=null,grant=null;
async function api(path,body){
 const response=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
 const data=await response.json();
 if(!response.ok)throw Error(data.error?.message||'请求失败');
 return data;
}
async function action(button,fn){button.disabled=true;$('notice').textContent='正在处理…';try{await fn();}catch(e){$('notice').textContent=e.message;}finally{button.disabled=false;}}
const request=()=>({request_id:crypto.randomUUID()});
async function directory(){
 const data=await api('/v1/nameplates');$('plates').replaceChildren();
 for(const item of data.items){const li=document.createElement('li'),code=document.createElement('code');code.textContent=item.code;li.append(code,document.createTextNode(' · '+item.agent.name+' · '+item.agent.status));$('plates').append(li);}
}
$('login').onsubmit=e=>{e.preventDefault();action(e.submitter,async()=>{await api('/v1/operator-session',{access_code:$('access').value});$('access').value='';$('notice').textContent='已登录。请填写电脑窗口中的配对码。';await directory();});};
$('code').oninput=()=>{preview=null;$('preview').hidden=true;};
$('lookup').onsubmit=e=>{e.preventDefault();action(e.submitter,async()=>{
 const code=$('code').value;
 const result=await api('/v1/device-pairings/preview',{...request(),user_code:code});
 if($('code').value!==code)return;
 preview={...result,user_code:code};$('device').textContent=result.label+' · '+result.shell_id;$('mode').textContent='运行模式：'+result.mode;
 $('capabilities').replaceChildren();const legend=document.createElement('legend');legend.textContent='允许的能力';$('capabilities').append(legend);
 for(const cap of result.capabilities){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=cap;input.checked=true;label.append(input,document.createTextNode(' '+cap));$('capabilities').append(label);}
 $('preview').hidden=false;$('notice').textContent='请核对设备和能力后确认。';
});};
$('approve').onclick=e=>action(e.target,async()=>{if(!preview)throw Error('请先核对设备。');const body={...request(),user_code:preview.user_code,shell_id:preview.shell_id,capabilities:[...$('capabilities').querySelectorAll('input:checked')].map(x=>x.value)};
 if(!body.capabilities.length)throw Error('至少选择一种能力。');
 grant=await api('/v1/device-pairings/approve',body);$('approved').hidden=false;$('preview').hidden=true;$('notice').textContent='授权完成，请回到电脑客户端。';await directory();});
$('revoke').onclick=e=>action(e.target,async()=>{if(!grant)return;await api('/v1/device-authorizations/'+grant.grant_id+'/revoke',request());grant=null;$('approved').hidden=true;$('notice').textContent='已撤销，相关会话将停止。';});
$('refresh').onclick=e=>action(e.target,async()=>{await directory();$('notice').textContent='铭牌目录已更新。';});
