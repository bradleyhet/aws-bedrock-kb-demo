from kb_demo.config import Config


def test_the_profile_defaults_to_triage():
    assert Config.from_env({}).profile == "triage"


def test_an_empty_profile_means_the_default_credential_chain():
    # How CI runs: credentials come from OIDC through the environment, not a named profile.
    assert Config.from_env({"KB_DEMO_PROFILE": ""}).profile is None
