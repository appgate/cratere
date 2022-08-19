FROM ubuntu:22.04 as base

FROM base as builder

RUN mkdir /install
WORKDIR /install

COPY requirements.txt /requirements.txt

RUN apt-get update && apt-get install python3-pip --yes
RUN pip3 install --target /install -r /requirements.txt

FROM base

WORKDIR /app
COPY --from=builder /install /modules
COPY cratere /app/cratere
RUN apt-get update && apt-get install python3 git --yes

CMD PYTHONPATH=/modules python3 -m hypercorn cratere:app --bind 0.0.0.0 --access-logfile -
