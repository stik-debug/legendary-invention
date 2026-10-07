(function(){
var root=document.documentElement;
try{if(localStorage.getItem('perf')==='1')root.classList.add('perf');if(localStorage.getItem('data-saver')==='1')root.classList.add('data-saver');}catch(e){}
document.addEventListener('click',function(e){
  var o=e.target.closest('[data-open]');if(o){var d=document.getElementById(o.getAttribute('data-open'));if(d&&d.showModal)d.showModal();}
  var f=e.target.closest('[data-form-open]');
  if(f){var dl=document.getElementById(f.getAttribute('data-form-open'));var fm=dl&&dl.querySelector('form');
    if(fm){fm.action=f.getAttribute('data-action');var am=f.getAttribute('data-amount');if(am!==null){var inp=fm.querySelector('[name=amount]');if(inp)inp.value=am;}
    var sl=dl.querySelector('[data-text-slot]');if(sl)sl.textContent=f.getAttribute('data-text')||'';dl.showModal();}}
  var c=e.target.closest('[data-close]');if(c){var p=c.closest('dialog');if(p)p.close();}
  if(e.target.tagName==='DIALOG')e.target.close();
  if(e.target.closest('[data-perf]')){var on=root.classList.toggle('perf');try{localStorage.setItem('perf',on?'1':'0');}catch(x){}}
  if(e.target.closest('[data-data-saver]')){var ds=root.classList.toggle('data-saver');try{localStorage.setItem('data-saver',ds?'1':'0');}catch(x){}}
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
  var steps = document.getElementById('pwa-install-steps');
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

  function showCard(title, message, actionText, instructionHtml){
    var strong = card.querySelector('strong');
    if (strong && title) strong.textContent = title;
    if (help && message) help.textContent = message;
    if (actionText) button.textContent = actionText;
    if (steps) {
      steps.innerHTML = instructionHtml || '';
      steps.hidden = !instructionHtml;
    }
    card.hidden = false;
  }

  function closeCard(){
    card.hidden = true;
    try { sessionStorage.setItem('pwa-install-dismissed','1'); } catch (_) {}
  }

  function toggleInstructions(html){
    if (!steps) return;
    steps.innerHTML = html;
    steps.hidden = false;
    button.textContent = 'Got it';
  }

  // iPhone/iPad: Safari uses Add to Home Screen rather than beforeinstallprompt.
  if (ios) {
    showCard('Install ChamaPay',
      safari ? 'Add ChamaPay to your Home Screen for quick access.' : 'For the best install experience, open ChamaPay in Safari.',
      'How to install',
      '<ol><li>Tap <b>Share</b> in Safari.</li><li>Tap <b>Add to Home Screen</b>.</li><li>Turn on <b>Open as Web App</b> if shown, then tap <b>Add</b>.</li></ol>');
    button.addEventListener('click', function(){
      if (steps && !steps.hidden) {
        button.textContent = 'Done';
        return;
      }
      toggleInstructions('<ol><li>Open ChamaPay in <b>Safari</b>.</li><li>Tap the <b>Share</b> button.</li><li>Select <b>Add to Home Screen</b>.</li><li>Turn on <b>Open as Web App</b> if shown, then tap <b>Add</b>.</li></ol>');
    });
  }
  // Android: use the native prompt when available; otherwise provide browser instructions.
  else if (android || mobile) {
    window.addEventListener('beforeinstallprompt', function(e){
      e.preventDefault();
      deferredPrompt = e;
      showCard('Install ChamaPay', 'Get the ChamaPay app on your phone for faster access.', 'Install');
    });

    setTimeout(function(){
      if (!deferredPrompt) {
        showCard('Install ChamaPay', 'Add ChamaPay to your phone from your browser menu.', 'How to install',
          '<ol><li>Tap the browser <b>⋮</b> menu.</li><li>Choose <b>Install app</b> or <b>Add to Home screen</b>.</li><li>Confirm the installation.</li></ol>');
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
      if (steps && !steps.hidden) {
        button.textContent = 'Done';
        return;
      }
      toggleInstructions('<ol><li>Tap the browser <b>⋮</b> menu.</li><li>Choose <b>Install app</b> or <b>Add to Home screen</b>.</li><li>Confirm the installation.</li></ol>');
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

// Menu + offline UX.
(function(){
  var menu = document.querySelector('.app-menu');
  if (menu) {
    document.addEventListener('click', function(e){
      if (e.target.closest('[data-menu-close]')) menu.removeAttribute('open');
      else if (!e.target.closest('.app-menu')) menu.removeAttribute('open');
    });
    document.addEventListener('keydown', function(e){
      if (e.key === 'Escape') menu.removeAttribute('open');
    });
  }

  // Save a deliberately small snapshot. It contains only elements explicitly marked
  // offline-safe; no authenticated pages or forms are cached by the service worker.
  if (location.pathname === '/dashboard' || location.pathname.indexOf('/my-dashboard') !== -1) {
    try {
      var cards = Array.prototype.slice.call(document.querySelectorAll('.dashboard-chama-card'));
      if (!cards.length) cards = Array.prototype.slice.call(document.querySelectorAll('.my-chamas-hero + .grid .card'));
      if (!cards.length) {
        var safe = Array.prototype.slice.call(document.querySelectorAll('.offline-safe'));
        if (safe.length) cards = [document.querySelector('.stats') || document.body];
      }
      var items = cards.map(function(card){
        var nameEl = card.querySelector('h3');
        var metaEl = card.querySelector('p');
        var metrics = [];
        card.querySelectorAll('.stat,.metric').forEach(function(node){
          var label=node.querySelector('small,span'); var value=node.querySelector('b');
          if(label && value) metrics.push({label:label.textContent.trim(),value:value.textContent.trim()});
        });
        return {name: nameEl ? nameEl.textContent.replace('→','').trim() : 'My Chama', meta: metaEl ? metaEl.textContent.trim() : 'Chama information', metrics:metrics.slice(0,4)};
      }).filter(function(x){return x.name;});
      if (location.pathname.indexOf('/my-dashboard') !== -1) {
        var safeItems = Array.prototype.slice.call(document.querySelectorAll('.offline-safe')).map(function(node){
          var label=node.querySelector('small'); var value=node.querySelector('b');
          return {name: label ? label.textContent.trim() : 'ChamaPay', meta:'Private offline snapshot', metrics: label && value ? [{label:label.textContent.trim(),value:value.textContent.trim()}] : []};
        });
        if (safeItems.length) items = [{name: document.title.replace('My Chama Dashboard - ','').trim() || 'My Chama',meta:'Private dashboard snapshot',metrics:safeItems.map(function(x){return x.metrics[0];}).filter(Boolean)}];
      }
      if (items.length) localStorage.setItem('chamapay-offline-summary', JSON.stringify({savedAt:Date.now(),items:items.slice(0,10)}));
    } catch(e) {}
  }

})();

// Small secure convenience actions used by payment and member-invitation screens.
(function(){
  function copyText(value, button){
    if(!value) return;
    var done=function(){ if(button){ var old=button.textContent; button.textContent='Copied'; setTimeout(function(){button.textContent=old;},1400); } };
    if(navigator.clipboard && navigator.clipboard.writeText){ navigator.clipboard.writeText(value).then(done).catch(function(){ fallback(); }); }
    else fallback();
    function fallback(){
      var ta=document.createElement('textarea'); ta.value=value; ta.setAttribute('readonly',''); ta.style.position='fixed'; ta.style.opacity='0';
      document.body.appendChild(ta); ta.select(); try{document.execCommand('copy');done();}catch(e){} document.body.removeChild(ta);
    }
  }
  document.addEventListener('click',function(e){
    var c=e.target.closest('[data-copy-text]');
    if(c){e.preventDefault();copyText(c.getAttribute('data-copy-text'),c);return;}
    var s=e.target.closest('[data-share-text]');
    if(s){
      e.preventDefault(); var text=s.getAttribute('data-share-text')||'';
      if(navigator.share){navigator.share({title:'ChamaPay member join code',text:text}).catch(function(){});}
      else copyText(text,s);
    }
  });
})();
