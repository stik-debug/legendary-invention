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

  var standalone = (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) || window.navigator.standalone === true;
  var ua = navigator.userAgent || '';
  var ios = /iphone|ipad|ipod/i.test(ua);
  var android = /android/i.test(ua);
  var mobile = ios || android || (navigator.userAgentData && navigator.userAgentData.mobile === true);
  var safari = ios && /safari/i.test(ua) && !/crios|fxios|edgios/i.test(ua);
  var deferredPrompt = null;

  if (standalone) return;
  try { if (sessionStorage.getItem('pwa-install-dismissed') === '1') return; } catch (_) {}

  function showCard(title, message, actionText){
    var strong = card.querySelector('strong');
    if (strong && title) strong.textContent = title;
    if (help && message) help.textContent = message;
    if (actionText) button.textContent = actionText;
    card.hidden = false;
  }

  function closeCard(){
    card.hidden = true;
    try { sessionStorage.setItem('pwa-install-dismissed','1'); } catch (_) {}
  }

  // iPhone/iPad: iOS Safari does not expose beforeinstallprompt.
  if (ios) {
    showCard('Install ChamaPay', safari
      ? 'Tap Share, then Add to Home Screen.'
      : 'Open this site in Safari, then tap Share → Add to Home Screen.', 'How to install');
    button.addEventListener('click', function(){
      alert('On iPhone/iPad: open ChamaPay in Safari, tap the Share button, choose “Add to Home Screen”, turn on “Open as Web App” if shown, then tap Add.');
    });
  }
  // Android: use the native install prompt when available. If the browser does not
  // expose it, still show a mobile fallback so the user knows where to install it.
  else if (android || mobile) {
    window.addEventListener('beforeinstallprompt', function(e){
      e.preventDefault();
      deferredPrompt = e;
      showCard('Install ChamaPay', 'Add ChamaPay to your phone for quick access.', 'Install');
    });

    setTimeout(function(){
      if (!deferredPrompt) {
        showCard('Install ChamaPay', 'Chrome: tap ⋮ → Add to Home screen or Install app.', 'How to install');
      }
    }, 1800);

    button.addEventListener('click', async function(){
      if (deferredPrompt) {
        deferredPrompt.prompt();
        try { await deferredPrompt.userChoice; } catch (_) {}
        deferredPrompt = null;
        card.hidden = true;
        return;
      }
      alert('On Android: open ChamaPay in Chrome, tap the ⋮ menu, then choose “Install app” or “Add to Home screen”.');
    });

    window.addEventListener('appinstalled', function(){
      card.hidden = true;
      deferredPrompt = null;
    });
  }
  // Desktop: only show the banner when the browser says the app can be installed.
  else {
    window.addEventListener('beforeinstallprompt', function(e){
      e.preventDefault();
      deferredPrompt = e;
      showCard('Install ChamaPay', 'Add ChamaPay to your computer for quick access.', 'Install');
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

  close.addEventListener('click', closeCard);
})();
