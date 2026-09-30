// Lite mode toggle
document.querySelector('[data-perf]')?.addEventListener('click', e => {
  document.documentElement.classList.toggle('perf');
  localStorage.setItem('perf', document.documentElement.classList.contains('perf'));
});

if (localStorage.getItem('perf') === 'true') {
  document.documentElement.classList.add('perf');
}

// 3D tilt effect on cards (if motion is preferred)
if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  document.querySelectorAll('.tilt').forEach(el => {
    el.addEventListener('mousemove', e => {
      const rect = el.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width;
      const y = (e.clientY - rect.top) / rect.height;
      const rotateX = (y - 0.5) * 8;
      const rotateY = (x - 0.5) * -8;
      el.style.transform = `perspective(800px) rotateX(${rotateX}deg) rotateY(${rotateY}deg)`;
    });
    el.addEventListener('mouseleave', () => {
      el.style.transform = '';
    });
  });
}
