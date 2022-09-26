# Cratere

Caching proxy for crates.io.
Based on https://doc.rust-lang.org/cargo/reference/registries.html#index-format

The caching proxy keeps an updated crates.io index via regular syncs and caches crates downloads.
If a given version of crate has been downloaded previously and is still in cache it is served directly from the cache. Unused crate versions will be cleaned up according to the cleanup policy.

> *Cratère*
>
> French for crater.
>
> a large bowl-shaped cavity in the ground or on a celestial object, typically used to store and serve crates.

## Configuration

Cratere is configurable via environment variables to make it easy to deploy as a container using e.g. Kubernetes (k8s).
It can also by run directly locally using e.g. [hypercorn](https://pgjones.gitlab.io/hypercorn/).

- `CRATERE_SCHEME`: http or https.
- `CRATERE_HOST`: hostname that will be used in the cargo crates.io and cargo configuration.
- `CRATERE_PORT`: port that will be used in the cargo crates.io and cargo configuration.
- `CRATERE_CACHE`: cache directory where the actual crates will be stored following the cargo registries standard storage paths.
- `CRATERE_INDEX`: crates.io git index repositories storage directory. Such a repository is about 1.2GB in size. At most 2 copies are kept at all times for each configured host.
- `CRATERE_INDEX_UPDATE_SCHEDULE`: cron schedule for updating the crates.io index. The index is synced from https://github.com/rust-lang/crates.io-index.git.
- `CRATERE_CLEANUP_CACHE_SCHEDULE`: cron schedule for cleaning cached crates. See `CRATERE_MAY_DAYS_UNUSED` for cleanup policy.
- `CRATERE_MAX_DAYS_UNUSED`: Crates that have not been used for more than `CRATERE_MAX_DAYS_UNUSED` will be deleted. This relies on atime or relatim being enabled on the filesystem used to store the `CRATERE_CACHE`.
- `CRATERE_ALTERNATE_HOSTS`: List of alternate hosts in json format as follows: `["<host1>[:<port1>", "<host2>[:<port2>]", ...]`. Each alternate host gets its own copies of the crates.io index. The crates cache itself is shared between all the hosts.


## Internals

Cratere written in Python using [FastAPI](https://fastapi.tiangolo.com/) and runs as a [ASGI](https://asgi.readthedocs.io/en/latest/) application.

Cratere uses the `git` command line to sync, modify and server the crates.io index. It relies on the `git` cli's support for feeding its output to an http stream.

When a crate version is not in cache it is streamed from crates.io to cargo and stored to cache simultaneously. Once it is stored in cache it is served directly from the cache filesystem.

Cratere uses [acron](https://github.com/appgate/acron) to run the following scheduled tasks:
- Synchronization of the crates.io index.
- Cleanup up of unused crates in cache.

## Development

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


### Kubernetes

A cratere proxy can easily be hosted on k8s using the dokcer image, a persistent volume to store the index and crates cache as well as a service to expose the port.
