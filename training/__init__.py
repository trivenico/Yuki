"""Opt-in training-data and prompt-quality utilities for Yuki."""
from training.sft_data_generator import SFTDataGenerator, SFTDataError
from training.few_shot_patch import FewShotPatchLayer
from training.template_consistency import TemplateConsistencyError, check_template_bundle
__all__ = ["SFTDataGenerator", "SFTDataError", "FewShotPatchLayer", "TemplateConsistencyError", "check_template_bundle"]
