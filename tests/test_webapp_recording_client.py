import httpx
import pytest

from webapp.recording_client import RecordingClient


class _Stub:
    def __init__(self, value):
        self.value = value
        self.closed = False

    def complete_json(self, *a, **k):
        return self.value

    def close(self):
        self.closed = True


class _Boom:
    def complete_json(self, *a, **k):
        raise httpx.ConnectError("network down")

    def close(self):
        pass


SCHEMA = {"properties": {"a": {}}}


def test_records_all_member_answers_and_returns_vote():
    client = RecordingClient([(_Stub({"a": "X"}), 1), (_Stub({"a": "X"}), 1), (_Stub({"a": "Y"}), 1)], 0.0)
    out = client.complete_json("s", "u", SCHEMA, "n")
    assert out == {"a": "X"}  # majority vote
    assert client.answers == [{"a": "X"}, {"a": "X"}, {"a": "Y"}]
    assert client.member_count == 3


def test_answers_reset_between_calls():
    client = RecordingClient([(_Stub({"a": "X"}), 1)], 0.0)
    client.complete_json("s", "u", SCHEMA, "n")
    client.complete_json("s", "u", SCHEMA, "n")
    assert client.answers == [{"a": "X"}]  # only the latest call's answers


def test_failed_member_does_not_sink_the_call():
    client = RecordingClient([(_Boom(), 1), (_Stub({"a": "X"}), 1)], 0.0)
    assert client.complete_json("s", "u", SCHEMA, "n") == {"a": "X"}
    assert client.answers == [{"a": "X"}]


def test_all_members_failing_raises():
    client = RecordingClient([(_Boom(), 1)], 0.0)
    with pytest.raises(httpx.HTTPError):
        client.complete_json("s", "u", SCHEMA, "n")


def test_close_closes_all_members():
    members = [_Stub({"a": "X"}), _Stub({"a": "Y"})]
    RecordingClient([(m, 1) for m in members], 0.0).close()
    assert all(m.closed for m in members)
