# Wavefront Flask SDK

This SDK provides support for reporting out of the box metric, histograms and tracing from your Flask based  application. That data is reported to Wavefront via proxy or direct ingestion. That data will help you understand how your application is performing in production.

## Install

```bash
pip install wavefront_flask_sdk_python
```

## Usage

Configure your Flask application to install the SDK as follows:

 ```python
from wavefront_sdk.common import ApplicationTags
from wavefront_opentracing_sdk import WavefrontTracer
from wavefront_opentracing_sdk.reporting import WavefrontSpanReporter
from wavefront_pyformance.wavefront_reporter import WavefrontDirectReporter
from wavefront_flask_sdk.flask_tracing import FlaskTracing

from flask import Flask

if __name__ == '__main__':
    app = Flask(__name__)

    source = '{SOURCE}'

    application_tags = ApplicationTags(
        application="{APP_NAME}",
        service="{SERVICE_NAME}",
        cluster="{CLUSTER_NAME}",  # Optional
        shard="{SHARD_NAME},",  # Optional
        custom_tags=[("{KEY}", "{VAL}")]  # Optional
    )

    wf_reporter = WavefrontDirectReporter(
        server="{SERV_ADDR}",
        token="{TOKEN}",
        source=source
    ).report_minute_distribution()

    span_reporter = WavefrontSpanReporter(wf_reporter.wavefront_client,
                                          source=source)

    tracer = WavefrontTracer(span_reporter, application_tags)

    tracing = FlaskTracing(tracer=tracer,
                           reporter=wf_reporter,
                           application_tags=application_tags,
                           app=app)

    app.run(host='0.0.0.0', port=8080)

 ```

## Out of the box metrics and histograms for your Flask based application.

 Assume you have the following API in your Flask Application:

```python
@app.route('/style/<path:id>/make')
def make_shirts(id):
    return "Shirts {} made.".format(id)
```

### Request Gauges

| Entity Name                                       | Entity Type | source | application | cluster   | service | shard   | flask.func |
| :------------------------------------------------ | :---------- | :----- | :---------- | :-------- | :------ | :------ | :------------------- |
| flask.request.style.path:id.make.GET.inflight.value | Gauge       | host-1 | Ordering    | us-west-1 | styling | primary | make_shirts          |
| flask.total_requests.inflight.value              | Gauge       | host-1 | Ordering    | us-west-1 | styling | primary | n/a                  |

### Granular Response related metrics

| Entity Name                                                  | Entity Type  | source             | application | cluster   | service | shard   | flask.func |
| :----------------------------------------------------------- | :----------- | :----------------- | :---------- | :-------- | :------ | :------ | :------------------- |
| flask.response.style.path:id.make.GET.200.cumulative.count    | Counter      | host-1             | Ordering    | us-west-1 | styling | primary | make_shirts          |
| flask.response.style.path:id.make.GET.200.aggregated_per_shard.count | DeltaCounter | wavefront-provided | Ordering    | us-west-1 | styling | primary | make_shirts          |
| flask.response.style.path:id.make.GET.200.aggregated_per_service.count | DeltaCounter | wavefront-provided | Ordering    | us-west-1 | styling | n/a     | make_shirts          |
| flask.response.style.path:id.make.GET.200.aggregated_per_cluster.count | DeltaCounter | wavefront-provided | Ordering    | us-west-1 | n/a     | n/a     | make_shirts          |
| flask.response.style.path:id.make.GET.200.aggregated_per_application.count | DeltaCounter | wavefront-provided | Ordering    | n/a       | n/a     | n/a     | make_shirts          |

### Granular Response related histograms

| Entity Name                                                | Entity Type        | source | application | cluster   | service | shard   | flask.func |
| :--------------------------------------------------------- | :----------------- | :----- | :---------- | :-------- | :------ | :------ | :------------------- |
| flask.response.style.path:id.make.summary.GET.200.latency.m | WavefrontHistogram | host-1 | Ordering    | us-west-1 | styling | primary | make_shirts          |
| flask.response.style.path:id.make.summary.GET.200.cpu_ns.m  | WavefrontHistogram | host-1 | Ordering    | us-west-1 | styling | primary | make_shirts          |

### Overall Response related metrics

This includes all the completed requests that returned a response (i.e. success + errors).

| Entity Name                                                | Entity Type  | source            | application | cluster   | service | shard   |
| :--------------------------------------------------------- | :----------- | :---------------- | :---------- | :-------- | :------ | :------ |
| flask.response.completed.aggregated_per_source.count      | Counter      | host-1            | Ordering    | us-west-1 | styling | primary |
| flask.response.completed.aggregated_per_shard.count       | DeltaCounter | wavefont-provided | Ordering    | us-west-1 | styling | primary |
| flask.response.completed.aggregated_per_service.count     | DeltaCounter | wavefont-provided | Ordering    | us-west-1 | styling | n/a     |
| flask.response.completed.aggregated_per_cluster.count     | DeltaCounter | wavefont-provided | Ordering    | us-west-1 | n/a     | n/a     |
| flask.response.completed.aggregated_per_application.count | DeltaCounter | wavefont-provided | Ordering    | n/a       | n/a     | n/a     |

### Overall Error Response related metrics

This includes all the completed requests that resulted in an error response (that is HTTP status code of 4xx or 5xx).

| Entity Name                                             | Entity Type  | source            | application | cluster   | service | shard   |
| :------------------------------------------------------ | :----------- | :---------------- | :---------- | :-------- | :------ | :------ |
| flask.response.errors.aggregated_per_source.count      | Counter      | host-1            | Ordering    | us-west-1 | styling | primary |
| flask.response.errors.aggregated_per_shard.count       | DeltaCounter | wavefont-provided | Ordering    | us-west-1 | styling | primary |
| flask.response.errors.aggregated_per_service.count     | DeltaCounter | wavefont-provided | Ordering    | us-west-1 | styling | n/a     |
| flask.response.errors.aggregated_per_cluster.count     | DeltaCounter | wavefont-provided | Ordering    | us-west-1 | n/a     | n/a     |
| flask.response.errors.aggregated_per_application.count | DeltaCounter | wavefont-provided | Ordering    | n/a       | n/a     | n/a     |

### Tracing Spans

Every span will have the operation name as span name, start time in millisec along with duration in millisec. The following table includes all the rest attributes of generated tracing spans.  

| Span Tag Key           | Span Tag Value                       |
| ---------------------- | ------------------------------------ |
| traceId                | 4a3dc181-d4ac-44bc-848b-133bb3811c31 |
| parent                 | q908ddfe-4723-40a6-b1d3-1e85b60d9016 |
| followsFrom            | b768ddfe-4723-40a6-b1d3-1e85b60d9016 |
| spanId                 | c908ddfe-4723-40a6-b1d3-1e85b60d9016 |
| component              | flask                                |
| span.kind              | server                               |
| application            | Ordering                             |
| service                | styling                              |
| cluster                | us-west-1                            |
| shard                  | primary                              |
| location               | Oregon (*custom tag)                 |
| env                    | Staging (*custom tag)                |
| http.method            | GET                                  |
| http.url               | http://{SERVER_ADDR}/style/{id}/make |
| http.status_code       | 502                                  |
| error                  | true                                 |
| flask.func             | make_shirts                          |

