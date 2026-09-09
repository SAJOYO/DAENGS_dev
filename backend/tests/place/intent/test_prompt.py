import json

from daengs_place.place.intent.prompt import (
    gemini_output_schema,
    proposer_instructions,
    strict_output_schema,
)


def test_provider_neutral_instructions_preserve_place_authority_boundary() -> None:
    instructions = proposer_instructions()

    assert "검색을 실행하거나 장소 사실을 만들지 말고" in instructions
    assert '"추천해줘"나 "골라줘"라는 동사만으로 open_discovery를 만들지 마라' in instructions
    assert "required_target은 사용자가 실제로 찾는 장소에만 쓴다" in instructions
    assert "semantic.proximity를 만들지 마라" in instructions


def test_strict_schema_requires_every_declared_object_property() -> None:
    schema = strict_output_schema()
    assert schema["type"] == "object" and "anyOf" not in schema
    serialized = json.dumps(schema)
    assert all(
        f'"{keyword}"' not in serialized
        for keyword in ("default", "title", "discriminator", "oneOf", "const")
    )

    def assert_strict(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                assert value.get("additionalProperties") is False
                assert set(value["required"]) == set(properties)
            for child in value.values():
                assert_strict(child)
        elif isinstance(value, list):
            for child in value:
                assert_strict(child)

    assert_strict(schema)


def test_gemini_schema_is_flat_and_keeps_supported_intent_axes() -> None:
    schema = gemini_output_schema()
    interpretation = schema["properties"]["interpretations"]["items"]
    proposal = interpretation["properties"]["proposals"]["items"]

    assert interpretation["properties"]["search_mode"]["enum"] == [
        "directed_search",
        "open_discovery",
    ]
    assert set(proposal["properties"]["intent_type"]["enum"]) == {
        "kind",
        "purpose",
        "boolean_capability",
        "semantic",
        "activity",
        "object",
    }
    assert proposal["properties"]["activity_id"]["enum"] == ["play", "buy"]
    assert proposal["properties"]["object_id"]["enum"] == ["dog_toy"]
    assert proposal["properties"]["concept_id"]["enum"] == [
        "semantic.quiet",
        "semantic.cheap",
        "semantic.dog_interest",
    ]
    serialized = json.dumps(schema)
    assert "$ref" not in serialized
    assert "$defs" not in serialized
