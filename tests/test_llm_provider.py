from types import SimpleNamespace

from app.llm import OpenAICompatibleProvider


class _Completions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(content='{"sql": "SELECT 1", "metric_id": "order_count"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_disable_thinking_is_opt_in():
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.model = "deepseek-flash"
    provider.disable_thinking = False
    completions = _Completions()
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    proposal = provider.propose_sql("How many orders?", "schema", "metrics")
    assert proposal.metric_id == "order_count"
    assert "extra_body" not in completions.kwargs

    provider.disable_thinking = True
    provider.propose_sql("How many orders?", "schema", "metrics")
    assert completions.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_invalid_json_becomes_a_clarification():
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.model = "deepseek-flash"
    provider.disable_thinking = False
    message = SimpleNamespace(content="not-json")
    completions = SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(message=message)])
    )
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    proposal = provider.propose_sql("How many orders?", "schema", "metrics")
    assert proposal.sql is None
    assert proposal.clarification
