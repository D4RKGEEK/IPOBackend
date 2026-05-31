/* ============================================================================
   Shared UI helpers — nav, footer, formatters, status pills, the IPO card,
   and the lifecycle stepper. Exposed as window.UI.
   ========================================================================== */
(function () {
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  const STATUS = {
    drhp_filed:    { label: 'DRHP filed',    pill: 'pill-amber',  dot: 'bg-amber-500',   step: 0 },
    sebi_approved: { label: 'SEBI approved', pill: 'pill-blue',   dot: 'bg-blue-500',    step: 1 },
    rhp_filed:     { label: 'RHP filed',     pill: 'pill-indigo', dot: 'bg-indigo-500',  step: 2 },
    fp_filed:      { label: 'Prospectus',    pill: 'pill-indigo', dot: 'bg-indigo-500',  step: 3 },
    upcoming:      { label: 'Opening soon',  pill: 'pill-purple', dot: 'bg-purple-500',  step: 4 },
    open:          { label: 'Open now',      pill: 'pill-green',  dot: 'bg-emerald-500', step: 5, live: true },
    closed:        { label: 'Closed',        pill: 'pill-red',    dot: 'bg-rose-500',    step: 6 },
    listed:        { label: 'Listed',        pill: 'pill-teal',   dot: 'bg-teal-500',    step: 7 },
    unknown:       { label: 'Status unknown',pill: 'pill-gray',   dot: 'bg-slate-400',   step: 0 },
  };

  const statusMeta = s => STATUS[s] || STATUS.unknown;

  function fmtDate(iso, withYear) {
    if (!iso) return '—';
    const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
    return `${d} ${MONTHS[m - 1]}${withYear ? ' ' + y : ''}`;
  }
  function relTime(iso) {
    if (!iso) return '';
    const then = new Date(iso).getTime();
    const now = new Date(window.TODAY + 'T08:00:00Z').getTime();
    const diff = Math.max(0, now - then), h = diff / 3.6e6;
    if (h < 1) return Math.max(1, Math.round(diff / 6e4)) + 'm ago';
    if (h < 24) return Math.round(h) + 'h ago';
    return Math.round(h / 24) + 'd ago';
  }
  const inr = n => '₹' + Number(n).toLocaleString('en-IN');
  function fmtCr(cr) {
    if (cr == null) return '—';
    if (cr >= 1000) return '₹' + (cr / 1000).toFixed(1).replace(/\.0$/, '') + 'k Cr';
    return '₹' + cr.toLocaleString('en-IN') + ' Cr';
  }
  function priceBand(pb) {
    if (!pb) return null;
    const [a, b] = pb.split('-');
    return b ? `₹${a} – ${b}` : `₹${a}`;
  }
  function minInvest(ipo) {
    if (!ipo.price_band || !ipo.lot_size) return null;
    const lo = Number(ipo.price_band.split('-')[0]);
    return lo * ipo.lot_size;
  }
  // whole days from a -> b (date-only)
  function daysB(a, b) {
    if (!a || !b) return null;
    return Math.round((new Date(b.slice(0,10)) - new Date(a.slice(0,10))) / 864e5);
  }
  const clamp = (n, lo = 0, hi = 100) => Math.max(lo, Math.min(hi, n));

  // tiny inline-SVG sparkline from a numeric series
  function sparkline(vals, opts = {}) {
    const { w = 132, h = 38, stroke = '#4F46E5', fill = 'rgba(99,102,241,.10)' } = opts;
    if (!vals || vals.length < 2) return '';
    const min = Math.min(...vals), max = Math.max(...vals), rng = (max - min) || 1;
    const pts = vals.map((v, i) => [ (i / (vals.length - 1)) * w, (h - 3) - ((v - min) / rng) * (h - 6) ]);
    const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
    const area = `M0 ${h} L` + pts.map(p => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' L') + ` L${w} ${h} Z`;
    const last = pts[pts.length - 1];
    return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" preserveAspectRatio="none">
      <path d="${area}" fill="${fill}"/>
      <path d="${line}" fill="none" stroke="${stroke}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.6" fill="${stroke}"/>
    </svg>`;
  }
  const platformBadge = p =>
    p === 'SME'
      ? `<span class="badge-mono text-violet-600 border-violet-200 bg-violet-50">SME</span>`
      : `<span class="badge-mono text-slate-600 border-slate-300 bg-slate-100">MAINBOARD</span>`;

  function statusPill(status) {
    const m = statusMeta(status);
    return `<span class="pill ${m.pill}"><span class="pill-dot ${m.dot} ${m.live ? 'animate-pulse-ring' : ''}"></span>${m.label}</span>`;
  }

  function trustNote(ipo) {
    if (ipo.publish_status === 'needs_review')
      return `<span class="pill pill-amber" title="Data verification pending">⚠ Review pending</span>`;
    if (ipo.confidence_score != null)
      return `<span class="text-[11px] text-textMuted mono">conf ${(ipo.confidence_score * 100).toFixed(0)}%</span>`;
    return '';
  }

  // GMP display helpers
  function gmpColor(trend) {
    return trend === 'up' ? 'text-success-600' : trend === 'down' ? 'text-danger-600' : 'text-warning-600';
  }
  function gmpArrow(trend) {
    return trend === 'up' ? '▲' : trend === 'down' ? '▼' : '→';
  }
  function gmpTile(gmp) {
    if (!gmp) return `<div class="rounded-lg bg-surfaceAlt border border-borderSoft px-2.5 py-2 flex items-center justify-between gap-2">
      <span class="text-[11px] text-textMuted">GMP</span><span class="badge-mono">soon</span></div>`;
    const pct = gmp.pct.toFixed(1);
    return `<div class="rounded-lg bg-surfaceAlt border border-borderSoft px-2.5 py-2 flex items-center justify-between gap-2">
      <span class="text-[11px] text-textMuted font-medium">GMP</span>
      <span class="mono text-[12px] font-semibold ${gmpColor(gmp.trend)}">
        ${gmpArrow(gmp.trend)} ₹${gmp.price} <span class="text-[10px]">(${pct}%)</span>
      </span></div>`;
  }
  function subsTile(sub) {
    if (!sub) return `<div class="rounded-lg bg-surfaceAlt border border-borderSoft px-2.5 py-2 flex items-center justify-between gap-2">
      <span class="text-[11px] text-textMuted">Subscribed</span><span class="badge-mono">soon</span></div>`;
    const cls = sub.total >= 5 ? 'text-success-600' : sub.total >= 1 ? 'text-primary-600' : 'text-warning-600';
    return `<div class="rounded-lg bg-surfaceAlt border border-borderSoft px-2.5 py-2 flex items-center justify-between gap-2">
      <span class="text-[11px] text-textMuted font-medium">Day ${sub.day} subs</span>
      <span class="mono text-[12px] font-semibold ${cls}">${sub.total.toFixed(2)}x</span>
    </div>`;
  }

  // small "coming soon" stat tile used on cards
  function softStat(label) {
    return `<div class="rounded-lg bg-surfaceAlt border border-borderSoft px-2.5 py-2 flex items-center justify-between">
      <span class="text-[11px] text-textMuted">${label}</span><span class="badge-mono">soon</span></div>`;
  }

  /* the canonical IPO card — status-adaptive, used on homepage + list */
  function ipoCard(ipo) {
    const m = statusMeta(ipo.status);
    const band = priceBand(ipo.price_band);
    const min = minInvest(ipo);
    const TODAY = window.TODAY;
    let context = '';

    if (ipo.status === 'open') {
      const total = daysB(ipo.dates.open, ipo.dates.close) || 1;
      const left = Math.max(0, daysB(TODAY, ipo.dates.close));
      const pct = clamp(((total - left) / total) * 100);
      context = `
        <div class="flex items-center justify-between mb-2">
          <span class="text-[12px] text-textSecondary font-medium num">${fmtDate(ipo.dates.open)} – ${fmtDate(ipo.dates.close)}</span>
          <span class="inline-flex items-center gap-1.5 text-[11px] font-semibold text-emerald-700">
            <span class="pill-dot bg-emerald-500 animate-pulse-ring"></span>${left === 0 ? 'Last day' : left + 'd left'}</span>
        </div>
        <div class="h-1.5 rounded-full bg-slate-100 overflow-hidden"><div class="h-full bg-emerald-500 rounded-full" style="width:${pct}%"></div></div>
        <div class="mt-3 grid grid-cols-2 gap-2">${gmpTile(ipo.gmp)}${subsTile(ipo.subscription)}</div>`;
    } else if (ipo.status === 'upcoming') {
      const left = Math.max(0, daysB(TODAY, ipo.dates.open));
      context = `
        <div class="flex items-center justify-between">
          <span class="text-[12px] text-textSecondary font-medium num">Opens ${fmtDate(ipo.dates.open, true)}</span>
          <span class="pill pill-purple">${left === 0 ? 'Opens today' : 'in ' + left + 'd'}</span>
        </div>
        ${ipo.gmp ? `<div class="mt-3">${gmpTile(ipo.gmp)}</div>` : `<div class="mt-3 grid grid-cols-2 gap-2">${softStat('GMP')}${softStat('Subscribed')}</div>`}`;
    } else if (ipo.status === 'listed') {
      const g = ipo.listing_gain_pct;
      context = `
        <div class="flex items-end justify-between">
          <div>
            <div class="kpi-label">Listing gain</div>
            <div class="mono text-[18px] font-bold num ${g >= 0 ? 'text-success-600' : 'text-danger-600'}">${g >= 0 ? '▲' : '▼'} ${g >= 0 ? '+' : ''}${g}%</div>
          </div>
          <div class="text-right"><div class="kpi-label">Listed</div><div class="mono text-[13px] text-textSecondary num">${fmtDate(ipo.dates.listing, true)}</div></div>
        </div>`;
    } else if (ipo.status === 'closed') {
      context = `
        <div class="flex items-center justify-between text-[12px]">
          <span class="text-textSecondary num">Bidding closed ${fmtDate(ipo.dates.close)}</span>
          <span class="pill pill-gray">Allotment ${fmtDate(ipo.dates.allotment)}</span>
        </div>`;
    } else {
      context = `
        <div class="flex items-center justify-between text-[12px]">
          <span class="text-textSecondary num">${m.label} · ${fmtDate(ipo.dates.rhp_filed || ipo.dates.drhp_filed, true)}</span>
          <span class="text-textMuted">Price band awaited</span>
        </div>`;
    }

    return `
    <a href="ipo.html?id=${ipo.id}" class="group block card hover-lift p-4 animate-fade-up">
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-2 min-w-0">
          ${platformBadge(ipo.platform)}
          <span class="text-[11px] text-textMuted truncate">${ipo.sector || ''}</span>
        </div>
        ${statusPill(ipo.status)}
      </div>
      <h3 class="mt-2.5 font-semibold text-[15.5px] text-text leading-snug truncate group-hover:text-primary-700 transition-colors">${ipo.company_name}</h3>

      <div class="mt-3 flex items-end justify-between gap-3">
        <div>
          <div class="kpi-label">Price band</div>
          <div class="mono text-[19px] font-semibold num ${band ? 'text-text' : 'text-slate-400'}">${band || 'TBA'}</div>
        </div>
        <div class="text-right">
          <div class="kpi-label">Min invest</div>
          <div class="mono text-[15px] font-semibold num ${min ? 'text-text' : 'text-slate-400'}">${min ? inr(min) : 'TBA'}</div>
        </div>
      </div>
      <div class="mt-1.5 text-[12px] text-textMuted num">${ipo.lot_size ? 'Lot ' + ipo.lot_size + ' sh' : 'Lot TBA'}${ipo.issue_size_cr ? ' · ' + fmtCr(ipo.issue_size_cr) + ' issue' : ''}</div>

      <div class="mt-3.5 pt-3.5 divider">${context}</div>
    </a>`;
  }

  /* compact row for "DRHP filed this month" lists */
  function ipoRow(ipo) {
    return `
    <a href="ipo.html?id=${ipo.id}" class="flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg hover:bg-slate-50 transition-colors">
      <div class="min-w-0 flex items-center gap-2.5">
        ${platformBadge(ipo.platform)}
        <span class="truncate text-[13.5px] text-text">${ipo.company_name}</span>
      </div>
      <span class="shrink-0 text-[12px] text-textMuted mono">${fmtDate(ipo.dates.drhp_filed, true)}</span>
    </a>`;
  }

  /* lifecycle stepper for the detail page */
  function lifecycle(status, dates) {
    const steps = [
      { key: 'drhp_filed',  label: 'DRHP',  sub: 'filed',    date: dates.drhp_filed },
      { key: 'sebi_approved', label: 'SEBI', sub: 'approved', date: dates.rhp_filed ? dates.drhp_filed : null },
      { key: 'rhp_filed',   label: 'RHP',   sub: 'filed',    date: dates.rhp_filed },
      { key: 'fp_filed',    label: 'FP',    sub: 'filed',    date: dates.fp_filed },
      { key: 'open',        label: 'Open',  sub: 'bidding',  date: dates.open },
      { key: 'closed',      label: 'Close', sub: 'bidding',  date: dates.close },
      { key: 'listed',      label: 'Listed',sub: '',         date: dates.listing },
    ];
    const cur = statusMeta(status).step;
    return `<div class="flex items-center" style="min-width:420px">${steps.map((s, i) => {
      const done = i < cur, active = i === cur;
      const dotCls = active ? 'bg-primary-500 ring-4 ring-primary-200 animate-pulse-ring'
        : done ? 'bg-primary-500' : 'bg-slate-100 border border-slate-300';
      const lineCls = i < cur ? 'bg-primary-500' : 'bg-border';
      return `
        <div class="flex flex-col items-center text-center shrink-0" style="min-width:48px">
          <div class="w-2.5 h-2.5 rounded-full ${dotCls}"></div>
          <div class="mt-1.5 text-[10px] font-semibold ${active ? 'text-primary-600' : done ? 'text-text' : 'text-slate-400'} leading-tight">${s.label}</div>
          <div class="text-[9px] text-slate-400 leading-tight">${s.date ? fmtDate(s.date) : s.sub}</div>
        </div>
        ${i < steps.length - 1 ? `<div class="h-0.5 flex-1 ${lineCls} mx-0.5 rounded-full" style="min-width:10px"></div>` : ''}`;
    }).join('')}</div>`;
  }

  /* ------- chrome: nav + footer ----------------------------------------- */
  const NAV = [
    { key: 'home', label: 'Home', href: 'index.html' },
    { key: 'all', label: 'All IPOs', href: 'ipos.html' },
    { key: 'open', label: 'Open', href: 'ipos.html?status=open' },
    { key: 'upcoming', label: 'Upcoming', href: 'ipos.html?status=upcoming' },
    { key: 'listed', label: 'Listed', href: 'ipos.html?status=listed' },
    { key: 'news', label: 'News', href: 'news.html' },
    { key: 'about', label: 'About', href: 'about.html' },
  ];

  function navHTML(active) {
    const links = NAV.map(n =>
      `<a href="${n.href}" class="nav-link ${n.key === active ? 'active' : ''}">${n.label}</a>`).join('');
    const mlinks = NAV.map(n =>
      `<a href="${n.href}" class="block px-3 py-2.5 rounded-lg text-textSecondary hover:bg-slate-50 hover:text-text">${n.label}</a>`).join('');
    return `
    <div class="sticky top-0 z-40 border-b border-border bg-surface/90 backdrop-blur-xl">
      <div class="container-app h-14 flex items-center justify-between gap-3">
        <a href="index.html" class="flex items-center gap-2 shrink-0">
          <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-400 to-primary-600 flex items-center justify-center shadow-sm">
            <span class="text-white font-extrabold text-xs tracking-tight">IPO</span>
          </div>
          <div class="leading-none hidden xs:block">
            <div class="font-bold text-[14px] tracking-tight">IPO<span class="text-primary-600">Radar</span></div>
          </div>
        </a>
        <nav class="hidden md:flex items-center gap-0.5 flex-1 justify-center">${links}</nav>
        <div class="flex items-center gap-1.5 shrink-0">
          <!-- search icon — always visible on mobile -->
          <button class="tap flex items-center justify-center w-9 h-9 rounded-lg text-textSecondary hover:bg-slate-100" onclick="document.getElementById('search-modal').classList.toggle('hidden');setTimeout(()=>document.getElementById('search-input').focus(),50)">
            <svg class="w-[18px] h-[18px]" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="m21 21-4.3-4.3M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/></svg>
          </button>
          <!-- desktop: log in + sign up -->
          <a href="login.html" class="btn btn-ghost btn-sm hidden md:inline-flex">Log in</a>
          <a href="signup.html" class="btn btn-primary btn-sm hidden sm:inline-flex">Sign up</a>
          <!-- mobile: hamburger -->
          <button class="md:hidden tap flex items-center justify-center w-9 h-9 rounded-lg text-textSecondary hover:bg-slate-100" onclick="UI.toggleMobile()">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" d="M4 6h16M4 12h16M4 18h16"/></svg>
          </button>
        </div>
      </div>
      <!-- mobile drawer -->
      <div id="mobile-nav" class="md:hidden hidden border-t border-border bg-surface">
        <div class="container-app py-2">
          <div class="grid grid-cols-2 gap-1">
            ${NAV.map(n=>`<a href="${n.href}" class="flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-[14px] font-medium text-textSecondary hover:bg-slate-50 hover:text-text ${n.key===active?'bg-primary-50 text-primary-700':''}">${n.label}</a>`).join('')}
          </div>
          <div class="border-t border-border mt-2 pt-2 flex gap-2">
            <a href="login.html" class="btn btn-secondary flex-1 tap">Log in</a>
            <a href="signup.html" class="btn btn-primary flex-1 tap">Sign up</a>
          </div>
        </div>
      </div>
    </div>
    ${searchModalHTML()}`;
  }

  function searchModalHTML() {
    return `
    <div id="search-modal" class="hidden fixed inset-0 z-50 flex items-start justify-center pt-14 sm:pt-24 px-0 sm:px-4 bg-slate-900/50 backdrop-blur-sm" onclick="if(event.target===this)this.classList.add('hidden')">
      <div class="surface w-full sm:max-w-xl shadow-lg overflow-hidden sm:rounded-xl rounded-none sm:mt-0">
        <div class="flex items-center gap-3 px-4 border-b border-border">
          <svg class="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="m21 21-4.3-4.3M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/></svg>
          <input id="search-input" class="flex-1 bg-transparent py-3.5 text-sm focus:outline-none placeholder:text-slate-400" placeholder="Search companies, sectors…" oninput="UI.runSearch(this.value)" autocomplete="off">
          <kbd class="px-1.5 py-0.5 rounded bg-slate-100 text-[10px] mono text-slate-400 border border-border">ESC</kbd>
        </div>
        <div id="search-results" class="max-h-80 overflow-y-auto p-2 text-sm text-textMuted">
          <div class="px-3 py-6 text-center text-slate-400 text-[13px]">Start typing to search across all IPOs…</div>
        </div>
      </div>
    </div>`;
  }

  function footerHTML() {
    return `
    <footer class="mt-20 border-t border-border">
      <div class="container-app py-12">
        <div class="grid grid-cols-2 md:grid-cols-4 gap-8">
          <div class="col-span-2 md:col-span-1">
            <div class="flex items-center gap-2.5">
              <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-400 to-primary-600 flex items-center justify-center">
                <span class="text-white font-extrabold text-xs">IPO</span>
              </div>
              <span class="font-bold tracking-tight">IPO<span class="text-primary-600">Radar</span></span>
            </div>
            <p class="mt-3 text-[13px] text-textMuted leading-relaxed">Every Indian IPO, parsed from the source filing. DRHP to listing day.</p>
          </div>
          <div>
            <div class="label">Browse</div>
            <div class="space-y-2 text-[13px]">
              <a href="ipos.html?status=open" class="block text-textSecondary hover:text-text">Open IPOs</a>
              <a href="ipos.html?status=upcoming" class="block text-textSecondary hover:text-text">Upcoming</a>
              <a href="ipos.html?status=listed" class="block text-textSecondary hover:text-text">Recently listed</a>
              <a href="ipos.html?platform=SME" class="block text-textSecondary hover:text-text">SME IPOs</a>
            </div>
          </div>
          <div>
            <div class="label">Product</div>
            <div class="space-y-2 text-[13px]">
              <a href="news.html" class="block text-textSecondary hover:text-text">Status feed</a>
              <a href="about.html" class="block text-textSecondary hover:text-text">Methodology</a>
              <a href="signup.html" class="block text-textSecondary hover:text-text">Alerts (soon)</a>
              <span class="block text-slate-400">API access (soon)</span>
            </div>
          </div>
          <div>
            <div class="label">Legal</div>
            <div class="space-y-2 text-[13px]">
              <span class="block text-slate-400">Terms</span>
              <span class="block text-slate-400">Privacy</span>
              <span class="block text-slate-400">Disclaimer</span>
            </div>
          </div>
        </div>
        <div class="mt-10 pt-6 border-t border-border flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-[12px] text-slate-400">
          <p>© 2026 IPORadar. Data sourced from SEBI / BSE / NSE filings. Not investment advice.</p>
          <p class="mono">Built for retail investors & analysts</p>
        </div>
      </div>
    </footer>`;
  }

  function layout(active) {
    const nav = document.getElementById('nav');
    const foot = document.getElementById('footer');
    if (nav) nav.innerHTML = navHTML(active);
    if (foot) foot.innerHTML = footerHTML();
    // keyboard: ⌘K / ESC for search
    document.addEventListener('keydown', e => {
      const modal = document.getElementById('search-modal');
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault(); modal.classList.toggle('hidden');
        if (!modal.classList.contains('hidden')) document.getElementById('search-input').focus();
      }
      if (e.key === 'Escape') modal.classList.add('hidden');
    });
  }

  function toggleMobile() { document.getElementById('mobile-nav').classList.toggle('hidden'); }

  function runSearch(q) {
    const box = document.getElementById('search-results');
    q = q.trim().toLowerCase();
    if (!q) { box.innerHTML = `<div class="px-3 py-6 text-center text-slate-400 text-[13px]">Start typing to search across all IPOs…</div>`; return; }
    const hits = window.IPO_DATA.ipos.filter(i =>
      i.company_name.toLowerCase().includes(q) || (i.sector || '').toLowerCase().includes(q)).slice(0, 8);
    box.innerHTML = hits.length ? hits.map(i => `
      <a href="ipo.html?id=${i.id}" class="flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg hover:bg-slate-50">
        <span class="flex items-center gap-2.5 min-w-0"><span class="truncate text-text">${i.company_name}</span></span>
        <span class="flex items-center gap-2 shrink-0">${platformBadge(i.platform)}${statusPill(i.status)}</span>
      </a>`).join('')
      : `<div class="px-3 py-6 text-center text-slate-400 text-[13px]">No matches for “${q}”.</div>`;
  }

  window.UI = {
    statusMeta, statusPill, platformBadge, trustNote, fmtDate, relTime, inr, fmtCr,
    priceBand, minInvest, daysB, sparkline, gmpColor, gmpArrow, gmpTile, subsTile,
    ipoCard, ipoRow, lifecycle, layout, toggleMobile, runSearch,
  };
})();
