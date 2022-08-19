FROM python:3-slim as base

FROM base as builder

RUN mkdir /install
WORKDIR /install

COPY requirements.txt /requirements.txt

RUN pip install --target /install -r /requirements.txt

FROM base

WORKDIR /app
COPY --from=builder /install /modules
COPY purple_crl_updater.py /app/purple_crl_updater.py
COPY known_hosts /app/known_hosts

CMD PYTHONPATH=/modules hypercorn cratere:app --bind 0.0.0.0 --access-logfile -
