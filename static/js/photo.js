// Profile picture picker: validates, shrinks the photo on the device, previews it, and saves automatically.
(function () {
  var OK = ['image/jpeg', 'image/png', 'image/webp'], SIZE = 384, SERVER_MAX = 2 * 1024 * 1024;
  function shrink(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file), img = new Image();
      img.onload = function () {
        try {
          var side = Math.min(img.naturalWidth, img.naturalHeight), c = document.createElement('canvas');
          c.width = c.height = Math.min(SIZE, side);
          c.getContext('2d').drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, c.width, c.height);
          URL.revokeObjectURL(url);
          c.toBlob(function (b) { b ? resolve(b) : reject(new Error('encode')); }, 'image/jpeg', 0.86);
        } catch (e) { reject(e); }
      };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error('read')); };
      img.src = url;
    });
  }
  function init(form) {
    var input = form.querySelector('[data-pp-input]'), ring = form.querySelector('[data-pp-avatar]');
    var note = form.parentNode.querySelector('[data-pp-note]');
    function say(t) { if (note) note.textContent = t; }
    function preview(blob) {
      var u = URL.createObjectURL(blob), img = ring.querySelector('img');
      if (!img) { var ini = ring.querySelector('[data-pp-initial]'); if (ini) ini.remove(); img = document.createElement('img'); img.alt = ''; ring.insertBefore(img, ring.firstChild); }
      img.src = u;
    }
    function send(blob, name) {
      try { var dt = new DataTransfer(); dt.items.add(new File([blob], name, { type: blob.type })); input.files = dt.files; } catch (e) { /* keep original file */ }
      preview(blob); say('Saving…'); ring.classList.add('is-busy'); form.submit();
    }
    input.addEventListener('change', function () {
      var f = input.files && input.files[0]; if (!f) return;
      if (OK.indexOf(f.type) < 0) { say('Use a JPG, PNG or WebP photo.'); input.value = ''; return; }
      if (f.size > 12 * 1024 * 1024) { say('That photo is too large.'); input.value = ''; return; }
      say('Preparing…');
      shrink(f).then(function (b) { send(b, 'photo.jpg'); }).catch(function () {
        if (f.size > SERVER_MAX) { say('That photo is too large. Try a smaller one.'); input.value = ''; return; }
        send(f, f.name);
      });
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll('[data-pp-form]'), init);
})();
