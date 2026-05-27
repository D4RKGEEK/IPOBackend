"""Examine actual text from multiple IPOs to understand real format."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from app.db_models import IPOParsedData, IPOMaster, get_session

with get_session() as s:
    # Get texts from 5 different IPOs, recent MainBoard + SME
    results = s.query(IPOParsedData, IPOMaster.company_name).join(
        IPOMaster, IPOParsedData.ipo_master_id == IPOMaster.id
    ).filter(
        IPOParsedData.data_type == 'raw_text_drhp',
        IPOMaster.drhp_filed_date >= '2026-01-01',
    ).limit(5).all()

    for i, (record, name) in enumerate(results):
        text = record.extracted_data.get('text', '')
        print(f"{'='*80}")
        print(f"IPO #{i+1}: {name} (ID: {record.ipo_master_id})")
        print(f"{'='*80}")
        # Print first 3000 chars
        print(text[:3000])
        print(f"\n... (total {len(text):,} chars)")
        print()
