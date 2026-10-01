/* Progressive enhancement for Python-backed bot field forms. Validation stays server side. */
document.querySelectorAll('.field-form').forEach(form => {
  const kind = form.querySelector('[data-field-kind]');
  const host = form.querySelector('[data-value-host]');
  const choices = form.querySelector('[name="choices"]');
  let current = form.dataset.value || '';
  function render() {
    const type = kind.value;
    const options = choices.value.split('\n').map(x => x.trim()).filter(Boolean);
    host.replaceChildren();
    let control;
    if (type === 'choice' && options.length) {
      control = document.createElement('select');
      control.append(new Option('Выберите вариант', ''));
      options.forEach(option => control.append(new Option(option, option)));
      control.value = current;
    } else if (type === 'multi_choice' && options.length) {
      control = document.createElement('div');
      control.className = 'choice-grid';
      let selected = [];
      try { selected = JSON.parse(current) || []; } catch (_) { selected = []; }
      options.forEach(option => {
        const label = document.createElement('label');
        const box = document.createElement('input');
        box.type = 'checkbox'; box.value = option; box.checked = selected.includes(option);
        box.addEventListener('change', updatePreview);
        label.append(box, document.createTextNode(' ' + option)); control.append(label);
      });
      const value = document.createElement('input');
      value.type = 'hidden'; value.name = 'value'; value.value = JSON.stringify(selected);
      control.append(value);
      control.addEventListener('change', () => {
        value.value = JSON.stringify([...control.querySelectorAll('input[type="checkbox"]:checked')].map(box => box.value));
        current = value.value;
      });
    } else if (type === 'boolean') {
      control = document.createElement('select');
      control.append(new Option('Не задано', ''), new Option('Да', 'true'), new Option('Нет', 'false'));
      control.value = current;
    } else if (['integer','number','date','email','url','secret'].includes(type)) {
      control = document.createElement('input');
      control.type = ({integer:'number', number:'number', date:'date', email:'email', url:'url', secret:'password'})[type];
      if (type === 'number') control.step = 'any';
      if (type === 'integer') control.step = '1';
      if (type === 'secret') { control.autocomplete = 'new-password'; control.placeholder = 'Секрет сохранён, если поле пустое'; }
      else control.value = current;
    } else {
      control = document.createElement('textarea'); control.rows = type === 'multiline' ? 4 : 2;
      control.value = current;
    }
    if (type !== 'multi_choice') {
      control.name = 'value';
      control.addEventListener('input', () => { current = control.value; updatePreview(); });
      host.append(control);
    } else host.append(control);
    choices.closest('label').hidden = !['choice','multi_choice'].includes(type);
    for (const name of ['min_value','max_value']) form.querySelector(`[name="${name}"]`).closest('label').hidden = !['integer','number'].includes(type);
  }
  kind.addEventListener('change', () => { current = ''; render(); updatePreview(); });
  choices.addEventListener('change', render);
  render();
});
function updatePreview() {
  const values = {};
  document.querySelectorAll('.field-editor').forEach(editor => {
    const form = editor.querySelector('.field-form');
    const input = form && form.querySelector('[name="value"]');
    if (input && form.querySelector('[name="kind"]').value !== 'secret') values[editor.dataset.key] = input.value;
  });
  document.querySelectorAll('[data-preview-key]').forEach(item => {
    const condition = JSON.parse(item.dataset.condition || '{}');
    item.hidden = Boolean(condition.key) && values[condition.key] !== condition.equals;
  });
}
updatePreview();
