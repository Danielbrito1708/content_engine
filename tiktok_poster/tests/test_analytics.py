"""`GET /analytics/variants` — comparação descritiva de desempenho por variante.

Sem Buffer nem rede: `Publication`/`PostMetric` são semeadas direto no banco
pelo fixture `session`, e a rota só agrega o que já está lá.
"""
import uuid
from datetime import datetime, timedelta, timezone

from src.tiktok_poster.db.models import PostMetric, Publication

NOW = datetime.now(tz=timezone.utc)


async def _make_publication(session, **overrides) -> Publication:
    defaults = dict(
        series_id=uuid.uuid4(),
        part_number=1,
        channel="tiktok",
        buffer_post_id=f"buf_{uuid.uuid4().hex[:12]}",
        scheduled_at=NOW - timedelta(hours=48),
        hashtag_variant="tiktok:seed-a",
        timing_bucket="11h",
        template_id=None,
        tts_voice=None,
    )
    defaults.update(overrides)
    pub = Publication(**defaults)
    session.add(pub)
    await session.commit()
    await session.refresh(pub)
    return pub


async def _make_metric(session, *, publication_id, value, metric_type="views", fetched_at=None, unit="count"):
    metric = PostMetric(
        publication_id=publication_id,
        metric_type=metric_type,
        value=value,
        unit=unit,
        fetched_at=fetched_at or datetime.now(tz=timezone.utc),
    )
    session.add(metric)
    await session.commit()
    return metric


async def test_aggregation_by_timing_bucket(client, session):
    pub_11h_a = await _make_publication(session, timing_bucket="11h")
    pub_11h_b = await _make_publication(session, timing_bucket="11h")
    pub_15h = await _make_publication(session, timing_bucket="15h")

    await _make_metric(session, publication_id=pub_11h_a.id, value=100)
    await _make_metric(session, publication_id=pub_11h_b.id, value=300)
    await _make_metric(session, publication_id=pub_15h.id, value=50)

    resp = await client.get("/analytics/variants", params={"dimension": "timing_bucket"})

    assert resp.status_code == 200
    by_variant = {row["variant"]: row for row in resp.json()}
    assert by_variant["11h"]["sample_size"] == 2
    assert by_variant["11h"]["avg"] == 200
    assert by_variant["11h"]["min"] == 100
    assert by_variant["11h"]["max"] == 300
    assert by_variant["11h"]["sum"] == 400
    assert by_variant["15h"]["sample_size"] == 1


async def test_aggregation_by_hashtag_variant(client, session):
    pub_a = await _make_publication(session, hashtag_variant="tiktok:series:1")
    pub_b = await _make_publication(session, hashtag_variant="tiktok:series:2")
    await _make_metric(session, publication_id=pub_a.id, value=10)
    await _make_metric(session, publication_id=pub_b.id, value=20)

    resp = await client.get("/analytics/variants", params={"dimension": "hashtag_variant"})

    assert resp.status_code == 200
    variants = {row["variant"] for row in resp.json()}
    assert variants == {"tiktok:series:1", "tiktok:series:2"}


async def test_channel_filter(client, session):
    tiktok_pub = await _make_publication(session, channel="tiktok", timing_bucket="19h")
    youtube_pub = await _make_publication(session, channel="youtube", timing_bucket="19h")
    await _make_metric(session, publication_id=tiktok_pub.id, value=100)
    await _make_metric(session, publication_id=youtube_pub.id, value=900)

    resp = await client.get(
        "/analytics/variants", params={"dimension": "timing_bucket", "channel": "tiktok"}
    )

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["sample_size"] == 1
    assert rows[0]["avg"] == 100


async def test_missing_metric_is_excluded_not_counted_as_zero(client, session):
    """Ausência de métrica != métrica zero — não pode puxar a média para baixo."""
    has_metric = await _make_publication(session, timing_bucket="11h")
    no_metric = await _make_publication(session, timing_bucket="11h")
    await _make_metric(session, publication_id=has_metric.id, value=500)
    # `no_metric` nunca recebe um PostMetric de "views".

    resp = await client.get("/analytics/variants", params={"dimension": "timing_bucket"})

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["sample_size"] == 1
    assert row["avg"] == 500


async def test_only_the_most_recent_snapshot_counts(client, session):
    """`PostMetric` guarda snapshots ao longo do tempo — só o mais novo entra."""
    pub = await _make_publication(session, timing_bucket="15h")
    await _make_metric(session, publication_id=pub.id, value=10,
                        fetched_at=NOW - timedelta(days=2))
    await _make_metric(session, publication_id=pub.id, value=999,
                        fetched_at=NOW - timedelta(days=1))

    resp = await client.get("/analytics/variants", params={"dimension": "timing_bucket"})

    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["variant"] == "15h")
    assert row["sample_size"] == 1
    assert row["avg"] == 999


async def test_null_template_id_groups_as_none(client, session):
    pub = await _make_publication(session, template_id=None)
    await _make_metric(session, publication_id=pub.id, value=42)

    resp = await client.get("/analytics/variants", params={"dimension": "template_id"})

    assert resp.status_code == 200
    variants = {row["variant"] for row in resp.json()}
    assert "none" in variants


async def test_different_metric_type(client, session):
    pub = await _make_publication(session)
    await _make_metric(session, publication_id=pub.id, value=7, metric_type="likes")
    await _make_metric(session, publication_id=pub.id, value=999, metric_type="views")

    resp = await client.get(
        "/analytics/variants", params={"dimension": "timing_bucket", "metric_type": "likes"}
    )

    assert resp.status_code == 200
    assert resp.json()[0]["avg"] == 7


async def test_invalid_dimension_returns_422(client):
    resp = await client.get("/analytics/variants", params={"dimension": "not_a_real_column"})
    assert resp.status_code == 422


async def test_missing_dimension_returns_422(client):
    resp = await client.get("/analytics/variants")
    assert resp.status_code == 422


async def test_empty_metric_type_returns_422(client):
    resp = await client.get(
        "/analytics/variants", params={"dimension": "timing_bucket", "metric_type": ""}
    )
    assert resp.status_code == 422


async def test_invalid_channel_returns_422(client):
    resp = await client.get(
        "/analytics/variants", params={"dimension": "timing_bucket", "channel": "instagram"}
    )
    assert resp.status_code == 422
