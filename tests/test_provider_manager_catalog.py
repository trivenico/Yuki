import unittest
from yuki_providers import ProviderManager

class ProviderManagerCatalogTests(unittest.TestCase):
    def test_manager_exposes_catalog_without_secrets(self):
        secret = "top-secret-key"
        manager = ProviderManager(config={"provider":"ollama","ollama_model":"qwen3"},
            environ={"YUKI_OLLAMA_MODEL":"qwen3","OPENAI_API_KEY":secret})
        catalog = manager.catalog()
        self.assertTrue(any(item["name"] == "ollama" and item["selected"] for item in catalog))
        self.assertNotIn(secret, repr(catalog))

    def test_oci_catalog_reports_configured_iam_without_secret_values(self):
        manager = ProviderManager(config={"provider":"oci_iam", "oci_region":"eu-frankfurt-1",
            "oci_model":"model-x", "oci_project_ocid":"ocid1.generativeaiproject.oc1..example"}, environ={})
        catalog = {item["name"]: item for item in manager.catalog()}
        self.assertTrue(catalog["oci_iam"]["implemented"])
        self.assertTrue(catalog["oci_iam"]["ready"])
        self.assertNotIn("ocid1.generativeaiproject", repr(catalog))

    def test_oci_iam_readiness_does_not_require_api_key(self):
        manager = ProviderManager(config={"provider":"oci_iam", "oci_region":"eu-frankfurt-1",
            "oci_model":"model-x", "oci_project_ocid":"ocid1.generativeaiproject.oc1..example"}, environ={})
        catalog = {item["name"]: item for item in manager.catalog()}
        self.assertTrue(catalog["oci_iam"]["ready"])
        self.assertFalse(catalog["oci_iam"]["credential_configured"])
        self.assertIn("principal_not_probed", catalog["oci_iam"]["status"])

    def test_oci_iam_catalog_does_not_claim_ready_without_required_config(self):
        manager = ProviderManager(config={"provider":"oci_iam", "oci_region":"eu-frankfurt-1",
            "oci_project_ocid":"ocid1.generativeaiproject.oc1..example"}, environ={})
        catalog = {item["name"]: item for item in manager.catalog()}
        self.assertFalse(catalog["oci_iam"]["ready"])

if __name__ == "__main__":
    unittest.main()
