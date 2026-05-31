/* ============================================================================
   IPORadar — design system (light, institutional-fintech)
   References in spirit: Koyfin · Tikr · Stripe · Mercury · Linear (light).
   Tokens authored by the team; components below build on them.
   ========================================================================== */
window.tailwind = window.tailwind || {};

tailwind.config = {
  darkMode: false,
  theme: {
    extend: {
      fontFamily: {
        sans: ['Geist', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      colors: {
        background: '#F7F8FA',
        surface: '#FFFFFF',
        surfaceAlt: '#F8FAFC',
        border: '#E6E8EC',
        borderSoft: '#F1F3F6',
        text: '#0B1220',
        textSecondary: '#475569',
        textMuted: '#7A8699',
        primary: {
          50:'#EEF2FF',100:'#E0E7FF',200:'#C7D2FE',300:'#A5B4FC',400:'#818CF8',
          500:'#6366F1',600:'#4F46E5',700:'#4338CA',800:'#3730A3',900:'#312E81',
        },
        success: { 50:'#ECFDF5',100:'#D1FAE5',500:'#10B981',600:'#059669',700:'#047857' },
        warning: { 50:'#FFFBEB',100:'#FEF3C7',500:'#F59E0B',600:'#D97706',700:'#B45309' },
        danger:  { 50:'#FEF2F2',100:'#FEE2E2',500:'#EF4444',600:'#DC2626',700:'#B91C1C' },
      },
      borderRadius: { sm:'4px', DEFAULT:'6px', md:'8px', lg:'10px', xl:'14px', '2xl':'18px' },
      boxShadow: {
        xs: '0 1px 2px rgba(11,18,32,.04)',
        sm: '0 1px 3px rgba(11,18,32,.06), 0 1px 2px rgba(11,18,32,.04)',
        md: '0 6px 16px -6px rgba(11,18,32,.10), 0 2px 6px -2px rgba(11,18,32,.06)',
        lg: '0 16px 36px -10px rgba(11,18,32,.14), 0 6px 14px -6px rgba(11,18,32,.08)',
        xl: '0 30px 60px -15px rgba(11,18,32,.20)',
        ring: '0 0 0 1px rgba(99,102,241,.18), 0 8px 24px -8px rgba(99,102,241,.25)',
      },
      keyframes: {
        'fade-up': { '0%': { opacity:'0', transform:'translateY(8px)' }, '100%': { opacity:'1', transform:'translateY(0)' } },
        'pulse-ring': {
          '0%':{ boxShadow:'0 0 0 0 rgba(16,185,129,.5)' },
          '70%':{ boxShadow:'0 0 0 5px rgba(16,185,129,0)' },
          '100%':{ boxShadow:'0 0 0 0 rgba(16,185,129,0)' },
        },
        marquee: { '0%':{ transform:'translateX(0)' }, '100%':{ transform:'translateX(-50%)' } },
        shimmer: { '100%':{ transform:'translateX(100%)' } },
      },
      animation: {
        'fade-up':'fade-up .5s cubic-bezier(.16,1,.3,1) both',
        'pulse-ring':'pulse-ring 1.8s cubic-bezier(.66,0,0,1) infinite',
        marquee:'marquee 38s linear infinite',
        shimmer:'shimmer 1.6s infinite',
      },
      maxWidth: { container: '1200px' },
    },
  },
};

(function injectComponents() {
  const css = `
  @layer base {
    html { color-scheme: light; -webkit-text-size-adjust: 100%; }
    body {
      @apply bg-background text-text font-sans antialiased;
      font-feature-settings: "cv11","ss01","tnum","cv03";
      background-image:
        radial-gradient(48rem 30rem at 88% -8%, rgba(99,102,241,.06), transparent 60%),
        radial-gradient(40rem 28rem at -6% -4%, rgba(139,92,246,.05), transparent 55%);
      background-attachment: fixed;
    }
    ::selection { @apply bg-primary-100 text-primary-900; }
    :focus-visible { @apply outline-none ring-2 ring-primary-500/40 ring-offset-2 ring-offset-background; }
    ::-webkit-scrollbar { @apply w-2 h-2; }
    ::-webkit-scrollbar-thumb { @apply bg-slate-300 rounded-full; }
    ::-webkit-scrollbar-thumb:hover { @apply bg-slate-400; }
    [x-cloak] { display:none !important; }
    h1,h2,h3,h4 { @apply tracking-tight text-text; }
  }

  @layer components {
    .container-app { @apply max-w-container mx-auto px-5 sm:px-6 lg:px-8; }
    .mono { font-family:'JetBrains Mono',ui-monospace,monospace; font-variant-numeric: tabular-nums; }
    .num  { font-variant-numeric: tabular-nums; letter-spacing:-.01em; }
    .eyebrow { @apply inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[.14em] text-textMuted; }

    /* ---------- surfaces ---------- */
    .surface  { @apply bg-surface border border-border rounded-xl shadow-xs; }
    .surface-2{ @apply bg-surfaceAlt border border-border rounded-lg; }
    .surface-elevated { @apply bg-surface border border-border rounded-xl shadow-md; }
    .card { @apply bg-surface border border-border rounded-xl shadow-xs; }
    .card-sm { @apply bg-surface border border-border rounded-lg; }
    .hover-lift { @apply transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:border-slate-300; }
    .divider { @apply border-t border-border; }

    /* ---------- buttons ---------- */
    .btn { @apply inline-flex items-center justify-center gap-2 px-4 py-2.5 text-[13.5px] font-semibold rounded-lg transition-all duration-150 select-none whitespace-nowrap disabled:opacity-50 disabled:pointer-events-none touch-manipulation; }
    .btn-sm { @apply px-3 py-1.5 text-[12.5px] rounded-md gap-1.5; }
    .btn-primary { @apply bg-primary-600 text-white shadow-sm; }
    .btn-primary:hover { @apply bg-primary-700 shadow-md; }
    .btn-secondary { @apply bg-surface border border-border text-text shadow-xs; }
    .btn-secondary:hover { @apply bg-surfaceAlt border-slate-300; }
    .btn-ghost { @apply text-textSecondary; }
    .btn-ghost:hover { @apply bg-slate-100 text-text; }
    .btn-dark { @apply bg-text text-white shadow-sm; }
    .btn-dark:hover { @apply bg-slate-700; }

    /* ---------- inputs ---------- */
    .label { @apply block mb-1.5 text-[11px] font-semibold uppercase tracking-[.1em] text-textMuted; }
    .input { @apply w-full px-3.5 py-2.5 text-sm bg-surface border border-border rounded-lg text-text placeholder:text-textMuted/70 shadow-xs focus:outline-none focus:border-primary-500 focus:ring-2 focus:ring-primary-100 transition-colors; }

    /* ---------- nav ---------- */
    .nav-link { @apply px-3 py-1.5 rounded-lg text-[13.5px] font-medium text-textSecondary transition-colors; }
    .nav-link:hover { @apply bg-slate-100 text-text; }
    .nav-link.active { @apply bg-primary-50 text-primary-700; }

    /* ---------- metrics / kpis ---------- */
    .kpi { @apply bg-surface border border-border rounded-lg p-4; }
    .kpi-label { @apply text-[10.5px] uppercase tracking-[.1em] text-textMuted font-semibold; }
    .kpi-value { @apply mt-1.5 text-lg font-semibold text-text mono; }
    .metric { @apply rounded-lg bg-surfaceAlt border border-borderSoft px-3.5 py-3; }
    .metric-label { @apply text-[10.5px] uppercase tracking-[.1em] text-textMuted font-semibold; }
    .metric-value { @apply mt-1 text-[17px] font-semibold text-text mono num; }
    .stat-up { @apply text-success-600; }
    .stat-down { @apply text-danger-600; }

    /* ---------- tables ---------- */
    .dt { @apply w-full text-sm border-separate border-spacing-0; }
    .dt thead th { @apply text-left text-[10.5px] font-semibold uppercase tracking-[.1em] text-textMuted px-3.5 py-2.5 bg-surfaceAlt first:rounded-l-lg last:rounded-r-lg; }
    .dt td { @apply px-3.5 py-2.5 border-b border-borderSoft; }
    .dt tbody tr:hover td { @apply bg-surfaceAlt/70; }

    /* ---------- status pills ---------- */
    .pill { @apply inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border leading-none; }
    .pill-dot { @apply w-1.5 h-1.5 rounded-full; }
    .pill-amber  { @apply bg-amber-50 text-amber-700 border-amber-200; }
    .pill-blue   { @apply bg-blue-50 text-blue-700 border-blue-200; }
    .pill-indigo { @apply bg-indigo-50 text-indigo-700 border-indigo-200; }
    .pill-purple { @apply bg-purple-50 text-purple-700 border-purple-200; }
    .pill-green  { @apply bg-emerald-50 text-emerald-700 border-emerald-200; }
    .pill-red    { @apply bg-rose-50 text-rose-700 border-rose-200; }
    .pill-teal   { @apply bg-teal-50 text-teal-700 border-teal-200; }
    .pill-gray   { @apply bg-slate-100 text-slate-600 border-slate-200; }

    /* ---------- chips / badges / segmented ---------- */
    .badge-mono { @apply inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold mono uppercase tracking-wide border border-slate-200 text-textMuted bg-surfaceAlt; }
    .seg { @apply inline-flex items-center gap-1 p-1 rounded-lg bg-slate-100 border border-border; }
    .seg-btn { @apply px-3 py-1.5 rounded-md text-[12.5px] font-medium text-textSecondary transition-colors cursor-pointer; }
    .seg-btn.active { @apply bg-surface text-text shadow-xs; }

    /* ---------- tabs ---------- */
    .tab { @apply relative px-3.5 py-3 text-[13.5px] font-medium text-textMuted hover:text-text transition-colors whitespace-nowrap cursor-pointer; }
    .tab.active { @apply text-primary-700; }
    .tab.active::after { content:''; @apply absolute left-2.5 right-2.5 -bottom-px h-0.5 bg-primary-600 rounded-full; }

    /* ---------- effects ---------- */
    .locked { @apply relative bg-surfaceAlt border border-border rounded-xl p-5 overflow-hidden; }
    .grid-bg {
      background-image:
        linear-gradient(rgba(11,18,32,.04) 1px, transparent 1px),
        linear-gradient(90deg, rgba(11,18,32,.04) 1px, transparent 1px);
      background-size: 26px 26px;
      -webkit-mask-image: radial-gradient(60% 60% at 50% 40%, #000, transparent);
              mask-image: radial-gradient(60% 60% at 50% 40%, #000, transparent);
    }
    .skeleton { @apply relative overflow-hidden bg-slate-100 rounded-md; }
    .skeleton::after { content:''; @apply absolute inset-0 -translate-x-full animate-shimmer; background:linear-gradient(90deg,transparent,rgba(255,255,255,.6),transparent); }

    /* ---------- mobile helpers ---------- */
    /* horizontal scroll row that fades at edges */
    .scroll-x { @apply flex items-center gap-2 overflow-x-auto pb-0.5; -webkit-overflow-scrolling:touch; scrollbar-width:none; }
    .scroll-x::-webkit-scrollbar { display:none; }
    .scroll-x-fade { -webkit-mask-image:linear-gradient(90deg,transparent,#000 16px,#000 calc(100% - 16px),transparent); mask-image:linear-gradient(90deg,transparent,#000 16px,#000 calc(100% - 16px),transparent); }
    /* compact 2-col data grid for mobile detail header */
    .data-grid { @apply grid grid-cols-2 gap-px bg-border rounded-xl overflow-hidden border border-border; }
    .data-cell { @apply bg-surface px-3.5 py-3; }
    .data-cell-label { @apply text-[10px] font-semibold uppercase tracking-[.1em] text-textMuted; }
    .data-cell-value { @apply mono text-[15px] font-semibold text-text mt-0.5 num; }
    /* sticky safe-area-aware bottom bar */
    .sticky-bar { @apply fixed bottom-0 left-0 right-0 z-50 bg-surface border-t border-border px-4 pt-3 pb-3; padding-bottom: calc(.75rem + env(safe-area-inset-bottom, 0px)); }
    /* touch-friendly tap target size */
    .tap { @apply min-h-[44px] min-w-[44px]; }
  }
  `;
  const style = document.createElement('style');
  style.setAttribute('type', 'text/tailwindcss');
  style.textContent = css;
  document.head.appendChild(style);
})();
