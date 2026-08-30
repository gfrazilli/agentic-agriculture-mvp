"""Prepare or reuse one authorized real Sentinel demonstration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from agriculture.container import get_analysis_pipeline, get_repository
from agriculture.demo import (
    RealDemoPreparationError,
    RealDemoSpec,
    build_redacted_demo_manifest,
    prepare_real_demo,
)

_MAX_PRIVATE_INPUT_BYTES = 256 * 1024
_PRIVATE_REPOSITORY_DIRECTORY = ".private-demo"


def resolve_private_input_path(raw_path: str) -> Path:
    """Allow external inputs or the repository's explicitly ignored private directory."""

    candidate = Path(raw_path).expanduser()
    try:
        path = candidate.resolve(strict=True)
    except OSError as exc:
        raise CommandError("The private demo input file could not be opened.") from exc
    if not path.is_file() or path.suffix.lower() != ".json":
        raise CommandError("The private demo input must be a regular JSON file.")

    repository_root = Path(settings.BASE_DIR).resolve()
    try:
        relative = path.relative_to(repository_root)
    except ValueError:
        relative = None
    if relative is not None and (
        not relative.parts or relative.parts[0].casefold() != _PRIVATE_REPOSITORY_DIRECTORY
    ):
        raise CommandError(
            "Private field input inside the repository is allowed only under .private-demo/, "
            "which is ignored by Git. Move the file there or outside the repository."
        )
    return path


def load_private_demo_spec(path: Path) -> RealDemoSpec:
    """Load strict JSON while keeping coordinate values out of validation messages."""

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CommandError("The private demo input file could not be inspected.") from exc
    if size <= 0 or size > _MAX_PRIVATE_INPUT_BYTES:
        raise CommandError("The private demo input must contain between 1 byte and 256 KiB.")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CommandError("The private demo input file could not be read.") from exc
    try:
        return RealDemoSpec.model_validate_json(payload)
    except ValidationError as exc:
        fields = sorted(
            {
                ".".join(str(component) for component in issue["loc"]) or "document"
                for issue in exc.errors(
                    include_url=False, include_context=False, include_input=False
                )
            }
        )
        field_summary = ", ".join(fields[:8])
        raise CommandError(
            f"The private demo input is invalid. Review these fields: {field_summary}."
        ) from None


def write_manifest(path: Path, encoded: str, *, overwrite: bool) -> None:
    """Atomically write a coordinate-redacted manifest with private file permissions."""

    target = path.expanduser().resolve()
    if target.exists() and not overwrite:
        raise CommandError("The manifest already exists; use --overwrite-manifest to replace it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as destination:
            destination.write(encoded)
            destination.write("\n")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(target)
    except OSError as exc:
        raise CommandError("The redacted demo manifest could not be written.") from exc
    finally:
        temporary.unlink(missing_ok=True)


class Command(BaseCommand):
    help = (
        "Prepare or reuse a real Sentinel demo from an explicitly authorized private JSON "
        "input, then emit a coordinate-redacted evidence manifest."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--input",
            required=True,
            help="Private JSON file outside the repository or under its ignored .private-demo/.",
        )
        parser.add_argument(
            "--confirm-authorized-data",
            action="store_true",
            help="Assert that the operator is authorized to use the supplied field data.",
        )
        parser.add_argument(
            "--reuse-only",
            action="store_true",
            help="Emit only an already-completed deterministic result; never access Sentinel.",
        )
        parser.add_argument(
            "--manifest",
            help="Optional destination for the redacted JSON manifest; stdout is always emitted.",
        )
        parser.add_argument(
            "--overwrite-manifest",
            action="store_true",
            help="Allow replacement of an existing redacted manifest file.",
        )

    def handle(self, *args: object, **options: object) -> None:  # noqa: ARG002
        if not options["confirm_authorized_data"]:
            raise CommandError(
                "Refusing to use field data without --confirm-authorized-data. Confirm only "
                "when the operator has permission to use the property and imagery context."
            )

        input_path = resolve_private_input_path(str(options["input"]))
        manifest_option = options.get("manifest")
        manifest_path = (
            Path(str(manifest_option)).expanduser().resolve() if manifest_option else None
        )
        if manifest_path is not None and manifest_path == input_path:
            raise CommandError("The redacted manifest cannot overwrite the private input file.")

        spec = load_private_demo_spec(input_path)
        reuse_only = bool(options["reuse_only"])
        pipeline = None if reuse_only else get_analysis_pipeline()
        try:
            prepared = prepare_real_demo(
                spec,
                get_repository(),
                pipeline,
                reuse_only=reuse_only,
            )
        except RealDemoPreparationError as exc:
            raise CommandError(f"{exc.code}: {exc}") from None

        manifest = build_redacted_demo_manifest(
            prepared,
            authorization_asserted=True,
        )
        encoded = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        if manifest_path is not None:
            write_manifest(
                manifest_path,
                encoded,
                overwrite=bool(options["overwrite_manifest"]),
            )
        self.stdout.write(encoded)
