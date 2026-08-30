from pathlib import Path

from django.urls import reverse

ROOT = Path(__file__).resolve().parents[1]


def test_landing_is_public_and_contains_the_complete_product_story(client):
    response = client.get(reverse("home"))
    content = response.content.decode()

    assert response.status_code == 200
    for section_id in (
        "how-it-works",
        "technology",
        "science",
        "trust",
        "about",
        "contact",
    ):
        assert f'id="{section_id}"' in content
    assert "Powered by Gemini" in content
    assert "Real Sentinel-2 data" in content
    assert "38" in content and "151" in content and "94-acre" in content
    assert "Know where to scout first. In minutes." in content
    assert "field scouting" in content
    assert "field inspection" not in content
    assert "development zones" not in content
    assert "46 days after planting" in content
    assert "64 days" in content
    assert "not a guarantee" in content
    assert "3 to 12" not in content


def test_landing_calls_to_action_open_the_protected_demo(client):
    response = client.get(reverse("home"))
    content = response.content.decode()

    assert content.count(f'href="{reverse("demo")}"') >= 3
    demo = client.get(reverse("demo"))
    assert demo.status_code == 302
    assert demo.url == f"{reverse('login')}?next=%2Fdemo%2F"


def test_landing_exposes_traceable_primary_sources_and_social_metadata(client):
    content = client.get(reverse("home")).content.decode()

    assert "Lucratividade-lavoura.pdf" in content
    assert "10.1002/ppj2.70009" in content
    assert "sentiwiki.copernicus.eu/web/s2-mission" in content
    assert 'property="og:image"' in content
    assert "core/brand/og-card" in content
    assert 'name="twitter:card" content="summary_large_image"' in content


def test_landing_contact_form_has_server_backed_controls(client):
    content = client.get(reverse("home")).content.decode()

    assert f'action="{reverse("contact")}"' in content
    for name in ("email", "subject", "message", "consent", "website"):
        assert f'name="{name}"' in content
    assert "csrfmiddlewaretoken" in content
    assert "this site does not store them" in content


def test_brand_assets_are_versioned_with_the_application():
    brand = ROOT / "core" / "static" / "core" / "brand"

    assert (brand / "favicon.svg").stat().st_size > 100
    assert (brand / "og-card.svg").stat().st_size > 500
    assert (brand / "og-card.png").stat().st_size > 10_000
