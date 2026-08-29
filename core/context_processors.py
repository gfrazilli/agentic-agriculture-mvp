from django.conf import settings


def product(request):  # noqa: ARG001
    return {"PRODUCT_NAME": settings.PRODUCT_NAME}
