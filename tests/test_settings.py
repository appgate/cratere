import os

from cratere.settings import Settings


def test_alternate_hosts():
    os.environ["CRATERE_HOST"] = "foo.example.com"
    os.environ["CRATERE_ALTERNATE_HOSTS"] = '["foobar", "bar", "baz"]'

    settings = Settings()

    assert settings.host == "foo.example.com"
    assert settings.alternate_hosts == ["foobar", "bar", "baz"]
