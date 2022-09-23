import os


def test_alternate_hosts():
    os.environ["CRATERE_HOST"] = "foo.example.com"
    os.environ["CRATERE_ALTERNATE_HOSTS"] = '["foobar", "bar", "baz"]'
    from cratere.settings import settings
    assert settings.host == "foo.example.com"
    assert settings.alternate_hosts == ["foobar", "bar", "baz"]
