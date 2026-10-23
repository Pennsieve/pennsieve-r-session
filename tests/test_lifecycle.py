"""Heartbeat classification: a token the execution no longer accepts is
TokenGone (the session should shut down); other errors are transient."""

import unittest

from processor.lifecycle import TaskTokenClient, TokenGone


class FakeClientError(Exception):
    """Shaped like botocore's ClientError (a .response with an error code)."""

    def __init__(self, code):
        super().__init__(f"An error occurred ({code})")
        self.response = {"Error": {"Code": code}}


class FakeSFN:
    def __init__(self, exc=None):
        self.exc = exc
        self.heartbeats = 0

    def send_task_heartbeat(self, taskToken):
        self.heartbeats += 1
        if self.exc:
            raise self.exc


def client(sfn):
    c = TaskTokenClient(token="", region="", task_arn="arn:task/1")
    c.token, c._sfn = "tok", sfn
    return c


class HeartbeatTest(unittest.TestCase):
    def test_success(self):
        sfn = FakeSFN()
        client(sfn).heartbeat()
        self.assertEqual(sfn.heartbeats, 1)

    def test_token_gone_errors(self):
        for code in ("TaskTimedOut", "TaskDoesNotExist", "InvalidToken"):
            with self.subTest(code=code), self.assertRaises(TokenGone):
                client(FakeSFN(FakeClientError(code))).heartbeat()

    def test_transient_errors_are_not_token_gone(self):
        for exc in (FakeClientError("ThrottlingException"), ConnectionError("reset")):
            with self.subTest(exc=exc):
                with self.assertRaises(Exception) as ctx:
                    client(FakeSFN(exc)).heartbeat()
                self.assertNotIsInstance(ctx.exception, TokenGone)

    def test_no_token_is_a_no_op(self):
        TaskTokenClient(token="", region="", task_arn="").heartbeat()


if __name__ == "__main__":
    unittest.main()
