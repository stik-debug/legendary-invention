(function(){
var box=document.getElementById('chat');if(!box)return;
var list=document.getElementById('msgs'),last=parseInt(box.getAttribute('data-last')||'0',10),url=box.getAttribute('data-poll');
function bottom(){box.scrollTop=box.scrollHeight;}bottom();
function add(m){
  var d=document.createElement('div');d.className='msg'+(m.mine?' mine':'');
  var b=document.createElement('b');b.textContent=m.mine?'You':m.sender;
  var p=document.createElement('p');p.textContent=m.body;
  var s=document.createElement('small');s.textContent=m.at;
  d.appendChild(b);d.appendChild(p);d.appendChild(s);list.appendChild(d);
  var n=document.getElementById('none');if(n)n.remove();
}
function poll(){
  if(document.hidden)return;
  fetch(url+'?after='+last,{credentials:'same-origin',headers:{'Accept':'application/json'}}).then(function(r){return r.ok?r.json():null;}).then(function(j){
    if(!j||!j.messages||!j.messages.length)return;
    j.messages.forEach(function(m){add(m);if(m.id>last)last=m.id;});bottom();
  }).catch(function(){});
}
setInterval(poll,10000);document.addEventListener('visibilitychange',poll);
})();
