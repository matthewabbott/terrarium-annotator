from terrarium_annotator.codex import extract_updates


def test_extract_updates_parses_tagged_json():
    payload = """
    <codex_updates>
    [
        {"term": "Soma", "definition": "The questmaster.", "status": "update", "source_post_id": 42}
    ]
    </codex_updates>
    """
    updates = extract_updates(payload)
    assert len(updates) == 1
    assert updates[0].term == "Soma"
    assert updates[0].definition.startswith("The questmaster")
