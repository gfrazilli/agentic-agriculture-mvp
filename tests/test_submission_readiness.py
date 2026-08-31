from pathlib import Path
from xml.etree import ElementTree

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SUBMISSION_DIR = REPOSITORY_ROOT / "docs" / "submission"


def test_submission_has_a_standalone_accessible_architecture_visual() -> None:
    diagram = SUBMISSION_DIR / "architecture.svg"
    root = ElementTree.parse(diagram).getroot()

    namespace = {"svg": "http://www.w3.org/2000/svg"}
    assert root.attrib["width"] == "1600"
    assert root.attrib["height"] == "900"
    assert root.attrib["role"] == "img"
    assert root.find("svg:title", namespace) is not None
    assert root.find("svg:desc", namespace) is not None

    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/submission/architecture.svg" in readme


def test_known_hosted_url_and_product_name_are_consistent_in_submission_copy() -> None:
    submission_files = tuple(SUBMISSION_DIR.glob("*.md"))
    combined = "\n".join(path.read_text(encoding="utf-8") for path in submission_files)

    assert "[FINAL_HOSTED_URL]" not in combined
    assert "Agentic Agriculture" not in combined
    assert "https://1415agri.com/" in combined
    assert "1415 Agri" in combined


def test_ephemeral_test_outputs_cannot_enter_git_or_build_contexts() -> None:
    expected = {
        ".gitignore": "tmp/",
        ".gcloudignore": "tmp/",
        ".dockerignore": "tmp",
    }
    for filename, pattern in expected.items():
        contents = (REPOSITORY_ROOT / filename).read_text(encoding="utf-8").splitlines()
        assert pattern in contents
