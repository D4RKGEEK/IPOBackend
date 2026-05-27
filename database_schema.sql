-- Supabase Database Schema for IPO Aggregation Platform
-- Run this in Supabase SQL Editor

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create IPO Master Table
CREATE TABLE ipo_master (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('drhp_filed', 'sebi_approved', 'rhp_filed', 'upcoming', 'open', 'closed', 'listed', 'unknown')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_scraped TIMESTAMP WITH TIME ZONE,
    data_confidence FLOAT DEFAULT 0.0,
    sources JSONB DEFAULT '{}',
    documents JSONB DEFAULT '[]',
    raw_data JSONB DEFAULT '{}',
    CONSTRAINT normalized_name_unique UNIQUE (normalized_name)
);

-- Create Status History Table
CREATE TABLE ipo_status_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ipo_master_id UUID REFERENCES ipo_master(id) ON DELETE CASCADE,
    old_status TEXT,
    new_status TEXT NOT NULL,
    change_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    source TEXT NOT NULL CHECK (source IN ('sebi', 'bse', 'nse', 'bse_sme', 'manual')),
    triggered_by TEXT NOT NULL CHECK (triggered_by IN ('cron', 'webhook', 'manual', 'system')),
    details JSONB DEFAULT '{}',
    CONSTRAINT status_change_valid CHECK (old_status IS NULL OR old_status != new_status)
);

-- Create Parsed Data Table
CREATE TABLE ipo_parsed_data (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ipo_master_id UUID REFERENCES ipo_master(id) ON DELETE CASCADE,
    data_type TEXT NOT NULL CHECK (data_type IN ('financial', 'promoter', 'business', 'risk', 'key_terms')),
    extracted_data JSONB NOT NULL,
    confidence_score FLOAT NOT NULL,
    extraction_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}',
    processing_time_ms INTEGER,
    CONSTRAINT confidence_score_valid CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0)
);

-- Create Documents Table
CREATE TABLE ipo_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ipo_master_id UUID REFERENCES ipo_master(id) ON DELETE CASCADE,
    document_type TEXT NOT NULL CHECK (document_type IN ('DRHP', 'RHP', 'Prospectus', 'Final_Prospectus', 'Abridged_Prospectus')),
    document_url TEXT,
    file_size_bytes BIGINT,
    mime_type TEXT,
    local_path TEXT,
    cloud_path TEXT,
    uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_processed BOOLEAN DEFAULT FALSE,
    is_archived BOOLEAN DEFAULT FALSE
);

-- Create Scraper Logs Table
CREATE TABLE scraper_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scraper_type TEXT NOT NULL CHECK (scraper_type IN ('sebi', 'bse', 'nse', 'bse_sme')),
    action TEXT NOT NULL,
    company_name TEXT,
    status TEXT NOT NULL CHECK (status IN ('success', 'error', 'warning')),
    message TEXT,
    error_details JSONB DEFAULT '{}',
    execution_time_ms INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create Indexes for Performance
CREATE INDEX idx_ipo_master_status ON ipo_master(status);
CREATE INDEX idx_ipo_master_created_at ON ipo_master(created_at);
CREATE INDEX idx_ipo_master_normalized_name ON ipo_master(normalized_name);
CREATE INDEX idx_ipo_status_history_ipo_id ON ipo_status_history(ipo_master_id);
CREATE INDEX idx_ipo_status_history_change_date ON ipo_status_history(change_date);
CREATE INDEX idx_ipo_status_history_new_status ON ipo_status_history(new_status);
CREATE INDEX idx_ipo_parsed_data_ipo_id ON ipo_parsed_data(ipo_master_id);
CREATE INDEX idx_ipo_parsed_data_data_type ON ipo_parsed_data(data_type);
CREATE INDEX idx_ipo_documents_ipo_id ON ipo_documents(ipo_master_id);
CREATE INDEX idx_ipo_documents_document_type ON ipo_documents(document_type);
CREATE INDEX idx_scraper_logs_status ON scraper_logs(status);
CREATE INDEX idx_scraper_logs_created_at ON scraper_logs(created_at);

-- Create Triggers for Automatic Updates
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_ipo_master_updated_at
    BEFORE UPDATE ON ipo_master
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Create View for Dashboard Statistics
CREATE VIEW v_ipo_dashboard_stats AS
SELECT 
    status,
    COUNT(*) as count,
    AVG(data_confidence) as avg_confidence,
    MAX(updated_at) as last_updated
FROM ipo_master
GROUP BY status;

-- Create Function for Status Change Detection
CREATE OR REPLACE FUNCTION detect_status_change(
    p_ipo_master_id UUID,
    p_new_status TEXT,
    p_source TEXT,
    p_triggered_by TEXT
) RETURNS UUID AS $$
DECLARE
    v_old_status TEXT;
    v_history_id UUID;
BEGIN
    -- Get current status
    SELECT status INTO v_old_status
    FROM ipo_master
    WHERE id = p_ipo_master_id;
    
    -- Insert status change record
    INSERT INTO ipo_status_history (
        ipo_master_id,
        old_status,
        new_status,
        source,
        triggered_by,
        details
    ) VALUES (
        p_ipo_master_id,
        v_old_status,
        p_new_status,
        p_source,
        p_triggered_by,
        jsonb_build_object('timestamp', NOW())
    ) RETURNING id INTO v_history_id;
    
    -- Update IPO master
    UPDATE ipo_master
    SET 
        status = p_new_status,
        updated_at = NOW(),
        last_scraped = NOW()
    WHERE id = p_ipo_master_id;
    
    RETURN v_history_id;
END;
$$ language plpgsql;

-- Create Row Level Security (RLS)
ALTER TABLE ipo_master ENABLE ROW LEVEL SECURITY;
ALTER TABLE ipo_status_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE ipo_parsed_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE ipo_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE scraper_logs ENABLE ROW LEVEL SECURITY;

-- RLS Policies (adjust based on your auth needs)
CREATE POLICY "Public ipo_master access" ON ipo_master
    FOR ALL USING (true);

CREATE POLICY "Public ipo_status_history access" ON ipo_status_history
    FOR ALL USING (true);

CREATE POLICY "Public ipo_parsed_data access" ON ipo_parsed_data
    FOR ALL USING (true);

CREATE POLICY "Public ipo_documents access" ON ipo_documents
    FOR ALL USING (true);

CREATE POLICY "Public scraper_logs access" ON scraper_logs
    FOR ALL USING (true);