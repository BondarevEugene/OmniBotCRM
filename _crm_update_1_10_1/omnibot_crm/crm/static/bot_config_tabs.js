(() => {
  const tabs = [...document.querySelectorAll('[data-config-tab]')];
  const names = tabs.map(tab => tab.getAttribute('href').slice(1));
  const panels = names.map(id => document.getElementById(id));
  const show = () => {
    const target = document.getElementById(location.hash.slice(1));
    const panel = target && target.closest('section[id]');
    const active = names.includes(panel?.id) ? panel.id : 'profile';
    panels.forEach((item, index) => { if (item) item.hidden = names[index] !== active; });
    tabs.forEach(tab => {
      const selected = tab.getAttribute('href') === `#${active}`;
      tab.classList.toggle('active', selected);
      if (selected) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
    });
    if (target && target.id !== active) target.scrollIntoView({block: 'start'});
  };
  addEventListener('hashchange', show);
  show();
})();
