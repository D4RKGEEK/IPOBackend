# Procfile — used by Railway as a fallback if it can't read railway.toml,
# also picked up by Heroku-style hosts. Same start command as fly + railway.
web: python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips=*
