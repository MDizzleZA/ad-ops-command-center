"""GA4 sync: daily website analytics via the Analytics Data API (Data API v1beta).

Auth uses the service account json at app.config.GA4_SERVICE_ACCOUNT
(your-service-account@your-project.iam.gserviceaccount.com). account['external_id'] is
the numeric GA4 property id (e.g. '123456789').

Five reports per sync window:
  1. date x sessionDefaultChannelGroup with sessions/activeUsers/screenPageViews/
     keyEvents/purchaseRevenue (falls back to the older 'conversions' metric if
     the property rejects keyEvents, and drops purchaseRevenue if rejected).
  2. date x eventName filtered to eventName == 'form_submit'; eventCount -> leads.
  3. date x landingPage sessions -> top 10 landing pages per day.
  4. date x deviceCategory sessions -> device split per day.
  5. date x eventName keyEvents (nonzero only) -> named key-event counts per day.

Only account-level rows are written (GA4 has no campaign spend): spend=0,
leads=form_submit count, conversions=keyEvents total, revenue=purchaseRevenue,
extra_json carries sessions/users/pageviews plus channels/landing_pages/devices/
key_events breakdowns. Note: activeUsers summed across channel rows can slightly
overcount unique users per day.
"""
import json
from collections import defaultdict

from app import config
from app.sync import base


def _client():
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
    except ImportError as exc:
        raise RuntimeError('google-analytics-data library not installed — '
                           'run: pip install google-analytics-data') from exc
    if not config.GA4_SERVICE_ACCOUNT.exists():
        raise RuntimeError(f'GA4 service account key not found at {config.GA4_SERVICE_ACCOUNT}')
    try:
        return BetaAnalyticsDataClient.from_service_account_file(str(config.GA4_SERVICE_ACCOUNT))
    except Exception as exc:
        raise RuntimeError(f'Failed to load GA4 service account credentials: {exc}') from exc


def _iso(ga4_date: str) -> str:
    """GA4 'date' dimension is YYYYMMDD — convert to YYYY-MM-DD."""
    return f'{ga4_date[:4]}-{ga4_date[4:6]}-{ga4_date[6:]}'


def _run_channel_report(client, property_id: str, date_from: str, date_to: str):
    """Report 1: per date+channel sessions/users/pageviews/keyEvents/revenue.

    Returns (rows, conversions_metric_name, has_revenue). Falls back from
    keyEvents to the legacy 'conversions' metric if the API rejects the
    request, and drops purchaseRevenue for properties that reject it.
    """
    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    def build(conv_metric: str, with_revenue: bool) -> RunReportRequest:
        metrics = [Metric(name='sessions'), Metric(name='activeUsers'),
                   Metric(name='screenPageViews'), Metric(name=conv_metric)]
        if with_revenue:
            metrics.append(Metric(name='purchaseRevenue'))
        return RunReportRequest(
            property=f'properties/{property_id}',
            date_ranges=[DateRange(start_date=date_from, end_date=date_to)],
            dimensions=[Dimension(name='date'), Dimension(name='sessionDefaultChannelGroup')],
            metrics=metrics,
            limit=100000,
        )

    attempts = (('keyEvents', True), ('keyEvents', False),
                ('conversions', True), ('conversions', False))
    first_exc = None
    for conv_metric, with_revenue in attempts:
        try:
            rows = client.run_report(build(conv_metric, with_revenue)).rows
            return rows, conv_metric, with_revenue
        except Exception as exc:
            first_exc = first_exc or exc
    raise RuntimeError(
        f'GA4 channel report failed for property {property_id} '
        f'({date_from}..{date_to}): {first_exc}') from first_exc


def _run_form_submit_report(client, property_id: str, date_from: str, date_to: str):
    """Report 2: per-date form_submit eventCount (leads)."""
    from google.analytics.data_v1beta.types import (
        DateRange, Dimension, Filter, FilterExpression, Metric, RunReportRequest,
    )

    request = RunReportRequest(
        property=f'properties/{property_id}',
        date_ranges=[DateRange(start_date=date_from, end_date=date_to)],
        dimensions=[Dimension(name='date'), Dimension(name='eventName')],
        metrics=[Metric(name='eventCount')],
        dimension_filter=FilterExpression(filter=Filter(
            field_name='eventName',
            string_filter=Filter.StringFilter(
                value='form_submit', match_type=Filter.StringFilter.MatchType.EXACT),
        )),
        limit=100000,
    )
    try:
        return client.run_report(request).rows
    except Exception as exc:
        raise RuntimeError(
            f'GA4 form_submit report failed for property {property_id} '
            f'({date_from}..{date_to}): {exc}') from exc


def _run_simple_report(client, property_id: str, date_from: str, date_to: str,
                       dimension: str, metric: str, label: str):
    """Shared shape for reports 3-5: date x <dimension> with one metric."""
    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    request = RunReportRequest(
        property=f'properties/{property_id}',
        date_ranges=[DateRange(start_date=date_from, end_date=date_to)],
        dimensions=[Dimension(name='date'), Dimension(name=dimension)],
        metrics=[Metric(name=metric)],
        limit=100000,
    )
    try:
        return client.run_report(request).rows
    except Exception as exc:
        raise RuntimeError(
            f'GA4 {label} report failed for property {property_id} '
            f'({date_from}..{date_to}): {exc}') from exc


TOP_LANDING_PAGES = 10


def _run_landing_page_report(client, property_id: str, date_from: str, date_to: str):
    """Report 3: per date+landingPage sessions."""
    return _run_simple_report(client, property_id, date_from, date_to,
                              'landingPage', 'sessions', 'landing page')


def _run_device_report(client, property_id: str, date_from: str, date_to: str):
    """Report 4: per date+deviceCategory sessions."""
    return _run_simple_report(client, property_id, date_from, date_to,
                              'deviceCategory', 'sessions', 'device')


def _run_key_events_report(client, property_id: str, date_from: str, date_to: str,
                           conv_metric: str):
    """Report 5: per date+eventName key-event counts (metric name matches
    whatever the channel report negotiated: keyEvents or conversions)."""
    return _run_simple_report(client, property_id, date_from, date_to,
                              'eventName', conv_metric, 'key events')


def sync(account: dict, date_from: str, date_to: str) -> int:
    """Write one account-level metrics row per date in the window."""
    client = _client()
    property_id = str(account['external_id']).strip()
    if not property_id.isdigit():
        raise RuntimeError(f"GA4 external_id must be a numeric property id, got {property_id!r}")

    channel_rows, conv_metric, has_revenue = _run_channel_report(
        client, property_id, date_from, date_to)
    form_rows = _run_form_submit_report(client, property_id, date_from, date_to)
    landing_rows = _run_landing_page_report(client, property_id, date_from, date_to)
    device_rows = _run_device_report(client, property_id, date_from, date_to)
    event_rows = _run_key_events_report(client, property_id, date_from, date_to, conv_metric)

    def empty_bucket():
        return {'sessions': 0, 'users': 0, 'pageviews': 0, 'key_events': 0.0,
                'revenue': 0.0, 'channels': {}, 'landing_pages': {}, 'devices': {},
                'events': {}}

    days: dict[str, dict] = defaultdict(empty_bucket)
    for row in channel_rows:
        day = _iso(row.dimension_values[0].value)
        channel = row.dimension_values[1].value or '(other)'
        sessions = int(float(row.metric_values[0].value or 0))
        bucket = days[day]
        bucket['sessions'] += sessions
        bucket['users'] += int(float(row.metric_values[1].value or 0))
        bucket['pageviews'] += int(float(row.metric_values[2].value or 0))
        bucket['key_events'] += float(row.metric_values[3].value or 0)
        if has_revenue:
            bucket['revenue'] += float(row.metric_values[4].value or 0)
        bucket['channels'][channel] = bucket['channels'].get(channel, 0) + sessions

    for row in landing_rows:
        day = _iso(row.dimension_values[0].value)
        page = row.dimension_values[1].value or '(not set)'
        days[day]['landing_pages'][page] = (
            days[day]['landing_pages'].get(page, 0) + int(float(row.metric_values[0].value or 0)))

    for row in device_rows:
        day = _iso(row.dimension_values[0].value)
        device = row.dimension_values[1].value or '(other)'
        days[day]['devices'][device] = (
            days[day]['devices'].get(device, 0) + int(float(row.metric_values[0].value or 0)))

    for row in event_rows:
        count = float(row.metric_values[0].value or 0)
        if count <= 0:
            continue
        day = _iso(row.dimension_values[0].value)
        event = row.dimension_values[1].value or '(not set)'
        days[day]['events'][event] = days[day]['events'].get(event, 0) + count

    leads_by_day: dict[str, int] = defaultdict(int)
    for row in form_rows:
        leads_by_day[_iso(row.dimension_values[0].value)] += int(
            float(row.metric_values[0].value or 0))

    rows_written = 0
    for day in sorted(set(days) | set(leads_by_day)):
        bucket = days.get(day) or empty_bucket()
        top_landing = sorted(bucket['landing_pages'].items(),
                             key=lambda kv: kv[1], reverse=True)[:TOP_LANDING_PAGES]
        base.upsert_metric(
            account['id'], 'account', '', day,
            spend=0, leads=leads_by_day.get(day, 0), conversions=bucket['key_events'],
            revenue=bucket['revenue'],
            extra_json=json.dumps({
                'sessions': bucket['sessions'],
                'users': bucket['users'],
                'pageviews': bucket['pageviews'],
                'channels': bucket['channels'],
                'landing_pages': [{'path': p, 'sessions': s} for p, s in top_landing],
                'devices': bucket['devices'],
                'key_events': bucket['events'],
                'conversions_metric': conv_metric,
            }))
        rows_written += 1

    return rows_written
