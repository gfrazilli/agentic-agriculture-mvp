from django.urls import reverse


def test_portuguese_is_the_default_language(client):
    response = client.get(reverse("login"))

    assert response.status_code == 200
    assert "Acesso de demonstração" in response.content.decode()
    assert "Usuário" in response.content.decode()


def test_language_can_be_switched_to_english(client):
    switch = client.post(
        reverse("set_language"),
        {"language": "en", "next": reverse("login")},
    )
    response = client.get(switch.url)

    assert switch.status_code == 302
    assert response.status_code == 200
    assert "Demonstration access" in response.content.decode()
    assert "Username" in response.content.decode()


def test_language_switch_rejects_external_redirect(client):
    response = client.post(
        reverse("set_language"),
        {"language": "en", "next": "https://attacker.example/"},
    )

    assert response.status_code == 302
    assert response.url == "/"
