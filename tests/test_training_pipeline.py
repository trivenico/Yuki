import json
import pytest
from training.few_shot_patch import FewShotPatchLayer
from training.sft_data_generator import SFTDataError, SFTDataGenerator
from training.template_consistency import TemplateConsistencyError, check_template_bundle

def _messages(user="How should I structure a Python test?", answer="Use pytest fixtures and focused assertions."):
    return [{"role": "user", "content": user}, {"role": "assistant", "content": answer}]

def test_sft_requires_explicit_consent_and_human_approval():
    generator = SFTDataGenerator()
    with pytest.raises(SFTDataError):
        generator.add_example(messages=_messages(), source_reference="conversation:1", task_type="coding",
                              training_consent=False, human_approved=True, approved_by="owner")
    with pytest.raises(SFTDataError):
        generator.add_example(messages=_messages(), source_reference="conversation:1", task_type="coding",
                              training_consent=True, human_approved=False, approved_by="owner")

def test_sft_export_is_jsonl_and_contains_no_raw_source_reference():
    generator = SFTDataGenerator(dataset_version="1.2.0")
    generator.add_example(messages=_messages(), source_reference="private://conversation/123", task_type="coding",
                          training_consent=True, human_approved=True, approved_by="owner")
    exported = generator.export_jsonl()
    assert exported.endswith("\n") and exported.count("\n") == 1
    row = json.loads(exported)
    assert row["metadata"]["approval_state"] == "human_approved"
    assert row["metadata"]["dataset_version"] == "1.2.0"
    assert "private://conversation/123" not in exported
    assert len(row["metadata"]["source_digest"]) == 64

def test_sft_rejects_credential_material_and_deduplicates():
    generator = SFTDataGenerator()
    with pytest.raises(SFTDataError):
        generator.add_example(messages=_messages(answer="api_key=supersecret123456789"), source_reference="x",
                              task_type="coding", training_consent=True, human_approved=True, approved_by="owner")
    first = generator.add_example(messages=_messages(), source_reference="a", task_type="coding",
                                  training_consent=True, human_approved=True, approved_by="owner")
    second = generator.add_example(messages=_messages(), source_reference="b", task_type="coding",
                                   training_consent=True, human_approved=True, approved_by="owner")
    assert first == second
    assert len(generator) == 1

def test_sft_distinguishes_task_type_for_same_messages():
    generator = SFTDataGenerator()
    common = dict(messages=_messages(), source_reference="a", training_consent=True,
                  human_approved=True, approved_by="owner")
    one = generator.add_example(task_type="coding", **common)
    two = generator.add_example(task_type="research", **common)
    assert one != two

def test_few_shot_patch_only_uses_approved_examples_and_is_bounded():
    generator = SFTDataGenerator()
    generator.add_example(messages=_messages(), source_reference="task:1", task_type="coding",
                          training_consent=True, human_approved=True, approved_by="owner")
    layer = FewShotPatchLayer(generator.export_jsonl(), max_examples=1, max_chars=2048)
    patch = layer.build_patch("pytest Python test fixtures", task_type="coding")
    assert patch and "approved historical examples" in patch
    assert "\n<approved_examples_json>\n" in patch
    assert layer.build_patch("completely unrelated astronomy", task_type="coding") is None

def test_few_shot_rejects_unapproved_records():
    row = {"schema_version": 1, "messages": _messages(),
           "metadata": {"approval_state": "pending", "example_id": "x", "task_type": "coding"}}
    layer = FewShotPatchLayer(json.dumps(row))
    assert layer.build_patch("Python pytest") is None

def _manifest():
    return {"schema_version": 1, "bundle_version": "1.0.0", "templates": {
        "assistant": {"version": "1.0.0", "required_variables": ["system_prompt", "user_prompt"]}}}

def test_template_consistency_returns_stable_digest():
    templates = {"assistant": "{system_prompt}\n{user_prompt}"}
    first = check_template_bundle(_manifest(), templates)
    second = check_template_bundle(_manifest(), templates)
    assert first["status"] == "consistent"
    assert first["bundle_sha256"] == second["bundle_sha256"]

@pytest.mark.parametrize("manifest,templates", [
    (_manifest(), {"assistant": "{system_prompt}"}),
    ({**_manifest(), "bundle_version": "2.0.0"}, {"assistant": "{system_prompt}\n{user_prompt}"}),
    (_manifest(), {"other": "{system_prompt}\n{user_prompt}"}),
    (_manifest(), {"assistant": "{system_prompt.__class__}\n{user_prompt}"}),
    (_manifest(), {"assistant": "{system_prompt"}),
])
def test_template_consistency_fails_closed(manifest, templates):
    with pytest.raises(TemplateConsistencyError):
        check_template_bundle(manifest, templates)
