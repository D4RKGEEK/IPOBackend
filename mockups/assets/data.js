/* ============================================================================
   Mock dataset — shaped exactly like the backend contract in UI_UX_BRIEF.md.
   "Today" is pinned to 2026-05-31 so open/upcoming/closed states are stable.
   Swap window.IPO_DATA.* fetches for the real API later:
     GET /api/ipos, /api/ipos/{id}/unified, /api/dashboard/stats, /api/status-changes
   ========================================================================== */
window.TODAY = '2026-05-31';

/* ---- list-level rows (always present) ---------------------------------- */
const IPOS = [
  { id: 122, company_name: 'Lorven International Limited', platform: 'SME', status: 'open',
    issue_type: 'IPO', price_band: '42-45', lot_size: 350, sector: 'Industrials',
    issue_size_cr: 16.0, dates: { drhp_filed:'2026-05-02', rhp_filed:'2026-05-18', fp_filed:'2026-05-24', open:'2026-05-29', close:'2026-06-02', allotment:'2026-06-04', listing:'2026-06-06' },
    documents: { drhp:'#', rhp:'#', final_prospectus:'#' }, publish_status:'published', confidence_score:0.96, unified_updated_at:'2026-05-31T07:40:00Z',
    gmp: { price: 12, pct: 26.7, upper: 45, trend: 'up', history: [6,7,8,9,10,11,12], updated: '2026-05-31T07:00:00Z' },
    subscription: { total: 3.18, qib: 0.0, nii: 2.87, retail: 4.12, day: 3, total_apps: 4820, updated: '2026-05-31T07:30:00Z' },
    allotment: { date: '2026-06-04', registrar: 'Bigshare Services Private Limited', registrar_slug: 'bigshare', registrar_url: 'https://ipo.bigshareonline.com/ipo_allotment.html', listing_date: '2026-06-06', refund_date: '2026-06-05' } },

  { id: 118, company_name: 'Aether Dynamics Limited', platform: 'MainBoard', status: 'open',
    issue_type: 'IPO', price_band: '475-500', lot_size: 30, sector: 'Aerospace & Defence',
    issue_size_cr: 1420.0, dates: { drhp_filed:'2026-03-10', rhp_filed:'2026-05-12', fp_filed:'2026-05-20', open:'2026-05-30', close:'2026-06-03', allotment:'2026-06-05', listing:'2026-06-09' },
    documents: { drhp:'#', rhp:'#', final_prospectus:'#' }, publish_status:'published', confidence_score:0.93, unified_updated_at:'2026-05-31T06:10:00Z',
    gmp: { price: 85, pct: 17.0, upper: 500, trend: 'up', history: [40,52,60,68,75,80,85], updated: '2026-05-31T06:00:00Z' },
    subscription: { total: 5.63, qib: 4.20, nii: 8.14, retail: 5.01, day: 2, total_apps: 62400, updated: '2026-05-31T07:30:00Z' },
    allotment: { date: '2026-06-05', registrar: 'KFin Technologies Limited', registrar_slug: 'kfin', registrar_url: 'https://ris.kfintech.com/clientservices/allotment/', listing_date: '2026-06-09', refund_date: '2026-06-06' } },

  { id: 131, company_name: 'Saraswati Agro Foods Limited', platform: 'SME', status: 'open',
    issue_type: 'IPO', price_band: '108-114', lot_size: 1200, sector: 'FMCG',
    issue_size_cr: 41.2, dates: { drhp_filed:'2026-04-01', rhp_filed:'2026-05-14', fp_filed:'2026-05-22', open:'2026-05-31', close:'2026-06-04', allotment:'2026-06-06', listing:'2026-06-10' },
    documents: { drhp:'#', rhp:'#', final_prospectus:'#' }, publish_status:'published', confidence_score:0.88, unified_updated_at:'2026-05-31T05:00:00Z',
    gmp: { price: 18, pct: 15.8, upper: 114, trend: 'neutral', history: [14,15,16,16,17,18,18], updated: '2026-05-31T05:00:00Z' },
    subscription: { total: 1.42, qib: 0.0, nii: 0.87, retail: 1.78, day: 1, total_apps: 2140, updated: '2026-05-31T05:00:00Z' },
    allotment: { date: '2026-06-06', registrar: 'Link Intime India Pvt. Ltd.', registrar_slug: 'linkintime', registrar_url: 'https://linkintime.co.in/initial_offer/public-issues.html', listing_date: '2026-06-10', refund_date: '2026-06-07' } },

  { id: 140, company_name: 'Quanta Semiconductors Limited', platform: 'MainBoard', status: 'upcoming',
    issue_type: 'IPO', price_band: '780-820', lot_size: 18, sector: 'Semiconductors',
    issue_size_cr: 3100.0, dates: { drhp_filed:'2026-02-20', rhp_filed:'2026-05-25', fp_filed:null, open:'2026-06-04', close:'2026-06-06', allotment:'2026-06-10', listing:'2026-06-13' },
    documents: { drhp:'#', rhp:'#', final_prospectus:null }, publish_status:'published', confidence_score:0.91, unified_updated_at:'2026-05-30T12:00:00Z',
    gmp: { price: 140, pct: 17.1, upper: 820, trend: 'up', history: [80,95,110,120,130,138,140], updated: '2026-05-30T12:00:00Z' } },

  { id: 142, company_name: 'Vermillion Retail Ventures Limited', platform: 'MainBoard', status: 'upcoming',
    issue_type: 'IPO', price_band: '210-225', lot_size: 66, sector: 'Retail',
    issue_size_cr: 640.0, dates: { drhp_filed:'2026-03-28', rhp_filed:'2026-05-26', fp_filed:null, open:'2026-06-05', close:'2026-06-09', allotment:'2026-06-11', listing:'2026-06-16' },
    documents: { drhp:'#', rhp:'#', final_prospectus:null }, publish_status:'published', confidence_score:0.9, unified_updated_at:'2026-05-30T09:30:00Z' },

  { id: 145, company_name: 'Nimbus Cloud Infra Limited', platform: 'SME', status: 'upcoming',
    issue_type: 'IPO', price_band: '88-92', lot_size: 1600, sector: 'Technology',
    issue_size_cr: 28.5, dates: { drhp_filed:'2026-04-12', rhp_filed:'2026-05-27', fp_filed:null, open:'2026-06-06', close:'2026-06-10', allotment:'2026-06-12', listing:'2026-06-17' },
    documents: { drhp:'#', rhp:'#', final_prospectus:null }, publish_status:'needs_review', confidence_score:0.62, unified_updated_at:'2026-05-29T18:00:00Z' },

  { id: 99, company_name: 'Helios Renewables Limited', platform: 'MainBoard', status: 'listed',
    issue_type: 'IPO', price_band: '320-340', lot_size: 44, sector: 'Renewable Energy',
    issue_size_cr: 980.0, dates: { drhp_filed:'2026-01-15', rhp_filed:'2026-04-20', fp_filed:'2026-04-28', open:'2026-05-08', close:'2026-05-12', allotment:'2026-05-14', listing:'2026-05-18' },
    documents: { drhp:'#', rhp:'#', final_prospectus:'#' }, publish_status:'published', confidence_score:0.95, unified_updated_at:'2026-05-18T11:00:00Z', listing_gain_pct: 38.2 },

  { id: 104, company_name: 'Indus Specialty Chemicals Limited', platform: 'MainBoard', status: 'listed',
    issue_type: 'IPO', price_band: '560-590', lot_size: 25, sector: 'Chemicals',
    issue_size_cr: 1850.0, dates: { drhp_filed:'2025-12-02', rhp_filed:'2026-04-10', fp_filed:'2026-04-18', open:'2026-04-29', close:'2026-05-02', allotment:'2026-05-06', listing:'2026-05-09' },
    documents: { drhp:'#', rhp:'#', final_prospectus:'#' }, publish_status:'published', confidence_score:0.94, unified_updated_at:'2026-05-09T11:00:00Z', listing_gain_pct: -4.6 },

  { id: 110, company_name: 'Mango Tree Hospitality Limited', platform: 'SME', status: 'listed',
    issue_type: 'IPO', price_band: '70-74', lot_size: 1600, sector: 'Hospitality',
    issue_size_cr: 22.0, dates: { drhp_filed:'2026-01-30', rhp_filed:'2026-04-15', fp_filed:'2026-04-22', open:'2026-05-02', close:'2026-05-06', allotment:'2026-05-08', listing:'2026-05-12' },
    documents: { drhp:'#', rhp:'#', final_prospectus:'#' }, publish_status:'published', confidence_score:0.87, unified_updated_at:'2026-05-12T11:00:00Z', listing_gain_pct: 12.9 },

  { id: 150, company_name: 'Trident Logistics & Warehousing Limited', platform: 'MainBoard', status: 'rhp_filed',
    issue_type: 'IPO', price_band: null, lot_size: null, sector: 'Logistics',
    issue_size_cr: 720.0, dates: { drhp_filed:'2026-03-05', rhp_filed:'2026-05-28', fp_filed:null, open:null, close:null, allotment:null, listing:null },
    documents: { drhp:'#', rhp:'#', final_prospectus:null }, publish_status:'published', confidence_score:0.84, unified_updated_at:'2026-05-28T15:00:00Z' },

  { id: 151, company_name: 'Peakform Sportswear Limited', platform: 'SME', status: 'sebi_approved',
    issue_type: 'IPO', price_band: null, lot_size: null, sector: 'Apparel',
    issue_size_cr: 34.0, dates: { drhp_filed:'2026-04-08', rhp_filed:null, fp_filed:null, open:null, close:null, allotment:null, listing:null },
    documents: { drhp:'#', rhp:null, final_prospectus:null }, publish_status:'published', confidence_score:0.81, unified_updated_at:'2026-05-27T10:00:00Z' },

  { id: 153, company_name: 'Greenwave Bio Energy Limited', platform: 'MainBoard', status: 'drhp_filed',
    issue_type: 'IPO', price_band: null, lot_size: null, sector: 'Renewable Energy',
    issue_size_cr: 1200.0, dates: { drhp_filed:'2026-05-29', rhp_filed:null, fp_filed:null, open:null, close:null, allotment:null, listing:null },
    documents: { drhp:'#', rhp:null, final_prospectus:null }, publish_status:'pending', confidence_score:null, unified_updated_at:'2026-05-29T16:00:00Z' },

  { id: 154, company_name: 'Kaveri Microfinance Limited', platform: 'MainBoard', status: 'drhp_filed',
    issue_type: 'IPO', price_band: null, lot_size: null, sector: 'Financial Services',
    issue_size_cr: 560.0, dates: { drhp_filed:'2026-05-26', rhp_filed:null, fp_filed:null, open:null, close:null, allotment:null, listing:null },
    documents: { drhp:'#', rhp:null, final_prospectus:null }, publish_status:'published', confidence_score:0.79, unified_updated_at:'2026-05-26T13:00:00Z' },

  { id: 155, company_name: 'Orbit Edutech Limited', platform: 'SME', status: 'drhp_filed',
    issue_type: 'IPO', price_band: null, lot_size: null, sector: 'Education',
    issue_size_cr: 19.5, dates: { drhp_filed:'2026-05-21', rhp_filed:null, fp_filed:null, open:null, close:null, allotment:null, listing:null },
    documents: { drhp:'#', rhp:null, final_prospectus:null }, publish_status:'published', confidence_score:0.74, unified_updated_at:'2026-05-21T13:00:00Z' },

  { id: 95, company_name: 'Coral Reef Beverages Limited', platform: 'SME', status: 'closed',
    issue_type: 'IPO', price_band: '55-58', lot_size: 2000, sector: 'FMCG',
    issue_size_cr: 18.7, dates: { drhp_filed:'2026-02-10', rhp_filed:'2026-05-05', fp_filed:'2026-05-12', open:'2026-05-25', close:'2026-05-28', allotment:'2026-06-02', listing:'2026-06-05' },
    documents: { drhp:'#', rhp:'#', final_prospectus:'#' }, publish_status:'published', confidence_score:0.86, unified_updated_at:'2026-05-28T18:00:00Z',
    gmp: { price: 8, pct: 13.8, upper: 58, trend: 'down', history: [14,12,11,10,9,8,8], updated: '2026-05-28T18:00:00Z' },
    subscription: { total: 4.87, qib: 0.0, nii: 5.12, retail: 4.68, day: 3, total_apps: 7230, updated: '2026-05-28T18:00:00Z' },
    allotment: { date: '2026-06-02', registrar: 'Bigshare Services Private Limited', registrar_slug: 'bigshare', registrar_url: 'https://ipo.bigshareonline.com/ipo_allotment.html', listing_date: '2026-06-05', refund_date: '2026-06-03' } },
];

/* ---- rich detail for the hero IPO (#122 Lorven, from the brief) -------- */
const DETAIL_122 = {
  cin: 'U74999MH2006PLC165838',
  registered_address: 'Lorven House, Opp. Kaka Petrol Pump, Near Metro Mall, LBS Marg, Bhandup (West), Mumbai, Maharashtra, 400078, India.',
  telephone: '+91 7208502171 / 7045646022', email: 'info@lorveninternational.in', website: 'www.lorveninternational.in',
  brlm_name: 'Hem Securities Limited', registrar_name: 'Bigshare Services Private Limited',
  statutory_auditor: 'M/s. Navin Dedhia & Company', legal_advisor: 'Vedanta Law Chambers',
  cfo_name: 'Roopali Uday Salunkhe', company_secretary_name: 'Meenakshi Jain',
  board_of_directors: ['Pankaj Baldevkumar Aggarwal (MD)', 'Sangeeta Deepak Aggarwal', 'Deepak Baldevkumar Aggarwal', 'Sanjay Bansal (Independent)', 'Neha Mehta (Independent)'],
  promoter_names: ['Pankaj Baldevkumar Aggarwal', 'Sangeeta Deepak Aggarwal'],
  promoter_group_names: ['Kavita Pankaj Aggarwal', 'Deepak Baldevkumar Aggarwal', 'Rupen Deepak Aggarwal', 'Sanjay Bansal', 'Arun Yashpal Aggarwal'],
  face_value: 10, authorized_shares: 15000000, paid_up_shares: 10091646,
  fresh_issue_shares: 2700000, offer_for_sale_shares: 692000,
  pre_issue_shares: 10091646, post_issue_shares: 13479646,
  qib_shares: 0.50, nii_shares: 0.15, retail_shares: 0.35, anchor_shares: null, market_maker_shares: 0.05,
  total_project_cost: 'Rs. 1,563 Lakhs',
  fund_usage_breakdown: [
    { use: 'Repayment of a portion of certain borrowings', amount: 'Rs. 150.00 Lakhs', pct: 12 },
    { use: 'To meet Working Capital requirements', amount: 'Rs. 980.00 Lakhs', pct: 63 },
    { use: 'General Corporate Purpose', amount: 'Rs. 433.00 Lakhs', pct: 25 },
  ],
  eps_basic: 'Rs. 4.04', eps_diluted: 'Rs. 4.04', pe_ratio: '11.1x', nav_per_share: 'Rs. 16.07',
  roe_percent: '25.15%', roce_percent: '29.54%', price_to_book_value: '2.8x', market_lot: 350,
  revenue_growth_percent: '19.68%', pat_margin_percent: '6.93%', ebitda_margin_percent: '7.13%',
  financial_years: ['FY2021', 'FY2022', 'FY2023'],
  total_revenue: [592.36, 3577.40, 4281.48],
  total_income: [616.10, 3584.20, 4400.30],
  profit_after_tax: [55.36, 236.81, 296.91],
  ebitda: [99.91, 415.68, 426.79],
  total_assets: [1295.0, 1927.0, 2057.0],
  net_worth: [665.50, 883.51, 1180.43],
  reserves_and_surplus: [565.50, 783.51, 1080.43],
  total_borrowings: [331.07, 308.30, 232.69],
  borrowings_breakdown: 'FY2023: Short-term secured (bank) Rs. 232.69 L · FY2022: Long-term Rs. 6.60 L + Short-term Rs. 274.47 L · FY2021: Long-term Rs. 29.19 L + Short-term Rs. 232.83 L',
  contingent_liabilities: 'Rs. 48.20 Lakhs (bank guarantees & disputed taxes)',
  bid_open_date: '2026-05-29', bid_close_date: '2026-06-02', allotment_date: '2026-06-04', listing_date: '2026-06-06',
  retail_min_lots: 1, retail_min_shares: 350, minimum_application: 15750,
  risks: [
    'Revenue concentration: top 5 customers contributed ~58% of FY2023 revenue.',
    'Working-capital intensive — receivable days averaged 96 over FY21–FY23.',
    'Promoter group holds 92.4% pre-issue; limited public float post-listing.',
    'Operations concentrated in a single Bhandup (Mumbai) facility.',
  ],
  sections: ['RISK_FACTORS', 'GENERAL_INFORMATION', 'CAPITAL_STRUCTURE', 'OBJECTS_OF_THE_ISSUE', 'BASIS_FOR_ISSUE_PRICE', 'OUR_MANAGEMENT', 'OUR_PROMOTERS', 'FINANCIAL_STATEMENTS', 'OUR_BUSINESS'],
};

/* rich detail for a mainboard IPO (#118 Aether Dynamics) */
const DETAIL_118 = {
  cin: 'L29253KA2009PLC051234',
  registered_address: 'Aether Tower, Outer Ring Road, Marathahalli, Bengaluru, Karnataka, 560037, India.',
  telephone: '+91 80 4567 8900', email: 'investors@aetherdynamics.in', website: 'www.aetherdynamics.in',
  brlm_name: 'Kotak Mahindra Capital, Axis Capital, JM Financial', registrar_name: 'KFin Technologies Limited',
  statutory_auditor: 'S.R. Batliboi & Associates LLP', legal_advisor: 'Cyril Amarchand Mangaldas',
  cfo_name: 'Arvind Rao', company_secretary_name: 'Priya Nair',
  board_of_directors: ['Vikram Sethi (Chairman & MD)', 'Arvind Rao (CFO)', 'Gen. (Retd.) R. Khanna', 'Lakshmi Venkat (Independent)', 'Thomas George (Independent)'],
  promoter_names: ['Vikram Sethi', 'Sethi Family Trust'],
  promoter_group_names: ['Meera Sethi', 'Aditya Sethi', 'Aether Holdings Pvt Ltd'],
  face_value: 2, authorized_shares: 300000000, paid_up_shares: 248000000,
  fresh_issue_shares: 18000000, offer_for_sale_shares: 10400000,
  pre_issue_shares: 248000000, post_issue_shares: 266000000,
  qib_shares: 0.50, nii_shares: 0.15, retail_shares: 0.35, anchor_shares: 0.30, market_maker_shares: null,
  total_project_cost: 'Rs. 1,420 Crores',
  fund_usage_breakdown: [
    { use: 'Capex — new composites facility, Hyderabad', amount: 'Rs. 620 Cr', pct: 44 },
    { use: 'Repayment / prepayment of borrowings', amount: 'Rs. 350 Cr', pct: 25 },
    { use: 'R&D and product development', amount: 'Rs. 250 Cr', pct: 18 },
    { use: 'General corporate purposes', amount: 'Rs. 200 Cr', pct: 13 },
  ],
  eps_basic: 'Rs. 14.85', eps_diluted: 'Rs. 14.62', pe_ratio: '33.7x', nav_per_share: 'Rs. 96.40',
  roe_percent: '18.4%', roce_percent: '21.2%', price_to_book_value: '5.2x', market_lot: 30,
  revenue_growth_percent: '27.3%', pat_margin_percent: '14.1%', ebitda_margin_percent: '23.6%',
  financial_years: ['FY2023', 'FY2024', 'FY2025'],
  total_revenue: [184500, 219800, 279700],
  total_income: [186200, 222400, 283100],
  profit_after_tax: [21800, 30100, 39400],
  ebitda: [39200, 51400, 66000],
  total_assets: [412000, 489000, 588000],
  net_worth: [198000, 224000, 263000],
  reserves_and_surplus: [193000, 219000, 258000],
  total_borrowings: [142000, 131000, 118000],
  borrowings_breakdown: 'FY2025: Long-term Rs. 78,000 L + Short-term Rs. 40,000 L, predominantly term loans for capex.',
  contingent_liabilities: 'Rs. 9,400 Lakhs (performance guarantees on defence contracts)',
  bid_open_date: '2026-05-30', bid_close_date: '2026-06-03', allotment_date: '2026-06-05', listing_date: '2026-06-09',
  retail_min_lots: 1, retail_min_shares: 30, minimum_application: 15000,
  risks: [
    'High dependence on government / defence orders (71% of order book).',
    'Long execution cycles expose margins to input-cost inflation.',
    'Customer concentration: Ministry of Defence ~52% of FY25 revenue.',
    'Valuation at 33.7x trailing P/E is rich versus listed peers.',
  ],
  sections: ['RISK_FACTORS', 'INDUSTRY_OVERVIEW', 'CAPITAL_STRUCTURE', 'OBJECTS_OF_THE_ISSUE', 'BASIS_FOR_ISSUE_PRICE', 'OUR_MANAGEMENT', 'OUR_PROMOTERS', 'FINANCIAL_STATEMENTS', 'OUR_BUSINESS'],
};

const DETAILS = { 122: DETAIL_122, 118: DETAIL_118 };

/* ---- dashboard stats --------------------------------------------------- */
const STATS = {
  total_ipos: 1311, open: IPOS.filter(i => i.status === 'open').length,
  upcoming: IPOS.filter(i => i.status === 'upcoming').length,
  listed: 187, drhp: 1002, this_week_opening: 5, mainboard: 309, sme: 1002,
};

/* ---- status-change feed ------------------------------------------------ */
const STATUS_CHANGES = [
  { id: 122, company_name: 'Lorven International Limited', from: 'rhp_filed', to: 'open', platform: 'SME', at: '2026-05-31T07:40:00Z' },
  { id: 131, company_name: 'Saraswati Agro Foods Limited', from: 'upcoming', to: 'open', platform: 'SME', at: '2026-05-31T03:30:00Z' },
  { id: 153, company_name: 'Greenwave Bio Energy Limited', from: null, to: 'drhp_filed', platform: 'MainBoard', at: '2026-05-29T16:00:00Z' },
  { id: 150, company_name: 'Trident Logistics & Warehousing Limited', from: 'sebi_approved', to: 'rhp_filed', platform: 'MainBoard', at: '2026-05-28T15:00:00Z' },
  { id: 95,  company_name: 'Coral Reef Beverages Limited', from: 'open', to: 'closed', platform: 'SME', at: '2026-05-28T18:00:00Z' },
  { id: 142, company_name: 'Vermillion Retail Ventures Limited', from: 'sebi_approved', to: 'rhp_filed', platform: 'MainBoard', at: '2026-05-26T11:20:00Z' },
  { id: 154, company_name: 'Kaveri Microfinance Limited', from: null, to: 'drhp_filed', platform: 'MainBoard', at: '2026-05-26T13:00:00Z' },
  { id: 110, company_name: 'Mango Tree Hospitality Limited', from: 'closed', to: 'listed', platform: 'SME', at: '2026-05-12T11:00:00Z' },
  { id: 99,  company_name: 'Helios Renewables Limited', from: 'closed', to: 'listed', platform: 'MainBoard', at: '2026-05-18T11:00:00Z' },
  { id: 104, company_name: 'Indus Specialty Chemicals Limited', from: 'closed', to: 'listed', platform: 'MainBoard', at: '2026-05-09T11:00:00Z' },
];

window.IPO_DATA = {
  ipos: IPOS,
  details: DETAILS,
  stats: STATS,
  statusChanges: STATUS_CHANGES,
  get(id) {
    const row = IPOS.find(i => String(i.id) === String(id));
    if (!row) return null;
    return Object.assign({}, row, DETAILS[id] || {});
  },
};
