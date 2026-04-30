const state = {
  ownerKey: localStorage.getItem('pinebitz.ownerKey') || 'smoke-owner',
  connections: [],
  plans: [],
  bots: [],
  selectedPlanId: null,
  batchSimulationResults: [],
  snapshots: JSON.parse(localStorage.getItem('pinebitz.snapshots') || '[]'),
  signals: [],
  executionJobs: [],
  executionAudit: [],
  paperPositions: [],
  runtimeGuardState: null,
  autoRefreshSeconds: Number(localStorage.getItem('pinebitz.autoRefreshSeconds') || 5),
  autoRefreshPaused: false,
  autoRefreshTimer: null,
  alertSoundEnabled: localStorage.getItem('pinebitz.alertSoundEnabled') !== 'false',
  desktopNotifyEnabled: localStorage.getItem('pinebitz.desktopNotifyEnabled') === 'true',
  currentPage: localStorage.getItem('pinebitz.currentPage') || 'execution',
  currentMode: localStorage.getItem('pinebitz.currentMode') || 'all',
  seenJobIds: new Set(),
  jobAlertInitialized: false,
};

const el = (id) => document.getElementById(id);
const logBox = () => el('eventLog');

function log(message, payload) {
  const now = new Date().toLocaleTimeString();
  const line = `[${now}] ${message}` + (payload ? ` ${JSON.stringify(payload)}` : '');
  logBox().textContent = `${line}\n${logBox().textContent}`;
}

function badge(value) {
  const s = String(value);
  return `<span class="badge ${s}">${s}</span>`;
}

function playAlertBeep(level = 'warn') {
  if (!state.alertSoundEnabled || typeof window === 'undefined' || !window.AudioContext) return;
  try {
    const ctx = new window.AudioContext();
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.type = level === 'danger' ? 'sawtooth' : 'sine';
    oscillator.frequency.value = level === 'danger' ? 880 : 660;
    gain.gain.value = 0.03;
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    oscillator.start();
    oscillator.stop(ctx.currentTime + (level === 'danger' ? 0.16 : 0.12));
  } catch {
    // ignore browser audio restrictions
  }
}

function currentNotifyPermission() {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported';
  return Notification.permission;
}

function updateNotifyPermissionUi() {
  const node = el('notifyPermissionStatus');
  if (!node) return;
  node.textContent = `notify: ${currentNotifyPermission()}`;
}

async function requestDesktopNotificationPermission() {
  if (typeof window === 'undefined' || !('Notification' in window)) {
    log('Desktop notification unavailable', { reason: 'unsupported' });
    updateNotifyPermissionUi();
    return;
  }
  const permission = await Notification.requestPermission();
  updateNotifyPermissionUi();
  log('Desktop notification permission', { permission });
}

function sendDesktopNotification(title, body, jobId = null) {
  if (!state.desktopNotifyEnabled) return;
  if (typeof window === 'undefined' || !('Notification' in window)) return;
  if (Notification.permission !== 'granted') return;
  try {
    // Browser may still suppress duplicate notifications based on OS rules.
    // Keep title/body concise for quick background visibility.
    const note = new Notification(title, { body });
    note.onclick = () => {
      try {
        window.focus();
        const jobsTable = el('jobsTable');
        if (jobsTable) {
          jobsTable.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
        if (jobId) {
          setValue('auditJobId', jobId);
          loadExecutionAudit(jobId).catch((err) => {
            log('Audit load from notification failed', { job_id: jobId, reason: err.message || String(err) });
          });
        }
      } catch {
        // ignore focus/scroll restrictions
      } finally {
        note.close();
      }
    };
  } catch {
    // ignore blocked notification failures
  }
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function isDemoEntity(...values) {
  return values.some((value) => /demo|test|smoke/i.test(String(value || '')));
}

function entityBadge(isDemo) {
  return `<span class="badge ${isDemo ? 'demo' : 'live'}">${isDemo ? 'demo' : 'live'}</span>`;
}

function shouldIncludeEntity(isDemo) {
  if (state.currentMode === 'demo') return isDemo;
  if (state.currentMode === 'live') return !isDemo;
  return true;
}

function applyConnectionFormDefaults() {
  const form = el('connectionForm');
  if (!form) return;
  const liveMode = state.currentPage === 'exchanges' && state.currentMode === 'live';
  form.label.value = liveMode ? 'Binance UM Account' : 'Demo Binance UM';
  form.venue.value = 'binance';
  form.market_lane.value = 'futures_um';
  form.environment.value = liveMode ? 'mainnet' : 'testnet';
  form.credential_ref.value = liveMode ? 'vault://live/binance/usdm' : 'vault://demo/binance/usdm';
}

function syncHashFromState() {
  if (typeof window === 'undefined') return;
  const nextHash = `#page=${encodeURIComponent(state.currentPage)}&mode=${encodeURIComponent(state.currentMode)}`;
  if (window.location.hash !== nextHash) {
    window.location.hash = nextHash;
  }
}

function applyStateFromHash() {
  if (typeof window === 'undefined') return false;
  const raw = window.location.hash || '';
  if (!raw.startsWith('#')) return false;
  const params = new URLSearchParams(raw.slice(1));
  const page = params.get('page');
  const mode = params.get('mode');
  let changed = false;
  if (page) {
    state.currentPage = page;
    changed = true;
  }
  if (mode) {
    state.currentMode = mode;
    changed = true;
  }
  return changed;
}

function applyPageMode() {
  const page = state.currentPage;
  document.body.dataset.currentPage = page;
  for (const section of document.querySelectorAll('main.page section[data-page]')) {
    const sectionPage = section.getAttribute('data-page');
    let show = sectionPage === 'all' || sectionPage === page;
    const showModes = section.getAttribute('data-show-modes');
    if (show && showModes) {
      const allowed = showModes.split(',').map((s) => s.trim()).filter(Boolean);
      if (!allowed.includes(state.currentMode)) show = false;
    }
    section.style.display = show ? '' : 'none';
  }
  const viewBanner = document.querySelector('main.page .view-banner');
  if (viewBanner) {
    viewBanner.style.display = page === 'dca' && state.currentMode === 'all' ? 'none' : '';
  }
  for (const btn of document.querySelectorAll('.nav-link[data-nav-page]')) {
    const active = btn.dataset.navPage === page && (btn.dataset.navMode || 'all') === state.currentMode;
    btn.classList.toggle('active', active);
  }
  const titleMap = {
    exchanges: 'Exchanges Workspace',
    dca: 'DCA Bot Workspace',
    execution: 'Execution Workspace',
  };
  const title = el('currentViewTitle');
  const mode = el('currentViewMode');
  if (title) {
    if (page === 'dca' && state.currentMode === 'all') {
      title.textContent = 'All bots';
    } else {
      title.textContent = titleMap[page] || 'Workspace';
    }
  }
  if (mode) mode.textContent = `mode: ${state.currentMode}`;
}

function setPlanEditorTab(tab) {
  const panelEdit = el('planEditorTabPanelEdit');
  const panelPaper = el('planEditorTabPanelPaper');
  const panelPreview = el('planEditorTabPanelPreview');
  const panelScenarios = el('planEditorTabPanelScenarios');
  const panelSnapshots = el('planEditorTabPanelSnapshots');
  const btnEdit = el('planEditorTabBtnEdit');
  const btnPaper = el('planEditorTabBtnPaper');
  const btnPreview = el('planEditorTabBtnPreview');
  const btnScenarios = el('planEditorTabBtnScenarios');
  const btnSnapshots = el('planEditorTabBtnSnapshots');
  if (
    !panelEdit ||
    !panelPaper ||
    !panelPreview ||
    !panelScenarios ||
    !panelSnapshots ||
    !btnEdit ||
    !btnPaper ||
    !btnPreview ||
    !btnScenarios ||
    !btnSnapshots
  ) {
    return;
  }
  const allowed = new Set(['edit', 'paper', 'preview', 'scenarios', 'snapshots']);
  const t = allowed.has(tab) ? tab : 'edit';
  panelEdit.hidden = t !== 'edit';
  panelPaper.hidden = t !== 'paper';
  panelPreview.hidden = t !== 'preview';
  panelScenarios.hidden = t !== 'scenarios';
  panelSnapshots.hidden = t !== 'snapshots';
  btnEdit.classList.toggle('active', t === 'edit');
  btnPaper.classList.toggle('active', t === 'paper');
  btnPreview.classList.toggle('active', t === 'preview');
  btnScenarios.classList.toggle('active', t === 'scenarios');
  btnSnapshots.classList.toggle('active', t === 'snapshots');
  btnEdit.setAttribute('aria-selected', String(t === 'edit'));
  btnPaper.setAttribute('aria-selected', String(t === 'paper'));
  btnPreview.setAttribute('aria-selected', String(t === 'preview'));
  btnScenarios.setAttribute('aria-selected', String(t === 'scenarios'));
  btnSnapshots.setAttribute('aria-selected', String(t === 'snapshots'));
}

function currentSelectedPlan() {
  return state.plans.find((p) => p.id === state.selectedPlanId) || null;
}

function planConfig(item) {
  return item?.config_json || {};
}

function setSelectedPlanMeta(item) {
  const meta = el('selectedPlanMeta');
  if (!item) {
    meta.textContent = 'No plan selected';
    return;
  }
  meta.textContent = `${item.name} | ${planConfig(item).pair || '-'} | ${item.instrument_kind || '-'}`;
}

function setValue(id, value, fallback = '') {
  const node = el(id);
  if (node) node.value = value ?? fallback;
}

function editorConfigPayload() {
  return {
    pair: el('editorPair').value.trim(),
    direction: el('editorDirection').value,
    notes: el('editorNotes').value.trim(),
    entry: {
      base_order_usdt: Number(el('editorBaseOrder').value || 0),
      leverage: Number(el('editorLeverage').value || 1),
      start_order_type: el('editorStartOrderType').value,
      margin_mode: el('editorMarginMode').value,
    },
    averaging: {
      enabled: el('editorAveragingEnabled').value === 'true',
      max_orders: Number(el('editorMaxOrders').value || 0),
      first_deviation_pct: Number(el('editorFirstDeviation').value || 0),
      deviation_multiplier: Number(el('editorDeviationMultiplier').value || 1),
      safety_order_size: Number(el('editorSafetyOrderSize').value || 0),
      order_size_multiplier: Number(el('editorOrderSizeMultiplier').value || 1),
    },
    exit: {
      tp_pct: Number(el('editorTakeProfit').value || 0),
      stop_loss_pct: Number(el('editorStopLoss').value || 0),
    },
    trade_start_conditions: {
      enabled: Boolean(el('tradeStartEnabled')?.checked),
      conditions: collectTradeStartConditionsFromDom(),
    },
  };
}

function updateTradeStartConditionsDisabled() {
  const fs = el('tradeStartFieldset');
  const box = el('tradeStartEnabled');
  if (fs && box) fs.disabled = !box.checked;
}

/** Dashboard trade-start AND clauses (persisted on plan; runtime evaluator TBD). */
const TRADE_START_KINDS = [
  { v: 'tv_webhook', label: 'TradingView custom signal' },
  { v: 'tv_screener', label: 'TradingView Crypto Screener' },
  { v: 'qfl_long', label: 'QFL (only long signals)' },
  { v: 'rsi', label: 'RSI' },
  { v: 'ultimate_oscillator', label: 'Ultimate Oscillator' },
  { v: 'bollinger_pctb', label: 'Bollinger Bands %B' },
  { v: 'ma', label: 'Moving Average (MA)' },
  { v: 'adx', label: 'Average Directional Index' },
  { v: 'stochastic', label: 'Stochastic' },
  { v: 'macd', label: 'MACD' },
  { v: 'parabolic_sar', label: 'Parabolic SAR' },
  { v: 'mfi', label: 'Money Flow Index' },
  { v: 'cci', label: 'Commodity Channel Index' },
  { v: 'heikin_ashi', label: 'Heikin Ashi' },
];

const TSC_TF_OPTS = [
  ['1m', '1 minute'],
  ['3m', '3 minutes'],
  ['5m', '5 minutes'],
  ['15m', '15 minutes'],
  ['1h', '1 hour'],
  ['4h', '4 hours'],
  ['1d', '1 day'],
];

function normalizeTradeStartKind(k) {
  const allowed = new Set(TRADE_START_KINDS.map((x) => x.v));
  return allowed.has(k) ? k : 'tv_webhook';
}

function defaultParamsForKind(kind) {
  switch (kind) {
    case 'tv_screener':
      return { timeframe: '1h', signal_value: 'buy' };
    case 'rsi':
      return { length: 14, timeframe: '3m', compare: 'lt', value: 30 };
    case 'macd':
      return {
        fast: 12,
        slow: 26,
        signal_line: 9,
        macd_trigger: 'crossing_up',
        line_trigger: 'less_than_0',
        timeframe: '3m',
      };
    case 'stochastic':
      return {
        k_length: 14,
        k_smoothing: 1,
        d_smoothing: 3,
        k_condition: 'lt',
        k_signal_value: 20,
        crossover: 'k_cross_up_d',
        timeframe: '3m',
      };
    case 'ma':
      return { period: 20, ma_type: 'sma', condition: 'price_above', timeframe: '1h' };
    case 'adx':
      return { period: 14, threshold: 25, timeframe: '1h' };
    case 'bollinger_pctb':
      return { period: 20, stddev: 2, pctb_condition: 'below_lower', timeframe: '1h' };
    case 'parabolic_sar':
      return { step: 0.02, max_af: 0.2, trigger: 'flip_bull', timeframe: '1h' };
    case 'mfi':
      return { length: 14, compare: 'lt', value: 20, timeframe: '1h' };
    case 'cci':
      return { length: 20, compare: 'lt', value: -100, timeframe: '1h' };
    case 'ultimate_oscillator':
      return { len_short: 7, len_mid: 14, len_long: 28, compare: 'lt', value: 30, timeframe: '1h' };
    case 'heikin_ashi':
      return { trend: 'bullish', timeframe: '1h' };
    default:
      return {};
  }
}

function mergeTradeStartParams(kind, saved) {
  const d = defaultParamsForKind(kind);
  if (!saved || typeof saved !== 'object') return { ...d };
  return { ...d, ...saved };
}

function tscEscAttr(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/'/g, '&#39;');
}

function tscEscHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function tscSelectHtml(dataKey, options, current) {
  const opts = options
    .map(([val, label]) => `<option value="${tscEscAttr(val)}"${val === current ? ' selected' : ''}>${tscEscHtml(label)}</option>`)
    .join('');
  return `<select class="tsc-inp" data-tsc-key="${tscEscAttr(dataKey)}">${opts}</select>`;
}

function tscNumberHtml(dataKey, val, attrs = {}) {
  const extra = Object.entries(attrs)
    .map(([k, v]) => ` ${tscEscAttr(k)}="${tscEscAttr(String(v))}"`)
    .join('');
  return `<input type="number" class="tsc-inp" data-tsc-key="${tscEscAttr(dataKey)}" value="${tscEscAttr(String(val ?? ''))}"${extra} />`;
}

function tradeStartKindOptionsHtml(kind) {
  return TRADE_START_KINDS.map(
    ({ v, label }) => `<option value="${tscEscAttr(v)}"${v === kind ? ' selected' : ''}>${tscEscHtml(label)}</option>`,
  ).join('');
}

function tradeStartParamsHtml(kind, row) {
  const merged = {
    params: mergeTradeStartParams(kind, row.params),
    timeframe: row.timeframe,
    signal_value: row.signal_value,
  };
  if (kind === 'tv_screener') {
    if (row.timeframe && merged.params.timeframe == null) merged.params.timeframe = row.timeframe;
    if (row.signal_value && merged.params.signal_value == null) merged.params.signal_value = row.signal_value;
  }
  const p = merged.params;
  if (kind === 'tv_webhook') {
    return '<p class="section-note trade-start-kind-note">Match when TradingView webhook payload is accepted for this owner/plan pair.</p>';
  }
  if (kind === 'qfl_long') {
    return '<p class="section-note trade-start-kind-note">QFL long-only lane (evaluator not implemented).</p>';
  }
  if (kind === 'tv_screener') {
    return `<div class="trade-start-param-grid">
      <label>Timeframe ${tscSelectHtml('timeframe', TSC_TF_OPTS, p.timeframe || '1h')}</label>
      <label>Signal ${tscSelectHtml('signal_value', [
        ['buy', 'Buy'],
        ['sell', 'Sell'],
      ], p.signal_value || 'buy')}</label>
    </div>`;
  }
  if (kind === 'rsi') {
    return `<div class="trade-start-param-grid">
      <label>RSI length ${tscNumberHtml('length', p.length, { min: 1, max: 100, step: 1 })}</label>
      <label>Timeframe ${tscSelectHtml('timeframe', TSC_TF_OPTS, p.timeframe)}</label>
      <label>Condition ${tscSelectHtml('compare', [
        ['lt', 'Less than'],
        ['gt', 'Greater than'],
      ], p.compare)}</label>
      <label>Signal value ${tscNumberHtml('value', p.value, { step: '0.1' })}</label>
    </div>`;
  }
  if (kind === 'macd') {
    return `<div class="trade-start-param-grid">
      <label>Fast ${tscNumberHtml('fast', p.fast, { min: 1, step: 1 })}</label>
      <label>Slow ${tscNumberHtml('slow', p.slow, { min: 1, step: 1 })}</label>
      <label>Signal line ${tscNumberHtml('signal_line', p.signal_line, { min: 1, step: 1 })}</label>
      <label>MACD trigger ${tscSelectHtml('macd_trigger', [
        ['crossing_up', 'Crossing up'],
        ['crossing_down', 'Crossing down'],
      ], p.macd_trigger)}</label>
      <label>Line trigger ${tscSelectHtml('line_trigger', [
        ['less_than_0', 'Less than 0'],
        ['greater_than_0', 'Greater than 0'],
      ], p.line_trigger)}</label>
      <label>Timeframe ${tscSelectHtml('timeframe', TSC_TF_OPTS, p.timeframe)}</label>
    </div>`;
  }
  if (kind === 'stochastic') {
    return `<div class="trade-start-param-grid">
      <label>K length ${tscNumberHtml('k_length', p.k_length, { min: 1, step: 1 })}</label>
      <label>K smoothing ${tscNumberHtml('k_smoothing', p.k_smoothing, { min: 1, step: 1 })}</label>
      <label>D smoothing ${tscNumberHtml('d_smoothing', p.d_smoothing, { min: 1, step: 1 })}</label>
      <label>K condition ${tscSelectHtml('k_condition', [
        ['lt', 'Less than'],
        ['gt', 'Greater than'],
      ], p.k_condition)}</label>
      <label>K signal value ${tscNumberHtml('k_signal_value', p.k_signal_value, { step: '0.1' })}</label>
      <label>Crossover ${tscSelectHtml('crossover', [
        ['k_cross_up_d', 'K crossing up D'],
        ['k_cross_down_d', 'K crossing down D'],
      ], p.crossover)}</label>
      <label>Timeframe ${tscSelectHtml('timeframe', TSC_TF_OPTS, p.timeframe)}</label>
    </div>`;
  }
  if (kind === 'ma') {
    return `<div class="trade-start-param-grid">
      <label>Period ${tscNumberHtml('period', p.period, { min: 1, step: 1 })}</label>
      <label>MA type ${tscSelectHtml('ma_type', [
        ['sma', 'SMA'],
        ['ema', 'EMA'],
      ], p.ma_type)}</label>
      <label>Condition ${tscSelectHtml('condition', [
        ['price_above', 'Price above MA'],
        ['price_below', 'Price below MA'],
      ], p.condition)}</label>
      <label>Timeframe ${tscSelectHtml('timeframe', TSC_TF_OPTS, p.timeframe)}</label>
    </div>`;
  }
  if (kind === 'adx') {
    return `<div class="trade-start-param-grid">
      <label>Period ${tscNumberHtml('period', p.period, { min: 1, step: 1 })}</label>
      <label>Threshold ${tscNumberHtml('threshold', p.threshold, { step: '0.1' })}</label>
      <label>Timeframe ${tscSelectHtml('timeframe', TSC_TF_OPTS, p.timeframe)}</label>
    </div>`;
  }
  if (kind === 'bollinger_pctb') {
    return `<div class="trade-start-param-grid">
      <label>Period ${tscNumberHtml('period', p.period, { min: 1, step: 1 })}</label>
      <label>Std dev ${tscNumberHtml('stddev', p.stddev, { min: 0.1, step: '0.1' })}</label>
      <label>%B condition ${tscSelectHtml('pctb_condition', [
        ['below_lower', 'Below lower band'],
        ['above_upper', 'Above upper band'],
      ], p.pctb_condition)}</label>
      <label>Timeframe ${tscSelectHtml('timeframe', TSC_TF_OPTS, p.timeframe)}</label>
    </div>`;
  }
  if (kind === 'parabolic_sar') {
    return `<div class="trade-start-param-grid">
      <label>Step ${tscNumberHtml('step', p.step, { min: 0.001, step: '0.001' })}</label>
      <label>Max AF ${tscNumberHtml('max_af', p.max_af, { min: 0.01, step: '0.01' })}</label>
      <label>Trigger ${tscSelectHtml('trigger', [
        ['flip_bull', 'Flip bullish'],
        ['flip_bear', 'Flip bearish'],
      ], p.trigger)}</label>
      <label>Timeframe ${tscSelectHtml('timeframe', TSC_TF_OPTS, p.timeframe)}</label>
    </div>`;
  }
  if (kind === 'mfi') {
    return `<div class="trade-start-param-grid">
      <label>Length ${tscNumberHtml('length', p.length, { min: 1, step: 1 })}</label>
      <label>Condition ${tscSelectHtml('compare', [
        ['lt', 'Less than'],
        ['gt', 'Greater than'],
      ], p.compare)}</label>
      <label>Value ${tscNumberHtml('value', p.value, { step: '0.1' })}</label>
      <label>Timeframe ${tscSelectHtml('timeframe', TSC_TF_OPTS, p.timeframe)}</label>
    </div>`;
  }
  if (kind === 'cci') {
    return `<div class="trade-start-param-grid">
      <label>Length ${tscNumberHtml('length', p.length, { min: 1, step: 1 })}</label>
      <label>Condition ${tscSelectHtml('compare', [
        ['lt', 'Less than'],
        ['gt', 'Greater than'],
      ], p.compare)}</label>
      <label>Value ${tscNumberHtml('value', p.value, { step: '1' })}</label>
      <label>Timeframe ${tscSelectHtml('timeframe', TSC_TF_OPTS, p.timeframe)}</label>
    </div>`;
  }
  if (kind === 'ultimate_oscillator') {
    return `<div class="trade-start-param-grid">
      <label>Short ${tscNumberHtml('len_short', p.len_short, { min: 1, step: 1 })}</label>
      <label>Mid ${tscNumberHtml('len_mid', p.len_mid, { min: 1, step: 1 })}</label>
      <label>Long ${tscNumberHtml('len_long', p.len_long, { min: 1, step: 1 })}</label>
      <label>Condition ${tscSelectHtml('compare', [
        ['lt', 'Less than'],
        ['gt', 'Greater than'],
      ], p.compare)}</label>
      <label>Value ${tscNumberHtml('value', p.value, { step: '0.1' })}</label>
      <label>Timeframe ${tscSelectHtml('timeframe', TSC_TF_OPTS, p.timeframe)}</label>
    </div>`;
  }
  if (kind === 'heikin_ashi') {
    return `<div class="trade-start-param-grid">
      <label>Trend ${tscSelectHtml('trend', [
        ['bullish', 'Bullish'],
        ['bearish', 'Bearish'],
      ], p.trend)}</label>
      <label>Timeframe ${tscSelectHtml('timeframe', TSC_TF_OPTS, p.timeframe)}</label>
    </div>`;
  }
  return '<p class="section-note trade-start-kind-note">No parameters for this type.</p>';
}

function collectParamsFromCard(card) {
  const pr = {};
  for (const inp of card.querySelectorAll('.tsc-inp[data-tsc-key]')) {
    const k = inp.dataset.tscKey;
    if (!k) continue;
    if (inp.tagName === 'SELECT') pr[k] = inp.value;
    else if (inp.type === 'number') pr[k] = Number(inp.value || 0);
    else pr[k] = inp.value;
  }
  return pr;
}

function collectTradeStartConditionsFromDom() {
  const host = el('tradeStartConditionsList');
  if (!host) return [];
  const out = [];
  for (const card of host.querySelectorAll('.trade-start-condition-card')) {
    const kind = normalizeTradeStartKind(card.querySelector('.trade-start-kind')?.value || 'tv_webhook');
    const params = collectParamsFromCard(card);
    let timeframe = null;
    let signal_value = null;
    if (kind === 'tv_screener') {
      timeframe = params.timeframe ?? null;
      signal_value = params.signal_value ?? null;
    }
    out.push({ kind, timeframe, signal_value, params });
  }
  return out;
}

function renderTradeStartConditionsList(conditions) {
  const host = el('tradeStartConditionsList');
  if (!host) return;
  host.innerHTML = '';
  conditions.forEach((row, index) => {
    host.appendChild(buildTradeStartConditionEl(row, index));
  });
}

function buildTradeStartConditionEl(row, index) {
  const wrap = document.createElement('div');
  wrap.className = 'trade-start-condition-card';
  wrap.dataset.index = String(index);
  const kind = normalizeTradeStartKind(row.kind);
  const normalizedRow = {
    kind,
    timeframe: row.timeframe ?? null,
    signal_value: row.signal_value ?? null,
    params: row.params && typeof row.params === 'object' ? { ...row.params } : {},
  };
  if (kind === 'tv_screener') {
    if (row.timeframe && normalizedRow.params.timeframe == null) normalizedRow.params.timeframe = row.timeframe;
    if (row.signal_value && normalizedRow.params.signal_value == null) normalizedRow.params.signal_value = row.signal_value;
  }
  normalizedRow.params = mergeTradeStartParams(kind, normalizedRow.params);
  wrap.innerHTML = `
    <button type="button" class="trade-start-remove" data-action="trade-start-remove" aria-label="Remove">×</button>
    <div class="trade-start-condition-meta">Condition ${index + 1}</div>
    <label>Indicator / source
      <select class="trade-start-kind">${tradeStartKindOptionsHtml(kind)}</select>
    </label>
    <div class="trade-start-dynamic-params">${tradeStartParamsHtml(kind, normalizedRow)}</div>
  `;
  return wrap;
}

function bindTradeStartConditionsUi() {
  const host = el('tradeStartConditionsList');
  if (!host || host.dataset.bound === '1') return;
  host.dataset.bound = '1';
  host.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-action="trade-start-remove"]');
    if (!btn) return;
    const card = btn.closest('.trade-start-condition-card');
    const idx = Number(card?.dataset.index ?? -1);
    if (Number.isNaN(idx) || idx < 0) return;
    const cur = collectTradeStartConditionsFromDom();
    cur.splice(idx, 1);
    renderTradeStartConditionsList(cur);
  });
  host.addEventListener('change', (ev) => {
    const sel = ev.target.closest('.trade-start-kind');
    if (!sel) return;
    const card = sel.closest('.trade-start-condition-card');
    const idx = Number(card?.dataset.index ?? -1);
    if (Number.isNaN(idx) || idx < 0) return;
    const cur = collectTradeStartConditionsFromDom();
    if (idx >= cur.length) return;
    const nk = normalizeTradeStartKind(sel.value);
    cur[idx] = { kind: nk, timeframe: null, signal_value: null, params: defaultParamsForKind(nk) };
    renderTradeStartConditionsList(cur);
  });
}

function syncTradeStartConditionsFromConfig(cfg) {
  if (!el('tradeStartEnabled') || !el('tradeStartConditionsList')) return;
  const raw = cfg?.trade_start_conditions;
  const enabled = raw?.enabled === true;
  const rows = Array.isArray(raw?.conditions) ? raw.conditions : [];
  const conditions = rows.map((r) => {
    const kind = normalizeTradeStartKind(r.kind);
    let params = r.params && typeof r.params === 'object' ? { ...r.params } : {};
    if (kind === 'tv_screener') {
      if (r.timeframe && params.timeframe == null) params.timeframe = r.timeframe;
      if (r.signal_value && params.signal_value == null) params.signal_value = r.signal_value;
    }
    params = mergeTradeStartParams(kind, params);
    return {
      kind,
      timeframe: r.timeframe ?? null,
      signal_value: r.signal_value ?? null,
      params,
    };
  });
  el('tradeStartEnabled').checked = enabled;
  renderTradeStartConditionsList(conditions);
  updateTradeStartConditionsDisabled();
}

function persistSnapshots() {
  localStorage.setItem('pinebitz.snapshots', JSON.stringify(state.snapshots));
}

function snapshotExportPayload() {
  return {
    version: 1,
    exported_at: new Date().toISOString(),
    snapshots: state.snapshots,
  };
}

function fillPlanEditor(item) {
  const cfg = planConfig(item);
  const entry = cfg.entry || {};
  const averaging = cfg.averaging || {};
  const exit = cfg.exit || {};
  setValue('editorPlanId', item?.id || '');
  setValue('editorName', item?.name || '');
  setValue('editorInstrumentKind', item?.instrument_kind || 'futures');
  setValue('editorStatus', item?.status || 'active');
  setValue('editorEnabled', String(Boolean(item?.enabled)));
  setValue('editorPlanVersion', item?.plan_version || 1);
  setValue('editorPair', cfg.pair || '');
  setValue('editorDirection', cfg.direction || 'long');
  setValue('editorBaseOrder', entry.base_order_usdt ?? 25);
  setValue('editorLeverage', entry.leverage ?? 5);
  setValue('editorStartOrderType', entry.start_order_type || 'market');
  setValue('editorMarginMode', entry.margin_mode || 'cross');
  setValue('editorTakeProfit', exit.tp_pct ?? 1.0);
  setValue('editorStopLoss', exit.stop_loss_pct ?? 0);
  setValue('editorAveragingEnabled', String(averaging.enabled ?? true));
  setValue('editorMaxOrders', averaging.max_orders ?? 3);
  setValue('editorFirstDeviation', averaging.first_deviation_pct ?? 3);
  setValue('editorDeviationMultiplier', averaging.deviation_multiplier ?? 1.5);
  setValue('editorSafetyOrderSize', averaging.safety_order_size ?? 25);
  setValue('editorOrderSizeMultiplier', averaging.order_size_multiplier ?? 1);
  setValue('editorNotes', cfg.notes || '');
  syncTradeStartConditionsFromConfig(cfg);
  setSelectedPlanMeta(item);
}

function renderPreview(data) {
  const tbody = el('previewTable');
  const summary = el('previewSummary');
  tbody.innerHTML = '';
  if (!data || !data.steps?.length) {
    summary.textContent = 'No preview yet';
    return;
  }
  const s = data.summary;
  summary.textContent = `steps=${s.total_steps} | total=${s.total_usdt} USDT | max dev=${s.max_deviation_pct}% | tp=${s.estimated_tp_price}`;
  for (const step of data.steps) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${step.index}</td>
      <td>${step.kind}</td>
      <td>${step.deviation_pct}</td>
      <td>${step.order_size_usdt}</td>
      <td>${step.cumulative_usdt}</td>
      <td>${step.trigger_price}</td>
      <td>${step.avg_price}</td>
      <td>${step.take_profit_price}</td>`;
    tbody.appendChild(tr);
  }
}

function parseSimulationPath() {
  return el('simulationPath').value
    .split(',')
    .map((value) => Number(value.trim()))
    .filter((value) => Number.isFinite(value));
}

function buildEquityCurve(data, pricePath) {
  const fillsByTick = new Map();
  let closeEvent = null;
  for (const event of data?.events || []) {
    if (event.action === 'fill') {
      const existing = fillsByTick.get(event.tick_index) || [];
      existing.push(event);
      fillsByTick.set(event.tick_index, existing);
    } else if (event.action === 'take_profit' || event.action === 'stop_loss') {
      closeEvent = event;
    }
  }

  let cumulativeUsdt = 0;
  let avgPrice = 0;
  let closed = false;
  const points = [];

  for (let tick = 0; tick < pricePath.length; tick += 1) {
    const fills = fillsByTick.get(tick) || [];
    if (fills.length) {
      const lastFill = fills[fills.length - 1];
      cumulativeUsdt = Number(lastFill.cumulative_usdt || 0);
      avgPrice = Number(lastFill.avg_price || 0);
    }

    let unrealizedPnl = 0;
    let roiPct = 0;
    if (!closed && cumulativeUsdt > 0 && avgPrice > 0) {
      const positionQty = cumulativeUsdt / avgPrice;
      if (data?.summary?.close_reason === 'not_opened') {
        unrealizedPnl = 0;
      } else if ((editorConfigPayload().direction || 'long') === 'long') {
        unrealizedPnl = (pricePath[tick] - avgPrice) * positionQty;
      } else {
        unrealizedPnl = (avgPrice - pricePath[tick]) * positionQty;
      }
      roiPct = cumulativeUsdt > 0 ? (unrealizedPnl / cumulativeUsdt) * 100 : 0;
    }

    if (closeEvent && closeEvent.tick_index === tick) {
      unrealizedPnl = Number(closeEvent.realized_pnl || unrealizedPnl);
      roiPct = Number(closeEvent.roi_pct || roiPct);
      closed = true;
    }

    points.push({
      tick_index: tick,
      price: pricePath[tick],
      cumulative_usdt: cumulativeUsdt,
      avg_price: avgPrice,
      unrealized_pnl: unrealizedPnl,
      roi_pct: roiPct,
    });
  }

  let peak = 0;
  let maxDrawdown = 0;
  for (const point of points) {
    peak = Math.max(peak, point.unrealized_pnl);
    maxDrawdown = Math.min(maxDrawdown, point.unrealized_pnl - peak);
  }
  return { points, maxDrawdown };
}

function renderSimulationChart(data, pricePath) {
  const host = el('simulationChart');
  if (!host) return;
  if (!data?.summary || !pricePath.length) {
    host.className = 'simulation-chart-empty';
    host.textContent = 'Run a simulation to render the timeline chart.';
    return;
  }

  const width = 920;
  const height = 280;
  const pad = { top: 20, right: 24, bottom: 34, left: 56 };
  const equity = buildEquityCurve(data, pricePath);
  const allPrices = [...pricePath];
  for (const event of data.events || []) {
    if (Number.isFinite(event.take_profit_price)) allPrices.push(Number(event.take_profit_price));
    if (Number.isFinite(event.stop_loss_price) && Number(event.stop_loss_price) > 0) allPrices.push(Number(event.stop_loss_price));
    if (Number.isFinite(event.avg_price) && Number(event.avg_price) > 0) allPrices.push(Number(event.avg_price));
  }
  const allPnls = equity.points.map((point) => point.unrealized_pnl);
  const minPrice = Math.min(...allPrices);
  const maxPrice = Math.max(...allPrices);
  const range = Math.max(maxPrice - minPrice, maxPrice * 0.01, 0.000001);
  const minPnl = Math.min(...allPnls, 0);
  const maxPnl = Math.max(...allPnls, 0);
  const pnlRange = Math.max(maxPnl - minPnl, Math.abs(maxPnl) * 0.2, 0.000001);
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const x = (idx) => pad.left + (pricePath.length <= 1 ? 0 : (idx / (pricePath.length - 1)) * innerWidth);
  const y = (price) => pad.top + ((maxPrice - price) / range) * innerHeight;
  const yPnl = (pnl) => pad.top + ((maxPnl - pnl) / pnlRange) * innerHeight;
  const priceLine = pricePath.map((price, idx) => `${x(idx)},${y(price)}`).join(' ');
  const equityLine = equity.points.map((point) => `${x(point.tick_index)},${yPnl(point.unrealized_pnl)}`).join(' ');

  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
    const price = maxPrice - range * ratio;
    const yy = pad.top + innerHeight * ratio;
    return `
      <line x1="${pad.left}" y1="${yy}" x2="${width - pad.right}" y2="${yy}" stroke="#324257" stroke-dasharray="4 4" />
      <text x="${pad.left - 8}" y="${yy + 4}" text-anchor="end" fill="#97a7bc" font-size="11">${price.toFixed(6)}</text>
      <text x="${width - pad.right + 8}" y="${yy + 4}" text-anchor="start" fill="#a78bfa" font-size="11">${(maxPnl - pnlRange * ratio).toFixed(4)}</text>
    `;
  }).join('');

  const tickLabels = pricePath.map((_, idx) => `
    <text x="${x(idx)}" y="${height - 10}" text-anchor="middle" fill="#97a7bc" font-size="11">${idx}</text>
  `).join('');

  const actionColors = {
    fill: '#fbbf24',
    take_profit: '#34d399',
    stop_loss: '#f87171',
  };
  const markers = (data.events || []).map((event) => {
    const cx = x(Math.min(event.tick_index, pricePath.length - 1));
    const cy = y(event.price);
    const color = actionColors[event.action] || '#e6eef8';
    const label = event.action === 'fill' ? `F${event.step_index ?? ''}` : event.action === 'take_profit' ? 'TP' : event.action === 'stop_loss' ? 'SL' : event.action;
    return `
      <circle cx="${cx}" cy="${cy}" r="5" fill="${color}" stroke="#0f1722" stroke-width="2" />
      <text x="${cx}" y="${cy - 10}" text-anchor="middle" fill="${color}" font-size="10">${label}</text>
    `;
  }).join('');

  host.className = '';
  host.innerHTML = `
    <svg class="simulation-chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Simulation timeline chart">
      <rect x="0" y="0" width="${width}" height="${height}" rx="12" ry="12" fill="#111a25" />
      ${gridLines}
      <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" stroke="#4b5e77" />
      <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" stroke="#4b5e77" />
      <line x1="${width - pad.right}" y1="${pad.top}" x2="${width - pad.right}" y2="${height - pad.bottom}" stroke="#5b4a86" />
      <polyline fill="none" stroke="#18c5c2" stroke-width="3" points="${priceLine}" />
      <polyline fill="none" stroke="#8b5cf6" stroke-width="2.5" points="${equityLine}" />
      ${markers}
      ${tickLabels}
      <text x="${width / 2}" y="${height - 10}" text-anchor="middle" fill="#97a7bc" font-size="11">Tick Index</text>
      <text x="${pad.left}" y="${12}" text-anchor="start" fill="#97a7bc" font-size="11">Price</text>
      <text x="${width - pad.right}" y="${12}" text-anchor="end" fill="#a78bfa" font-size="11">Unrealized PnL</text>
    </svg>
  `;
}

function renderSimulation(data) {
  const tbody = el('simulationTable');
  const summary = el('simulationSummary');
  const pricePath = parseSimulationPath();
  const equity = buildEquityCurve(data, pricePath);
  tbody.innerHTML = '';
  if (!data || !data.events?.length) {
    if (data?.summary) {
      const s = data.summary;
      summary.textContent = `reason=${s.close_reason} | ticks=${s.ticks_processed} | filled=${s.filled_steps} | total=${s.total_usdt} USDT | pnl=${s.realized_pnl} | roi=${s.roi_pct}% | max dd=${equity.maxDrawdown.toFixed(4)}`;
    } else {
      summary.textContent = 'No simulation yet';
    }
    renderSimulationChart(data, pricePath);
    return;
  }
  const s = data.summary;
  summary.textContent = `reason=${s.close_reason} | ticks=${s.ticks_processed} | filled=${s.filled_steps} | total=${s.total_usdt} USDT | avg=${s.final_avg_price} | close=${s.close_price} | pnl=${s.realized_pnl} | roi=${s.roi_pct}% | max dd=${equity.maxDrawdown.toFixed(4)}`;
  for (const event of data.events) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${event.tick_index}</td>
      <td>${event.price}</td>
      <td>${event.action}</td>
      <td>${event.step_index ?? ''}</td>
      <td>${event.avg_price ?? ''}</td>
      <td>${event.take_profit_price ?? ''}</td>
      <td>${event.stop_loss_price ?? ''}</td>
      <td>${event.cumulative_usdt ?? ''}</td>
      <td>${event.realized_pnl ?? ''}</td>
      <td>${event.roi_pct ?? ''}</td>`;
    tbody.appendChild(tr);
  }
  renderSimulationChart(data, pricePath);
}

function presetLabel(value) {
  return String(value || '').replaceAll('_', ' ');
}

function batchRiskWeight() {
  return Math.max(0, Number(el('batchRiskWeight')?.value || 1));
}

function riskAdjustedScore(item) {
  return Number(item?.summary?.roi_pct || 0) + Number(item?.max_drawdown || 0) * batchRiskWeight();
}

function renderBatchSimulation(results) {
  const tbody = el('batchSimulationTable');
  const summary = el('batchSimulationSummary');
  tbody.innerHTML = '';
  if (!results?.length) {
    summary.textContent = 'No batch run yet';
    return;
  }

  const sorted = [...results].sort((a, b) => riskAdjustedScore(b) - riskAdjustedScore(a));
  const tpCount = sorted.filter((item) => item.summary.close_reason === 'take_profit').length;
  const slCount = sorted.filter((item) => item.summary.close_reason === 'stop_loss').length;
  const openCount = sorted.filter((item) => item.summary.close_reason === 'open' || item.summary.close_reason === 'not_opened').length;
  const avgRoi = sorted.reduce((sum, item) => sum + Number(item.summary.roi_pct || 0), 0) / sorted.length;
  const avgMaxDd = sorted.reduce((sum, item) => sum + Number(item.max_drawdown || 0), 0) / sorted.length;
  const avgScore = sorted.reduce((sum, item) => sum + riskAdjustedScore(item), 0) / sorted.length;
  const best = sorted[0];
  const worstDd = [...sorted].sort((a, b) => Number(a.max_drawdown || 0) - Number(b.max_drawdown || 0))[0];
  summary.textContent = `runs=${sorted.length} | tp=${tpCount} | sl=${slCount} | open=${openCount} | avg roi=${avgRoi.toFixed(4)}% | avg dd=${avgMaxDd.toFixed(4)} | weight=${batchRiskWeight().toFixed(2)} | avg score=${avgScore.toFixed(4)} | best=${presetLabel(best?.preset)} | worst dd=${presetLabel(worstDd?.preset)}`;

  for (const item of sorted) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${presetLabel(item.preset)}</td>
      <td>${item.summary.close_reason}</td>
      <td>${item.summary.ticks_processed}</td>
      <td>${item.summary.filled_steps}</td>
      <td>${item.summary.total_usdt}</td>
      <td>${item.summary.close_price}</td>
      <td>${item.summary.realized_pnl}</td>
      <td>${item.summary.roi_pct}</td>
      <td>${Number(item.max_drawdown || 0).toFixed(4)}</td>
      <td>${riskAdjustedScore(item).toFixed(4)}</td>
      <td><button type="button" data-action="view-batch-simulation" data-preset="${item.preset}">View</button></td>`;
    tbody.appendChild(tr);
  }
}

function fmtOpt(v) {
  if (v === null || v === undefined) return '';
  const n = Number(v);
  if (!Number.isFinite(n)) return '';
  return n.toString();
}

function renderSignals() {
  const tbody = el('signalsTable');
  if (!tbody) return;
  tbody.innerHTML = '';
  for (const item of state.signals) {
    const received = item.received_at ? new Date(item.received_at).toLocaleString() : '';
    const owner = item.owner_key || 'public';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${received}</td>
      <td>${owner}</td>
      <td>${item.symbol}</td>
      <td>${item.side}</td>
      <td>${item.timeframe || ''}</td>
      <td>${fmtOpt(item.price)}</td>
      <td>${fmtOpt(item.take_profit)}</td>
      <td>${fmtOpt(item.stop_loss)}</td>
      <td>${fmtOpt(item.risk_pct)}</td>`;
    tbody.appendChild(tr);
  }
}

function renderExecutionJobs() {
  const tbody = el('jobsTable');
  const summary = el('jobsSummary');
  const jobsAlert = el('jobsAlert');
  if (!tbody) return;
  tbody.innerHTML = '';
  const counts = {
    queued: 0,
    approved: 0,
    filled: 0,
    rejected: 0,
    sent: 0,
    failed: 0,
  };
  for (const job of state.executionJobs) {
    const statusText = job.status?.value || job.status;
    if (counts[statusText] !== undefined) {
      counts[statusText] += 1;
    }
    const owner = job.owner_key || job.risk_checks?.matched_owner_key || 'public';
    const planId = job.execution_payload?.plan_id || job.risk_checks?.matched_plan_id || '';
    const connectionId = job.connection_id || job.risk_checks?.matched_connection_id || '';
    const autoReasons = job.risk_checks?.auto_reject_reasons || [];
    const noteParts = [];
    if (job.notes) noteParts.push(job.notes);
    if (job.risk_checks?.requested_plan_name) noteParts.push(`requested plan=${job.risk_checks.requested_plan_name}`);
    if (job.risk_checks?.requested_plan_id) noteParts.push(`requested id=${job.risk_checks.requested_plan_id}`);
    if (!planId) noteParts.push('unmatched');
    if (autoReasons.length) noteParts.push(`rejected: ${autoReasons.join(',')}`);
    const notes = noteParts.join(' | ');
    const price = job.execution_payload?.price;

    const buttons = [];
    if (statusText === 'queued') {
      buttons.push(`<button type="button" data-action="approve-job" data-id="${job.job_id}">Approve</button>`);
      buttons.push(`<button type="button" data-action="reject-job" data-id="${job.job_id}">Reject</button>`);
      buttons.push(`<button type="button" data-action="dispatch-job" data-id="${job.job_id}">Dispatch</button>`);
    } else if (statusText === 'approved') {
      buttons.push(`<button type="button" data-action="dispatch-job" data-id="${job.job_id}">Dispatch</button>`);
    }
    buttons.push(`<button type="button" data-action="view-audit" data-id="${job.job_id}">Audit</button>`);

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${badge(statusText || '')}</td>
      <td>${owner}</td>
      <td>${job.symbol}</td>
      <td>${job.side}</td>
      <td>${job.adapter?.value || job.adapter || ''}</td>
      <td>${planId}</td>
      <td>${connectionId}</td>
      <td>${fmtOpt(price)}</td>
      <td>${notes}</td>
      <td>${buttons.join(' ')}</td>`;
    tbody.appendChild(tr);
  }
  if (summary) {
    summary.textContent = `queued=${counts.queued} | approved=${counts.approved} | sent=${counts.sent} | filled=${counts.filled} | rejected=${counts.rejected} | failed=${counts.failed}`;
  }
  if (jobsAlert && !jobsAlert.textContent.trim()) {
    jobsAlert.textContent = 'Alerts: none';
  }
}

function renderPaperPositions() {
  const tbody = el('paperPositionsTable');
  if (!tbody) return;
  tbody.innerHTML = '';
  for (const pos of state.paperPositions) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${pos.symbol}</td>
      <td>${pos.side}</td>
      <td>${pos.quantity}</td>
      <td>${pos.avg_entry_price}</td>
      <td>${pos.mark_price ?? ''}</td>
      <td>${pos.unrealized_pnl ?? ''}</td>
      <td>${pos.updated_at ? new Date(pos.updated_at).toLocaleString() : ''}</td>`;
    tbody.appendChild(tr);
  }
}

function renderRuntimeGuardState() {
  const host = el('runtimeGuardState');
  if (!host) return;
  const s = state.runtimeGuardState;
  if (!s) {
    host.textContent = 'No state yet';
    return;
  }
  const halted = s.halted ? 'HALTED' : 'RUNNING';
  const reasons = s.halt_reasons?.length ? ` | reasons=${s.halt_reasons.join(',')}` : '';
  host.textContent = `${halted} | openJobs=${s.open_jobs} | openPositions=${s.open_positions} | realizedToday=${s.realized_pnl_today} | unrealized=${s.unrealized_pnl}${reasons}`;
}

function compactJson(obj, maxLen = 240) {
  if (obj === null || obj === undefined) return '';
  try {
    const text = JSON.stringify(obj);
    if (text.length <= maxLen) return text;
    return `${text.slice(0, maxLen)}…`;
  } catch {
    return '';
  }
}

function renderExecutionAudit() {
  const tbody = el('auditTable');
  if (!tbody) return;
  tbody.innerHTML = '';
  for (const item of state.executionAudit) {
    const timeText = item.occurred_at ? new Date(item.occurred_at).toLocaleString() : '';
    const statusText = item.status?.value || item.status || '';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${timeText}</td>
      <td>${escapeHtml(item.job_id || '')}</td>
      <td>${badge(statusText)}</td>
      <td>${escapeHtml(item.event_type || '')}</td>
      <td><code>${escapeHtml(compactJson(item.details))}</code></td>`;
    tbody.appendChild(tr);
  }
}

async function loadSignals() {
  const data = await api('/signals/tradingview/inbox?limit=50&offset=0');
  state.signals = data.items || [];
  renderSignals();
}

async function loadExecutionJobs() {
  const data = await api('/execution/jobs?limit=200&offset=0');
  state.executionJobs = data.items || [];
  detectExecutionJobAlerts(state.executionJobs);
  renderExecutionJobs();
}

function detectExecutionJobAlerts(items) {
  const alertBox = el('jobsAlert');
  if (!alertBox) return;
  const currentIds = new Set(items.map((item) => item.job_id));
  const newJobs = items.filter((item) => !state.seenJobIds.has(item.job_id));
  const newQueued = newJobs.filter((item) => (item.status?.value || item.status) === 'queued');
  const newRisky = newJobs.filter((item) => {
    const status = item.status?.value || item.status;
    return status === 'failed' || status === 'rejected';
  });
  state.seenJobIds = currentIds;
  if (!state.jobAlertInitialized) {
    state.jobAlertInitialized = true;
    alertBox.classList.remove('alert-warn', 'alert-danger');
    alertBox.textContent = 'Alerts: none';
    return;
  }
  if (newRisky.length) {
    alertBox.classList.remove('alert-warn');
    alertBox.classList.add('alert-danger');
    alertBox.textContent = `Alerts: ${newRisky.length} new failed/rejected`;
    playAlertBeep('danger');
    const riskySymbols = [...new Set(newRisky.map((item) => item.symbol))].slice(0, 3).join(', ');
    sendDesktopNotification(
      'Pinebitz: failed/rejected detected',
      `${newRisky.length} job(s) | ${riskySymbols || 'multiple symbols'}`,
      newRisky[0]?.job_id || null,
    );
    return;
  }
  if (newQueued.length) {
    alertBox.classList.remove('alert-danger');
    alertBox.classList.add('alert-warn');
    alertBox.textContent = `Alerts: ${newQueued.length} new queued job(s)`;
    playAlertBeep('warn');
    const queuedSymbols = [...new Set(newQueued.map((item) => item.symbol))].slice(0, 3).join(', ');
    sendDesktopNotification(
      'Pinebitz: new queued job',
      `${newQueued.length} job(s) | ${queuedSymbols || 'multiple symbols'}`,
      newQueued[0]?.job_id || null,
    );
    return;
  }
  alertBox.classList.remove('alert-warn', 'alert-danger');
  alertBox.textContent = 'Alerts: none';
}

async function loadExecutionAudit(jobId = null) {
  const qs = jobId
    ? `?job_id=${encodeURIComponent(jobId)}&limit=200&offset=0`
    : '?limit=200&offset=0';
  const data = await api(`/execution/audit${qs}`);
  state.executionAudit = data.items || [];
  renderExecutionAudit();
}

async function loadPaperPositions() {
  const data = await api('/execution/paper/positions?limit=200&offset=0');
  state.paperPositions = data.items || [];
  renderPaperPositions();
}

async function loadRuntimeGuardState() {
  const data = await api('/execution/runtime-state');
  state.runtimeGuardState = data;
  renderRuntimeGuardState();
}

async function refreshTradingState() {
  await loadSignals();
  await loadExecutionJobs();
  await loadPaperPositions();
  await loadRuntimeGuardState();
  await loadExecutionAudit(el('auditJobId')?.value?.trim() || null);
}

async function markPaperAll() {
  const markPrice = Number(el('paperMarkPriceAll').value);
  if (!Number.isFinite(markPrice) || markPrice <= 0) {
    log('Invalid mark price');
    return;
  }
  const symbols = [...new Set((state.paperPositions || []).map((p) => p.symbol))];
  if (!symbols.length) {
    log('No paper positions to mark');
    return;
  }
  for (const symbol of symbols) {
    await api(
      `/execution/paper/mark-to-market?symbol=${encodeURIComponent(symbol)}&mark_price=${encodeURIComponent(markPrice)}`,
      { method: 'GET' },
    );
  }
  await loadPaperPositions();
  await loadRuntimeGuardState();
  log('Paper marked');
}

async function dispatchAllQueuedJobs() {
  const queuedJobs = state.executionJobs.filter((job) => {
    const statusText = job.status?.value || job.status;
    return statusText === 'queued' || statusText === 'approved';
  });
  if (!queuedJobs.length) {
    log('Dispatch all skipped', { reason: 'no queued or approved jobs' });
    return;
  }
  for (const job of queuedJobs) {
    try {
      await api(`/execution/jobs/${job.job_id}/dispatch`, { method: 'POST' });
    } catch (err) {
      log('Dispatch all item failed', { job_id: job.job_id, reason: err.message || String(err) });
    }
  }
  log('Dispatch all completed', { attempted: queuedJobs.length });
  await refreshTradingState();
}

async function clearTestQueue() {
  const result = await api('/execution/jobs/purge-test?statuses=queued,rejected', { method: 'POST' });
  log('Test queue cleared', result);
  await refreshTradingState();
}

function stopAutoRefresh() {
  if (state.autoRefreshTimer) {
    clearInterval(state.autoRefreshTimer);
    state.autoRefreshTimer = null;
  }
}

function syncAutoRefreshUi() {
  const toggle = el('toggleAutoRefreshBtn');
  if (!toggle) return;
  if (state.autoRefreshPaused || state.autoRefreshSeconds <= 0) {
    toggle.textContent = 'Resume';
  } else {
    toggle.textContent = 'Pause';
  }
}

function startAutoRefresh() {
  stopAutoRefresh();
  if (state.autoRefreshPaused || state.autoRefreshSeconds <= 0) {
    syncAutoRefreshUi();
    return;
  }
  state.autoRefreshTimer = setInterval(() => {
    refreshTradingState().catch((err) => {
      log('Auto refresh failed', { message: err.message || String(err) });
    });
  }, state.autoRefreshSeconds * 1000);
  syncAutoRefreshUi();
}

function renderSnapshots() {
  const tbody = el('snapshotTable');
  const summary = el('snapshotSummary');
  tbody.innerHTML = '';
  if (!state.snapshots.length) {
    summary.textContent = 'No snapshots saved';
    return;
  }
  summary.textContent = `saved=${state.snapshots.length} | latest=${state.snapshots[0].name}`;
  for (const item of state.snapshots) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${item.name}</td>
      <td>${item.config?.pair || ''}</td>
      <td>${item.config?.direction || ''}</td>
      <td>${Number(item.weight || 1).toFixed(2)}</td>
      <td>${new Date(item.saved_at).toLocaleString()}</td>
      <td>
        <div class="action-row">
          <button type="button" data-action="load-snapshot" data-id="${item.id}">Load</button>
          <button type="button" data-action="delete-snapshot" data-id="${item.id}">Delete</button>
        </div>
      </td>`;
    tbody.appendChild(tr);
  }
}

function applyConfigToEditor(config) {
  if (!config) return;
  setValue('editorPair', config.pair || '');
  setValue('editorDirection', config.direction || 'long');
  setValue('editorNotes', config.notes || '');
  setValue('editorBaseOrder', config.entry?.base_order_usdt ?? 25);
  setValue('editorLeverage', config.entry?.leverage ?? 5);
  setValue('editorStartOrderType', config.entry?.start_order_type || 'market');
  setValue('editorMarginMode', config.entry?.margin_mode || 'cross');
  setValue('editorTakeProfit', config.exit?.tp_pct ?? 1.0);
  setValue('editorStopLoss', config.exit?.stop_loss_pct ?? 0);
  setValue('editorAveragingEnabled', String(config.averaging?.enabled ?? true));
  setValue('editorMaxOrders', config.averaging?.max_orders ?? 3);
  setValue('editorFirstDeviation', config.averaging?.first_deviation_pct ?? 3);
  setValue('editorDeviationMultiplier', config.averaging?.deviation_multiplier ?? 1.5);
  setValue('editorSafetyOrderSize', config.averaging?.safety_order_size ?? 25);
  setValue('editorOrderSizeMultiplier', config.averaging?.order_size_multiplier ?? 1);
  syncTradeStartConditionsFromConfig(config);
}

function saveSnapshot() {
  const name = el('snapshotName').value.trim() || `${editorConfigPayload().pair || 'snapshot'} ${new Date().toLocaleTimeString()}`;
  const snapshot = {
    id: crypto.randomUUID(),
    name,
    saved_at: new Date().toISOString(),
    config: editorConfigPayload(),
    selected_plan_id: state.selectedPlanId,
    weight: batchRiskWeight(),
    simulation_path: el('simulationPath').value.trim(),
    simulation_preset: el('simulationPreset').value,
    batch_results: state.batchSimulationResults,
  };
  state.snapshots = [snapshot, ...state.snapshots].slice(0, 20);
  persistSnapshots();
  renderSnapshots();
  setPlanEditorTab('snapshots');
  log('Snapshot saved', { name: snapshot.name });
}

function loadSnapshot(snapshotId) {
  const snapshot = state.snapshots.find((item) => item.id === snapshotId);
  if (!snapshot) return;
  state.selectedPlanId = snapshot.selected_plan_id || null;
  applyConfigToEditor(snapshot.config);
  setValue('batchRiskWeight', snapshot.weight ?? 1);
  setValue('simulationPreset', snapshot.simulation_preset || 'drop_bounce');
  setValue('simulationPath', snapshot.simulation_path || '');
  state.batchSimulationResults = snapshot.batch_results || [];
  setSelectedPlanMeta(currentSelectedPlan());
  renderBatchSimulation(state.batchSimulationResults);
  if (state.batchSimulationResults.length) {
    setPlanEditorTab('scenarios');
  }
  if (state.batchSimulationResults[0]) {
    const first = state.batchSimulationResults[0];
    renderSimulation(first);
  } else {
    renderSimulation(null);
  }
  log('Snapshot loaded', { name: snapshot.name });
}

function deleteSnapshot(snapshotId) {
  state.snapshots = state.snapshots.filter((item) => item.id !== snapshotId);
  persistSnapshots();
  renderSnapshots();
  log('Snapshot deleted', { snapshotId });
}

function exportSnapshots() {
  const payload = JSON.stringify(snapshotExportPayload(), null, 2);
  const blob = new Blob([payload], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `pinebitz-snapshots-${new Date().toISOString().replaceAll(':', '-')}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  log('Snapshots exported', { count: state.snapshots.length });
}

async function importSnapshotsFromFile(file) {
  if (!file) return;
  const text = await file.text();
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    log('Snapshot import failed', { reason: 'invalid json' });
    return;
  }
  const imported = Array.isArray(parsed) ? parsed : parsed.snapshots;
  if (!Array.isArray(imported)) {
    log('Snapshot import failed', { reason: 'missing snapshots array' });
    return;
  }
  const normalized = imported
    .filter((item) => item && typeof item === 'object')
    .map((item) => ({
      id: item.id || crypto.randomUUID(),
      name: item.name || `imported ${new Date().toLocaleTimeString()}`,
      saved_at: item.saved_at || new Date().toISOString(),
      config: item.config || {},
      selected_plan_id: item.selected_plan_id || null,
      weight: Number(item.weight || 1),
      simulation_path: item.simulation_path || '',
      simulation_preset: item.simulation_preset || 'drop_bounce',
      batch_results: Array.isArray(item.batch_results) ? item.batch_results : [],
    }));
  state.snapshots = [...normalized, ...state.snapshots]
    .filter((item, idx, arr) => arr.findIndex((other) => other.id === item.id) === idx)
    .slice(0, 50);
  persistSnapshots();
  renderSnapshots();
  setPlanEditorTab('snapshots');
  log('Snapshots imported', { count: normalized.length });
}

async function api(path, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    'X-Owner-Key': state.ownerKey,
    ...(options.headers || {}),
  };
  const res = await fetch(path, { ...options, headers });
  const isJson = (res.headers.get('content-type') || '').includes('application/json');
  const body = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    log(`API error ${res.status} ${path}`, body);
    throw new Error(body.message || body.code || `HTTP ${res.status}`);
  }
  return body;
}

async function refreshHealth() {
  try {
    const live = await fetch('/health/live').then((r) => r.json());
    el('healthLive').textContent = live.status;
    el('healthLive').className = `stat-value ${live.status === 'live' ? 'ok' : 'bad'}`;
  } catch {
    el('healthLive').textContent = 'error';
    el('healthLive').className = 'stat-value bad';
  }

  try {
    const ready = await fetch('/health/ready').then((r) => r.json());
    el('healthReady').textContent = ready.status;
    el('healthReady').className = `stat-value ${ready.status === 'ready' ? 'ok' : 'bad'}`;
  } catch {
    el('healthReady').textContent = 'error';
    el('healthReady').className = 'stat-value bad';
  }
}

function renderConnections() {
  const select = el('planConnectionId');
  const cardsHost = el('connectionsCards');
  const summary = el('connectionsCardSummary');
  if (cardsHost) cardsHost.innerHTML = '';
  select.innerHTML = '';
  let visibleCount = 0;

  for (const item of state.connections) {
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = `${item.label} (${item.market_lane})`;
    select.appendChild(option);
    const isDemo = isDemoEntity(item.label, item.environment, item.credential_ref);
    if (!shouldIncludeEntity(isDemo)) continue;
    visibleCount += 1;

    if (cardsHost) {
      const card = document.createElement('article');
      card.className = 'account-card';
      const statusText = item.status?.value || item.status || '';
      card.innerHTML = `
        <div class="account-card-head">
          <strong>${item.label}</strong>
          ${entityBadge(isDemo)}
        </div>
        <div class="account-card-sub">${item.venue} • ${item.market_lane} • ${item.environment || '-'}</div>
        <div class="account-card-sub">status: ${statusText}</div>
        <div class="account-card-bar"><span></span></div>
        <div class="account-card-actions">
          <button type="button" class="ghost" data-action="open-bots-for-connection" data-id="${item.id}" data-venue="${item.venue}">Open Bots</button>
          <button type="button" class="ghost" data-action="create-bot-from-connection" data-id="${item.id}">Create Bot</button>
          <button type="button" class="ghost" data-action="pause-connection" data-id="${item.id}">Pause</button>
          <button type="button" class="ghost" data-action="delete-connection" data-id="${item.id}">Delete</button>
        </div>`;
      cardsHost.appendChild(card);
    }
  }
  if (summary) {
    summary.textContent = `accounts=${visibleCount}`;
  }
}

function renderPlans() {
  const tbody = el('plansTable');
  const editorSelect = el('editorPlanId');
  const signalPlanSelect = el('testSignalPlanId');
  tbody.innerHTML = '';
  editorSelect.innerHTML = '<option value="">Select a bot plan</option>';
  if (signalPlanSelect) {
    signalPlanSelect.innerHTML = '<option value="">auto-match by owner + pair</option>';
  }
  for (const item of state.plans) {
    const pair = item.config_json?.pair || '';
    const direction = item.config_json?.direction || '';
    const isDemo = isDemoEntity(item.name, pair);
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = `${item.name} (${pair || 'no pair'})`;
    editorSelect.appendChild(option);
    if (signalPlanSelect) {
      const signalOption = document.createElement('option');
      signalOption.value = item.id;
      signalOption.textContent = `${item.name} (${pair || 'no pair'})`;
      signalPlanSelect.appendChild(signalOption);
    }
    if (!shouldIncludeEntity(isDemo)) continue;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${item.name} ${entityBadge(isDemo)}</td>
      <td>${badge(item.status)}</td>
      <td>${badge(item.enabled)}</td>
      <td>${pair}</td>
      <td>${direction}</td>
      <td>
        <div class="action-row">
          <button type="button" data-action="edit-plan" data-id="${item.id}">Edit</button>
          <button type="button" data-action="toggle-plan" data-id="${item.id}" data-enabled="${item.enabled}">${item.enabled ? 'Disable' : 'Enable'}</button>
          <button type="button" data-action="delete-plan" data-id="${item.id}">Delete</button>
        </div>
      </td>`;
    tbody.appendChild(tr);
  }
  if (state.selectedPlanId) {
    editorSelect.value = state.selectedPlanId;
    if (signalPlanSelect) {
      signalPlanSelect.value = state.selectedPlanId;
    }
    const selected = currentSelectedPlan();
    fillPlanEditor(selected);
  }
}

function renderBots() {
  const tbody = el('botsTable');
  tbody.innerHTML = '';
  let visibleCount = 0;
  for (const item of state.bots) {
    const isDemo = isDemoEntity(item.plan_name, item.connection_label, item.pair);
    if (!shouldIncludeEntity(isDemo)) continue;
    visibleCount += 1;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${item.plan_name} ${entityBadge(isDemo)}</td>
      <td>${item.connection_label}</td>
      <td>${item.venue}</td>
      <td>${item.market_lane}</td>
      <td>${item.instrument_kind || ''}</td>
      <td>${badge(item.enabled)}</td>`;
    tbody.appendChild(tr);
  }
  el('botCount').textContent = String(visibleCount);
}

async function loadConnections() {
  const data = await api('/connections?limit=200&offset=0');
  state.connections = data.items;
  renderConnections();
}

async function loadPlans() {
  const data = await api('/bot-plans?limit=200&offset=0');
  state.plans = data.items;
  renderPlans();
}

async function loadBots() {
  const params = new URLSearchParams();
  const lane = el('botMarketLaneFilter').value.trim();
  const venue = el('botVenueFilter').value.trim();
  const enabled = el('botEnabledFilter').value.trim();
  if (lane) params.set('market_lane', lane);
  if (venue) params.set('venue', venue);
  if (enabled) params.set('enabled', enabled);
  const qs = params.toString() ? `?${params.toString()}` : '';
  state.bots = await api(`/dashboard/bots${qs}`);
  renderBots();
}

async function refreshAll() {
  await refreshHealth();
  await loadConnections();
  await loadPlans();
  await loadBots();
  await refreshTradingState();
  log('Dashboard refreshed');
}

async function createConnection(ev) {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const payload = Object.fromEntries(fd.entries());
  if (state.currentPage === 'exchanges' && state.currentMode === 'live' && /testnet/i.test(String(payload.environment || ''))) {
    payload.environment = 'mainnet';
  }
  await api('/connections', { method: 'POST', body: JSON.stringify(payload) });
  log('Connection created', payload);
  ev.target.reset();
  applyConnectionFormDefaults();
  await refreshAll();
}

async function createPlan(ev) {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const payload = Object.fromEntries(fd.entries());
  payload.enabled = payload.enabled === 'true';
  payload.config_json = {
    pair: payload.pair,
    direction: payload.direction,
    notes: '',
    entry: { base_order_usdt: 25 },
    averaging: { enabled: true, max_orders: 3 },
    exit: { tp_pct: 1.0, stop_loss_pct: 0 },
    trade_start_conditions: { enabled: false, conditions: [] },
  };
  delete payload.pair;
  delete payload.direction;
  await api('/bot-plans', { method: 'POST', body: JSON.stringify(payload) });
  log('Plan created', payload);
  await refreshAll();
}

async function savePlanEditor(ev) {
  ev.preventDefault();
  const planId = el('editorPlanId').value;
  if (!planId) {
    log('No bot plan selected for editing');
    return;
  }
  const payload = {
    name: el('editorName').value.trim(),
    instrument_kind: el('editorInstrumentKind').value,
    status: el('editorStatus').value,
    enabled: el('editorEnabled').value === 'true',
    plan_version: Number(el('editorPlanVersion').value || 1),
    config_json: editorConfigPayload(),
  };
  await api(`/bot-plans/${planId}`, { method: 'PATCH', body: JSON.stringify(payload) });
  state.selectedPlanId = planId;
  log('Plan updated from editor', { planId, payload });
  await refreshAll();
}

async function previewPlan() {
  const referencePrice = Number(el('previewReferencePrice').value || 1);
  const result = await api(`/dca/preview?reference_price=${encodeURIComponent(referencePrice)}`, {
    method: 'POST',
    body: JSON.stringify(editorConfigPayload()),
  });
  renderPreview(result);
  log('Preview updated', result.summary);
}

function buildPresetPath(preset) {
  const ref = Number(el('previewReferencePrice').value || 1);
  const cfg = editorConfigPayload();
  const baseDev = Number(cfg.averaging.first_deviation_pct || 3) / 100;
  const tpPct = Number(cfg.exit.tp_pct || 1) / 100;
  const slPct = Number(cfg.exit.stop_loss_pct || 0) / 100;
  const isLong = cfg.direction === 'long';
  let points;

  if (preset === 'trend_up') {
    points = isLong
      ? [ref, ref * 1.002, ref * 1.004, ref * (1 + tpPct * 1.2)]
      : [ref, ref * 1.01, ref * 1.02, ref * 1.03];
  } else if (preset === 'trend_down') {
    points = isLong
      ? [ref, ref * (1 - baseDev * 0.6), ref * (1 - baseDev), ref * (1 - baseDev * 1.8)]
      : [ref, ref * 0.998, ref * 0.996, ref * (1 - tpPct * 1.2)];
  } else if (preset === 'stop_loss_hit') {
    const shock = Math.max(slPct || baseDev * 2, baseDev * 1.5);
    points = isLong
      ? [ref, ref * (1 - baseDev * 0.5), ref * (1 - baseDev), ref * (1 - shock * 1.05)]
      : [ref, ref * (1 + baseDev * 0.5), ref * (1 + baseDev), ref * (1 + shock * 1.05)];
  } else {
    points = isLong
      ? [
          ref,
          ref * (1 - baseDev * 0.4),
          ref * (1 - baseDev),
          ref * (1 - baseDev * 1.8),
          ref * (1 - baseDev * 0.8),
          ref * (1 + tpPct * 1.2),
        ]
      : [
          ref,
          ref * (1 + baseDev * 0.4),
          ref * (1 + baseDev),
          ref * (1 + baseDev * 1.8),
          ref * (1 + baseDev * 0.8),
          ref * (1 - tpPct * 1.2),
        ];
  }
  return points.map((v) => Number(v.toFixed(6)));
}

function generateSamplePath() {
  const preset = el('simulationPreset').value;
  const points = buildPresetPath(preset);
  el('simulationPath').value = points.map((v) => v.toFixed(6)).join(',');
  log('Sample price path generated', { preset, points });
}

async function runSimulation() {
  const path = el('simulationPath').value.trim();
  if (!path) {
    log('Simulation path is empty');
    return;
  }
  const result = await api(`/dca/simulate?price_path=${encodeURIComponent(path)}`, {
    method: 'POST',
    body: JSON.stringify(editorConfigPayload()),
  });
  renderSimulation(result);
  log('Simulation completed', result.summary);
}

async function runBatchSimulation() {
  const presets = ['drop_bounce', 'trend_up', 'trend_down', 'stop_loss_hit'];
  const results = [];
  for (const preset of presets) {
    const pathPoints = buildPresetPath(preset);
    const path = pathPoints.join(',');
    const result = await api(`/dca/simulate?price_path=${encodeURIComponent(path)}`, {
      method: 'POST',
      body: JSON.stringify(editorConfigPayload()),
    });
    const equity = buildEquityCurve(result, pathPoints);
    results.push({ preset, max_drawdown: equity.maxDrawdown, ...result });
  }
  state.batchSimulationResults = results;
  renderBatchSimulation(results);
  setPlanEditorTab('scenarios');
  if (results[0]) {
    el('simulationPreset').value = results[0].preset;
    el('simulationPath').value = buildPresetPath(results[0].preset).map((v) => v.toFixed(6)).join(',');
    renderSimulation(results[0]);
  }
  log('Batch simulation completed', results.map((item) => ({ preset: item.preset, reason: item.summary.close_reason, roi: item.summary.roi_pct })));
}

function selectedSignalPlan() {
  const planId = el('testSignalPlanId')?.value?.trim();
  if (!planId) return null;
  return state.plans.find((item) => item.id === planId) || null;
}

function buildTestSignalPayload(sideOverride = null) {
  const symbol = el('testSignalSymbol').value.trim().toUpperCase();
  if (!symbol) {
    throw new Error('symbol is required');
  }
  const selectedPlan = selectedSignalPlan();
  const payload = {
    symbol,
    side: sideOverride || el('testSignalSide').value,
    timeframe: el('testSignalTimeframe').value.trim() || undefined,
    strategy: el('testSignalStrategy').value.trim() || 'dashboard-test',
    price: Number(el('testSignalPrice').value || 0) || undefined,
    volume: Number(el('testSignalVolume').value || 0) || undefined,
    risk_pct: Number(el('testSignalRiskPct').value || 0) || undefined,
    owner_key: state.ownerKey,
  };
  if (selectedPlan) {
    payload.plan_id = selectedPlan.id;
    payload.plan_name = selectedPlan.name;
  }
  return payload;
}

async function submitTestSignal(sideOverride = null) {
  const payload = buildTestSignalPayload(sideOverride);
  const headers = {};
  const webhookSecret = el('testSignalWebhookSecret').value.trim();
  if (webhookSecret) {
    headers['X-Webhook-Secret'] = webhookSecret;
  }
  const signal = await api('/signals/tradingview/webhook', {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  });
  log('Test signal sent', {
    signal_id: signal.signal_id,
    symbol: signal.symbol,
    side: signal.side,
    plan_id: payload.plan_id || null,
  });
  const afterSendMode = el('testSignalAfterSend')?.value || 'none';
  if (afterSendMode === 'dispatch') {
    const jobsData = await api('/execution/jobs?limit=200&offset=0');
    const job = (jobsData.items || []).find((item) => item.signal_id === signal.signal_id);
    if (!job) {
      log('Auto dispatch skipped', { reason: 'job not found', signal_id: signal.signal_id });
    } else {
      const status = job.status?.value || job.status;
      if (status === 'queued' || status === 'approved') {
        await api(`/execution/jobs/${job.job_id}/dispatch`, { method: 'POST' });
        log('Auto dispatched', { job_id: job.job_id, signal_id: signal.signal_id });
      } else {
        log('Auto dispatch skipped', { job_id: job.job_id, status });
      }
    }
  }
  await refreshTradingState();
}

async function sendTestSignal(ev) {
  ev.preventDefault();
  try {
    await submitTestSignal();
  } catch (err) {
    log('Test signal failed', { reason: err.message || String(err) });
  }
}

async function quickSendSignal(side) {
  setValue('testSignalSide', side);
  try {
    await submitTestSignal(side);
  } catch (err) {
    log('Quick signal failed', { side, reason: err.message || String(err) });
  }
}

function selectPlan(planId) {
  state.selectedPlanId = planId || null;
  const item = currentSelectedPlan();
  fillPlanEditor(item);
  if (el('testSignalPlanId')) {
    el('testSignalPlanId').value = item?.id || '';
  }
  const pair = item?.config_json?.pair;
  if (pair && el('testSignalSymbol')) {
    el('testSignalSymbol').value = pair;
  }
}

async function handleTableAction(ev) {
  const btn = ev.target.closest('button[data-action]');
  if (!btn) return;
  const { action, id } = btn.dataset;
  if (action === 'delete-connection') {
    await api(`/connections/${id}`, { method: 'DELETE' });
    log('Connection deleted', { id });
  } else if (action === 'pause-connection') {
    await api(`/connections/${id}`, { method: 'PATCH', body: JSON.stringify({ status: 'paused' }) });
    log('Connection paused', { id });
  } else if (action === 'open-bots-for-connection') {
    const venue = btn.dataset.venue || '';
    setValue('botVenueFilter', venue);
    state.currentPage = 'dca';
    state.currentMode = 'all';
    localStorage.setItem('pinebitz.currentPage', state.currentPage);
    localStorage.setItem('pinebitz.currentMode', state.currentMode);
    syncHashFromState();
    applyPageMode();
    await loadBots();
    log('Opened bots for connection', { id, venue });
    return;
  } else if (action === 'create-bot-from-connection') {
    state.currentPage = 'dca';
    state.currentMode = 'all';
    localStorage.setItem('pinebitz.currentPage', state.currentPage);
    localStorage.setItem('pinebitz.currentMode', state.currentMode);
    syncHashFromState();
    applyPageMode();
    setValue('planConnectionId', id);
    const form = el('planForm');
    if (form) form.scrollIntoView({ behavior: 'smooth', block: 'start' });
    log('Prepare create bot from connection', { id });
    return;
  } else if (action === 'delete-plan') {
    await api(`/bot-plans/${id}`, { method: 'DELETE' });
    log('Plan deleted', { id });
  } else if (action === 'toggle-plan') {
    const enabled = btn.dataset.enabled === 'true';
    await api(`/bot-plans/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !enabled }) });
    log('Plan toggled', { id, enabled: !enabled });
  } else if (action === 'approve-job') {
    await api(`/execution/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ status: 'approved' }) });
    log('Job approved', { id });
    await refreshTradingState();
    return;
  } else if (action === 'reject-job') {
    await api(`/execution/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ status: 'rejected' }) });
    log('Job rejected', { id });
    await refreshTradingState();
    return;
  } else if (action === 'dispatch-job') {
    await api(`/execution/jobs/${id}/dispatch`, { method: 'POST' });
    log('Job dispatched', { id });
    await refreshTradingState();
    return;
  } else if (action === 'view-audit') {
    setValue('auditJobId', id);
    await loadExecutionAudit(id);
    log('Audit loaded', { job_id: id });
    return;
  } else if (action === 'edit-plan') {
    state.selectedPlanId = id;
    fillPlanEditor(currentSelectedPlan());
    log('Plan loaded into editor', { id });
    return;
  } else if (action === 'view-batch-simulation') {
    const item = state.batchSimulationResults.find((entry) => entry.preset === btn.dataset.preset);
    if (!item) return;
    el('simulationPreset').value = item.preset;
    el('simulationPath').value = buildPresetPath(item.preset).map((v) => v.toFixed(6)).join(',');
    renderSimulation(item);
    setPlanEditorTab('paper');
    log('Batch simulation loaded into detail view', { preset: item.preset });
    return;
  } else if (action === 'load-snapshot') {
    loadSnapshot(btn.dataset.id);
    return;
  } else if (action === 'delete-snapshot') {
    deleteSnapshot(btn.dataset.id);
    return;
  }
  await refreshAll();
}

function bindEvents() {
  el('saveOwnerBtn').addEventListener('click', async () => {
    state.ownerKey = el('ownerKey').value.trim();
    localStorage.setItem('pinebitz.ownerKey', state.ownerKey);
    log('Owner key updated');
    await refreshAll();
  });
  el('connectionForm').addEventListener('submit', createConnection);
  el('planForm').addEventListener('submit', createPlan);
  el('planEditorForm').addEventListener('submit', savePlanEditor);
  el('testSignalForm').addEventListener('submit', sendTestSignal);
  el('testSignalForm').addEventListener('click', async (ev) => {
    const btn = ev.target.closest('button[data-action="quick-signal"]');
    if (!btn) return;
    await quickSendSignal(btn.dataset.side);
  });
  el('previewPlanBtn').addEventListener('click', previewPlan);
  for (const btn of document.querySelectorAll('[data-plan-editor-tab]')) {
    btn.addEventListener('click', () => {
      setPlanEditorTab(btn.dataset.planEditorTab || 'edit');
    });
  }
  el('simulateAutoBtn').addEventListener('click', generateSamplePath);
  el('simulatePlanBtn').addEventListener('click', runSimulation);
  el('runBatchSimulationBtn').addEventListener('click', runBatchSimulation);
  el('saveSnapshotBtn').addEventListener('click', saveSnapshot);
  el('exportSnapshotsBtn').addEventListener('click', exportSnapshots);
  el('importSnapshotsInput').addEventListener('change', async (ev) => {
    const [file] = ev.target.files || [];
    await importSnapshotsFromFile(file);
    ev.target.value = '';
  });
  el('batchRiskWeight').addEventListener('input', () => renderBatchSimulation(state.batchSimulationResults));
  const connectionsCards = el('connectionsCards');
  if (connectionsCards) connectionsCards.addEventListener('click', handleTableAction);
  el('plansTable').addEventListener('click', handleTableAction);
  el('batchSimulationTable').addEventListener('click', handleTableAction);
  el('jobsTable').addEventListener('click', handleTableAction);
  el('snapshotTable').addEventListener('click', handleTableAction);
  el('refreshAllBtn').addEventListener('click', refreshAll);
  el('reloadPlansBtn').addEventListener('click', loadPlans);
  el('editorPlanId').addEventListener('change', (ev) => selectPlan(ev.target.value));
  el('resetPlanEditorBtn').addEventListener('click', () => fillPlanEditor(currentSelectedPlan()));
  bindTradeStartConditionsUi();
  const tradeStartEn = el('tradeStartEnabled');
  if (tradeStartEn) tradeStartEn.addEventListener('change', updateTradeStartConditionsDisabled);
  const tradeStartAdd = el('tradeStartAddBtn');
  if (tradeStartAdd) {
    tradeStartAdd.addEventListener('click', () => {
      const cur = collectTradeStartConditionsFromDom();
      cur.push({ kind: 'tv_webhook', timeframe: null, signal_value: null, params: defaultParamsForKind('tv_webhook') });
      renderTradeStartConditionsList(cur);
    });
  }
  el('clearLogBtn').addEventListener('click', () => { logBox().textContent = ''; });
  el('reloadSignalsBtn').addEventListener('click', loadSignals);
  el('reloadJobsBtn').addEventListener('click', loadExecutionJobs);
  el('autoRefreshSeconds').addEventListener('change', (ev) => {
    state.autoRefreshSeconds = Number(ev.target.value || 0);
    localStorage.setItem('pinebitz.autoRefreshSeconds', String(state.autoRefreshSeconds));
    startAutoRefresh();
    log('Auto refresh interval updated', { seconds: state.autoRefreshSeconds });
  });
  el('alertSoundEnabled').addEventListener('change', (ev) => {
    state.alertSoundEnabled = ev.target.value === 'true';
    localStorage.setItem('pinebitz.alertSoundEnabled', String(state.alertSoundEnabled));
    log('Alert sound updated', { enabled: state.alertSoundEnabled });
  });
  el('desktopNotifyEnabled').addEventListener('change', (ev) => {
    state.desktopNotifyEnabled = ev.target.value === 'true';
    localStorage.setItem('pinebitz.desktopNotifyEnabled', String(state.desktopNotifyEnabled));
    log('Desktop notification updated', { enabled: state.desktopNotifyEnabled });
  });
  el('requestNotifyPermissionBtn').addEventListener('click', requestDesktopNotificationPermission);
  el('toggleAutoRefreshBtn').addEventListener('click', () => {
    state.autoRefreshPaused = !state.autoRefreshPaused;
    startAutoRefresh();
    log('Auto refresh toggled', { paused: state.autoRefreshPaused });
  });
  el('dispatchAllQueuedBtn').addEventListener('click', dispatchAllQueuedJobs);
  el('clearTestQueueBtn').addEventListener('click', clearTestQueue);
  el('reloadRuntimeBtn').addEventListener('click', loadRuntimeGuardState);
  el('reloadPaperBtn').addEventListener('click', loadPaperPositions);
  el('markPaperAllBtn').addEventListener('click', markPaperAll);
  el('reloadAuditBtn').addEventListener('click', () => loadExecutionAudit(el('auditJobId').value.trim() || null));
  el('clearAuditFilterBtn').addEventListener('click', async () => {
    setValue('auditJobId', '');
    await loadExecutionAudit(null);
    log('Audit filter cleared');
  });
  el('botMarketLaneFilter').addEventListener('change', loadBots);
  el('botVenueFilter').addEventListener('change', loadBots);
  el('botEnabledFilter').addEventListener('change', loadBots);
  for (const btn of document.querySelectorAll('.nav-link[data-nav-page]')) {
    btn.addEventListener('click', () => {
      state.currentPage = btn.dataset.navPage || 'execution';
      state.currentMode = btn.dataset.navMode || 'all';
      localStorage.setItem('pinebitz.currentPage', state.currentPage);
      localStorage.setItem('pinebitz.currentMode', state.currentMode);
      syncHashFromState();
      applyPageMode();
      applyConnectionFormDefaults();
      renderConnections();
      renderPlans();
      renderBots();
      log('Workspace switched', { page: state.currentPage, mode: state.currentMode });
    });
  }
  window.addEventListener('hashchange', () => {
    if (!applyStateFromHash()) return;
    normalizePageMode();
    localStorage.setItem('pinebitz.currentPage', state.currentPage);
    localStorage.setItem('pinebitz.currentMode', state.currentMode);
    applyPageMode();
    renderConnections();
    renderPlans();
    renderBots();
  });
}

function normalizePageMode() {
  const allowedPages = new Set(['exchanges', 'dca', 'execution']);
  const allowedModes = new Set(['all', 'live', 'demo']);
  if (!allowedPages.has(state.currentPage)) state.currentPage = 'execution';
  if (!allowedModes.has(state.currentMode)) state.currentMode = 'all';
}

async function boot() {
  el('ownerKey').value = state.ownerKey;
  applyStateFromHash();
  normalizePageMode();
  setValue('autoRefreshSeconds', state.autoRefreshSeconds > 0 ? String(state.autoRefreshSeconds) : '0');
  setValue('alertSoundEnabled', String(state.alertSoundEnabled));
  setValue('desktopNotifyEnabled', String(state.desktopNotifyEnabled));
  updateNotifyPermissionUi();
  bindEvents();
  fillPlanEditor(null);
  renderSnapshots();
  applyConnectionFormDefaults();
  syncHashFromState();
  applyPageMode();
  await refreshAll();
  startAutoRefresh();
}

boot().catch((err) => {
  log('Dashboard boot failed', { message: err.message });
});
