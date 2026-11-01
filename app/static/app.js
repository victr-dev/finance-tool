'use strict';
/* Finance app front end. Plain JS, no build step. Server does the math (calc.py); this file draws pages and forms. */

const $main = document.getElementById('main');
let L = null;                                   // master lists + settings (from /api/lists)
const UI = loadUI();                            // per-browser view preferences

// ------------------------------------------------------------------ utilities
function loadUI() {
  try { return JSON.parse(localStorage.getItem('finance-ui')) || {}; } catch { return {}; }
}
function saveUI() {
  try { localStorage.setItem('finance-ui', JSON.stringify(UI)); } catch { /* private mode: fine */ }
}

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch('/api/' + path, {
      method: opts.method || 'GET',
      headers: opts.body !== undefined ? { 'Content-Type': 'application/json' } : {},
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new Error("Can't reach the app. Double-click “Finance” on your desktop to start it again.");
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}
const send = (method, path, body) => api(path, { method, body: body || {} });

function h(tag, props, ...kids) {
  const [t, ...cls] = tag.split('.');
  const el = document.createElement(t || 'div');
  if (cls.length) el.className = cls.join(' ');
  if (props === null || typeof props !== 'object' || props instanceof Node || Array.isArray(props)) {
    kids.unshift(props);
    props = null;
  }
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = (el.className + ' ' + v).trim();
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'style') el.style.cssText = v;
    else if (['value', 'checked', 'disabled', 'selected', 'open'].includes(k)) el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    el.append(k instanceof Node ? k : String(k));
  }
  return el;
}

const money = (n, cents = true) => (n === null || n === undefined || Number.isNaN(n)) ? '–'
  : (n < -0.004 ? '-' : '') + '$' + Math.abs(n).toLocaleString('en-US',
    { minimumFractionDigits: cents ? 2 : 0, maximumFractionDigits: cents ? 2 : 0 });
const money0 = n => money(n, false);
const pct = n => (n === null || n === undefined) ? '–' : Math.round(n * 100) + '%';
const pd = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d || 1); };
const fdate = s => s ? pd(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '';
const fdateW = s => s ? pd(s).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) : '';
const fdateY = s => s ? pd(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '';
const fmonth = (s, long) => s ? pd(s.slice(0, 7)).toLocaleDateString('en-US', { month: long ? 'long' : 'short', year: 'numeric' }) : '';
const ymOf = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
const addMonth = (ym, n) => { const d = pd(ym); d.setMonth(d.getMonth() + n); return ymOf(d); };
const todayISO = () => L.today;
const curMonth = () => { const t = L.today.slice(0, 7), s = L.settings.start_month || t; return t > s ? t : s; };
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const byId = (list, id) => list.find(r => r.id === id);

function tone(s) {
  s = s || '';
  if (/✓|Hit|HIT|OVER|on track|Worked|Active/.test(s)) return 'good';
  if (/Overdue|Over budget|Short|UNDER|✗/.test(s)) return 'bad';
  if (/Due soon|Partly|Due today|Needs tips|In progress|Not yet/.test(s)) return 'warn';
  if (/Upcoming|Scheduled|^In \d|used|Starts/.test(s)) return 'info';
  return '';
}
const badge = (text, t) => h('span.badge', { class: t === undefined ? tone(text) : t }, text);
const bar = (frac, cls) => h('div.bar', { class: cls || null }, h('i', { style: `width:${Math.max(0, Math.min(1, frac || 0)) * 100}%` }));

function toast(msg, bad) {
  const t = h('div.toast', { class: bad ? 'bad' : null }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), bad ? 5000 : 2200);
}

// ------------------------------------------------------------------ modal + forms
function modal(title, body, buttons) {
  const back = h('div.modal-back');
  const close = () => { back.remove(); document.removeEventListener('keydown', esc); };
  const esc = e => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', esc);
  back.addEventListener('mousedown', e => { if (e.target === back) close(); });
  back.append(h('div.modal', h('header', title), h('div.body', body), h('footer', buttons(close))));
  document.body.append(back);
  return { close, el: back };
}

function confirmBox(title, text, okLabel = 'Delete', danger = true) {
  return new Promise(resolve => {
    const m = modal(title, h('p.muted', text), close => [
      h('button.btn', { class: danger ? 'danger' : 'primary', onclick: () => { close(); resolve(true); } }, okLabel),
      h('span.spacer'),
      h('button.btn.ghost', { onclick: () => { close(); resolve(false); } }, 'Cancel'),
    ]);
    m.el.querySelector('button').focus();
  });
}

function optionNodes(options, value, blank) {
  const out = [];
  if (blank !== undefined) out.push(h('option', { value: '' }, blank));
  const groups = new Map();
  for (const o of options) {
    const [v, label, group] = Array.isArray(o) ? o : (typeof o === 'object' ? [o.value, o.label, o.group] : [o, o]);
    const opt = h('option', { value: String(v), selected: String(v) === String(value ?? '') }, label);
    if (!group) { out.push(opt); continue; }
    if (!groups.has(group)) { const g = h('optgroup', { label: group }); groups.set(group, g); out.push(g); }
    groups.get(group).append(opt);
  }
  return out;
}

/* fields: {name, label, type: text|money|number|date|month|select|check|textarea, options, required, hint, full,
            blank (label of the empty option), placeholder, min, max, step} */
function openForm({ title, fields, values = {}, submitLabel = 'Save', onSubmit, onDelete, extraButtons }) {
  const inputs = {};
  const form = h('div.form');
  for (const f of fields) {
    const v = values[f.name];
    let input;
    if (f.type === 'select') {
      input = h('select', null, optionNodes(f.options, v, f.required ? undefined : (f.blank ?? '—')));
    } else if (f.type === 'check') {
      input = h('input', { type: 'checkbox', checked: !!v });
    } else if (f.type === 'textarea') {
      input = h('textarea', { rows: 2, value: v ?? '', placeholder: f.placeholder || null });
    } else {
      const type = { money: 'number', number: 'number', date: 'date', month: 'month' }[f.type] || 'text';
      input = h('input', {
        type, value: v ?? '', placeholder: f.placeholder || null, min: f.min ?? null, max: f.max ?? null,
        step: f.step || (f.type === 'money' ? '0.01' : (type === 'number' ? 'any' : null)), inputmode: type === 'number' ? 'decimal' : null,
      });
    }
    inputs[f.name] = input;
    if (f.type === 'check') {
      form.append(h('label.f.full', h('span.check', input, f.label), f.hint && h('span.hint', f.hint)));
    } else {
      form.append(h('label.f', { class: f.full ? 'full' : null }, f.label, input, f.hint && h('span.hint', f.hint)));
    }
  }
  const err = h('div.err');
  const collect = () => {
    const out = {};
    for (const f of fields) {
      const el = inputs[f.name];
      out[f.name] = f.type === 'check' ? el.checked : el.value.trim();
      if (f.required && out[f.name] === '') throw new Error(`${f.label} is required.`);
    }
    return out;
  };
  let busy = false;
  const save = async () => {
    if (busy) return;
    err.textContent = '';
    try {
      busy = true;
      const vals = collect();
      await onSubmit(vals);
      m.close();
      await refresh();
    } catch (e) { err.textContent = e.message; } finally { busy = false; }
  };
  const m = modal(title, [form, err], close => [
    h('button.btn.primary', { onclick: save }, submitLabel),
    ...(extraButtons ? extraButtons(close) : []),
    h('span.spacer'),
    onDelete && h('button.btn.danger.ghost', { onclick: async () => { close(); await onDelete(); } }, 'Delete'),
    h('button.btn.ghost', { onclick: close }, 'Cancel'),
  ]);
  m.el.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') { e.preventDefault(); save(); } });
  const first = form.querySelector('input:not([type=checkbox]), select, textarea');
  if (first) setTimeout(() => first.focus(), 30);
  return m;
}

const splitRef = v => { if (!v) return [null, null]; const [t, id] = v.split(':'); return [t, Number(id)]; };
const joinRef = (t, id) => (t && id ? `${t}:${id}` : '');
const num = v => (v === '' || v === null || v === undefined) ? null : Number(v);

// ------------------------------------------------------------------ dropdown option builders
const live = r => !r.archived;
function categoryOptions(current) {
  const cm = curMonth(), o = [];
  for (const g of ['Needs', 'Wants'])
    for (const i of L.budget_items)
      if (i.grp === g && (!i.end_month || i.end_month >= addMonth(cm, -1) || current === 'item:' + i.id))
        o.push({ value: 'item:' + i.id, label: i.name, group: g });
  for (const d of L.debts) if (live(d) || current === 'debt:' + d.id) o.push({ value: 'debt:' + d.id, label: d.name, group: 'Debt payment' });
  for (const f of L.funds) if (live(f) || current === 'fund:' + f.id) o.push({ value: 'fund:' + f.id, label: f.name, group: 'Put into savings' });
  for (const a of L.accounts) if (live(a) || current === 'account:' + a.id) o.push({ value: 'account:' + a.id, label: a.name, group: 'Move to account' });
  return o;
}
function sourceOptions(current) {
  const o = [];
  for (const a of L.accounts) if (live(a) || current === 'account:' + a.id) o.push({ value: 'account:' + a.id, label: a.name, group: 'Accounts' });
  for (const d of L.debts) if (live(d) || current === 'debt:' + d.id) o.push({ value: 'debt:' + d.id, label: d.name, group: 'Credit cards & loans' });
  for (const f of L.funds) if (live(f) || current === 'fund:' + f.id) o.push({ value: 'fund:' + f.id, label: f.name, group: 'Savings (not counted in budget)' });
  return o;
}
function destOptions(current) {
  const o = [];
  for (const a of L.accounts) if (live(a) || current === 'account:' + a.id) o.push({ value: 'account:' + a.id, label: a.name, group: 'Accounts' });
  for (const f of L.funds) if (live(f) || current === 'fund:' + f.id) o.push({ value: 'fund:' + f.id, label: f.name, group: 'Savings funds' });
  return o;
}
const accountOptions = () => L.accounts.filter(live).map(a => [a.id, a.name]);
const shiftTypeOptions = () => L.shift_types.map(t => [t.id, t.name]);
const incomeTypeOptions = () => L.income_types.map(t => [t.id, t.name]);
const defaultTipsName = () => { const a = byId(L.accounts, L.settings.default_tips_account); return a ? a.name : 'no default set'; };

// ------------------------------------------------------------------ record forms
function txForm(rec = {}, preset = {}) {
  const v = {
    date: rec.date || preset.date || todayISO(), amount: rec.amount ?? preset.amount ?? '', description: rec.description ?? preset.description ?? '',
    cat: joinRef(rec.cat_type, rec.cat_id) || preset.cat || '', src: joinRef(rec.src_type, rec.src_id) || preset.src || UI.lastSrc || '',
    unexpected: !!rec.unexpected, notes: rec.notes || '',
  };
  openForm({
    title: rec.id ? 'Edit transaction' : (preset.title || 'Add spending / payment'),
    values: v,
    fields: [
      { name: 'date', label: 'Date', type: 'date', required: true },
      { name: 'amount', label: 'Amount', type: 'money', required: true, min: 0 },
      { name: 'description', label: 'Description', full: true, placeholder: 'e.g. Grocery store' },
      { name: 'cat', label: 'Category (what it was for)', type: 'select', options: categoryOptions(v.cat), required: true, full: true,
        hint: 'Paying a card? Pick the card under Debt payment. Saving? Pick the fund.' },
      { name: 'src', label: 'Paid with', type: 'select', options: sourceOptions(v.src), blank: '—', full: true,
        hint: 'Paid from a savings fund? It comes out of that fund and stays out of your budget totals.' },
      { name: 'unexpected', label: 'Unexpected expense', type: 'check' },
      { name: 'notes', label: 'Notes', full: true },
    ],
    onSubmit: async f => {
      const [ct, ci] = splitRef(f.cat), [st, si] = splitRef(f.src);
      const body = { date: f.date, amount: f.amount, description: f.description, cat_type: ct, cat_id: ci, src_type: st,
        src_id: si, unexpected: f.unexpected, notes: f.notes };
      if (f.src) { UI.lastSrc = f.src; saveUI(); }
      if (rec.id) await send('PUT', `t/transactions/${rec.id}`, body); else await send('POST', 't/transactions', body);
      toast('Saved');
    },
    onDelete: rec.id && (() => deleteSimple('transactions', rec, 'this transaction')),
  });
}

function shiftForm(rec = {}, preset = {}) {
  const v = { date: rec.date || preset.date || todayISO(), type_id: rec.type_id ?? UI.lastShiftType ?? (L.shift_types[0] || {}).id,
    hours: rec.hours ?? '', tips: rec.tips ?? '', dest: joinRef(rec.dest_type, rec.dest_id), notes: rec.notes || '' };
  openForm({
    title: rec.id ? 'Edit shift' : 'Log a shift',
    values: v,
    fields: [
      { name: 'date', label: 'Date', type: 'date', required: true },
      { name: 'type_id', label: 'Shift', type: 'select', options: shiftTypeOptions(), required: true },
      { name: 'hours', label: 'Hours', type: 'number', step: '0.25', min: 0 },
      { name: 'tips', label: 'Cash tips', type: 'money', min: 0, hint: 'Leave blank for a future shift: it counts as Scheduled.' },
      { name: 'dest', label: 'Tips went to', type: 'select', options: destOptions(v.dest), blank: `Default (${defaultTipsName()})`, full: true },
      { name: 'notes', label: 'Notes', full: true },
    ],
    onSubmit: async f => {
      const [dt, di] = splitRef(f.dest);
      const body = { date: f.date, type_id: f.type_id, hours: f.hours, tips: f.tips, dest_type: dt, dest_id: di, notes: f.notes };
      UI.lastShiftType = Number(f.type_id); saveUI();
      if (rec.id) await send('PUT', `t/shifts/${rec.id}`, body); else await send('POST', 't/shifts', body);
      toast('Saved');
    },
    onDelete: rec.id && (() => deleteSimple('shifts', rec, 'this shift')),
  });
}

function incomeForm(rec = {}) {
  const v = { date: rec.date || todayISO(), type_id: rec.type_id ?? (L.income_types[0] || {}).id, amount: rec.amount ?? '',
    description: rec.description || '', dest: joinRef(rec.dest_type, rec.dest_id), notes: rec.notes || '' };
  const hint = L.income_types.map(t => `${t.name}: ${t.counts ? 'counts' : "doesn't count"} toward goal`).join(' · ');
  openForm({
    title: rec.id ? 'Edit income' : 'Add income',
    values: v,
    fields: [
      { name: 'date', label: 'Date', type: 'date', required: true },
      { name: 'amount', label: 'Amount', type: 'money', required: true, min: 0 },
      { name: 'type_id', label: 'Type', type: 'select', options: incomeTypeOptions(), required: true, full: true, hint },
      { name: 'description', label: 'Description', full: true, placeholder: 'e.g. Weekly paycheck' },
      { name: 'dest', label: 'Deposited to', type: 'select', options: destOptions(v.dest), blank: 'Default for this type', full: true },
      { name: 'notes', label: 'Notes', full: true },
    ],
    onSubmit: async f => {
      const [dt, di] = splitRef(f.dest);
      const body = { date: f.date, type_id: f.type_id, amount: f.amount, description: f.description, dest_type: dt, dest_id: di, notes: f.notes };
      if (rec.id) await send('PUT', `t/income/${rec.id}`, body); else await send('POST', 't/income', body);
      toast('Saved');
    },
    onDelete: rec.id && (() => deleteSimple('income', rec, 'this income entry')),
  });
}

function itemForm(rec = {}, kind) {
  openForm({
    title: rec.id ? `Edit ${rec.name}` : `Add ${({ Bill: 'a bill', Subscription: 'a subscription', Spending: 'a spending category' })[kind || rec.kind] || 'an expense'}`,
    values: { grp: 'Needs', kind: kind || 'Bill', start_month: curMonth(), ...rec },
    fields: [
      { name: 'name', label: 'Name', required: true, full: true },
      { name: 'amount', label: 'Monthly amount', type: 'money', min: 0 },
      { name: 'due_day', label: 'Due day (1–31)', type: 'number', min: 1, max: 31, step: '1', hint: 'Blank for things like groceries.' },
      { name: 'grp', label: 'Group', type: 'select', options: ['Needs', 'Wants'], required: true },
      { name: 'kind', label: 'Shows under', type: 'select', options: [['Bill', 'Bills'], ['Subscription', 'Subscriptions'], ['Spending', 'Spending categories']], required: true },
      { name: 'start_month', label: 'Starts', type: 'month', hint: 'First month it counts.' },
      { name: 'end_month', label: 'Ends', type: 'month', hint: 'Last month it counts. Blank = ongoing.' },
      { name: 'notes', label: 'Notes', full: true },
    ],
    onSubmit: async f => {
      if (rec.id) await send('PUT', `t/budget_items/${rec.id}`, f); else await send('POST', 't/budget_items', f);
      toast('Saved');
    },
    onDelete: rec.id && (() => removeRecord('budget_items', rec)),
  });
}

function debtForm(rec = {}) {
  openForm({
    title: rec.id ? `Edit ${rec.name}` : 'Add a debt',
    values: { extra_payoff: rec.id ? !!rec.extra_payoff : true, anchor_date: todayISO(), type: 'Credit Card', ...rec },
    fields: [
      { name: 'name', label: 'Name', required: true },
      { name: 'type', label: 'Type', type: 'select', options: ['Credit Card', 'Loan', 'Buy Now Pay Later', 'Medical', 'Other'], required: true },
      { name: 'apr', label: 'APR %', type: 'number', min: 0, step: '0.001', placeholder: 'e.g. 24.99' },
      { name: 'minimum', label: 'Minimum payment', type: 'money', min: 0 },
      { name: 'due_day', label: 'Due day (1–31)', type: 'number', min: 1, max: 31, step: '1' },
      { name: 'last_payment_month', label: 'Last payment month', type: 'month', hint: 'Optional, for fixed installment plans (buy now, pay later).' },
      { name: 'anchor_balance', label: 'Balance', type: 'money', min: 0 },
      { name: 'anchor_date', label: 'Balance as of', type: 'date', hint: 'Charges & payments after this date adjust it.' },
      { name: 'extra_payoff', label: 'Can receive extra payments (Avalanche / Snowball)', type: 'check' },
      { name: 'notes', label: 'Notes', full: true },
    ],
    onSubmit: async f => {
      if (rec.id) await send('PUT', `t/debts/${rec.id}`, f); else await send('POST', 't/debts', f);
      toast('Saved');
    },
    onDelete: rec.id && (() => removeRecord('debts', rec)),
  });
}

function fundForm(rec = {}) {
  openForm({
    title: rec.id ? `Edit ${rec.name}` : 'Add a savings goal or fund',
    values: { kind: 'Goal', anchor_date: todayISO(), anchor_balance: 0, ...rec },
    fields: [
      { name: 'name', label: 'Name', required: true },
      { name: 'kind', label: 'Kind', type: 'select', required: true, options: [['Goal', 'Goal (one-time target)'], ['Sinking', 'Sinking fund (yearly cost)'], ['Emergency', 'Emergency fund']] },
      { name: 'target', label: 'Target / yearly cost', type: 'money', min: 0 },
      { name: 'monthly_amount', label: 'Your monthly amount (optional)', type: 'money', min: 0, hint: 'Overrides the suggestion.' },
      { name: 'due_month', label: 'Due month (yearly costs)', type: 'select', options: MONTHS.map((m, i) => [i + 1, m]), blank: '— none —' },
      { name: 'target_date', label: 'Target date (goals)', type: 'date' },
      { name: 'held_at', label: 'Kept in account', type: 'select', options: accountOptions(), blank: '— not specified —', hint: 'e.g. a high-yield savings account' },
      { name: 'anchor_balance', label: 'Balance', type: 'money' },
      { name: 'anchor_date', label: 'Balance as of', type: 'date' },
      { name: 'notes', label: 'Notes', full: true },
    ],
    onSubmit: async f => {
      if (rec.id) await send('PUT', `t/funds/${rec.id}`, f); else await send('POST', 't/funds', f);
      toast('Saved');
    },
    onDelete: rec.id && (() => removeRecord('funds', rec)),
  });
}

function accountForm(rec = {}) {
  openForm({
    title: rec.id ? `Edit ${rec.name}` : 'Add an account',
    values: { type: 'Checking', anchor_date: todayISO(), anchor_balance: 0, ...rec },
    fields: [
      { name: 'name', label: 'Name', required: true },
      { name: 'type', label: 'Type', type: 'select', options: ['Checking', 'Savings', 'Cash', 'Investment', 'Other'], required: true },
      { name: 'anchor_balance', label: 'Balance', type: 'money' },
      { name: 'anchor_date', label: 'Balance as of', type: 'date', hint: 'Money in/out logged after this date adjusts it.' },
      { name: 'notes', label: 'Notes', full: true },
    ],
    onSubmit: async f => {
      if (rec.id) await send('PUT', `t/accounts/${rec.id}`, f); else await send('POST', 't/accounts', f);
      toast('Saved');
    },
    onDelete: rec.id && (() => removeRecord('accounts', rec)),
  });
}

function fixBalanceForm(table, rec, current) {
  openForm({
    title: `Fix balance: ${rec.name}`,
    values: { anchor_balance: current !== undefined ? Math.round(current * 100) / 100 : rec.anchor_balance, anchor_date: todayISO() },
    fields: [
      { name: 'anchor_balance', label: table === 'debts' ? 'Amount owed right now' : 'Real balance right now', type: 'money', required: true },
      { name: 'anchor_date', label: 'As of', type: 'date', required: true,
        hint: 'Entries dated after this day keep adjusting it. Entries on or before it are treated as already included.' },
    ],
    submitLabel: 'Update balance',
    onSubmit: async f => { await send('PUT', `t/${table}/${rec.id}`, f); toast('Balance updated'); },
  });
}

async function deleteSimple(table, rec, what) {
  if (!(await confirmBox(`Delete ${what}?`, "This can't be undone."))) return;
  await send('DELETE', `t/${table}/${rec.id}?mode=delete`);
  toast('Deleted');
  await refresh();
}

/* Delete with history awareness: things with logged entries can be ended/archived instead. */
async function removeRecord(table, rec) {
  const { refs } = await send('DELETE', `t/${table}/${rec.id}?mode=check`);
  if (!refs) {
    if (await confirmBox(`Delete ${rec.name}?`, "It isn't used by anything you've logged. This can't be undone.")) {
      await send('DELETE', `t/${table}/${rec.id}?mode=delete`);
      toast('Deleted');
      await refresh();
    }
    return;
  }
  const n = `${refs} logged entr${refs === 1 ? 'y' : 'ies'}`;
  const endSel = h('input', { type: 'month', value: curMonth(), style: 'width:170px' });
  const body = [h('p.muted', `${rec.name} has ${n}. Choose what to do:`)];
  const m = modal(`Remove ${rec.name}`, body, close => [h('span.spacer'), h('button.btn.ghost', { onclick: close }, 'Cancel')]);
  const run = async (mode, extra = '') => {
    m.close();
    await send('DELETE', `t/${table}/${rec.id}?mode=${mode}${extra}`);
    toast('Done');
    await refresh();
  };
  const box = m.el.querySelector('.body');
  if (table === 'budget_items') {
    box.append(h('div.choice', h('b', 'End it (recommended)'),
      h('p', 'Keeps past months accurate. It stops showing after this month:'),
      h('div', { style: 'display:flex;gap:8px;align-items:center' }, endSel,
        h('button.btn.primary', { onclick: () => run('end', '&end=' + endSel.value) }, 'End it'))));
  } else {
    box.append(h('div.choice', h('b', 'Archive it (recommended)'),
      h('p', 'Hides it from lists and dropdowns. Your history stays exactly as it is.'),
      h('button.btn.primary', { onclick: () => run('archive') }, 'Archive')));
  }
  box.append(h('div.choice', h('b', 'Delete it completely'),
    h('p', `Its ${n} stay, but lose their link (they'll show as Uncategorized / default account).`),
    h('button.btn.danger', { onclick: () => run('delete') }, 'Delete completely')));
}

// ------------------------------------------------------------------ router
const NAV = [['home', 'Home', '⌂'], ['month', 'This Month', '▦'], null, ['shifts', 'Shifts', '◷'], ['spending', 'Spending', '↓'],
  ['income', 'Income', '↑'], null, ['budget', 'Budget', '☰'], ['debts', 'Debts', '◔'], ['savings', 'Savings', '◎'],
  ['accounts', 'Accounts', '▤'], null, ['insights', 'Insights', '◢'], ['settings', 'Settings', '⚙']];
const PAGES = {};

function drawNav(active) {
  const nav = document.getElementById('nav');
  nav.replaceChildren(...NAV.map(n => n ? h('a', { href: '#/' + n[0], class: n[0] === active ? 'on' : null },
    h('span.ico', n[2]), n[1]) : h('div.sep')));
}

let renderSeq = 0;
async function render(keepScroll) {
  const [name, ...args] = (location.hash.replace(/^#\/?/, '') || 'home').split('/');
  const page = PAGES[name] ? name : 'home';
  drawNav(page);
  const y = window.scrollY, seq = ++renderSeq;
  if (!keepScroll) $main.replaceChildren(h('div.loading', 'Loading…'));
  try {
    const node = await PAGES[page](...args);
    if (seq !== renderSeq) return;
    $main.replaceChildren(node);
    document.title = (NAV.find(n => n && n[0] === page) || [, 'Finance'])[1] + ' · Finance';
    window.scrollTo(0, keepScroll ? y : 0);
  } catch (e) {
    $main.replaceChildren(h('div.banner.bad', e.message));
  }
}
async function refresh() {
  L = await api('lists');
  await render(true);
}
window.addEventListener('hashchange', () => render(false));

function head(title, sub, ...right) {
  return h('div.page-head', h('h1', title), h('span.spacer'), ...right, sub && h('div.sub', sub));
}
function monthNav(month, go) {
  return h('div.monthnav',
    h('button.btn.ghost', { onclick: () => go(addMonth(month, -1)), title: 'Previous month' }, '‹'),
    h('b', fmonth(month, true)),
    h('button.btn.ghost', { onclick: () => go(addMonth(month, 1)), title: 'Next month' }, '›'),
    month !== curMonth() && h('button.btn.ghost.sm', { onclick: () => go(curMonth()) }, 'Today'));
}
function paceBasis(p) {
  if (p.method !== 'Your scheduled shifts') return p.method;
  const n = `${p.scheduled_left} scheduled shift${p.scheduled_left === 1 ? '' : 's'} left`;
  return p.estimate_per_shift ? `Estimate: ${n} × ${money0(p.estimate_per_shift)} (no tip history yet)` : `Based on ${n} at your averages`;
}
function tile(label, value, foot, extra) {
  return h('div.card.tile', h('div.label', label), h('div.value', value), extra, foot && h('div.foot', foot));
}

// ------------------------------------------------------------------ HOME
PAGES.home = async () => {
  const d = await api('home');
  const p = d.pace, w = d.week, plan = d.plan, t = d.totals;
  const quick = [h('button.btn.primary', { onclick: () => shiftForm() }, '+ Log shift'),
    h('button.btn', { onclick: () => txForm() }, '+ Spending'),
    h('button.btn', { onclick: () => incomeForm() }, '+ Income')];
  return h('div',
    head(fmonth(d.month, true), null, ...quick),
    h('div.banner', { class: p.status.tone }, h('span', p.status.tone === 'good' ? '▲' : p.status.tone === 'bad' ? '▼' : '●'), h('span', p.status.text),
      h('span.spacer'), h('span.small', paceBasis(p))),
    h('div.grid.g4',
      tile('Earned this month', money0(p.earned), `${pct(p.pct_goal)} of ${money0(p.goal)} · ${pct(p.pct_month)} of month gone`, bar(p.pct_goal, p.pct_goal >= p.pct_month ? 'good' : '')),
      tile('Projected finish', money0(p.projected), p.projected >= p.goal ? `${money0(p.projected - p.goal)} over goal` : `${money0(p.goal - p.projected)} under goal`),
      tile('Need per remaining shift', p.need_per_shift === null ? 'Add shifts' : money0(p.need_per_shift),
        `${p.scheduled_left} scheduled · your avg ${p.avg_per_shift ? money0(p.avg_per_shift) : 'n/a yet'}`),
      w ? tile('This week', `${money0(w.earned)} / ${money0(w.target)}`,
        w.on_track ? 'On track this week' : `${money0(w.left)} to go · ${w.need_per_shift === null ? 'no shifts left' : money0(w.need_per_shift) + ' per shift'}`,
        bar(w.target ? w.earned / w.target : 0, w.on_track ? 'good' : ''))
        : tile('This week', '–', 'Starts when the month starts')),
    h('div.grid.g2.mt',
      h('div.card', h('h2', 'Bills due in the next 14 days'),
        d.bills.length ? h('div.list', d.bills.map(b => h('div.li',
          h('span.muted', { style: 'width:62px' }, fdate(b.due)), h('span.name', b.name), h('span.num', money(b.amount)),
          badge(b.status, b.tone),
          b.status !== '✓ Paid' && h('button.btn.sm', { onclick: () => txForm({}, { cat: `${b.ref_type}:${b.ref_id}`, amount: b.amount, description: b.name, title: `Pay ${b.name}` }) }, 'Pay'))))
          : h('div.empty', 'Nothing due in the next two weeks.')),
      h('div.card', h('h2', 'The plan this month', h('span.spacer'), h('a', { href: '#/month' }, 'Open month →')),
        h('div.kv',
          h('span.k', 'Budgeted at your goal'), h('span.v', money(plan.goal)),
          h('span.k', 'Planned (needs, minimums, wants, savings)'), h('span.v', money(plan.planned)),
          plan.over > 0 ? [h('span.k', 'Over your goal'), h('span.v', { style: 'color:var(--bad)' }, money(plan.over))]
            : [h('span.k', `Left for extra debt (${plan.strategy})`), h('span.v', { style: 'color:var(--good)' }, money(plan.pool))],
          h('span.k', 'Spent / paid so far'), h('span.v', money(plan.spent)),
          h('span.k', `Unexpected (${plan.unexpected_count})`), h('span.v', money(plan.unexpected)),
          h('span.k', 'Lines not covered at projection'), h('span.v', plan.at_risk_projected)),
        plan.over > 0 && h('div.note', '⚠ The plan is more than your goal. Lower something on the Budget or Savings page, or plan on a stronger month.'))),
    h('div.grid.g3.mt',
      h('div.card', h('h2', 'Debts', h('span.spacer'), h('a', { href: '#/debts' }, 'All →')),
        h('div.list', d.debts.map(x => h('div.li', h('span.name', x.name), h('span.muted', `${x.apr}%`), h('span.num', money0(x.balance))))),
        h('div.note', `Total ${money0(t.debts)} · debt-free ${d.debt_free.label} (${d.strategy}, minimums only)`)),
      h('div.card', h('h2', 'Savings', h('span.spacer'), h('a', { href: '#/savings' }, 'All →')),
        h('div.list', d.funds.map(f => h('div.li', { style: 'flex-wrap:wrap' }, h('span.name', f.name), h('span.num', money0(f.balance)),
          f.target ? h('div', { style: 'width:100%' }, bar(f.progress, 'thin good')) : null)))),
      h('div.card', h('h2', 'Net worth', h('span.spacer'), h('a', { href: '#/accounts' }, 'Accounts →')),
        h('div.kv',
          h('span.k', 'Checking, cash & accounts'), h('span.v', money(t.accounts)),
          h('span.k', 'Savings funds'), h('span.v', money(t.funds)),
          h('span.k', 'Debts'), h('span.v', money(-t.debts)),
          h('span.k.total', 'Net worth'), h('span.v.total', { style: `color:var(--${t.net_worth < 0 ? 'bad' : 'good'})` }, money(t.net_worth))),
        h('div.note', `Emergency fund covers ${t.emergency_months.toFixed(1)} months of needs + minimums.`))));
};

// ------------------------------------------------------------------ MONTH
PAGES.month = async (m) => {
  const month = m || curMonth();
  const d = await api('month?m=' + month);
  const p = d.pace, w = d.week, plan = d.plan;
  const go = x => { location.hash = '#/month/' + x; };
  const kvCard = (title, rows) => h('div.card', h('h2', title), h('div.kv', rows.map(([k, v, style]) => [h('span.k', k), h('span.v', { style }, v)])));

  const covCell = (v, b) => {
    if (!b) return h('td.num.muted', '–');
    const c = v >= b - 0.005 ? 'good' : v > 0 ? 'warn' : 'bad';
    return h('td.num', h('span.cov', { class: c }, money(v)));
  };
  const lineRow = ln => {
    const input = h('input.cell', { type: 'number', step: '0.01', placeholder: ' ', value: ln.override ?? '',
      title: 'This month only. Leave blank to use the default.',
      onchange: async e => {
        try {
          await send('PUT', 'override', { month: d.month, ref_type: ln.ov_type, ref_id: ln.ref_id, amount: e.target.value === '' ? null : Number(e.target.value) });
          await render(true);
        } catch (err) { toast(err.message, true); }
      } });
    const payPreset = { amount: Math.max(0, ln.left), description: ln.name,
      cat: `${ln.ref_type}:${ln.ref_id}`,
      title: ln.section === 'savings' ? `Add to ${ln.name}` : `Pay ${ln.name}`,
      date: d.is_current ? undefined : (todayISO() < d.month_start ? d.month_start : d.month_end) };
    return h('tr',
      h('td', ln.name), h('td.muted.nowrap', ln.due ? (ln.section === 'savings' ? fmonth(ln.due) : fdate(ln.due)) : ''),
      h('td.num.muted', money(ln.default)), h('td.num', input), h('td.num', h('b', money(ln.budget))),
      h('td.num', money(ln.actual)), h('td.num', { style: ln.left < -0.005 ? 'color:var(--bad)' : null }, money(ln.left)),
      h('td', badge(ln.status)), covCell(ln.covered_now, ln.budget), covCell(ln.covered_proj, ln.budget),
      h('td.row-actions', h('button.btn.ghost.sm', { title: 'Log a payment for this line', onclick: () => txForm({}, payPreset) }, '+ Pay')));
  };
  const tbody = h('tbody');
  for (const s of d.sections) {
    tbody.append(h('tr.sec-row', { class: s.key }, h('td', { colspan: 11 }, `${d.sections.indexOf(s) + 1} · ${s.label}`,
      s.key === 'extra' ? ` (${plan.strategy}, from what's left over)` : '')));
    if (!s.lines.length) tbody.append(h('tr', h('td.muted', { colspan: 11 }, s.key === 'extra' ? 'Nothing left over for extra payments this month.' : 'Nothing here this month.')));
    s.lines.forEach(ln => tbody.append(lineRow(ln)));
    const t = s.total;
    tbody.append(h('tr.sub-row', h('td', 'Total ' + s.label.toLowerCase()), h('td'), h('td.num', money(t.default)), h('td'),
      h('td.num', money(t.budget)), h('td.num', money(t.actual)), h('td.num', money(t.left)), h('td'),
      h('td.num', money(t.covered_now)), h('td.num', money(t.covered_proj)), h('td')));
  }
  const T = d.total;
  tbody.append(h('tr.grand-row', h('td', 'Grand total'), h('td'), h('td.num', money(T.default)), h('td'), h('td.num', money(T.budget)),
    h('td.num', money(T.actual)), h('td.num', money(T.left)), h('td'), h('td.num', money(T.covered_now)), h('td.num', money(T.covered_proj)), h('td')));

  return h('div',
    head(fmonth(d.month, true) + ' budget', null, monthNav(d.month, go)),
    h('div.banner', { class: p.status.tone }, p.status.text, h('span.spacer'),
      h('span.small', paceBasis(p), w ? (w.on_track ? ' · This week is on track' : ` · ${money0(w.left)} to go this week`) : '')),
    h('div.grid.g3',
      kvCard('This month', [['Monthly goal', money(p.goal)], ['Earned so far', money(p.earned)], ['Still needed', money(p.still_needed)],
        ['% of goal earned', pct(p.pct_goal)], ['% of month gone', pct(p.pct_month)], ['Projected finish', money(p.projected)]]),
      kvCard('Shifts', [['Shifts worked', p.shifts_worked], ['Avg tips per shift', money(p.avg_per_shift)], ['Avg per hour', money(p.avg_per_hour)],
        ['Scheduled shifts left', p.scheduled_left], ['Need per remaining shift', p.need_per_shift === null ? 'Add shifts' : money(p.need_per_shift)],
        ['Projection based on', p.method]]),
      kvCard('This week (Mon–Sun)', w ? [['Week of', fdate(w.start)], ['Weekly target (full week)', money(w.full_target)],
        ["This week's share", money(w.target)], ['Earned this week', money(w.earned)], ['Shifts left this week', w.shifts_left],
        ['Need per shift', w.need_per_shift === null ? 'Add shifts' : money(w.need_per_shift)]]
        : [['', d.is_current ? '' : 'Not this month']])),
    h('div.card.mt', h('h2', 'Week by week'), h('div.table-wrap', h('table',
      h('thead', h('tr', ['Week', 'Days', 'Target', 'Earned', 'Shifts', '+ / –', 'Status'].map((x, i) => h('th', { class: i > 0 && i < 6 ? 'num' : null }, x)))),
      h('tbody', d.weeks.map(x => h('tr', h('td', `${fdate(x.start)} – ${fdate(x.end)}`), h('td.num', x.days), h('td.num', money(x.target)),
        h('td.num', h('b', money(x.earned))), h('td.num', x.worked),
        h('td.num', { style: x.diff === null ? null : `color:var(--${x.diff >= 0 ? 'good' : 'bad'})` }, x.diff === null ? '' : money(x.diff)),
        h('td', badge(x.status)))))))),
    h('div.grid.g2.mt',
      kvCard('The plan: budgeted at your goal', [['Goal income (what you plan with)', money(plan.goal)],
        ['Planned before extra debt (1–4)', money(plan.planned)],
        ['Over plan', money(plan.over), plan.over > 0 ? 'color:var(--bad)' : null],
        ['Left over → extra debt payoff (5)', money(plan.pool), plan.pool > 0 ? 'color:var(--good)' : null]]),
      kvCard('Actual so far', [['Earned so far', money(p.earned)], ['Spent / paid (all sections)', money(plan.spent)],
        [`Unexpected spending (${plan.unexpected_count})`, money(plan.unexpected)], ['Paid from savings (not in budget)', money(plan.from_savings)],
        ['Projected finish − full plan', money(plan.projected_minus_plan), `color:var(--${plan.projected_minus_plan >= 0 ? 'good' : 'bad'})`],
        ...(plan.uncategorized ? [['Uncategorized spending', money(plan.uncategorized), 'color:var(--warn)']] : [])])),
    h('div.banner.mt', { class: plan.over > 0.005 ? 'bad' : 'good' }, plan.over > 0.005
      ? `⚠ Your plan is ${money(plan.over)} over your goal. Lower something (or plan to earn more).`
      : `✓ Plan fits your goal. ${money(plan.pool)} goes to extra debt payoff (${plan.strategy}).`),
    h('div.card', h('h2', 'Budget', h('span.spacer'), h('span.muted', { style: 'font-weight:400;text-transform:none;letter-spacing:0' },
      'Type in “This month” only if the amount differs from the default. Covered = what your earnings pay for, in priority order.')),
      h('div.table-wrap', h('table',
        h('thead', h('tr', ['Item', 'Due', 'Default', 'This month ✎', 'Budget', 'Actual', 'Left', 'Status', 'Covered so far', 'Covered at projection', '']
          .map((x, i) => h('th', { class: [2, 3, 4, 5, 6, 8, 9].includes(i) ? 'num' : null }, x)))),
        tbody))));
};

// ------------------------------------------------------------------ SHIFTS
PAGES.shifts = async () => {
  const month = UI.shiftsMonth || curMonth();
  const rows = await api('t/shifts?month=' + month);
  const go = x => { UI.shiftsMonth = x; saveUI(); render(false); };
  const worked = rows.filter(r => r.status === 'Worked');
  const tips = worked.reduce((a, r) => a + r.tips, 0);
  const tbody = h('tbody', rows.map(r => {
    const open = r.status !== 'Worked' && r.date <= todayISO();   // quick tip entry for today and past shifts
    let hoursIn, tipsIn;
    const quickSave = async () => {
      if (tipsIn.value === '') { tipsIn.focus(); return; }
      try { await send('PUT', `t/shifts/${r.id}`, { hours: hoursIn.value, tips: tipsIn.value }); toast('Tips logged'); await refresh(); }
      catch (e) { toast(e.message, true); }
    };
    if (open) {
      hoursIn = h('input.cell', { type: 'number', step: '0.25', placeholder: 'hrs', value: r.hours ?? '' });
      tipsIn = h('input.cell.wide', { type: 'number', step: '0.01', placeholder: 'tips', onkeydown: e => { if (e.key === 'Enter') quickSave(); } });
    }
    return h('tr', h('td', fdateW(r.date)), h('td', r.type || '–'),
      h('td.num', open ? hoursIn : (r.hours ?? '–')), h('td.num', open ? tipsIn : (r.tips === null ? h('span.muted', '–') : h('b', money(r.tips)))),
      h('td.num.muted', r.per_hour ? money(r.per_hour) : ''), h('td', badge(r.status)), h('td.muted', r.dest_name || ''), h('td.muted', r.notes || ''),
      h('td.row-actions', open && h('button.btn.sm.primary', { onclick: quickSave }, 'Save tips'),
        h('button.btn.ghost.sm', { onclick: () => shiftForm(r) }, 'Edit')));
  }));
  return h('div',
    head('Shifts', 'After each shift, type your tips on its row. Future shifts with no tips count as Scheduled and drive your pace.',
      monthNav(month, go), h('button.btn', { onclick: () => scheduleForm(month) }, 'Plan schedule…'), h('button.btn.primary', { onclick: () => shiftForm() }, '+ Log shift')),
    h('div.grid.g4',
      tile('Tips this month', money0(tips), `${worked.length} shift${worked.length === 1 ? '' : 's'} worked`),
      tile('Avg per shift', worked.length ? money0(tips / worked.length) : '–'),
      tile('Scheduled', rows.filter(r => r.status === 'Scheduled').length, 'still to work'),
      tile('Needs tips', rows.filter(r => r.status === 'Needs tips').length, 'past shifts with no tips yet')),
    h('div.card.mt', rows.length ? h('div.table-wrap', h('table',
      h('thead', h('tr', ['Date', 'Shift', 'Hours', 'Tips', '$ / hr', 'Status', 'Went to', 'Notes', ''].map((x, i) => h('th', { class: [2, 3, 4].includes(i) ? 'num' : null }, x)))),
      tbody)) : h('div.empty', 'No shifts this month yet. Use “Plan schedule…” to add your schedule in one go.')));
};

function scheduleForm(month) {
  const start = h('input', { type: 'date', value: month + '-01' });
  const end = h('input', { type: 'date', value: (() => { const d = pd(month); d.setMonth(d.getMonth() + 1, 0); return d.toISOString().slice(0, 10); })() });
  const prev = UI.pattern || {};
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((n, i) => {
    const sel = h('select', null, optionNodes(shiftTypeOptions(), prev[i], 'Off'));
    return { n, i, sel };
  });
  const err = h('div.err');
  const m = modal('Plan your schedule', [
    h('p.muted', 'Adds a scheduled shift on each chosen weekday. Shifts already on the list are skipped.'),
    h('div.form', h('label.f', 'From', start), h('label.f', 'To', end)),
    h('div.weekpick.mt', days.map(x => h('div', x.n, x.sel))), err,
  ], close => [h('button.btn.primary', {
    onclick: async () => {
      const pattern = {};
      days.forEach(x => { if (x.sel.value) pattern[x.i] = Number(x.sel.value); });
      if (!Object.keys(pattern).length) { err.textContent = 'Pick a shift for at least one day.'; return; }
      try {
        const r = await send('POST', 'shifts-bulk', { start: start.value, end: end.value, pattern });
        UI.pattern = pattern; saveUI();
        close(); toast(`Added ${r.added} shift${r.added === 1 ? '' : 's'}`); await refresh();
      } catch (e) { err.textContent = e.message; }
    },
  }, 'Add shifts'), h('span.spacer'), h('button.btn.ghost', { onclick: close }, 'Cancel')]);
  void m;
}

// ------------------------------------------------------------------ SPENDING
PAGES.spending = async () => {
  const month = UI.txMonth || curMonth();
  const rows = await api('t/transactions?month=' + month);
  const go = x => { UI.txMonth = x; saveUI(); render(false); };
  const search = h('input', { type: 'text', placeholder: 'Search…', value: UI.txSearch || '', style: 'max-width:220px' });
  const tbody = h('tbody');
  const draw = () => {
    const q = search.value.toLowerCase();
    UI.txSearch = search.value; saveUI();
    const shown = rows.filter(r => !q || [r.description, r.cat_name, r.src_name, r.notes].some(x => (x || '').toLowerCase().includes(q)));
    tbody.replaceChildren(...shown.map(r => h('tr', { style: 'cursor:pointer', onclick: () => txForm(r) },
      h('td', fdateW(r.date)), h('td', r.description || h('span.muted', '—')),
      h('td', r.cat_name || h('span.badge.warn', 'Uncategorized'), ' ', h('span.muted', { style: 'font-size:12px' }, r.group !== 'Uncategorized' ? '· ' + r.group : '')),
      h('td.num', h('b', money(r.amount))), h('td.muted', r.src_name || '—'), h('td', r.unexpected ? badge('Unexpected', 'warn') : ''))));
    if (!shown.length) tbody.replaceChildren(h('tr', h('td.empty', { colspan: 6 }, rows.length ? 'No matches.' : 'Nothing logged this month yet.')));
  };
  search.addEventListener('input', draw);
  draw();
  const sum = g => rows.filter(r => r.group === g && r.src_type !== 'fund').reduce((a, r) => a + r.amount, 0);
  return h('div',
    head('Spending', 'Every purchase, bill and payment. Category = what it was for. Paid with = where the money came from.',
      monthNav(month, go), h('button.btn.primary', { onclick: () => txForm() }, '+ Add')),
    h('div.grid.g4', tile('Needs', money0(sum('Needs'))), tile('Wants', money0(sum('Wants'))),
      tile('Debt payments', money0(sum('Debt payment'))), tile('Into savings', money0(sum('Savings')))),
    h('div.card.mt', h('h2', `${rows.length} entr${rows.length === 1 ? 'y' : 'ies'}`, h('span.spacer'), search),
      h('div.table-wrap', h('table', h('thead', h('tr', ['Date', 'Description', 'Category', 'Amount', 'Paid with', ''].map((x, i) => h('th', { class: i === 3 ? 'num' : null }, x)))), tbody))));
};

// ------------------------------------------------------------------ INCOME
PAGES.income = async () => {
  const month = UI.incMonth || curMonth();
  const rows = await api('t/income?month=' + month);
  const go = x => { UI.incMonth = x; saveUI(); render(false); };
  const counted = rows.filter(r => (byId(L.income_types, r.type_id) || { counts: 1 }).counts);
  return h('div',
    head('Other income', 'Money that isn’t shift tips: side gigs, gifts, refunds, or your hourly paycheck.',
      monthNav(month, go), h('button.btn.primary', { onclick: () => incomeForm() }, '+ Add income')),
    h('div.grid.g3',
      tile('Counts toward goal', money0(counted.reduce((a, r) => a + r.amount, 0))),
      tile('Doesn’t count (e.g. hourly wage)', money0(rows.filter(r => !counted.includes(r)).reduce((a, r) => a + r.amount, 0))),
      tile('Entries', rows.length)),
    h('div.card.mt', rows.length ? h('div.table-wrap', h('table',
      h('thead', h('tr', ['Date', 'Description', 'Type', 'Amount', 'Deposited to', 'Goal?'].map((x, i) => h('th', { class: i === 3 ? 'num' : null }, x)))),
      h('tbody', rows.map(r => h('tr', { style: 'cursor:pointer', onclick: () => incomeForm(r) }, h('td', fdateW(r.date)), h('td', r.description || '—'),
        h('td', r.type || '—'), h('td.num', h('b', money(r.amount))), h('td.muted', r.dest_name || '—'),
        h('td', counted.includes(r) ? badge('Counts', 'good') : badge('No', '')))))))
      : h('div.empty', 'No other income this month.')));
};

// ------------------------------------------------------------------ BUDGET
PAGES.budget = async () => {
  const cm = curMonth();
  const mv = await api('month?m=' + cm);   // same numbers as the This Month page
  const secTotal = k => mv.sections.find(s => s.key === k).total.budget;
  const showEnded = !!UI.showEnded;
  const status = i => {
    if (i.end_month && i.end_month < cm) return ['Ended ' + fmonth(i.end_month), ''];
    if (i.start_month && i.start_month > cm) return ['Starts ' + fmonth(i.start_month), 'info'];
    return [i.end_month ? 'Until ' + fmonth(i.end_month) : 'Active', 'good'];
  };
  const activeNow = i => (!i.start_month || i.start_month <= cm) && (!i.end_month || i.end_month >= cm);
  const plan = mv.plan;

  const group = (kind, title, hint) => {
    const items = L.budget_items.filter(i => i.kind === kind && (showEnded || !(i.end_month && i.end_month < cm)));
    const sum = items.filter(activeNow).reduce((a, i) => a + (i.amount || 0), 0);
    return h('div.card.mt', h('h2', title, h('span.muted', { style: 'text-transform:none;letter-spacing:0;font-weight:400' }, `${money(sum)}/mo`),
      h('span.spacer'), h('button.btn.sm.primary', { onclick: () => itemForm({}, kind) }, '+ Add')),
      items.length ? h('div.table-wrap', h('table',
        h('thead', h('tr', ['Name', 'Group', 'Monthly', 'Due', 'Status', 'Notes', ''].map((x, i) => h('th', { class: i === 2 ? 'num' : null }, x)))),
        h('tbody', items.map(i => {
          const [st, t] = status(i);
          return h('tr', { class: st.startsWith('Ended') ? 'archived' : null },
            h('td', h('b', i.name)), h('td', h('span.dot', { style: `background:var(--${i.grp === 'Needs' ? 'needs' : 'wants'})` }), i.grp),
            h('td.num', money(i.amount)), h('td.muted', i.due_day ? ordinal(i.due_day) : '—'), h('td', badge(st, t)),
            h('td.muted', { style: 'max-width:320px' }, i.notes || ''),
            h('td.row-actions', h('button.btn.ghost.sm', { onclick: () => itemForm(i) }, 'Edit'),
              h('button.btn.ghost.sm.danger', { onclick: () => removeRecord('budget_items', i) }, 'Remove')));
        })))) : h('div.empty', hint));
  };
  return h('div',
    head('Budget', `Your bills, subscriptions and spending categories. Changes apply from the month you set in “Starts”, so past months stay accurate.`,
      h('label.check', h('input', { type: 'checkbox', checked: showEnded, onchange: e => { UI.showEnded = e.target.checked; saveUI(); render(true); } }), 'Show ended')),
    h('div.grid.g4',
      tile('Needs', money0(secTotal('needs')), fmonth(cm, true)), tile('Wants', money0(secTotal('wants')), fmonth(cm, true)),
      tile('Debt minimums', money0(secTotal('debt')), h('a', { href: '#/debts' }, 'Debts →')),
      tile('Savings', money0(secTotal('savings')), h('a', { href: '#/savings' }, 'Savings →'))),
    h('div.banner.mt', { class: plan.over > 0.005 ? 'bad' : 'good' },
      plan.over > 0.005 ? `Planned ${money(plan.planned)}: ${money(plan.over)} over your ${money0(plan.goal)} goal.`
        : `Planned ${money(plan.planned)}: ${money(plan.pool)} left for extra debt payoff.`,
      h('span.spacer'), h('a.small', { href: '#/month/' + cm }, `${fmonth(cm, true)} in detail →`)),
    group('Bill', 'Bills', 'No bills yet.'),
    group('Subscription', 'Subscriptions', 'No subscriptions yet.'),
    group('Spending', 'Spending categories', 'No spending categories yet.'));
};
const ordinal = n => n + (['th', 'st', 'nd', 'rd'][(n % 100 > 10 && n % 100 < 14) ? 0 : (n % 10 < 4 ? n % 10 : 0)] || 'th');

// ------------------------------------------------------------------ DEBTS
PAGES.debts = async () => {
  const extra = UI.extra || 0;
  const ov = await api('overview?extra=' + extra);
  const debts = ov.debts.filter(live), arch = ov.debts.filter(x => x.archived);
  const strat = L.settings.strategy;
  const payoff = ov.payoff;
  const extraIn = h('input', { type: 'number', step: '25', min: 0, value: extra || '', placeholder: '0', style: 'width:120px' });
  let timer;
  extraIn.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { UI.extra = Number(extraIn.value) || 0; saveUI(); render(true); }, 450); });
  const setStrat = async s => { await send('PUT', 'settings', { strategy: s }); await refresh(); };
  const row = x => h('tr', { class: x.archived ? 'archived' : null },
    h('td', h('b', x.name), h('div.muted', { style: 'font-size:12px' }, x.type || '')),
    h('td.num', `${x.apr ?? 0}%`), h('td.num', money(x.minimum)), h('td.muted', x.due_day ? ordinal(x.due_day) : '—'),
    h('td.num', h('b', money(x.balance))), h('td.num.muted', money(x.interest_month)),
    h('td', x.extra_payoff ? (strat === 'Snowball' ? x.order_snowball : x.order_avalanche) ?? '—' : h('span.muted', 'no')),
    h('td', x.payoff_avalanche ? x.payoff_avalanche.label : ''), h('td', x.payoff_snowball ? x.payoff_snowball.label : ''),
    h('td.row-actions', h('button.btn.ghost.sm', { onclick: () => fixBalanceForm('debts', x, x.balance) }, 'Fix balance'),
      h('button.btn.ghost.sm', { onclick: () => debtForm(x) }, 'Edit')));
  return h('div',
    head('Debts', 'Balances update from what you log: charges on a card add to it, payments (Category = the card) take it down.',
      h('button.btn.primary', { onclick: () => debtForm() }, '+ Add debt')),
    h('div.grid.g4',
      tile('Total debt', money0(ov.totals.debts)), tile('Minimums', money0(ov.totals.minimums), 'per month'),
      tile('Interest (est.)', money0(ov.totals.interest_month), 'per month'),
      tile('Debt-free', payoff[strat].debt_free.label, `${strat}${extra ? ` + ${money0(extra)}/mo extra` : ', minimums only'}`)),
    h('div.card.mt', h('div.table-wrap', h('table',
      h('thead', h('tr', ['Debt', 'APR', 'Minimum', 'Due', 'Balance', 'Interest/mo', `${strat} order`, 'Avalanche payoff', 'Snowball payoff', '']
        .map((x, i) => h('th', { class: [1, 2, 4, 5].includes(i) ? 'num' : null }, x)))),
      h('tbody', debts.map(row)))),
      arch.length ? h('details', h('summary', `Archived (${arch.length})`), h('div.table-wrap', h('table', h('tbody', arch.map(x => h('tr.archived',
        h('td', x.name), h('td.num', money(x.balance)),
        h('td.row-actions', h('button.btn.ghost.sm', { onclick: async () => { await send('PUT', `t/debts/${x.id}`, { archived: 0 }); await refresh(); } }, 'Restore')))))))) : null),
    h('div.card.mt', h('h2', 'Payoff plan'),
      h('div', { style: 'display:flex;gap:16px;flex-wrap:wrap;align-items:center;margin-bottom:12px' },
        h('label.check', 'Extra toward debt each month', extraIn),
        h('span.muted', `Monthly debt budget: ${money(payoff[strat].budget)}`), h('span.spacer'),
        h('div.seg', ['Avalanche', 'Snowball'].map(s => h('button', { class: s === strat ? 'on' : null, onclick: () => setStrat(s) }, s)))),
      h('div.table-wrap', h('table', h('thead', h('tr', h('th', 'Strategy'), h('th', 'Debt-free by'), h('th.num', 'Months'), h('th.num', 'Total interest'))),
        h('tbody', ['Avalanche', 'Snowball'].map(s => h('tr', h('td', h('b', s), s === strat ? ' ' : '', s === strat ? badge('Your strategy', 'info') : ''),
          h('td', payoff[s].debt_free.label), h('td.num', payoff[s].debt_free.months), h('td.num', money(payoff[s].interest))))))),
      h('div.note', h('b', payoff.message), ' Avalanche = highest interest first (saves the most). Snowball = smallest balance first (fastest wins). Paid-off minimums roll into the next debt.')));
};

// ------------------------------------------------------------------ SAVINGS
PAGES.savings = async () => {
  const ov = await api('overview');
  const funds = ov.funds.filter(live), arch = ov.funds.filter(f => f.archived);
  const card = f => h('div.card.fund-card',
    h('div.top', h('b', { style: 'font-size:15px' }, f.name), h('span.spacer'), badge(f.kind, f.kind === 'Emergency' ? 'bad' : f.kind === 'Sinking' ? 'info' : 'good')),
    h('div', { style: 'margin-top:8px' }, h('span.amt', money0(f.balance)), f.target ? h('span.muted', ` of ${money0(f.target)}${f.kind === 'Sinking' ? ' / yr' : ''}`) : null),
    f.target ? h('div', { style: 'margin-top:8px' }, bar(f.progress, 'good')) : null,
    h('div.meta',
      f.target ? h('span', `Still needed ${money0(f.still_needed)}`) : null,
      h('span', h('b', money(f.suggested)), '/mo ', f.monthly_amount !== null && f.monthly_amount !== undefined ? '(your amount)' : 'suggested'),
      f.target_date ? h('span', 'By ' + fdateY(f.target_date)) : null,
      f.due_month ? h('span', 'Due every ' + MONTHS[f.due_month - 1]) : null,
      f.held_at_name ? h('span', 'In ' + f.held_at_name) : null),
    f.notes ? h('div.note', f.notes) : null,
    h('div.acts',
      h('button.btn.sm.primary', { onclick: () => txForm({}, { cat: 'fund:' + f.id, description: 'Savings: ' + f.name, title: `Add money to ${f.name}` }) }, '+ Add money'),
      h('button.btn.sm', { onclick: () => fixBalanceForm('funds', f, f.balance) }, 'Fix balance'),
      h('button.btn.sm', { onclick: () => fundForm(f) }, 'Edit')));
  return h('div',
    head('Savings', 'Add money: “+ Add money” (or log a transaction with Category = the fund). Spend from a fund: log it with Paid with = the fund.',
      h('button.btn.primary', { onclick: () => fundForm() }, '+ Add goal / fund')),
    h('div.grid.g3', tile('Total saved', money0(ov.totals.funds)), tile('Suggested per month', money0(ov.totals.suggested_savings), 'all funds'),
      tile('Emergency fund covers', ov.totals.emergency_months.toFixed(1) + ' months', 'of needs + debt minimums')),
    h('div.grid.g3.mt', funds.map(card)),
    arch.length ? h('details', h('summary', `Archived (${arch.length})`), h('div.list', arch.map(f => h('div.li.archived', h('span.name', f.name),
      h('span.num', money(f.balance)), h('button.btn.ghost.sm', { onclick: async () => { await send('PUT', `t/funds/${f.id}`, { archived: 0 }); await refresh(); } }, 'Restore'))))) : null);
};

// ------------------------------------------------------------------ ACCOUNTS
PAGES.accounts = async () => {
  const ov = await api('overview');
  const accts = ov.accounts.filter(live), arch = ov.accounts.filter(a => a.archived), t = ov.totals;
  const tipsId = L.settings.default_tips_account;
  return h('div',
    head('Accounts', 'Checking, cash and other accounts. Fix a balance any time. Everything logged after that date keeps it current.',
      h('button.btn.primary', { onclick: () => accountForm() }, '+ Add account')),
    h('div.card', h('div.table-wrap', h('table',
      h('thead', h('tr', ['Account', 'Type', 'Balance', 'Holds', 'Balance as of', 'Notes', ''].map((x, i) => h('th', { class: i === 2 ? 'num' : null }, x)))),
      h('tbody', accts.map(a => h('tr',
        h('td', h('b', a.name), a.id === tipsId ? h('div.muted', { style: 'font-size:12px' }, 'Tips go here by default') : null),
        h('td.muted', a.type || ''), h('td.num', h('b', money(a.balance))),
        h('td', a.funds.length ? a.funds.map(f => h('div', f.name, ' ', h('span.muted', money(f.balance)))) : h('span.muted', '—')),
        h('td.muted', a.anchor_date ? fdateY(a.anchor_date) : '—'), h('td.muted', a.notes || ''),
        h('td.row-actions', a.funds.length === 1
          ? h('button.btn.ghost.sm', { title: `Fixes ${a.funds[0].name}`, onclick: () => fixBalanceForm('funds', a.funds[0], a.funds[0].balance) }, 'Fix balance')
          : a.funds.length ? h('a.btn.ghost.sm', { href: '#/savings' }, 'Fix on Savings')
            : h('button.btn.ghost.sm', { onclick: () => fixBalanceForm('accounts', a, a.own_balance) }, 'Fix balance'),
          h('button.btn.ghost.sm', { onclick: () => accountForm(a) }, 'Edit'))))))),
      h('div.note', 'Accounts that hold savings funds (like a high-yield savings account) show the funds’ balances. Fix those on the Savings page. To link a fund to an account, edit the fund and pick “Kept in account”.'),
      arch.length ? h('details', h('summary', `Archived (${arch.length})`), h('div.list', arch.map(a => h('div.li.archived', h('span.name', a.name),
        h('span.num', money(a.balance)), h('button.btn.ghost.sm', { onclick: async () => { await send('PUT', `t/accounts/${a.id}`, { archived: 0 }); await refresh(); } }, 'Restore'))))) : null),
    h('div.card.mt', { style: 'max-width:520px' }, h('h2', 'Net worth'), h('div.kv',
      h('span.k', 'Checking, cash & other accounts'), h('span.v', money(t.accounts)),
      h('span.k', 'Savings funds'), h('span.v', money(t.funds)),
      h('span.k', 'Debts'), h('span.v', money(-t.debts)),
      h('span.k.total', 'Net worth'), h('span.v.total', { style: `color:var(--${t.net_worth < 0 ? 'bad' : 'good'})` }, money(t.net_worth)))));
};

// ------------------------------------------------------------------ INSIGHTS
function barChart(items, { target, tolerance = 0, height = 180, labelEvery = 1 } = {}) {
  const W = 640, H = height, pad = { l: 46, r: 8, t: 10, b: 24 };
  const max = Math.max(1, ...items.map(i => Math.max(i.value, i.target ?? target ?? 0))) * 1.08;
  const bw = Math.min(90, (W - pad.l - pad.r) / Math.max(1, items.length));
  const y = v => pad.t + (H - pad.t - pad.b) * (1 - v / max);
  const ns = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs, text) => { const e = document.createElementNS(ns, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text !== undefined) e.textContent = text; return e; };
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  for (let k = 0; k <= 3; k++) {
    const v = max / 1.08 * k / 3;
    svg.append(el('line', { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), stroke: 'var(--line)', 'stroke-width': 1 }));
    svg.append(el('text', { x: pad.l - 6, y: y(v) + 4, 'text-anchor': 'end' }, '$' + Math.round(v).toLocaleString()));
  }
  items.forEach((it, i) => {
    const x = pad.l + i * bw + bw * 0.18, w = bw * 0.64, tg = it.target ?? target;
    const hit = tg !== undefined && it.value >= tg - tolerance;
    const r = el('rect', { x, y: y(it.value), width: w, height: Math.max(0, y(0) - y(it.value)), rx: 3, fill: hit ? 'var(--good)' : 'var(--teal)' });
    r.append(el('title', {}, `${it.label}: $${Math.round(it.value).toLocaleString()}`));
    svg.append(r);
    if (tg !== undefined) svg.append(el('line', { x1: x - 3, x2: x + w + 3, y1: y(tg), y2: y(tg), stroke: 'var(--bad)', 'stroke-width': 2, 'stroke-dasharray': '4 3' }));
    if (i % labelEvery === 0) svg.append(el('text', { x: x + w / 2, y: H - 7, 'text-anchor': 'middle' }, it.label));
  });
  return h('div.chart', svg, h('div.legend', h('span', h('i', { style: 'background:var(--teal)' }), 'Earned'),
    h('span', h('i', { style: 'background:var(--good)' }), 'Hit target'), h('span', h('i', { style: 'background:var(--bad)' }), 'Target')));
}

PAGES.insights = async () => {
  const d = await api('insights');
  const a = d.all_time, l4 = d.last4;
  return h('div',
    head('Insights', 'Your averages and history. These sharpen the projections as you log more shifts.'),
    h('div.grid.g4',
      tile('Avg per shift (all time)', a.avg ? money0(a.avg) : '–', `${a.shifts} shifts · ${money0(a.tips)} tips`),
      tile('Avg per hour', a.per_hour ? money(a.per_hour) : '–', 'shifts with hours logged'),
      tile('Last 4 weeks', money0(l4.tips), `${l4.shifts} shifts · ${money0(l4.per_week)}/week`),
      tile('Best shift', a.best ? money0(a.best.tips) : '–', a.best ? `${fdateY(a.best.date)} · ${a.best.type || ''}` : 'no shifts logged yet')),
    h('div.grid.g2.mt',
      h('div.card', h('h2', 'Month by month vs goal'), barChart(d.by_month.map(m => ({ label: fmonth(m.month).replace(' 20', " '"), value: m.total, target: m.goal })),
        { labelEvery: Math.ceil(d.by_month.length / 8), tolerance: L.settings.tolerance || 0 })),
      h('div.card', h('h2', 'Last 12 weeks'), barChart(d.weeks.map(w => ({ label: fdate(w.start), value: w.earned, target: w.target })), { labelEvery: 2 }))),
    h('div.grid.g2.mt',
      h('div.card', h('h2', 'By weekday'), h('div.table-wrap', h('table',
        h('thead', h('tr', h('th', 'Day'), h('th.num', 'Shifts'), h('th.num', 'Avg tips'), ...L.shift_types.map(t => h('th.num', `Avg ${t.name}`)), h('th.num', '$/hr'))),
        h('tbody', d.by_day.map(r => h('tr', h('td', h('b', r.day)), h('td.num', r.shifts), h('td.num', h('b', r.avg ? money0(r.avg) : '–')),
          ...r.types.map(t => h('td.num.muted', t.avg ? `${money0(t.avg)} (${t.shifts})` : '–')), h('td.num.muted', r.per_hour ? money(r.per_hour) : '–'))))))),
      h('div.card', h('h2', 'By shift type'), h('div.list', d.by_type.map(t => h('div.li', h('span.name', h('b', t.name)),
        h('span.muted', `${t.shifts} shifts`), h('span.num', t.avg ? money0(t.avg) + ' avg' : '–'), h('span.num.muted', t.per_hour ? money(t.per_hour) + '/hr' : '')))),
        h('div.note', `Other income that counts toward your goal: ${money0(a.other_income)} all time. Hourly wage (not counted): ${money0(a.hourly_wage)}.`))),
    h('div.card.mt', h('h2', 'Monthly history'), h('div.table-wrap', h('table',
      h('thead', h('tr', ['Month', 'Tips', 'Other income', 'Total', '+ / – goal', 'Shifts', 'Avg/shift', 'Spent (budget)', 'Hit goal?'].map((x, i) => h('th', { class: i > 0 && i < 8 ? 'num' : null }, x)))),
      h('tbody', [...d.by_month].reverse().map(m => h('tr', h('td', h('b', fmonth(m.month, true))), h('td.num', money0(m.tips)), h('td.num', money0(m.other)),
        h('td.num', h('b', money0(m.total))), h('td.num', { style: m.diff === null ? null : `color:var(--${m.diff >= 0 ? 'good' : 'bad'})` }, m.diff === null ? '' : money0(m.diff)),
        h('td.num', m.shifts), h('td.num', m.avg ? money0(m.avg) : '–'), h('td.num', money0(m.spent)), h('td', m.hit ? badge(m.hit) : ''))))))));
};

// ------------------------------------------------------------------ SETTINGS
PAGES.settings = async () => {
  const s = L.settings;
  const f = {
    goal: h('input', { type: 'number', step: '50', value: s.goal }),
    tolerance: h('input', { type: 'number', step: '10', value: s.tolerance }),
    strategy: h('select', null, optionNodes(['Avalanche', 'Snowball'], s.strategy)),
    typical_shifts: h('input', { type: 'number', step: '1', value: s.typical_shifts }),
    start_month: h('input', { type: 'month', value: s.start_month }),
    default_tips_account: h('select', null, optionNodes(accountOptions(), s.default_tips_account, '— none —')),
  };
  const saveSettings = async () => {
    try {
      await send('PUT', 'settings', { goal: Number(f.goal.value), tolerance: Number(f.tolerance.value), strategy: f.strategy.value,
        typical_shifts: Number(f.typical_shifts.value), start_month: f.start_month.value,
        default_tips_account: f.default_tips_account.value ? Number(f.default_tips_account.value) : null });
      toast('Settings saved'); await refresh();
    } catch (e) { toast(e.message, true); }
  };
  const lbl = (text, input, hint) => h('label.f', text, input, hint && h('span.hint', hint));
  const typeRow = t => {
    const inp = h('input', { type: 'text', value: t.name, onchange: async e => { await send('PUT', `t/shift_types/${t.id}`, { name: e.target.value }); await refresh(); } });
    return h('div.li', inp, h('button.btn.ghost.sm.danger', { onclick: async () => {
      try { if (await confirmBox(`Delete ${t.name}?`, 'Only possible if no shifts use it.')) { await send('DELETE', `t/shift_types/${t.id}?mode=delete`); await refresh(); } }
      catch (e) { toast(e.message, true); }
    } }, 'Delete'));
  };
  const itypeForm = (t = {}) => openForm({
    title: t.id ? `Edit ${t.name}` : 'Add income type',
    values: { counts: t.id ? !!t.counts : true, ...t, dest: joinRef(t.dest_type, t.dest_id) },
    fields: [{ name: 'name', label: 'Name', required: true, full: true },
      { name: 'counts', label: 'Counts toward my monthly goal', type: 'check' },
      { name: 'dest', label: 'Goes to by default', type: 'select', options: destOptions(joinRef(t.dest_type, t.dest_id)), blank: `Default account (${defaultTipsName()})`, full: true }],
    onSubmit: async v => {
      const [dt, di] = splitRef(v.dest);
      const body = { name: v.name, counts: v.counts, dest_type: dt, dest_id: di };
      if (t.id) await send('PUT', `t/income_types/${t.id}`, body); else await send('POST', 't/income_types', body);
    },
    onDelete: t.id && (async () => {
      try { if (await confirmBox(`Delete ${t.name}?`, 'Only possible if no income uses it.')) { await send('DELETE', `t/income_types/${t.id}?mode=delete`); await refresh(); } }
      catch (e) { toast(e.message, true); }
    }),
  });
  const backupNote = h('div.note', `Automatic backups go to ${s.backup_dir} (one per day, last 30 kept).`);
  return h('div',
    head('Settings'),
    h('div.grid.g2',
      h('div.card', h('h2', 'Goal & planning'), h('div.form',
        lbl('Monthly income goal', f.goal, 'Tips + other income that counts (not hourly wage).'),
        lbl('“On target” cushion (±)', f.tolerance, 'Within this much counts as hitting the goal.'),
        lbl('Extra debt strategy', f.strategy),
        lbl('Typical shifts per month', f.typical_shifts, 'Used until you have shift history.'),
        lbl('Tracking start month', f.start_month),
        lbl('Tips go to (default account)', f.default_tips_account)),
        h('div', { style: 'margin-top:14px' }, h('button.btn.primary', { onclick: saveSettings }, 'Save settings'))),
      h('div',
        h('div.card', h('h2', 'Shift types'), h('div.list', L.shift_types.map(typeRow)),
          h('button.btn.sm', { style: 'margin-top:10px', onclick: async () => { await send('POST', 't/shift_types', { name: 'New shift type', sort: L.shift_types.length }); await refresh(); } }, '+ Add shift type')),
        h('div.card.mt', h('h2', 'Income types'), h('div.list', L.income_types.map(t => h('div.li', { style: 'cursor:pointer', onclick: () => itypeForm(t) },
          h('span.name', h('b', t.name)), t.counts ? badge('Counts', 'good') : badge('Not counted', ''),
          h('span.muted', t.dest_id ? '→ ' + ((t.dest_type === 'fund' ? byId(L.funds, t.dest_id) : byId(L.accounts, t.dest_id)) || {}).name : '→ default')))),
          h('button.btn.sm', { style: 'margin-top:10px', onclick: () => itypeForm() }, '+ Add income type')))),
    h('div.card.mt', h('h2', 'Your data'),
      h('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' },
        h('button.btn', { onclick: async () => { try { const r = await send('POST', 'backup'); toast('Backed up'); backupNote.textContent = 'Backed up to ' + r.path; } catch (e) { toast(e.message, true); } } }, 'Back up now'),
        h('a.btn', { href: '/api/export' }, 'Export to spreadsheet (CSV)')),
      backupNote));
};

// ------------------------------------------------------------------ boot
(async () => {
  try { L = await api('lists'); } catch (e) { $main.replaceChildren(h('div.banner.bad', e.message)); return; }
  render(false);
})();
