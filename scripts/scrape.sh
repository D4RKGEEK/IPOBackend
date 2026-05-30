#!/bin/bash
# IPO Scraper Cron Script
# Runs the full scrape and reports new IPOs / status changes
cd /Users/vaibhav/Documents/IPOScraper
.venv/bin/python -m app.scraper_service 2>&1
