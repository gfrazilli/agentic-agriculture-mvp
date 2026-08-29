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
