"""Any-provider LLM endpoints: env resolution per role, request shapes per provider, reply parsing, usage."""
import base64

import pytest

from voyageur.llm import (Endpoint, ImagesNotAllowed, LLMConfigError, Usage, build_request, endpoint_from_env, parse_reply,
                              usage_of)

PNG = b"\x89PNG\r\n\x1a\nfake"


# --- env resolution ----------------------------------------------------------------------------
def test_shared_settings_are_the_text_roles_fallback_and_vision_does_not_inherit_them():
    # Changed from the original ("shared settings apply to every role"): a vision call must be set up on purpose, so
    # vision with nothing set is unconfigured rather than the text model with an image attached.
    env = {"HEAL_BASE_URL": "https://api.example.com/v1", "HEAL_MODEL": "m", "HEAL_API_KEY": "k"}
    ep = endpoint_from_env("text", env)
    assert (ep.provider, ep.base_url, ep.model, ep.api_key) == ("openai", "https://api.example.com/v1", "m", "k")
    vision = endpoint_from_env("vision", env)
    assert not vision.configured and vision.api_key == "" and vision.base_url is None


def test_the_text_role_reads_its_own_names_first_and_the_old_names_as_a_fallback():
    env = {"HEAL_BASE_URL": "https://old/v1", "HEAL_MODEL": "old-model", "HEAL_TEXT_MODEL": "new-model"}
    ep = endpoint_from_env("text", env)
    assert (ep.base_url, ep.model) == ("https://old/v1", "new-model")


def test_role_settings_beat_the_shared_ones():
    env = {"HEAL_BASE_URL": "https://a/v1", "HEAL_MODEL": "text-model", "HEAL_API_KEY": "shared",
           "HEAL_VISION_PROVIDER": "anthropic", "HEAL_VISION_MODEL": "vision-model", "HEAL_VISION_API_KEY": "vk",
           "HEAL_VISION_BASE_URL": ""}
    text, vision = endpoint_from_env("text", env), endpoint_from_env("vision", env)
    assert (text.provider, text.model, text.api_key) == ("openai", "text-model", "shared")
    assert (vision.provider, vision.model, vision.api_key) == ("anthropic", "vision-model", "vk")
    assert vision.base_url is None   # an empty role value is unset, and vision has no shared base URL to fall back on


def test_anthropic_needs_no_base_url():
    ep = endpoint_from_env("text", {"HEAL_PROVIDER": "anthropic", "HEAL_MODEL": "m"})
    assert ep.configured and ep.url == "https://api.anthropic.com/v1/messages"


def test_openai_needs_a_base_url():
    assert not endpoint_from_env("text", {"HEAL_MODEL": "m"}).configured


@pytest.mark.parametrize("name,value", [("HEAL_HEADERS", "{not json"), ("HEAL_HEADERS", '["a"]'),
                                        ("HEAL_TEXT_EXTRA_BODY", "sk-secret-123"), ("HEAL_HEADERS", '{"api-key": 5}'),
                                        ("HEAL_TEXT_MAX_TOKENS", "lots"), ("HEAL_TIMEOUT_S", "-5"),
                                        ("HEAL_TEXT_IMAGES_ALLOWED", "perhaps")])
def test_bad_json_is_a_config_error_that_never_echoes_the_value(name, value):
    with pytest.raises(LLMConfigError) as err:
        endpoint_from_env("text", {name: value})
    assert name in str(err.value) and value not in str(err.value)


def test_unknown_provider_is_a_config_error():
    with pytest.raises(LLMConfigError, match="HEAL_TEXT_PROVIDER"):
        endpoint_from_env("text", {"HEAL_TEXT_PROVIDER": "bedrock"})


def test_reasoning_is_switched_off_by_default_and_on_when_asked_for():
    # Changed from the original, where nothing was sent unless asked: the service wants one short answer, so the
    # Qwen / vLLM switches go out by default (an Endpoint built by hand still sends nothing extra).
    assert Endpoint().thinking is True
    assert endpoint_from_env("text", {}).thinking is False
    assert endpoint_from_env("text", {"HEAL_THINKING": "off"}).thinking is False
    assert endpoint_from_env("text", {"HEAL_THINKING": "on"}).thinking is True
    assert endpoint_from_env("vision", {"HEAL_THINKING": "off", "HEAL_VISION_THINKING": "on"}).thinking is True


def test_small_answers_and_a_two_minute_timeout_are_the_defaults():
    ep = endpoint_from_env("text", {})
    assert (ep.max_tokens, ep.timeout, ep.caller, ep.images_allowed) == (64, 120, "voyageur", False)
    ep = endpoint_from_env("vision", {"HEAL_MAX_OUTPUT_TOKENS": "200", "HEAL_TIMEOUT_S": "30",
                                      "HEAL_VISION_TIMEOUT_S": "45", "HEAL_VISION_IMAGES_ALLOWED": "true",
                                      "HEAL_CALLER": "ci"})
    assert (ep.max_tokens, ep.timeout, ep.caller, ep.images_allowed) == (200, 45, "ci", True)


def test_an_unknown_role_is_a_config_error():
    with pytest.raises(LLMConfigError):
        endpoint_from_env("audio", {})


def test_api_key_and_header_values_are_secrets():
    ep = endpoint_from_env("text", {"HEAL_API_KEY": "k1\n", "HEAL_HEADERS": '{"api-key": " k2 "}'})
    assert ep.api_key == "k1" and ep.secrets() == ["k1", "k2"]


# --- openai request ------------------------------------------------------------------------------
def test_openai_default_body_is_portable():
    url, headers, body = build_request(Endpoint(base_url="https://api.example.com/v1/", model="m"), "fix it")
    assert url == "https://api.example.com/v1/chat/completions"
    assert body == {"model": "m", "temperature": 0, "max_tokens": 64, "messages": [{"role": "user", "content": "fix it"}]}
    assert "Authorization" not in headers and headers["X-Heal-Caller"] == "voyageur"
    assert "X-Heal-Id" not in headers


def test_openai_thinking_off_sends_the_qwen_switches():
    _, _, body = build_request(Endpoint(base_url="http://h/v1", model="m", thinking=False), "fix it")
    assert body["messages"][0]["content"].endswith(" /no_think")
    assert body["chat_template_kwargs"] == {"enable_thinking": False} and body["reasoning_effort"] == "none"


def test_extra_body_merges_last_and_null_removes():
    ep = Endpoint(base_url="https://h/v1", model="m",
                  extra_body={"temperature": None, "max_tokens": None, "max_completion_tokens": 2048})
    _, _, body = build_request(ep, "p")
    assert "temperature" not in body and "max_tokens" not in body and body["max_completion_tokens"] == 2048


def test_extra_headers_are_sent_and_bearer_only_with_a_key():
    _, headers, _ = build_request(Endpoint(base_url="https://h/v1", model="m", api_key="k",
                                           headers={"api-key": "azure", "X-Heal-Caller": "mine"}), "p")
    assert headers["Authorization"] == "Bearer k" and headers["api-key"] == "azure"
    assert headers["X-Heal-Caller"] == "mine"


def test_empty_caller_sends_no_caller_header():
    _, headers, _ = build_request(Endpoint(base_url="https://h/v1", model="m", caller=""), "p")
    assert "X-Heal-Caller" not in headers


def test_the_heal_id_travels_in_a_header_only_when_it_is_plain_id_characters():
    ep = Endpoint(base_url="https://h/v1", model="m")
    assert build_request(ep, "p", heal_id="h-20261105-ab12")[1]["X-Heal-Id"] == "h-20261105-ab12"
    for bad in ("a\r\nX-Evil: 1", "has space", "x" * 65, "a/b"):
        assert "X-Heal-Id" not in build_request(ep, "p", heal_id=bad)[1]


def test_an_image_is_refused_unless_the_endpoint_was_allowed_to_receive_one():
    with pytest.raises(ImagesNotAllowed):
        build_request(Endpoint(base_url="https://h/v1", model="m"), "p", [("Image 1: a.png", PNG)])
    with pytest.raises(ImagesNotAllowed):
        build_request(Endpoint(provider="anthropic", model="m"), "p", [("Image 1: a.png", PNG)])


def test_openai_images_are_named_image_url_parts():
    _, _, body = build_request(Endpoint(base_url="https://h/v1", model="m", images_allowed=True), "p",
                               [("Image 1: a.png", PNG)])
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "p"} and content[1] == {"type": "text", "text": "Image 1: a.png"}
    assert content[2]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()


# --- anthropic request ---------------------------------------------------------------------------
@pytest.mark.parametrize("base", [None, "https://api.anthropic.com", "https://api.anthropic.com/", "https://gw/v1"])
def test_anthropic_url_with_or_without_v1(base):
    ep = Endpoint(provider="anthropic", base_url=base, model="m")
    assert build_request(ep, "p")[0].endswith("/v1/messages")


def test_anthropic_request_shape():
    ep = Endpoint(provider="anthropic", model="claude-x", api_key="ak", thinking=False, max_tokens=512,
                  images_allowed=True)
    _, headers, body = build_request(ep, "fix it", [("Image 1: a.png", PNG)])
    assert headers["x-api-key"] == "ak" and headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers
    assert set(body) == {"model", "max_tokens", "messages"} and body["max_tokens"] == 512
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "fix it"}          # thinking=off is ignored: no /no_think
    assert content[2] == {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                      "data": base64.b64encode(PNG).decode()}}


def test_anthropic_without_images_sends_plain_text():
    _, _, body = build_request(Endpoint(provider="anthropic", model="m"), "p")
    assert body["messages"] == [{"role": "user", "content": "p"}]


# --- replies -------------------------------------------------------------------------------------
def test_openai_reply_reads_only_content():
    data = {"choices": [{"message": {"content": "page.get_by_label('A')", "reasoning_content": "secret plan"}}]}
    assert parse_reply(Endpoint(), data).text == "page.get_by_label('A')"


def test_anthropic_reply_joins_text_blocks_and_skips_thinking():
    data = {"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": ""},
                                                   {"type": "text", "text": "page."}, {"type": "text", "text": "x()"}]}
    assert parse_reply(Endpoint(provider="anthropic"), data).text == "page.x()"


def test_anthropic_refusal_is_no_answer_with_a_reason():
    r = parse_reply(Endpoint(provider="anthropic"), {"stop_reason": "refusal", "content": [{"type": "text", "text": "no"}]})
    assert r.text is None and "refused" in r.why


@pytest.mark.parametrize("provider,data", [("openai", {"choices": []}), ("openai", "x"), ("anthropic", {"content": None}),
                                           ("anthropic", {"content": []})])
def test_malformed_replies_are_no_answer(provider, data):
    r = parse_reply(Endpoint(provider=provider), data)
    assert r.text is None and r.why


def test_usage_is_reported_in_one_vocabulary_as_integers():
    # Rewritten from the original, which returned strings like "prompt_tokens=9": the service stores and sums counts.
    assert usage_of(Endpoint(provider="anthropic"), {"usage": {"input_tokens": 9, "output_tokens": 2}}) == Usage(9, 2, 0)
    assert usage_of(Endpoint(), {"usage": {"prompt_tokens": 9, "completion_tokens": 2,
                                           "prompt_tokens_details": {"multimodal_tokens": {"image": 7}}}}) == \
        Usage(9, 2, 7)


@pytest.mark.parametrize("data", [None, "x", {}, {"usage": None}, {"usage": {"prompt_tokens": "9"}},
                                  {"usage": {"prompt_tokens": True, "completion_tokens": -3}}])
def test_missing_or_malformed_usage_counts_as_zero(data):
    for provider in ("openai", "anthropic"):
        assert usage_of(Endpoint(provider=provider), data) == Usage()


def test_usage_adds_up_and_serialises_to_plain_ints():
    total = Usage(9, 2, 0) + Usage(1, 1, 7)
    assert total.as_dict() == {"input_tokens": 10, "output_tokens": 3, "image_tokens": 7}


# --- credentials never follow a role to another destination -------------------------------------
def test_shared_key_and_headers_stay_with_the_shared_destination():
    env = {"HEAL_BASE_URL": "http://localhost:8000/v1", "HEAL_MODEL": "local", "HEAL_API_KEY": "sk-local-gateway",
           "HEAL_HEADERS": '{"api-key": "gw-secret"}',
           "HEAL_VISION_PROVIDER": "anthropic", "HEAL_VISION_MODEL": "claude-x"}
    vision = endpoint_from_env("vision", env)
    assert vision.api_key == "" and dict(vision.headers) == {}
    assert endpoint_from_env("text", env).api_key == "sk-local-gateway"


def test_a_text_role_moved_to_another_provider_gets_no_shared_key_or_headers():
    env = {"HEAL_BASE_URL": "http://localhost:8000/v1", "HEAL_MODEL": "local", "HEAL_API_KEY": "sk-local-gateway",
           "HEAL_HEADERS": '{"api-key": "gw-secret"}', "HEAL_TEXT_PROVIDER": "anthropic", "HEAL_TEXT_MODEL": "claude-x"}
    text = endpoint_from_env("text", env)
    assert text.api_key == "" and dict(text.headers) == {} and text.provider == "anthropic"


def test_a_role_on_another_base_url_gets_no_shared_key():
    env = {"HEAL_BASE_URL": "https://a.example/v1", "HEAL_API_KEY": "sk-a", "HEAL_MODEL": "m",
           "HEAL_TEXT_BASE_URL": "https://b.example/v1"}
    assert endpoint_from_env("text", env).api_key == ""


def test_a_role_that_only_changes_the_model_keeps_the_shared_key():
    env = {"HEAL_BASE_URL": "https://a.example/v1/", "HEAL_API_KEY": "sk-a", "HEAL_MODEL": "m",
           "HEAL_TEXT_MODEL": "vm", "HEAL_TEXT_BASE_URL": "https://a.example/v1"}
    ep = endpoint_from_env("text", env)
    assert ep.api_key == "sk-a" and ep.model == "vm"              # same destination (trailing slash aside)


def test_a_role_key_is_used_on_its_own_destination():
    env = {"HEAL_BASE_URL": "https://a.example/v1", "HEAL_API_KEY": "sk-a", "HEAL_MODEL": "m",
           "HEAL_VISION_PROVIDER": "anthropic", "HEAL_VISION_API_KEY": "sk-ant", "HEAL_VISION_HEADERS": '{"x": "1"}'}
    vision = endpoint_from_env("vision", env)
    assert vision.api_key == "sk-ant" and dict(vision.headers) == {"x": "1"}


def test_shared_extra_body_stays_with_the_shared_destination():
    env = {"HEAL_BASE_URL": "https://a.example/v1", "HEAL_MODEL": "m", "HEAL_EXTRA_BODY": '{"api_key": "BODYSECRET"}',
           "HEAL_VISION_BASE_URL": "https://b.example/v1"}
    assert dict(endpoint_from_env("vision", env).extra_body) == {}
    assert dict(endpoint_from_env("text", env).extra_body) == {"api_key": "BODYSECRET"}
    moved = dict(env, HEAL_TEXT_BASE_URL="https://b.example/v1")
    assert dict(endpoint_from_env("text", moved).extra_body) == {}


@pytest.mark.parametrize("role_base", ["HTTPS://A.example/v1", "https://a.example:443/v1", "https://a.example/v1/"])
def test_the_same_destination_written_differently_keeps_the_shared_key(role_base):
    env = {"HEAL_BASE_URL": "https://a.example/v1", "HEAL_MODEL": "m", "HEAL_API_KEY": "sk-a", "HEAL_TEXT_BASE_URL": role_base}
    assert endpoint_from_env("text", env).api_key == "sk-a"


@pytest.mark.parametrize("role_base", ["https://a.example:8443/v1", "http://a.example/v1", "https://a.example/v2",
                                       "https://a.example.evil/v1"])
def test_a_different_destination_drops_the_shared_key(role_base):
    env = {"HEAL_BASE_URL": "https://a.example/v1", "HEAL_MODEL": "m", "HEAL_API_KEY": "sk-a", "HEAL_TEXT_BASE_URL": role_base}
    assert endpoint_from_env("text", env).api_key == ""


def test_only_credential_like_values_are_secrets():
    ep = Endpoint(api_key="sk-1", headers={"anthropic-beta": "true", "x-env": "prod", "api-key": "az-key-1",
                                           "Authorization": "Bearer tok-123", "X-Goog-Api-Key": "g-key-9"},
                  extra_body={"tenant": "acme", "user_token": "ut-55", "nested": {"secret": "deep-77"}})
    s = set(ep.secrets())
    assert {"sk-1", "az-key-1", "Bearer tok-123", "tok-123", "g-key-9", "ut-55", "deep-77"} <= s
    assert not {"true", "prod", "acme"} & s


@pytest.mark.parametrize("body", ['{"model": "other"}', '{"messages": []}'])
def test_extra_body_cannot_replace_the_model_or_the_messages(body):
    with pytest.raises(LLMConfigError, match="HEAL_EXTRA_BODY"):
        endpoint_from_env("text", {"HEAL_EXTRA_BODY": body})


@pytest.mark.parametrize("url", ["http://a.example:99999/v1", "http://[::1/v1", "https://a.example/v 1", "https://a\n.ex/v1"])
def test_unusable_base_urls_are_config_errors(url):
    with pytest.raises(LLMConfigError, match="HEAL_TEXT_BASE_URL"):
        endpoint_from_env("text", {"HEAL_TEXT_BASE_URL": url})


def test_more_credential_names_and_any_bearer_value_are_secrets():
    ep = Endpoint(headers={"X-JWT": "jwt-1111", "X-Session": "sess-2222", "X-Org": "Bearer org-tok-3"})
    assert {"jwt-1111", "sess-2222", "org-tok-3"} <= set(ep.secrets())
