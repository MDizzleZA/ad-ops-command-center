"""Seed the Ad Ops database with a small fictional demo dataset.

This populates a couple of example clients, a brand profile, personas, a generic
compliance ruleset, competitors, ad accounts, and a few reference ads so a fresh
install has something to look at. All data below is invented — replace it with
your own, or point the app at your real accounts and let the sync jobs fill the
database.

Idempotent: uses slug/name upserts, safe to re-run. `--review` prints what would
be written without touching the DB.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.config import CLIENTS_ROOT

GENERIC_DISCLAIMER = ('This content is for informational purposes only and does not '
                      'constitute professional advice. Terms and conditions apply.')

CLIENTS = [
    {
        'name': 'Acme Corp', 'slug': 'acme-corp', 'status': 'active',
        'industry': 'Example industry (demo client)',
        'monthly_budget_zar': 40000,
        'kpi_json': {
            'budget_split': {'meta': 0.40, 'google': 0.30, 'linkedin': 0.25, 'retargeting': 0.05},
            'cpl_target': {'meta': [200, 600], 'linkedin': [800, 2000], 'blended': [300, 900]},
            'leads_per_month': [30, 80],
            'lead_to_client_pct': [15, 30],
        },
        'vault_path': str(CLIENTS_ROOT / 'Acme Corp'),
        'notes': 'Demo client. Replace with your own or delete once you add real clients.',
    },
    {
        'name': 'Globex (house account)', 'slug': 'globex', 'status': 'active',
        'industry': 'Digital marketing (demo house account)', 'monthly_budget_zar': None,
        'kpi_json': None, 'vault_path': str(CLIENTS_ROOT / 'Globex'),
        'notes': 'Example house account.',
    },
    {
        'name': 'Initech', 'slug': 'initech', 'status': 'prospect',
        'industry': 'Ecommerce (demo prospect)', 'monthly_budget_zar': None, 'kpi_json': None,
        'vault_path': str(CLIENTS_ROOT / 'Initech'),
        'notes': 'Example prospect at proposal stage.',
    },
]

DEMO_BRAND = {
    'colors_json': [
        {'name': 'Primary', 'hex': '#2B5CE6', 'role': 'primary', 'usage': 'Primary brand, headings'},
        {'name': 'Accent', 'hex': '#F5A623', 'role': 'accent', 'usage': 'Highlights, CTAs (sparingly)'},
        {'name': 'Ink', 'hex': '#1A1A2E', 'role': 'text', 'usage': 'Body text'},
        {'name': 'Positive', 'hex': '#3FB950', 'role': 'positive', 'usage': 'Growth indicators'},
    ],
    'fonts_json': [
        {'name': 'Inter', 'weights': 'Regular, SemiBold, Light', 'usage': 'All copy, UI, CTAs'},
        {'name': 'Fraunces', 'usage': 'Display headlines only'},
    ],
    'tagline': 'BUILT FOR GROWTH',
    'tone_of_voice': ('Confident, clear, helpful. Speaks to outcomes, avoids jargon and hype. '
                      'No guaranteed-return or fear-based language.'),
    'ad_specs_json': {
        'boosted': '1080x1080 (square)',
        'facebook_organic': '1080x1080', 'linkedin': '1080x1080',
        'instagram_feed': '1080x1350', 'reels': '1920x1080',
        'headline': 'SemiBold, left or right aligned',
        'subheader': 'Regular',
    },
    'disclaimer_text': GENERIC_DISCLAIMER,
    'style_rules': ('Keep the logo clear of busy backgrounds. Product names in display font. '
                    'Photography: candid, positive, diverse. Replace with your own brand rules.'),
    'source_path': 'demo',
}

DEMO_PERSONAS = [
    {
        'name': 'Alex', 'headline': 'Alex, 34 - operations lead evaluating a new vendor',
        'demographics_json': {'age': 34, 'profession': 'Operations lead', 'segment': 'SMB decision-maker',
                              'location': 'Metro', 'investable': 'Departmental budget'},
        'pain_points_json': ['Wary of long contracts', 'Needs proof it integrates with existing tools',
                             'Limited time to evaluate options'],
        'triggers_json': ['Current tool being sunset', 'Team growth outpacing current process',
                          'Budget cycle planning'],
        'objections_json': ['How long is onboarding?', 'What does it cost at our scale?'],
    },
    {
        'name': 'Sam', 'headline': 'Sam, 46 - founder comparing agencies',
        'demographics_json': {'age': 46, 'profession': 'Founder', 'segment': 'Owner-operator',
                              'location': 'Metro', 'investable': 'Marketing budget'},
        'pain_points_json': ['Burned by a previous agency', 'Wants clear reporting, not vanity metrics',
                             'Cautious about spend without results'],
        'triggers_json': ['Flat growth quarter', 'New product launch', 'Competitor gaining share'],
        'objections_json': ['Can I see case studies?', 'What happens if it does not work?'],
    },
]

# severity 'block' = must never appear; 'warn' = needs review/substantiation.
# A generic, editable advertising-standards ruleset. Replace with the rules that
# apply to your industry and jurisdiction.
COMPLIANCE_RULES = {
    'client': [
        ('Standards', 'required', 'Include the client disclaimer verbatim where required: "'
         + GENERIC_DISCLAIMER + '".', 'block'),
        ('Standards', 'prohibited', 'No guaranteed results or performance claims '
         '("guaranteed", "risk-free", "assured returns").', 'block'),
        ('Standards', 'required', 'When a percentage or statistic appears, include the source and basis '
         'of calculation.', 'warn'),
    ],
    'framework': [
        ('Standards', 'prohibited', 'Superlatives without substantiation: "best", "leading", "#1" — '
         'replace with factual positioning.', 'block'),
        ('Standards', 'prohibited', 'Urgency/scarcity pressure tactics: "act now", "limited time" — '
         'unless genuinely true and substantiated.', 'warn'),
        ('Standards', 'prohibited', 'Competitor disparagement without disclosing material differences '
         'and substantiation.', 'block'),
        ('Standards', 'prohibited', 'Unsourced statistics. Every quantitative claim needs a source and date.', 'block'),
        ('Privacy', 'required', 'Lead capture requires affirmative opt-in (no pre-ticked boxes), a stated '
         'purpose, and a privacy-policy link.', 'warn'),
    ],
}

DEMO_COMPETITORS = [
    {'name': 'Competitor One', 'website': 'https://example-one.test',
     'fb_page_url': 'https://www.facebook.com/example-one',
     'notes': 'Demo competitor. Replace with a real competitor to enable ad-library scans.'},
    {'name': 'Competitor Two', 'website': 'https://example-two.test',
     'fb_page_url': 'https://www.facebook.com/example-two',
     'notes': 'Demo competitor.'},
]

DEMO_REFERENCE_ADS = [
    {'page_name': 'Acme Corp', 'format': 'image',
     'headline': 'Stop guessing. Start growing.',
     'body': 'See how teams like yours cut cost-per-lead in a quarter. Book a free walkthrough.',
     'cta': 'Learn More',
     'raw_json': json.dumps({'title': 'Demo ad 1'}, ensure_ascii=False),
     'tags': 'demo,seed'},
    {'page_name': 'Acme Corp', 'format': 'image',
     'headline': 'Reporting you can actually read.',
     'body': 'Clear dashboards, no vanity metrics. Know exactly where every rand goes.',
     'cta': 'Sign Up',
     'raw_json': json.dumps({'title': 'Demo ad 2'}, ensure_ascii=False),
     'tags': 'demo,seed'},
]


def seed(review: bool = False):
    db.migrate()

    def report(msg):
        print(('[review] ' if review else '[seed] ') + msg)

    # --- Clients ---
    client_ids = {}
    for c in CLIENTS:
        kpi = json.dumps(c['kpi_json']) if c['kpi_json'] else None
        if not review:
            db.execute(
                'INSERT INTO clients (name, slug, status, industry, monthly_budget_zar, kpi_json, vault_path, notes) '
                'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET status=excluded.status, '
                'industry=excluded.industry, monthly_budget_zar=excluded.monthly_budget_zar, '
                'kpi_json=excluded.kpi_json, vault_path=excluded.vault_path, notes=excluded.notes',
                (c['name'], c['slug'], c['status'], c['industry'], c['monthly_budget_zar'], kpi,
                 c['vault_path'], c['notes']))
            client_ids[c['slug']] = db.row('SELECT id FROM clients WHERE slug=?', (c['slug'],))['id']
        report(f"client: {c['name']} ({c['status']})")

    if review:
        print(f'[review] brand profile, {len(DEMO_PERSONAS)} personas, '
              f"{len(COMPLIANCE_RULES['client']) + len(COMPLIANCE_RULES['framework'])} compliance rules, "
              f'{len(DEMO_COMPETITORS)} competitors, {len(DEMO_REFERENCE_ADS)} reference ads')
        return

    primary = client_ids['acme-corp']

    # --- Brand profile ---
    b = DEMO_BRAND
    db.execute(
        'INSERT INTO brand_profiles (client_id, colors_json, fonts_json, logo_path, logo_dark_path, tagline, '
        'tone_of_voice, ad_specs_json, disclaimer_text, style_rules, source_path) VALUES (?,?,?,?,?,?,?,?,?,?,?) '
        'ON CONFLICT(client_id) DO UPDATE SET colors_json=excluded.colors_json, fonts_json=excluded.fonts_json, '
        'logo_path=excluded.logo_path, logo_dark_path=excluded.logo_dark_path, tagline=excluded.tagline, '
        'tone_of_voice=excluded.tone_of_voice, ad_specs_json=excluded.ad_specs_json, '
        'disclaimer_text=excluded.disclaimer_text, style_rules=excluded.style_rules, source_path=excluded.source_path',
        (primary, json.dumps(b['colors_json']), json.dumps(b['fonts_json']), None, None,
         b['tagline'], b['tone_of_voice'], json.dumps(b['ad_specs_json']), b['disclaimer_text'],
         b['style_rules'], b['source_path']))
    report('brand profile: Acme Corp')

    # --- Personas ---
    src = 'demo'
    for p in DEMO_PERSONAS:
        existing = db.row('SELECT id FROM personas WHERE client_id=? AND name=?', (primary, p['name']))
        vals = (p['headline'], json.dumps(p['demographics_json']), json.dumps(p['pain_points_json']),
                json.dumps(p['triggers_json']), json.dumps(p['objections_json']), src)
        if existing:
            db.execute('UPDATE personas SET headline=?, demographics_json=?, pain_points_json=?, '
                       'triggers_json=?, objections_json=?, source_path=? WHERE id=?', vals + (existing['id'],))
        else:
            db.execute('INSERT INTO personas (client_id, name, headline, demographics_json, pain_points_json, '
                       'triggers_json, objections_json, source_path) VALUES (?,?,?,?,?,?,?,?)',
                       (primary, p['name']) + vals[:-1] + (src,))
    report(f'personas: {len(DEMO_PERSONAS)} (Acme Corp)')

    # --- Compliance rules (replace wholesale) ---
    db.execute('DELETE FROM compliance_rules')
    for framework, rtype, rule, severity in COMPLIANCE_RULES['client']:
        db.execute('INSERT INTO compliance_rules (client_id, framework, rule_type, rule_text, severity) '
                   'VALUES (?,?,?,?,?)', (primary, framework, rtype, rule, severity))
    for framework, rtype, rule, severity in COMPLIANCE_RULES['framework']:
        db.execute('INSERT INTO compliance_rules (client_id, framework, rule_type, rule_text, severity) '
                   'VALUES (NULL,?,?,?,?)', (framework, rtype, rule, severity))
    report(f"compliance rules: {len(COMPLIANCE_RULES['client'])} client + {len(COMPLIANCE_RULES['framework'])} framework")

    # --- Competitors ---
    for comp in DEMO_COMPETITORS:
        existing = db.row('SELECT id FROM competitors WHERE client_id=? AND name=?', (primary, comp['name']))
        if not existing:
            db.execute('INSERT INTO competitors (client_id, name, fb_page_url, website, notes, source_path) '
                       'VALUES (?,?,?,?,?,?)',
                       (primary, comp['name'], comp['fb_page_url'], comp['website'], comp['notes'], 'demo'))
    report(f'competitors: {len(DEMO_COMPETITORS)} (Acme Corp)')

    # --- Ad accounts (placeholder external IDs — swap for your own) ---
    accounts = [
        (primary, 'ga4', '000000000', 'acme-ga4', 'ZAR', None),
        (primary, 'linkedin', '000000000', 'acme-linkedin', 'ZAR', None),
        (primary, 'google', '0000000000', 'acme-google', 'ZAR',
         json.dumps({'note': 'Replace with your Google Ads customer ID.'})),
    ]
    for client_id, platform, ext_id, alias, currency, config in accounts:
        db.execute('INSERT INTO ad_accounts (client_id, platform, external_id, alias, currency, config_json) '
                   'VALUES (?,?,?,?,?,?) ON CONFLICT(platform, external_id) DO UPDATE SET alias=excluded.alias, '
                   'config_json=excluded.config_json', (client_id, platform, ext_id, alias, currency, config))
    report(f'ad accounts: {len(accounts)}')

    # --- Reference ads ---
    inserted = 0
    for ad in DEMO_REFERENCE_ADS:
        exists = db.row('SELECT id FROM reference_ads WHERE source=? AND headline=? AND client_id=?',
                        ('demo', ad['headline'], primary))
        if not exists:
            db.execute('INSERT INTO reference_ads (source, client_id, platform, page_name, format, headline, '
                       'body, cta, raw_json, tags) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       ('demo', primary, 'meta', ad['page_name'], ad['format'], ad['headline'],
                        ad['body'], ad['cta'], ad['raw_json'], ad['tags']))
            inserted += 1
    report(f'reference ads: {inserted} new / {len(DEMO_REFERENCE_ADS)} defined')
    report('done')


if __name__ == '__main__':
    seed(review='--review' in sys.argv)
