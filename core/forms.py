from django import forms
from django.utils.translation import gettext_lazy as _


class LoginForm(forms.Form):
    username = forms.CharField(
        label=_("Username"),
        max_length=150,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "username",
                "autocapitalize": "none",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class ContactForm(forms.Form):
    """Validate the public contact form without persisting its contents."""

    email = forms.EmailField(
        label=_("Email"),
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    subject = forms.CharField(
        label=_("Subject"),
        min_length=3,
        max_length=120,
        strip=True,
    )
    message = forms.CharField(
        label=_("Message"),
        min_length=10,
        max_length=4_000,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 6}),
    )
    consent = forms.BooleanField(
        label=_("I agree that my email may be used only to reply to this message."),
        required=True,
    )
    website = forms.CharField(
        required=False,
        max_length=200,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "tabindex": "-1",
                "aria-hidden": "true",
            }
        ),
    )
    turnstile_token = forms.CharField(
        required=False,
        max_length=2_048,
        strip=True,
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args, **kwargs):
        # Cloudflare's widget submits this conventional field name. Supporting
        # it here keeps views thin while retaining a stable internal field name.
        if args and args[0] is not None:
            data = args[0].copy()
            if not data.get("turnstile_token") and data.get("cf-turnstile-response"):
                data["turnstile_token"] = data.get("cf-turnstile-response")
            args = (data, *args[1:])
        elif kwargs.get("data") is not None:
            data = kwargs["data"].copy()
            if not data.get("turnstile_token") and data.get("cf-turnstile-response"):
                data["turnstile_token"] = data.get("cf-turnstile-response")
            kwargs["data"] = data
        super().__init__(*args, **kwargs)

    def clean_subject(self) -> str:
        subject = self.cleaned_data["subject"]
        if "\r" in subject or "\n" in subject:
            raise forms.ValidationError(_("Enter a subject on one line."), code="header_injection")
        return subject

    def clean_website(self) -> str:
        if self.cleaned_data["website"]:
            raise forms.ValidationError(
                _("Unable to submit this message."),
                code="spam_detected",
            )
        return ""
