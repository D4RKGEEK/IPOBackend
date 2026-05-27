"""Migrate all date fields to ISO format (YYYY-MM-DD)."""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from datetime import datetime
from app.db_models import IPOMaster, init_db, get_session
from app.db_service import DatabaseService

def to_iso(val):
    """Convert any date string to YYYY-MM-DD. Returns None if invalid."""
    if not val or not val.strip():
        return None
    val = val.strip()
    # Already ISO
    if re.match(r'^\d{4}-\d{2}-\d{2}$', val):
        return val
    # DD-Mon-YYYY or DD-Month-YYYY
    for fmt in ('%d-%b-%Y', '%d-%B-%Y', '%d %b %Y', '%d %B %Y'):
        try:
            return datetime.strptime(val, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    # Already a date object?
    if hasattr(val, 'strftime'):
        return val.strftime('%Y-%m-%d')
    return val  # Return as-is if we can't parse

date_fields = [
    'drhp_filed_date', 'rhp_filed_date', 'fp_filed_date',
    'open_date', 'close_date',
]

init_db()
db = DatabaseService()
updated = 0

with get_session() as s:
    all_ipos = s.query(IPOMaster).all()
    for ipo in all_ipos:
        changed = False
        for field in date_fields:
            old = getattr(ipo, field, None)
            if old:
                new = to_iso(old)
                if new and new != old:
                    setattr(ipo, field, new)
                    changed = True
        if changed:
            updated += 1
            s.add(ipo)
    
    s.commit()

print(f'Updated {updated} IPOs with date normalization.')

# Verify
iso = 0
non_iso = 0
for ipo in s.query(IPOMaster).filter(IPOMaster.drhp_filed_date.isnot(None)).all():
    v = ipo.drhp_filed_date
    if v and re.match(r'^\d{4}-\d{2}-\d{2}$', v):
        iso += 1
    else:
        non_iso += 1
        print(f'  Still non-ISO: #{ipo.id} {ipo.company_name}: drhp_filed_date={v}')

print(f'\nAfter migration: {iso} ISO dates, {non_iso} non-ISO')
