import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
MEDIA_DIR = DATA_DIR / 'media'
DB_PATH = DATA_DIR / 'adops.db'
WEB_DIR = BASE_DIR / 'web'

# All external paths are configurable via environment variables so the app is
# portable. Sensible per-user defaults live under a single config directory
# (~/.adops), overridable with ADOPS_CONFIG_DIR.
CONFIG_DIR = Path(os.environ.get('ADOPS_CONFIG_DIR', Path.home() / '.adops'))
ENV_FILE = Path(os.environ.get('ADOPS_ENV_FILE', CONFIG_DIR / '.env'))
# Root of your own knowledge base / client files (used by the optional seed).
VAULT_ROOT = Path(os.environ.get('ADOPS_VAULT_ROOT', CONFIG_DIR / 'vault'))
CLIENTS_ROOT = VAULT_ROOT / 'clients'
META_ACCOUNTS_JSON = Path(os.environ.get('ADOPS_META_ACCOUNTS', CONFIG_DIR / 'meta-accounts.json'))
GA4_SERVICE_ACCOUNT = Path(os.environ.get('ADOPS_GA4_SERVICE_ACCOUNT', CONFIG_DIR / 'ga4-service-account.json'))
GSC_SERVICE_ACCOUNT = GA4_SERVICE_ACCOUNT  # same key; needs per-site GSC user grant
GOOGLE_ADS_YAML = Path(os.environ.get('ADOPS_GOOGLE_ADS_YAML', Path.home() / 'google-ads.yaml'))
LINKEDIN_TOKENS = Path(os.environ.get('ADOPS_LINKEDIN_TOKENS', CONFIG_DIR / 'linkedin-tokens.json'))
GBP_TOKENS = Path(os.environ.get('ADOPS_GBP_TOKENS', CONFIG_DIR / 'gbp-tokens.json'))
# Optional directory of extra connector modules added to sys.path at runtime.
CONNECTORS_DIR = Path(os.environ.get('ADOPS_CONNECTORS', CONFIG_DIR / 'connectors'))
# WordPress app passwords, keyed by site slug ({url, username, password}). Used by
# app/publish/media_host.py to host images on a public URL, which is the only way
# Instagram will accept media -- its container API makes Meta fetch the URL itself.
WP_TARGETS = Path(os.environ.get('ADOPS_WP_TARGETS', CONFIG_DIR / 'wp-targets.json'))

load_dotenv(ENV_FILE)

APIFY_TOKEN = os.environ.get('APIFY_TOKEN', '')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')

for d in (MEDIA_DIR / 'thumbnails', MEDIA_DIR / 'reference', MEDIA_DIR / 'generated',
          MEDIA_DIR / 'logos', MEDIA_DIR / 'uploads'):
    d.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    'apify_actor_ad_library': 'curious_coder/facebook-ads-library-scraper',
    'apify_actor_google_ads': 'scrapesage/google-ads-transparency-scraper',
    'apify_actor_linkedin_ads': 's-r/linkedin-ads-library',
    'apify_actor_posts': 'apify/facebook-posts-scraper',
    'apify_actor_reddit': 'trudax/reddit-scraper-lite',
    'gemini_text_model': 'gemini-2.5-flash',
    'gemini_vision_model': 'gemini-2.5-flash',
    'gemini_image_model': 'gemini-2.5-flash-image',
    'sync_daily_time': '06:30',
    'scan_weekly_day': 'mon',
    'scan_weekly_time': '06:00',
    'default_country': 'ZA',
    'fatigue_min_spend': '500',
    'fatigue_ctr_drop_pct': '25',
    'grader_min_spend': '300',
    'grader_lookback_days': '14',
    'grader_scale_budget_pct': '20',
    'pipeline_daily_time': '07:00',
    'pipeline_ads_per_day': '5',
    'pipeline_enabled_clients': '',
    'landing_max_per_competitor': '2',
    # Publishing. publish_enabled_clients is an empty allow-list by design: no client
    # auto-publishes until its slug is added here, mirroring pipeline_enabled_clients.
    'publish_tick_minutes': '5',
    'publish_catchup_minutes': '30',
    'publish_enabled_clients': '',
    'wp_media_host_domain': 'example',
    # 202506 was expired and returned HTTP 426 on every call; 202607 verified 2026-07-30.
    # Read by both app/sync/linkedin_sync.py and the LinkedIn publish adapter.
    'linkedin_api_version': '202607',
}
