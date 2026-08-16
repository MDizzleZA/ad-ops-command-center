"""FAIS/POPIA compliance guardrails for AI-generated ad content.

Three layers (the third lives in overlay.py):
1. prompt_block() - rules embedded in every generation prompt
2. audit() - post-generation structured review, stored in briefs.compliance_json
"""
import json

from app import db
from app.services import gemini

AUDIT_SCHEMA = {
    'type': 'object',
    'properties': {
        'status': {'type': 'string', 'enum': ['pass', 'warn', 'block']},
        'violations': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'rule': {'type': 'string'},
                    'severity': {'type': 'string', 'enum': ['block', 'warn']},
                    'phrase': {'type': 'string'},
                    'fix': {'type': 'string'},
                },
                'required': ['rule', 'severity', 'phrase', 'fix'],
            },
        },
    },
    'required': ['status', 'violations'],
}


def rules_for(client_id: int | None) -> list[dict]:
    if client_id:
        return db.rows('SELECT * FROM compliance_rules WHERE client_id=? OR client_id IS NULL '
                       'ORDER BY severity, rule_type', (client_id,))
    return db.rows('SELECT * FROM compliance_rules WHERE client_id IS NULL ORDER BY severity, rule_type')


def prompt_block(client_id: int | None) -> str:
    rules = rules_for(client_id)
    if not rules:
        return ''
    lines = ['', '## COMPLIANCE RULES (non-negotiable - South African FAIS/FSCA/POPIA)']
    for r in rules:
        prefix = {'prohibited': 'NEVER', 'required': 'ALWAYS', 'disclaimer': 'DISCLAIMER'}[r['rule_type']]
        lines.append(f"- [{prefix}] {r['rule_text']}")
    lines.append('Violating a NEVER rule makes the output unusable. When in doubt, use factual, '
                 'educational, invitation-to-consult language.')
    return '\n'.join(lines)


def audit(content_text: str, client_id: int | None) -> dict:
    """Second-model review of generated content against the client's rules."""
    rules = rules_for(client_id)
    rules_text = '\n'.join(f"{i + 1}. [{r['severity'].upper()}/{r['rule_type']}] {r['rule_text']}"
                           for i, r in enumerate(rules))
    prompt = f"""You are a South African financial-services marketing compliance officer (FAIS General
Code of Conduct s14/s15, FSCA Conduct Standard 1/2020, TCF, POPIA). Audit the ad content below
against these rules. Be strict: FSCA penalties reached R943 million in 2023/24.

RULES:
{rules_text}

CONTENT TO AUDIT:
---
{content_text}
---

Report every violation with the exact offending phrase and a compliant fix. status = 'block' if any
block-severity rule is violated, 'warn' if only warn-severity issues, else 'pass'. A missing required
disclaimer counts as a violation of that rule. Do not invent violations for rules that clearly do not
apply to this content type."""
    result = gemini.gen_text(prompt, schema=AUDIT_SCHEMA)
    # Defensive: status must reflect worst violation severity
    severities = {v['severity'] for v in result.get('violations', [])}
    if 'block' in severities:
        result['status'] = 'block'
    elif 'warn' in severities and result['status'] == 'pass':
        result['status'] = 'warn'
    return result


def audit_and_store(brief_id: int) -> dict:
    brief = db.row('SELECT * FROM briefs WHERE id=?', (brief_id,))
    if not brief:
        raise ValueError('brief not found')
    payload = db.jloads(brief['brief_json'], {})
    text = '\n'.join(str(payload.get(k) or '') for k in
                     ('hook', 'headline', 'primary_text', 'cta', 'visual_direction'))
    result = audit(text, brief['client_id'])
    db.execute('UPDATE briefs SET compliance_json=? WHERE id=?', (json.dumps(result), brief_id))
    return result
