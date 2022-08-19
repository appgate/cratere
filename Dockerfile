FROM python:3-slim as base

FROM base as builder

RUN mkdir /install
WORKDIR /install

COPY requirements.txt /requirements.txt

RUN apt-get update && apt-get install git --yes
RUN pip install --target /install -r /requirements.txt

FROM base

WORKDIR /app
COPY --from=builder /install /modules
COPY cratere /app/cratere

CMD PYTHONPATH=/modules python3 -m hypercorn cratere:app --bind 0.0.0.0 --access-logfile -
