import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "8844212979:AAGkeBq5dZAZ8vAU4082nlfCUufe_y5LHoo")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8040452117"))

# Base URL - dynamically discovered from MovieLinkBD.com hub if needed
# Default to current active mirror provided by user
BASE_URL = os.getenv("BASE_URL", "https://x33jbm.movielinkbd.li").rstrip("/")

# Alternative mirrors for fallback
MIRRORS = [
    "https://x33jbm.movielinkbd.li",
    "https://movielinkbd.com",
    "https://movielinkbd.one",
    "https://movielinkbd.shop",
    "https://movielinkbd.work",
    "https://movielinkbd.tv",
]

# CDN base
CDN_BASE = "https://cdn.dramalinkbd.tv"

# Cache settings
CACHE_TTL_SEARCH = 300
CACHE_TTL_DETAILS = 600
