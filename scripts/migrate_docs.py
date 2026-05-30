"""Migrate existing document URLs from ipo_master to ipo_documents table."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from app.db_models import IPOMaster, IPODocument, get_session

with get_session() as s:
    ipos = s.query(IPOMaster).all()
    created = 0
    skipped = 0
    
    for ipo in ipos:
        for doc_type, url_field in [
            ("drhp", "drhp_url"),
            ("rhp", "rhp_url"),
            ("prospectus", "final_prospectus_url"),
            ("abridged", "abridged_prospectus_url"),
        ]:
            url = getattr(ipo, url_field, None)
            if not url:
                continue
            
            # Check if already migrated
            existing = s.query(IPODocument).filter(
                IPODocument.ipo_master_id == ipo.id,
                IPODocument.doc_type == doc_type,
            ).first()
            if existing:
                skipped += 1
                continue
            
            # Set phase based on processed flag
            phase = "discovered"
            if doc_type == "drhp" and ipo.drhp_processed:
                phase = "parsed"
            elif doc_type == "rhp" and ipo.rhp_processed:
                phase = "parsed"
            
            doc = IPODocument(
                ipo_master_id=ipo.id,
                doc_type=doc_type,
                doc_version=1,
                url=url,
                phase=phase,
            )
            s.add(doc)
            created += 1
    
    s.commit()
    print(f"Migrated: {created} new documents, {skipped} already existed")
    
    # Verify
    total = s.query(IPODocument).count()
    print(f"Total documents in ipo_documents: {total}")
