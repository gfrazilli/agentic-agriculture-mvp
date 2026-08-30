from django.urls import reverse

from core.contact import ContactDeliveryError


def switch_to_portuguese(client, next_url):
    return client.post(
        reverse("set_language"),
        {"language": "pt-br", "next": next_url},
    )


def test_english_is_the_default_language(client):
    response = client.get(reverse("login"))

    assert response.status_code == 200
    assert "Demonstration access" in response.content.decode()
    assert "Username" in response.content.decode()


def test_language_can_be_switched_to_portuguese(client):
    switch = switch_to_portuguese(client, reverse("login"))
    response = client.get(switch.url)

    assert switch.status_code == 302
    assert response.status_code == 200
    assert "Acesso de demonstração" in response.content.decode()
    assert "Usuário" in response.content.decode()


def test_landing_story_and_science_are_available_in_portuguese(client):
    switch = switch_to_portuguese(client, reverse("home"))
    response = client.get(switch.url)
    content = response.content.decode()

    assert response.status_code == 200
    assert "Descubra onde sua lavoura merece atenção. Em poucos minutos." in content
    assert "A inteligência artificial avançada faz análises técnicas para te ajudar." in content
    assert "Uma área plantada não se desenvolve toda por igual." in content
    assert "Do satélite à próxima visita em campo." in content
    assert "Uma safra inteira de dados. Uma explicação que faz sentido." in content
    assert "A lavoura reflete sinais que nossos olhos não conseguem ver." in content
    assert "talhão" not in content.lower()
    assert "zona" not in content.lower()
    assert "não é uma média" not in content.lower()
    assert "Faixa de produtividade e dimensão da área: página 2." in content
    assert "Seu e-mail, assunto e mensagem são enviados pelo Resend" in content


def test_contact_validation_error_is_translated_to_portuguese(client):
    switch_to_portuguese(client, reverse("home"))

    response = client.post(
        reverse("contact"),
        {
            "email": "produtor@example.com",
            "subject": "Dúvida\nBcc: target@example.com",
            "message": "Quero acompanhar minha lavoura durante esta safra.",
            "consent": "on",
            "website": "",
            "turnstile_token": "verified-token",
        },
    )
    content = response.content.decode()

    assert response.status_code == 400
    assert "Confira os campos abaixo ou tente novamente em alguns instantes." in content
    assert "Digite o assunto em uma única linha." in content


def test_contact_delivery_error_is_translated_to_portuguese(client, monkeypatch):
    def fail_delivery(*args, **kwargs):
        raise ContactDeliveryError("provider details")

    switch_to_portuguese(client, reverse("home"))
    monkeypatch.setattr("core.views.ContactService.submit", fail_delivery)
    response = client.post(
        reverse("contact"),
        {
            "email": "produtor@example.com",
            "subject": "Acompanhar lavoura",
            "message": "Quero acompanhar minha lavoura durante esta safra.",
            "consent": "on",
            "website": "",
            "turnstile_token": "verified-token",
        },
    )
    content = response.content.decode()

    assert response.status_code == 502
    assert "Não foi possível enviar sua mensagem agora. Tente novamente." in content
    assert "provider details" not in content


def test_language_switch_rejects_external_redirect(client):
    response = client.post(
        reverse("set_language"),
        {"language": "en", "next": "https://attacker.example/"},
    )

    assert response.status_code == 302
    assert response.url == "/"
