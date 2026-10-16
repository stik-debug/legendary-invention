// Register the root-scoped service worker from this same-origin external script.
// The app uses a restrictive CSP that blocks inline scripts, so registration must not
// live in an inline script in base.html.
(function registerChamaPayServiceWorker(){
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('/sw.js', {scope: '/'}).catch(function(error){
    if (window.console && console.warn) console.warn('ChamaPay service worker registration failed:', error);
  });
})();

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
    if(button) button.addEventListener('click', function(){
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

  // Offline storage is deliberately metadata-only. Never persist financial amounts,
  // loan balances, fines, dividends, member contact details or transaction records in localStorage.
  if (location.pathname === '/dashboard' || location.pathname.indexOf('/my-dashboard') !== -1) {
    try {
      var items=[];
      if (location.pathname.indexOf('/my-dashboard') !== -1) {
        items=[{name:document.title.replace('My Chama Dashboard - ','').trim() || 'My Chama',meta:'Reconnect to view current private financial figures.',metrics:[]}];
      } else {
        var cards=Array.prototype.slice.call(document.querySelectorAll('.dashboard-chama-card'));
        if (!cards.length) cards=Array.prototype.slice.call(document.querySelectorAll('.my-chamas-hero + .grid .card'));
        items=cards.map(function(card){
          var nameEl=card.querySelector('h3');
          return {name:nameEl ? nameEl.textContent.replace('→','').trim() : '',meta:'Reconnect to view current private financial figures.',metrics:[]};
        }).filter(function(x){return x.name;}).slice(0,10);
      }
      // Replace any older cached snapshot, which may have included financial totals.
      localStorage.removeItem('chamapay-offline-summary');
      if (items.length) localStorage.setItem('chamapay-offline-summary',JSON.stringify({savedAt:Date.now(),items:items}));
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


// Low-connectivity guard: never pretend that a financial/governance POST was queued offline.
(function(){
  var banner=document.getElementById('network-status');
  function refresh(){ if(banner) banner.hidden=!!navigator.onLine; }
  window.addEventListener('online',refresh); window.addEventListener('offline',refresh); refresh();
  document.addEventListener('submit',function(e){
    if(!navigator.onLine && (e.target.method||'').toLowerCase()==='post'){
      e.preventDefault();
      if(banner){banner.hidden=false;banner.textContent='You are offline. This change was not submitted or queued. Reconnect, review the form, and submit again.';}
      else window.alert('You are offline. This change was not submitted or queued. Reconnect and try again.');
    }
  },true);
})();


// App-wide live alerts: a toast while the app is open, native notification when backgrounded.
(function(){
  var root = document.getElementById('chamapay-notification-root');
  if (root && root.getAttribute('data-poll-url')) {
    var url = root.getAttribute('data-poll-url');
    var last = parseInt(root.getAttribute('data-last-id') || '0', 10) || 0;
    var busy = false;
    function toast(item) {
      var host = document.getElementById('chamapay-toast-stack');
      if (!host) { host = document.createElement('div'); host.id = 'chamapay-toast-stack'; host.className = 'chamapay-toast-stack'; host.setAttribute('aria-live','polite'); host.setAttribute('aria-relevant','additions'); document.body.appendChild(host); }
      var a = document.createElement('a'); a.className = 'chamapay-toast';
      var href = item.link;
      a.href = (typeof href === 'string' && href.charAt(0) === '/' && href.indexOf('//') !== 0) ? href : '/notifications';
      var icon = document.createElement('span'); icon.className = 'chamapay-toast-icon'; icon.textContent = '●';
      var copy = document.createElement('span'); copy.className = 'chamapay-toast-copy';
      var strong = document.createElement('strong'); strong.textContent = item.chama || 'ChamaPay update';
      var body = document.createElement('span'); body.textContent = item.text || 'You have a new update.';
      copy.appendChild(strong); copy.appendChild(body); a.appendChild(icon); a.appendChild(copy);
      var close = document.createElement('button'); close.type = 'button'; close.className = 'chamapay-toast-close'; close.setAttribute('aria-label','Dismiss notification'); close.textContent = '×';
      close.addEventListener('click', function(e){ e.preventDefault(); e.stopPropagation(); a.remove(); }); a.appendChild(close); host.appendChild(a);
      window.setTimeout(function(){ if (a.parentNode) a.classList.add('is-leaving'); window.setTimeout(function(){ if (a.parentNode) a.remove(); }, 350); }, 8000);
    }
    function poll() {
      if (busy || !navigator.onLine) return; busy = true;
      fetch(url + '?after=' + encodeURIComponent(last), {credentials:'same-origin',headers:{'Accept':'application/json','Cache-Control':'no-store'}})
        .then(function(r){ if (r.status === 401 || r.redirected) return null; return r.ok ? r.json() : null; })
        .then(function(data){ if (!data || !Array.isArray(data.notifications)) return;
          data.notifications.forEach(function(n){ last = Math.max(last, Number(n.id)||0);
            if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
              try { var note = new Notification(n.chama || 'ChamaPay update', {body:n.text || 'You have a new update.',icon:'/static/icons/icon-192.png',tag:'chamapay-' + n.id}); note.onclick = function(){ window.focus(); window.location.assign((n.link && n.link.charAt(0)==='/' && n.link.indexOf('//')!==0) ? n.link : '/notifications'); }; } catch(_) { toast(n); }
            } else { toast(n); }
          });
        }).catch(function(){}).then(function(){ busy = false; });
    }
    window.setInterval(poll, 7000); document.addEventListener('visibilitychange', function(){ if (!document.hidden) poll(); });
  }

  // Existing one-request success/warning/error flashes are also surfaced as a short popup.
  document.querySelectorAll('.flash').forEach(function(flash){
    var text = (flash.textContent || '').trim(); if (!text || flash.dataset.popupShown === '1') return;
    flash.dataset.popupShown = '1';
    var host = document.getElementById('chamapay-toast-stack');
    if (!host) { host = document.createElement('div'); host.id = 'chamapay-toast-stack'; host.className = 'chamapay-toast-stack'; host.setAttribute('aria-live','polite'); document.body.appendChild(host); }
    var a = document.createElement('div'); a.className = 'chamapay-toast'; a.setAttribute('role','status');
    var icon = document.createElement('span'); icon.className = 'chamapay-toast-icon'; icon.textContent = flash.classList.contains('danger') ? '!' : '✓';
    var copy = document.createElement('span'); copy.className = 'chamapay-toast-copy';
    var strong = document.createElement('strong'); strong.textContent = 'ChamaPay update';
    var body = document.createElement('span'); body.textContent = text;
    copy.appendChild(strong); copy.appendChild(body); a.appendChild(icon); a.appendChild(copy); host.appendChild(a);
    window.setTimeout(function(){ if(a.parentNode)a.classList.add('is-leaving'); window.setTimeout(function(){if(a.parentNode)a.remove();},350); },6500);
  });

  // Ask signed-in members whether they want phone notifications on first eligible visit.
  // The browser's native permission prompt must follow an explicit user action, so the Allow choice opens Notification Settings.
  (function notificationOnboarding(){
    var panel = document.getElementById('notification-onboarding');
    if (!panel) return;
    var allow = document.getElementById('notification-onboarding-allow');
    var deny = document.getElementById('notification-onboarding-deny');
    var key = 'chamapay-notification-choice-v1';
    var permission = ('Notification' in window) ? Notification.permission : 'unsupported';
    if (permission !== 'default') return;
    var choice = null;
    try { choice = window.localStorage.getItem(key); } catch (_) {}
    if (choice) return;
    window.setTimeout(function(){ panel.hidden = false; if (allow) allow.focus(); }, 900);
    function close(choiceValue){
      try { window.localStorage.setItem(key, choiceValue); } catch (_) {}
      panel.hidden = true;
    }
    if (deny) deny.addEventListener('click', function(){ close('later'); });
    if (allow) allow.addEventListener('click', function(){
      try { window.localStorage.setItem(key, 'settings'); } catch (_) {}
      // On the settings page members press the enable button, which triggers the browser's permission prompt.
    });
    panel.addEventListener('click', function(e){ if(e.target === panel) close('later'); });
  })();

  var button = document.getElementById('enable-push'), testButton = document.getElementById('test-push'), status = document.getElementById('push-status');
  if (!button && !testButton) return;
  function say(s){ if(status) status.textContent=s; }
  function csrf(){ return (document.querySelector('meta[name="csrf-token"]')||{}).content||''; }
  function refreshPushStatus(){ fetch('/api/push/status',{credentials:'same-origin',headers:{'Accept':'application/json','Cache-Control':'no-store'}}).then(function(r){return r.ok?r.json():null;}).then(function(j){ if(!j)return; if(testButton)testButton.disabled=!(j.server_configured && j.delivery_library && j.device_subscriptions>0); if(!j.server_configured)say('Push service key setup is unavailable. Please contact ChamaPay support.'); else if(!j.delivery_library)say('The server is missing its push delivery package. The host must install requirements.txt.'); else if(!j.device_subscriptions)say('Enable device notifications on this phone, then send a test.'); else say('This account has '+j.device_subscriptions+' registered device(s).'); }).catch(function(){}); }
  refreshPushStatus();
  if(testButton)testButton.addEventListener('click',function(){ testButton.disabled=true; say('Sending test notification…'); fetch('/api/push/test',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf(),'Accept':'application/json'},body:'{}'}).then(function(r){return r.json().then(function(j){if(!r.ok||!j.ok)throw new Error(j.error||'Test notification failed.');return j;});}).then(function(j){say(j.message||'Test queued. Check this phone in a few seconds.');}).catch(function(e){say(e.message||'Test notification failed.');}).then(refreshPushStatus); });
  function decodeKey(base64) { var pad='='.repeat((4-base64.length%4)%4); var raw=atob((base64+pad).replace(/-/g,'+').replace(/_/g,'/')); var out=new Uint8Array(raw.length); for(var i=0;i<raw.length;i++)out[i]=raw.charCodeAt(i); return out; }
  if(button) button.addEventListener('click', function(){
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) { say('This browser does not support push notifications. On-screen alerts still work while ChamaPay is open.'); return; }
    var key = document.querySelector('meta[name="vapid-public-key"]'); var publicKey = key && key.content;
    if (!publicKey) { say('Server push is not configured yet. Ask the ChamaPay administrator to configure VAPID keys.'); return; }
    button.disabled = true; say('Requesting permission…');
    Notification.requestPermission().then(function(permission){
      if(permission !== 'granted') throw new Error('Notification permission was not granted. You can change this in browser settings.');
      return navigator.serviceWorker.register('/sw.js', {scope: '/'});
    }).then(function(reg){ return reg.pushManager.getSubscription().then(function(existing){ return existing || reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:decodeKey(publicKey)}); }); })
      .then(function(subscription){ return fetch('/api/push/subscribe',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify({subscription:subscription.toJSON()})}); })
      .then(function(r){ return r.json().then(function(j){ if(!r.ok || !j.ok) throw new Error(j.error || 'Could not enable push notifications.'); return j; }); })
      .then(function(){ say('Device notifications are enabled on this browser. Send a test notification to confirm delivery.'); button.textContent='Notifications enabled'; refreshPushStatus(); })
      .catch(function(e){ say(e.message || 'Could not enable notifications.'); }).then(function(){ button.disabled=false; });
  });
})();


// Keep this device registered for background push once the member has allowed notifications.
// Subscriptions can be lost (browser data cleared, push service rotation, expired endpoint),
// so silently re-register on any signed-in page. This never shows a permission prompt.
(function keepPushRegistered(){
  try {
    var root = document.getElementById('chamapay-notification-root');
    if (!root || !root.getAttribute('data-poll-url')) return;
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return;
    if (Notification.permission !== 'granted') return;
    var keyMeta = document.querySelector('meta[name="vapid-public-key"]');
    var publicKey = keyMeta && keyMeta.content;
    var csrfMeta = document.querySelector('meta[name="csrf-token"]');
    if (!publicKey || !csrfMeta) return;
    function decodeKey(base64){ var pad='='.repeat((4-base64.length%4)%4); var raw=atob((base64+pad).replace(/-/g,'+').replace(/_/g,'/')); var out=new Uint8Array(raw.length); for(var i=0;i<raw.length;i++)out[i]=raw.charCodeAt(i); return out; }
    navigator.serviceWorker.register('/sw.js', {scope: '/'}).then(function(reg){
      return navigator.serviceWorker.ready.then(function(){ return reg.pushManager.getSubscription(); }).then(function(existing){
        return existing || reg.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:decodeKey(publicKey)});
      });
    }).then(function(subscription){
      if (!subscription) return;
      // Re-send on every signed-in session start so the server always knows this device belongs to this account.
      var stamp = 'chamapay-push-sync';
      var signature = subscription.endpoint.slice(-24) + ':' + (root.getAttribute('data-last-id') ? 'u' : 'x');
      try { if (window.sessionStorage.getItem(stamp) === signature) return; } catch (_) {}
      return fetch('/api/push/subscribe', {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrfMeta.content},
        body:JSON.stringify({subscription:subscription.toJSON()})}).then(function(r){
          if (r.ok) { try { window.sessionStorage.setItem(stamp, signature); } catch (_) {} }
        });
    }).catch(function(){});
  } catch (_) {}
})();
