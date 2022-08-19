# Cratere

Caching proxy for crates.io

### Running locally

poetry run hypercorn cratere:app --bind 0.0.0.0 --reload --access-logfile -


### Code formatting

poetry run black cratere


### Configuring cargo to use cratere as a repository

Assuming cratere is listening on 172.17.0.1:8000:

```
# cat ~/.cargo/config.toml
[source.my-mirror]
registry = "http://172.17.0.1:8000/crates.io-index"
[source.crates-io]
replace-with = "my-mirror"
```

### Docker

poetry export --without-hashes --format=requirements.txt > requirements.txt
docker build -t cratere .
