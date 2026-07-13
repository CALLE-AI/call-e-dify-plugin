from pathlib import Path
import tomllib

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(relative_path: str):
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def test_plugin_author_is_lowercase_and_consistent():
    manifest = load_yaml("manifest.yaml")
    provider = load_yaml("provider/call_e.yaml")
    tool_paths = provider["tools"]

    author = manifest["author"]
    assert author == author.lower()
    assert provider["identity"]["author"] == author

    for tool_path in tool_paths:
        tool = load_yaml(tool_path)
        assert tool["identity"]["author"] == author


def test_manifest_declares_packaged_privacy_policy():
    manifest = load_yaml("manifest.yaml")
    privacy_path = manifest["privacy"]

    assert privacy_path == "./PRIVACY.md"
    assert (ROOT / privacy_path.removeprefix("./")).is_file()


def test_readme_links_source_repository():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "https://github.com/CALLE-AI/call-e-dify-plugin" in readme


def test_api_key_onboarding_links_are_user_visible():
    provider = load_yaml("provider/call_e.yaml")
    api_key_help = provider["credentials_for_provider"]["api_key"]["help"]["en_US"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for url in (
        "https://dashboard.heycall-e.com/account/api-keys",
        "https://docs.heycall-e.com/#/api-reference",
    ):
        assert url in api_key_help
        assert url in readme


def test_release_version_is_synchronized():
    manifest = load_yaml("manifest.yaml")
    with (ROOT / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    assert manifest["version"] == "0.1.8"
    assert pyproject["project"]["version"] == manifest["version"]


def test_marketplace_runtime_dependencies_are_declared():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "dify_plugin>=0.9.0" in requirements
    assert "requests>=2.32.0" in requirements
