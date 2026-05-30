"""
Supabase Database Integration for IPO Aggregation Platform
Handles all database operations with proper error handling and logging.
"""

import os
import json
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from uuid import uuid4
import logging
from dotenv import load_dotenv

# Try to import supabase, but make it optional for now
try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False
    Client = None

from pydantic import BaseModel

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class DatabaseConfig:
    """Database configuration and connection management"""
    
    def __init__(self):
        self.supabase_url: str = os.getenv("SUPABASE_URL", "")
        self.supabase_key: str = os.getenv("SUPABASE_KEY", "")
        self.supabase_service_role: str = os.getenv("SUPABASE_SERVICE_ROLE", "")
        
        if not SUPABASE_AVAILABLE:
            raise ImportError("Supabase package not installed. Install with: pip install supabase")
        
        if not all([self.supabase_url, self.supabase_key]):
            raise ValueError("Missing Supabase configuration in environment variables")
        
        # Initialize Supabase client
        self.supabase: Client = create_client(self.supabase_url, self.supabase_key)
        self.supabase_service: Client = create_client(self.supabase_url, self.supabase_service_role)
        
        logger.info("Supabase database client initialized successfully")

    def get_service_client(self) -> Client:
        """Get service client for admin operations"""
        return self.supabase_service


class IPOMaster(BaseModel):
    """IPO Master record model"""
    id: Optional[str] = None
    company_name: str
    normalized_name: str
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_scraped: Optional[datetime] = None
    data_confidence: float = 0.0
    sources: Dict[str, Any] = {}
    documents: List[Dict[str, Any]] = []
    raw_data: Dict[str, Any] = {}

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class StatusHistory(BaseModel):
    """Status history record model"""
    id: Optional[str] = None
    ipo_master_id: str
    old_status: Optional[str] = None
    new_status: str
    change_date: Optional[datetime] = None
    source: str
    triggered_by: str
    details: Dict[str, Any] = {}

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class ParsedData(BaseModel):
    """Parsed data record model"""
    id: Optional[str] = None
    ipo_master_id: str
    data_type: str
    extracted_data: Dict[str, Any]
    confidence_score: float
    extraction_date: Optional[datetime] = None
    metadata: Dict[str, Any] = {}
    processing_time_ms: Optional[int] = None

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class DatabaseService:
    """Main database service for all IPO operations"""
    
    def __init__(self, config: DatabaseConfig):
        if not SUPABASE_AVAILABLE:
            raise ImportError("Supabase package not installed. Install with: pip install supabase")
        
        self.config = config
        self.supabase = config.supabase
        self.service_client = config.get_service_client()
        
    async def get_ipo_by_name(self, company_name: str) -> Optional[IPOMaster]:
        """Get IPO by company name (case-insensitive)"""
        try:
            normalized_name = self._normalize_company_name(company_name)
            
            response = self.supabase.table("ipo_master").select("*").eq(
                "normalized_name", normalized_name
            ).execute()
            
            if response.data:
                return self._parse_ipo_master(response.data[0])
            return None
            
        except Exception as e:
            logger.error(f"Error getting IPO by name {company_name}: {e}")
            return None
    
    async def get_ipo_by_id(self, ipo_id: str) -> Optional[IPOMaster]:
        """Get IPO by ID"""
        try:
            response = self.supabase.table("ipo_master").select("*").eq(
                "id", ipo_id
            ).execute()
            
            if response.data:
                return self._parse_ipo_master(response.data[0])
            return None
            
        except Exception as e:
            logger.error(f"Error getting IPO by ID {ipo_id}: {e}")
            return None
    
    async def save_ipo(self, ipo_data: Dict[str, Any]) -> str:
        """Save or update IPO data with status change tracking"""
        try:
            # Normalize company name
            company_name = ipo_data.get("company_name", "")
            normalized_name = self._normalize_company_name(company_name)
            
            # Check if IPO exists
            existing = await self.get_ipo_by_name(company_name)
            
            ipo_record = {
                "company_name": company_name,
                "normalized_name": normalized_name,
                "status": ipo_data.get("status", "unknown"),
                "sources": ipo_data.get("sources", {}),
                "documents": ipo_data.get("documents", []),
                "raw_data": ipo_data.get("raw_data", {}),
                "data_confidence": ipo_data.get("data_confidence", 0.0),
                "last_scraped": datetime.now(timezone.utc)
            }
            
            if existing:
                # Update existing IPO
                ipo_record["updated_at"] = datetime.now(timezone.utc)
                
                # Check for status change
                if existing.status != ipo_record["status"]:
                    await self._handle_status_change(existing, ipo_record)
                
                # Update record
                response = self.supabase.table("ipo_master").update(ipo_record).eq(
                    "id", existing.id
                ).execute()
                
                if response.data:
                    logger.info(f"Updated IPO: {company_name} (ID: {existing.id})")
                    return existing.id
                else:
                    raise Exception("Failed to update IPO record")
            else:
                # Create new IPO
                ipo_record["created_at"] = datetime.now(timezone.utc)
                ipo_record["updated_at"] = datetime.now(timezone.utc)
                
                response = self.supabase.table("ipo_master").insert(ipo_record).execute()
                
                if response.data:
                    new_id = response.data[0]["id"]
                    logger.info(f"Created new IPO: {company_name} (ID: {new_id})")
                    return new_id
                else:
                    raise Exception("Failed to create IPO record")
                    
        except Exception as e:
            logger.error(f"Error saving IPO {company_name}: {e}")
            raise
    
    async def get_status_history(self, ipo_id: str) -> List[StatusHistory]:
        """Get status history for an IPO"""
        try:
            response = self.supabase.table("ipo_status_history").select("*").eq(
                "ipo_master_id", ipo_id
            ).order("change_date", desc=True).execute()
            
            return [self._parse_status_history(record) for record in response.data]
            
        except Exception as e:
            logger.error(f"Error getting status history for IPO {ipo_id}: {e}")
            return []
    
    async def save_parsed_data(self, ipo_id: str, parsed_data: Dict[str, Any], 
                              data_type: str, confidence_score: float) -> str:
        """Save PDF parsing data"""
        try:
            parsed_record = {
                "ipo_master_id": ipo_id,
                "data_type": data_type,
                "extracted_data": parsed_data,
                "confidence_score": confidence_score,
                "extraction_date": datetime.now(timezone.utc),
                "metadata": {
                    "created_by": "pdf_parser",
                    "version": "1.0"
                }
            }
            
            response = self.supabase.table("ipo_parsed_data").insert(parsed_record).execute()
            
            if response.data:
                new_id = response.data[0]["id"]
                logger.info(f"Saved parsed data for IPO {ipo_id} (Type: {data_type})")
                return new_id
            else:
                raise Exception("Failed to save parsed data")
                
        except Exception as e:
            logger.error(f"Error saving parsed data for IPO {ipo_id}: {e}")
            raise
    
    async def get_parsed_data(self, ipo_id: str, data_type: Optional[str] = None) -> List[ParsedData]:
        """Get parsed data for an IPO"""
        try:
            query = self.supabase.table("ipo_parsed_data").select("*").eq(
                "ipo_master_id", ipo_id
            )
            
            if data_type:
                query = query.eq("data_type", data_type)
            
            response = query.order("extraction_date", desc=True).execute()
            
            return [self._parse_parsed_data(record) for record in response.data]
            
        except Exception as e:
            logger.error(f"Error getting parsed data for IPO {ipo_id}: {e}")
            return []
    
    async def get_dashboard_stats(self) -> Dict[str, Any]:
        """Get dashboard statistics"""
        try:
            # Get IPO counts by status
            response = self.supabase.table("v_ipo_dashboard_stats").select("*").execute()
            
            stats = {
                "total_ipos": 0,
                "ipos_by_status": {},
                "avg_confidence": 0.0
            }
            
            if response.data:
                for stat in response.data:
                    stats["ipos_by_status"][stat["status"]] = stat["count"]
                    stats["total_ipos"] += stat["count"]
                    stats["avg_confidence"] += stat["avg_confidence"] * stat["count"]
                
                if stats["total_ipos"] > 0:
                    stats["avg_confidence"] /= stats["total_ipos"]
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting dashboard stats: {e}")
            return {}
    
    async def save_scraper_log(self, scraper_type: str, action: str, status: str,
                              company_name: Optional[str] = None, 
                              message: Optional[str] = None,
                              error_details: Optional[Dict[str, Any]] = None,
                              execution_time_ms: Optional[int] = None) -> str:
        """Save scraper log entry"""
        try:
            log_entry = {
                "scraper_type": scraper_type,
                "action": action,
                "company_name": company_name,
                "status": status,
                "message": message,
                "error_details": error_details or {},
                "execution_time_ms": execution_time_ms,
                "created_at": datetime.now(timezone.utc)
            }
            
            response = self.supabase.table("scraper_logs").insert(log_entry).execute()
            
            if response.data:
                return response.data[0]["id"]
            else:
                raise Exception("Failed to save scraper log")
                
        except Exception as e:
            logger.error(f"Error saving scraper log: {e}")
            raise
    
    async def get_recent_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent scraper logs"""
        try:
            response = self.supabase.table("scraper_logs").select("*").order(
                "created_at", desc=True
            ).limit(limit).execute()
            
            return response.data
            
        except Exception as e:
            logger.error(f"Error getting recent logs: {e}")
            return []
    
    async def search_ipos(self, query: str, limit: int = 20) -> List[IPOMaster]:
        """Search IPOs by company name"""
        try:
            response = self.supabase.table("ipo_master").select("*").ilike(
                "company_name", f"%{query}%"
            ).limit(limit).execute()
            
            return [self._parse_ipo_master(record) for record in response.data]
            
        except Exception as e:
            logger.error(f"Error searching IPOs: {e}")
            return []
    
    async def get_ipos_by_status(self, status: str, limit: int = 50) -> List[IPOMaster]:
        """Get IPOs by status"""
        try:
            response = self.supabase.table("ipo_master").select("*").eq(
                "status", status
            ).order("updated_at", desc=True).limit(limit).execute()
            
            return [self._parse_ipo_master(record) for record in response.data]
            
        except Exception as e:
            logger.error(f"Error getting IPOs by status {status}: {e}")
            return []
    
    def _normalize_company_name(self, name: str) -> str:
        """Normalize company name for consistent matching"""
        if not name:
            return ""
        
        normalized = name.upper().strip()
        normalized = re.sub(r"\s*-\s*(DRHP|RHP|UDRHP|IPO|FPO)$", "", normalized)
        normalized = re.sub(r"\s+PRIVATE\s+LIMITED$", " PVT LTD", normalized)
        normalized = re.sub(r"\s+LIMITED$", " LTD", normalized)
        normalized = re.sub(r"[^A-Z0-9 ]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()
    
    def _parse_ipo_master(self, data: Dict[str, Any]) -> IPOMaster:
        """Parse IPO master data from database response"""
        return IPOMaster(
            id=data.get("id"),
            company_name=data.get("company_name", ""),
            normalized_name=data.get("normalized_name", ""),
            status=data.get("status", "unknown"),
            created_at=self._parse_datetime(data.get("created_at")),
            updated_at=self._parse_datetime(data.get("updated_at")),
            last_scraped=self._parse_datetime(data.get("last_scraped")),
            data_confidence=data.get("data_confidence", 0.0),
            sources=data.get("sources", {}),
            documents=data.get("documents", []),
            raw_data=data.get("raw_data", {})
        )
    
    def _parse_status_history(self, data: Dict[str, Any]) -> StatusHistory:
        """Parse status history data from database response"""
        return StatusHistory(
            id=data.get("id"),
            ipo_master_id=data.get("ipo_master_id", ""),
            old_status=data.get("old_status"),
            new_status=data.get("new_status", ""),
            change_date=self._parse_datetime(data.get("change_date")),
            source=data.get("source", ""),
            triggered_by=data.get("triggered_by", ""),
            details=data.get("details", {})
        )
    
    def _parse_parsed_data(self, data: Dict[str, Any]) -> ParsedData:
        """Parse parsed data from database response"""
        return ParsedData(
            id=data.get("id"),
            ipo_master_id=data.get("ipo_master_id", ""),
            data_type=data.get("data_type", ""),
            extracted_data=data.get("extracted_data", {}),
            confidence_score=data.get("confidence_score", 0.0),
            extraction_date=self._parse_datetime(data.get("extraction_date")),
            metadata=data.get("metadata", {}),
            processing_time_ms=data.get("processing_time_ms")
        )
    
    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Parse datetime string to datetime object"""
        if not dt_str:
            return None
        
        try:
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return None
    
    async def _handle_status_change(self, existing: IPOMaster, new_data: Dict[str, Any]):
        """Handle status change detection and logging"""
        try:
            # Detect status change
            old_status = existing.status
            new_status = new_data["status"]
            
            if old_status != new_status:
                # Log status change
                await self.save_scraper_log(
                    scraper_type="system",
                    action="status_change",
                    status="success",
                    company_name=existing.company_name,
                    message=f"Status changed: {old_status} → {new_status}",
                    error_details={
                        "ipo_id": existing.id,
                        "old_status": old_status,
                        "new_status": new_status,
                        "change_date": datetime.now(timezone.utc).isoformat()
                    }
                )
                
                logger.info(f"Status change detected: {existing.company_name} {old_status} → {new_status}")
                
        except Exception as e:
            logger.error(f"Error handling status change for {existing.company_name}: {e}")


class MockDatabaseService:
    """Mock database service for testing without Supabase"""
    
    def __init__(self):
        self.ipos = {}
        self.logs = []
        logger.info("Mock database service initialized")
    
    async def get_ipo_by_name(self, company_name: str) -> Optional[IPOMaster]:
        """Mock implementation"""
        normalized_name = self._normalize_company_name(company_name)
        for ipo_id, ipo in self.ipos.items():
            if ipo.normalized_name == normalized_name:
                return ipo
        return None
    
    async def get_ipo_by_id(self, ipo_id: str) -> Optional[IPOMaster]:
        """Mock implementation"""
        return self.ipos.get(ipo_id)
    
    async def save_ipo(self, ipo_data: Dict[str, Any]) -> str:
        """Mock implementation"""
        company_name = ipo_data.get("company_name", "")
        normalized_name = self._normalize_company_name(company_name)
        
        # Generate mock ID
        ipo_id = str(uuid4())
        
        ipo_record = IPOMaster(
            id=ipo_id,
            company_name=company_name,
            normalized_name=normalized_name,
            status=ipo_data.get("status", "unknown"),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            last_scraped=datetime.now(timezone.utc),
            data_confidence=ipo_data.get("data_confidence", 0.0),
            sources=ipo_data.get("sources", {}),
            documents=ipo_data.get("documents", []),
            raw_data=ipo_data.get("raw_data", {})
        )
        
        self.ipos[ipo_id] = ipo_record
        logger.info(f"Mock saved IPO: {company_name} (ID: {ipo_id})")
        return ipo_id
    
    async def get_dashboard_stats(self) -> Dict[str, Any]:
        """Mock implementation"""
        stats = {
            "total_ipos": len(self.ipos),
            "ipos_by_status": {},
            "avg_confidence": 0.0
        }
        
        if self.ipos:
            total_confidence = 0
            for ipo in self.ipos.values():
                status_count = stats["ipos_by_status"].get(ipo.status, 0) + 1
                stats["ipos_by_status"][ipo.status] = status_count
                total_confidence += ipo.data_confidence
            
            stats["avg_confidence"] = total_confidence / len(self.ipos)
        
        return stats
    
    async def save_scraper_log(self, scraper_type: str, action: str, status: str,
                              company_name: Optional[str] = None, 
                              message: Optional[str] = None,
                              error_details: Optional[Dict[str, Any]] = None,
                              execution_time_ms: Optional[int] = None) -> str:
        """Mock implementation"""
        log_id = str(uuid4())
        log_entry = {
            "id": log_id,
            "scraper_type": scraper_type,
            "action": action,
            "company_name": company_name,
            "status": status,
            "message": message,
            "error_details": error_details or {},
            "execution_time_ms": execution_time_ms,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        self.logs.append(log_entry)
        return log_id
    
    async def get_recent_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Mock implementation"""
        return self.logs[-limit:] if limit else self.logs
    
    async def search_ipos(self, query: str, limit: int = 20) -> List[IPOMaster]:
        """Mock implementation"""
        results = []
        query_lower = query.lower()
        
        for ipo in self.ipos.values():
            if query_lower in ipo.company_name.lower():
                results.append(ipo)
                if len(results) >= limit:
                    break
        
        return results
    
    async def get_ipos_by_status(self, status: str, limit: int = 50) -> List[IPOMaster]:
        """Mock implementation"""
        results = []
        
        for ipo in self.ipos.values():
            if ipo.status == status:
                results.append(ipo)
                if len(results) >= limit:
                    break
        
        return results
    
    async def get_status_history(self, ipo_id: str) -> List[StatusHistory]:
        """Mock implementation"""
        return []
    
    async def save_parsed_data(self, ipo_id: str, parsed_data: Dict[str, Any], 
                              data_type: str, confidence_score: float) -> str:
        """Mock implementation"""
        return str(uuid4())
    
    async def get_parsed_data(self, ipo_id: str, data_type: Optional[str] = None) -> List[ParsedData]:
        """Mock implementation"""
        return []
    
    def _normalize_company_name(self, name: str) -> str:
        """Normalize company name for consistent matching"""
        if not name:
            return ""
        
        normalized = name.upper().strip()
        normalized = re.sub(r"\s*-\s*(DRHP|RHP|UDRHP|IPO|FPO)$", "", normalized)
        normalized = re.sub(r"\s+PRIVATE\s+LIMITED$", " PVT LTD", normalized)
        normalized = re.sub(r"\s+LIMITED$", " LTD", normalized)
        normalized = re.sub(r"[^A-Z0-9 ]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()