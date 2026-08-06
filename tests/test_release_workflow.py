from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_single_package_release_tag_omits_component_name() -> None:
    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))

    assert config["packages"]["."]["include-v-in-tag"] is True
    assert config["packages"]["."]["include-component-in-tag"] is False


def test_release_workflow_can_recover_existing_release_tag() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "release_tag:" in workflow
    assert "steps.manual.outputs.release_created" in workflow
    assert 'f"pansh-v{source_version}"' in workflow
    assert "refs/tags/${{ inputs.release_tag }}" in workflow
    assert "getReleaseByTag" in workflow
    assert 'f"refs/tags/{release_tag}"' in workflow
    assert 'workflow_ref != "refs/heads/main"' in workflow
