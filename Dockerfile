FROM python:3.8.2-slim

ARG TELEMETRY_RELEASE

MAINTAINER LightQuantum

WORKDIR /app

RUN pip install --upgrade pip

COPY ./requirements.txt ./requirements.txt

RUN pip install -r requirements.txt

RUN pip install sentry_sdk

COPY bot.py ./

COPY observatory ./observatory

ENV TELEMETRY_RELEASE=${TELEMETRY_RELEASE}

CMD ["python", "bot.py"]
