# Cratere

Caching proxy for crates.io.
Based on https://doc.rust-lang.org/cargo/reference/registries.html#index-format

### Running locally

```bash
poetry run hypercorn cratere:app --bind 0.0.0.0 --reload --access-logfile -
```

### Run all checks

```bash
poetry run check
```

### Type checking

```bash
poetry run mypy cratere
```

### Unit tests

```bash
poetry run pytest tests
```

### Code formatting

```bash
poetry run fmt
```


### Configuring cargo to use cratere as a repository

Assuming cratere is listening on 172.17.0.1:8000:

```bash
# cat ~/.cargo/config.toml
[source.my-mirror]
registry = "http://172.17.0.1:8000/crates.io-index"
[source.crates-io]
replace-with = "my-mirror"
```

### Docker

#### Build

Export poetry lock file to standard requirements file and run docker build:
```bash
poetry export --format=requirements.txt > requirements.txt
docker build -t cratere .
```

#### Run
Expose the required port and mount storage volume:
```
docker run -p 0.0.0.0:8000:8000 cratere
```
