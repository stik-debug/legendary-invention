(function(){
var root=document.documentElement;
try{if(localStorage.getItem('perf')==='1')root.classList.add('perf');}catch(e){}
document.addEventListener('click',function(e){
  var o=e.target.closest('[data-open]');if(o){var d=document.getElementById(o.getAttribute('data-open'));if(d&&d.showModal)d.showModal();}
  var c=e.target.closest('[data-close]');if(c){var p=c.closest('dialog');if(p)p.close();}
  if(e.target.tagName==='DIALOG')e.target.close();
  if(e.target.closest('[data-perf]')){var on=root.classList.toggle('perf');try{localStorage.setItem('perf',on?'1':'0');}catch(x){}}
});
document.addEventListener('submit',function(e){var m=e.target.getAttribute('data-confirm');if(m&&!window.confirm(m))e.preventDefault();});
var still=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
if(!still&&window.matchMedia('(hover:hover)').matches){
  document.addEventListener('mousemove',function(e){
    if(root.classList.contains('perf'))return;
    var t=e.target.closest&&e.target.closest('.tilt');if(!t)return;
    var r=t.getBoundingClientRect(),x=(e.clientX-r.left)/r.width-.5,y=(e.clientY-r.top)/r.height-.5;
    t.style.transform='rotateY('+(x*9)+'deg) rotateX('+(-y*9)+'deg)';
  });
  document.addEventListener('mouseout',function(e){var t=e.target.closest&&e.target.closest('.tilt');if(t&&!t.contains(e.relatedTarget))t.style.transform='';});
}
})();
