(() => {
  const key = 'quant-studio.return-colors';
  const root = document.documentElement;
  let chosen = 'red-up';
  try { if (localStorage.getItem(key) === 'green-up') chosen = 'green-up'; } catch (_) {}
  root.dataset.returnColors = chosen;
  document.querySelectorAll('input[data-rate-bp]').forEach(input => {
    input.addEventListener('input', () => {
      const amount = Number(input.value);
      input.closest('label').querySelector('.field-help').textContent = Number.isFinite(amount)
        ? '= ' + (amount / 100).toLocaleString('zh-CN', {maximumFractionDigits:10}) + '%；1 bp = 0.01%'
        : '请输入有效的基点数值';
    });
  });
  const select = document.getElementById('return-colors');
  if (select) {
    select.value = chosen;
    select.addEventListener('change', () => {
      root.dataset.returnColors = select.value;
      try { localStorage.setItem(key, select.value); } catch (_) {}
    });
  }
  // Only annotate signed return displays, never form inputs or technical output.
  document.querySelectorAll('.metric strong,.stat b,td').forEach(node => {
    const text = node.textContent.trim();
    if (/^[+]\d+(\.\d+)?%$/.test(text)) node.classList.add('return-positive');
    if (/^[−-]\d+(\.\d+)?%$/.test(text)) node.classList.add('return-negative');
  });
  const editor = document.getElementById('recipe-editor');
  if (editor) {
    const fields = [...editor.querySelectorAll('.recipe-field')];
    const toggle = document.getElementById('show-technical-names');
    toggle?.addEventListener('change', () => {
      editor.querySelectorAll('.technical-name').forEach(n => { n.hidden = !toggle.checked; });
    });
    const summary = document.getElementById('plan-summary');
    function summarize() {
      if (!summary) return;
      summary.replaceChildren();
      ['区间与标的','资金与交易','因子与方法'].forEach(group => {
        const values = fields.filter(f => f.dataset.group === group && !f.closest('details')).map(field => {
          const input = field.querySelector('input[name],select[name],textarea[name]');
          if (!input) return '';
          let value = input.tagName === 'SELECT' ? input.selectedOptions[0]?.textContent : input.value;
          if (field.dataset.summaryValue) value = field.dataset.summaryValue;
          if (field.dataset.key === 'factors' && field.querySelector('[data-factor-label]')) {
            value = [...field.querySelectorAll('[data-factor-label]:checked')].map(n=>n.dataset.factorLabel).join('、');
          }
          if (field.dataset.key === 'instruments') {
            const rows = [...field.querySelectorAll('.instrument-row')];
            if (rows.length) value = rows.filter(r=>r.querySelector('input[type=checkbox]').checked).map(r=>r.querySelector('[data-key=name]').value || r.querySelector('[data-key=symbol]').value).join('、');
            else try { value = JSON.parse(input.value).map(i => i.name || i.symbol).join('、'); } catch (_) {}
          }
          return field.dataset.label + '：' + (value || '未填写');
        }).filter(Boolean);
        const p = document.createElement('p');
        p.textContent = group + ' · ' + (values.join('；') || '模板未声明');
        summary.appendChild(p);
      });
    }
    fields.forEach(field => {
      const input = field.querySelector('input[name],select[name],textarea[name]');
      if (!input) return;
      input.addEventListener('input', () => {
        if (field.dataset.unit === 'bp') {
          const number = Number(input.value);
          field.querySelector('.field-help').textContent = Number.isFinite(number)
            ? '= ' + (number / 100).toLocaleString('zh-CN', {maximumFractionDigits:10}) + '%；1 bp = 0.01%'
            : '请输入有效的基点数值';
        }
        summarize();
      });
      input.addEventListener('blur', () => {
        const error = field.querySelector('.field-error');
        if (!error) return;
        const invalid = !input.checkValidity();
        input.setAttribute('aria-invalid', String(invalid));
        error.textContent = invalid ? field.dataset.label + '：' + input.validationMessage : '';
      });
    });
    editor.addEventListener('input', summarize);
    summarize();
  }
})();
