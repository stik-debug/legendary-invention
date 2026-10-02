(function(){
var root=document.documentElement;
try{if(localStorage.getItem('perf')==='1')root.classList.add('perf');}catch(e){}
document.addEventListener('click',function(e){
  var o=e.target.closest('[data-open]');if(o){var d=document.getElementById(o.getAttribute('data-open'));if(d&&d.showModal)d.showModal();}
  var f=e.target.closest('[data-form-open]');
  if(f){var dl=document.getElementById(f.getAttribute('data-form-open'));var fm=dl&&dl.querySelector('form');
    if(fm){fm.action=f.getAttribute('data-action');var am=f.getAttribute('data-amount');if(am!==null){var inp=fm.querySelector('[name=amount]');if(inp)inp.value=am;}
    var sl=dl.querySelector('[data-text-slot]');if(sl)sl.textContent=f.getAttribute('data-text')||'';dl.showModal();}}
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


// ChamaPay PWA installation helper.
(function(){
  var card = document.getElementById('pwa-install-card');
  var button = document.getElementById('pwa-install-btn');
  var help = document.getElementById('pwa-install-help');
  var close = document.getElementById('pwa-install-close');
  if (!card || !button) return;

  var standalone = window.matchMedia && window.matchMedia('(display-mode: standalone)').matches;
  var ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  var safari = ios && /safari/i.test(navigator.userAgent) && !/crios|fxios|edgios/i.test(navigator.userAgent);
  var deferredPrompt = null;

  if (standalone || sessionStorage.getItem('pwa-install-dismissed') === '1') return;

  if (ios) {
    card.hidden = false;
    button.textContent = 'How to install';
    help.textContent = safari
      ? 'Safari: tap Share, then Add to Home Screen.'
      : 'Open this site in Safari, tap Share, then Add to Home Screen.';
    button.addEventListener('click', function(){
      alert('On iPhone: open ChamaPay in Safari, tap the Share button, choose “Add to Home Screen”, then tap Add.');
    });
  } else {
    window.addEventListener('beforeinstallprompt', function(e){
      e.preventDefault();
      deferredPrompt = e;
      card.hidden = false;
    });
    button.addEventListener('click', async function(){
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      try { await deferredPrompt.userChoice; } catch (_) {}
      deferredPrompt = null;
      card.hidden = true;
    });
    window.addEventListener('appinstalled', function(){
      card.hidden = true;
      deferredPrompt = null;
    });
  }

  close.addEventListener('click', function(){
    card.hidden = true;
    try { sessionStorage.setItem('pwa-install-dismissed','1'); } catch (_) {}
  });
})();
