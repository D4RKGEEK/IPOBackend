#!/bin/bash
# IPO Scraper Cron Script
# Runs Upstox scrape by default (fast). Use --sources all for full scrape.
cd /Users/vaibhav/Documents/IPOScraper
.venv/bin/python -m app.scraper_service --sources upstox 2>&1
