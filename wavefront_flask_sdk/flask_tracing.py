"""
Wavefront Flask Middleware.

@author: Hao Song (songhao@vmware.com)
"""
import math
import time
from timeit import default_timer

from flask import _request_ctx_stack as stack

import opentracing
from opentracing.ext import tags

from wavefront_pyformance.delta import delta_counter
from wavefront_pyformance.tagged_registry import TaggedRegistry
from wavefront_pyformance.wavefront_histogram import wavefront_histogram

from wavefront_sdk.common import HeartbeaterService

from .constants import FLASK_COMPONENT, NULL_TAG_VAL, REPORTER_PREFIX, \
    REQUEST_PREFIX, RESPONSE_PREFIX, WAVEFRONT_PROVIDED_SOURCE


# pylint: disable=invalid-name, too-many-instance-attributes
class FlaskTracing(opentracing.Tracer):
    """Wavefront Flask Middleware."""

    # pylint: disable=too-many-arguments, unused-variable
    def __init__(self, tracer=None, reporter=None, application_tags=None,
                 trace_all_requests=None, app=None, traced_attributes=None,
                 start_span_cb=None):
        """Construct Wavefront Flask Middleware.

        :param tracer: Tracer
        :param reporter: WavefrontReporter
        :param application_tags: ApplicationTags
        :param trace_all_requests: Trace all requests or not
        :param app: Flask app
        :param traced_attributes: Traced attributes
        """
        super().__init__()
        self._app = app
        self.reg = TaggedRegistry()
        self.reporter = reporter
        self.application_tags = application_tags
        self.reporter.prefix = REPORTER_PREFIX
        self.reporter.registry = self.reg
        self.reporter.start()
        self.heartbeater_service = HeartbeaterService(
            wavefront_client=self.reporter.wavefront_client,
            application_tags=self.application_tags,
            components=FLASK_COMPONENT,
            source=self.reporter.source)

        self.APPLICATION = self.application_tags.application or NULL_TAG_VAL
        self.CLUSTER = self.application_tags.cluster or NULL_TAG_VAL
        self.SERVICE = self.application_tags.service or NULL_TAG_VAL
        self.SHARD = self.application_tags.shard or NULL_TAG_VAL
        self.reporter.prefix = REPORTER_PREFIX

        if traced_attributes is None:
            traced_attributes = []
        if start_span_cb is not None and not callable(start_span_cb):
            raise ValueError('start_span_cb is not callable')

        # tracing all requests requires that app != None
        if trace_all_requests is True and app is None:
            raise ValueError('trace_all_requests=True requires an app object')

        if trace_all_requests is None:
            trace_all_requests = False if app is None else True

        if not callable(tracer):
            self.__tracer = tracer
            self.__tracer_getter = None
        else:
            self.__tracer = None
            self.__tracer_getter = tracer

        self._trace_all_requests = trace_all_requests
        self._start_span_cb = start_span_cb
        self._current_scopes = {}

        if self._trace_all_requests:
            @app.before_request
            def start_trace():
                """Pre-process request."""
                self._before_request_fn(traced_attributes)

            @app.after_request
            def end_trace(response):
                """Post-process request."""
                self._after_request_fn(response)
                return response

            @app.teardown_request
            def end_trace_with_error(error):
                """Process error response."""
                if error is not None:
                    self._after_request_fn(error=error)

    @property
    def tracer(self):
        """Get the tracer."""
        if not self.__tracer:
            if self.__tracer_getter is None:
                return opentracing.tracer

            self.__tracer = self.__tracer_getter()

        return self.__tracer

    def trace(self, *attributes):
        """Decorate the functions with tracing.

        NOTE: @tracing.trace must be placed after the @app.route decorator.

        :param attributes: Set flask.Request attributes as tags of the span.
        """
        def decorator(f):
            """Decorate the functions with tracing."""
            def wrapper(*args, **kwargs):
                """Wrap the decorator function."""
                if self._trace_all_requests:
                    return f(*args, **kwargs)

                self._before_request_fn(list(attributes))
                try:
                    r = f(*args, **kwargs)
                    self._after_request_fn()
                except Exception as e:
                    self._after_request_fn(error=e)
                    raise

                self._after_request_fn()
                return r

            wrapper.__name__ = f.__name__
            return wrapper

        return decorator

    def get_span(self, request=None):
        """Return the span tracing `request`, or the current request.

        :param request: the request to get the span from
        """
        if request is None and stack.top:
            request = stack.top.request

        scope = self._current_scopes.get(request, None)
        return None if scope is None else scope.span

    def _before_request_fn(self, attributes):
        """Pre-process request."""
        request = stack.top.request
        operation_name = request.endpoint
        headers = {}
        for k, v in request.headers:
            headers[k.lower()] = v

        request.environ['_wf_start_timestamp'] = default_timer()
        request.environ['_wf_cpu_nanos'] = time.clock()

        entity_name = (request.url_rule.rule or operation_name). \
            replace('-', '_').replace('/', '.').replace('{', '_'). \
            replace('}', '_').replace('<', '').replace('>', '').lstrip('.')

        self.update_gauge(
            registry=self.reg,
            key=self.get_metric_name(entity_name, request) + ".inflight",
            tags=self.get_tags_map(func_name=operation_name),
            val=1
        )
        self.update_gauge(
            registry=self.reg,
            key="total_requests.inflight",
            tags=self.get_tags_map(
                cluster=self.CLUSTER,
                service=self.SERVICE,
                shard=self.SHARD),
            val=1
        )
        try:
            span_ctx = self.tracer.extract(opentracing.Format.HTTP_HEADERS,
                                           headers)
            scope = self.tracer.start_active_span(operation_name,
                                                  child_of=span_ctx)
        except (opentracing.InvalidCarrierException,
                opentracing.SpanContextCorruptedException):
            scope = self.tracer.start_active_span(operation_name)

        self._current_scopes[request] = scope

        span = scope.span
        span.set_tag("component", FLASK_COMPONENT)
        span.set_tag('http.method', request.method)
        span.set_tag('http.url', request.base_url)
        span.set_tag('span.kind', 'server')
        span.set_tag("flask.func", operation_name)

        for attr in attributes:
            if hasattr(request, attr):
                payload = str(getattr(request, attr))
                if payload:
                    span.set_tag(attr, payload)

        self._call_start_span_cb(span, request)

    # pylint: disable=too-many-locals, too-many-statements
    def _after_request_fn(self, response=None, error=None):
        """Post-process request."""
        request = stack.top.request

        # the pop call can fail if the request is interrupted by a
        # `before_request` method so we need a default
        scope = self._current_scopes.pop(request, None)
        if scope is not None:
            if response is not None:
                scope.span.set_tag('http.status_code', response.status_code)
            if 400 <= response.status_code <= 599 or error is not None:
                scope.span.set_tag('error', 'true')
                scope.span.log_kv({
                    'event': tags.ERROR,
                    'error.object': error,
                })
            scope.close()

        operation_name = request.endpoint
        entity_name = (request.url_rule.rule or operation_name). \
            replace('-', '_').replace('/', '.').replace('{', '_'). \
            replace('}', '_').replace('<', '').replace('>', '').lstrip('.')

        self.update_gauge(
            registry=self.reg,
            key=self.get_metric_name(entity_name, request) + ".inflight",
            tags=self.get_tags_map(func_name=operation_name),
            val=-1
        )
        self.update_gauge(
            registry=self.reg,
            key="total_requests.inflight",
            tags=self.get_tags_map(
                cluster=self.CLUSTER,
                service=self.SERVICE,
                shard=self.SHARD),
            val=-1
        )

        response_metric_key = self.get_metric_name(entity_name, request,
                                                   response)

        complete_tags_map = self.get_tags_map(
            cluster=self.CLUSTER,
            service=self.SERVICE,
            shard=self.SHARD,
            func_name=operation_name
        )

        aggregated_per_shard_map = self.get_tags_map(
            cluster=self.CLUSTER,
            service=self.SERVICE,
            shard=self.SHARD,
            func_name=operation_name,
            source=WAVEFRONT_PROVIDED_SOURCE)

        overall_aggregated_per_source_map = self.get_tags_map(
            cluster=self.CLUSTER,
            service=self.SERVICE,
            shard=self.SHARD)

        overall_aggregated_per_shard_map = self.get_tags_map(
            cluster=self.CLUSTER,
            service=self.SERVICE,
            shard=self.SHARD,
            source=WAVEFRONT_PROVIDED_SOURCE)

        aggregated_per_service_map = self.get_tags_map(
            cluster=self.CLUSTER,
            service=self.SERVICE,
            func_name=operation_name,
            source=WAVEFRONT_PROVIDED_SOURCE)

        overall_aggregated_per_service_map = self.get_tags_map(
            cluster=self.CLUSTER,
            service=self.SERVICE,
            source=WAVEFRONT_PROVIDED_SOURCE)

        aggregated_per_cluster_map = self.get_tags_map(
            cluster=self.CLUSTER,
            func_name=operation_name,
            source=WAVEFRONT_PROVIDED_SOURCE)

        overall_aggregated_per_cluster_map = self.get_tags_map(
            cluster=self.CLUSTER,
            source=WAVEFRONT_PROVIDED_SOURCE)

        aggregated_per_application_map = self.get_tags_map(
            func_name=operation_name,
            source=WAVEFRONT_PROVIDED_SOURCE
        )

        overall_aggregated_per_application_map = self.get_tags_map(
            source=WAVEFRONT_PROVIDED_SOURCE)

        # flask.server.response.style._id_.make.GET.200.cumulative.count
        # flask.server.response.style._id_.make.GET.200.aggregated_per_shard.count
        # flask.server.response.style._id_.make.GET.200.aggregated_per_service.count
        # flask.server.response.style._id_.make.GET.200.aggregated_per_cluster.count
        # flask.server.response.style._id_.make.GET.200.aggregated_per_application.count
        # flask.server.response.style._id_.make.GET.errors
        self.reg.counter(response_metric_key + ".cumulative",
                         tags=complete_tags_map).inc()
        if self.application_tags.shard:
            delta_counter(
                self.reg, response_metric_key + ".aggregated_per_shard",
                tags=aggregated_per_shard_map).inc()
        delta_counter(
            self.reg, response_metric_key + ".aggregated_per_service",
            tags=aggregated_per_service_map).inc()
        if self.application_tags.cluster:
            delta_counter(
                self.reg, response_metric_key + ".aggregated_per_cluster",
                tags=aggregated_per_cluster_map).inc()
        delta_counter(
            self.reg, response_metric_key + ".aggregated_per_application",
            tags=aggregated_per_application_map).inc()

        # flask.server.response.errors.aggregated_per_source.count
        # flask.server.response.errors.aggregated_per_shard.count
        # flask.server.response.errors.aggregated_per_service.count
        # flask.server.response.errors.aggregated_per_cluster.count
        # flask.server.response.errors.aggregated_per_application.count
        if self.is_error_status_code(response):
            self.reg.counter(
                self.get_metric_name_without_status(entity_name, request),
                tags=complete_tags_map).inc()
            self.reg.counter("response.errors", tags=complete_tags_map).inc()
            self.reg.counter("response.errors.aggregated_per_source",
                             tags=overall_aggregated_per_source_map).inc()
            if self.application_tags.shard:
                delta_counter(self.reg, "response.errors.aggregated_per_shard",
                              tags=overall_aggregated_per_shard_map).inc()
            delta_counter(self.reg, "response.errors.aggregated_per_service",
                          tags=overall_aggregated_per_service_map).inc()
            if self.application_tags.cluster:
                delta_counter(self.reg,
                              "response.errors.aggregated_per_cluster",
                              tags=overall_aggregated_per_cluster_map).inc()
            delta_counter(self.reg,
                          "response.errors.aggregated_per_application",
                          tags=overall_aggregated_per_application_map).inc()

        # flask.server.response.completed.aggregated_per_source.count
        # flask.server.response.completed.aggregated_per_shard.count
        # flask.server.response.completed.aggregated_per_service.count
        # flask.server.response.completed.aggregated_per_cluster.count
        # flask.server.response.completed.aggregated_per_application.count
        self.reg.counter("response.completed.aggregated_per_source",
                         tags=overall_aggregated_per_source_map).inc()
        if self.SHARD is not NULL_TAG_VAL:
            delta_counter(
                self.reg, "response.completed.aggregated_per_shard",
                tags=overall_aggregated_per_shard_map).inc()
            self.reg.counter("response.completed.aggregated_per_service",
                             tags=overall_aggregated_per_service_map).inc()
        if self.CLUSTER is not NULL_TAG_VAL:
            delta_counter(
                self.reg, "response.completed.aggregated_per_cluster",
                tags=overall_aggregated_per_cluster_map).inc()
            self.reg.counter("response.completed.aggregated_per_application",
                             tags=overall_aggregated_per_application_map).inc()

        # flask.server.response.style._id_.make.summary.GET.200.latency.m
        # flask.server.response.style._id_.make.summary.GET.200.cpu_ns.m
        # flask.server.response.style._id_.make.summary.GET.200.total_time.count
        wf_start_timestamp = request.environ.get('_wf_start_timestamp')
        wf_cpu_nanos = request.environ.get('_wf_cpu_nanos')
        if wf_start_timestamp:
            timestamp_duration = default_timer() - wf_start_timestamp
            wavefront_histogram(self.reg, response_metric_key + ".latency",
                                tags=complete_tags_map).add(timestamp_duration)
            self.reg.counter(response_metric_key + ".total_time",
                             tags=complete_tags_map).inc(timestamp_duration)
        if wf_cpu_nanos:
            cpu_nanos_duration = time.clock() - wf_cpu_nanos
            wavefront_histogram(self.reg, response_metric_key + ".cpu_ns",
                                tags=complete_tags_map).add(cpu_nanos_duration)

    def _call_start_span_cb(self, span, request):
        if self._start_span_cb is None:
            return

        try:
            self._start_span_cb(span, request)
        except Exception:
            pass

    @staticmethod
    def update_gauge(registry, key, tags, val):
        """Update gauge value.

        :param registry: TaggedRegistry from pyformance.
        :param key: Key of the gauge.
        :param tags: Tags of the gauge.
        :param val: Value of the gauge.
        """
        gauge = registry.gauge(key=key, tags=tags)
        cur_val = gauge.get_value()
        if math.isnan(cur_val):
            cur_val = 0
        gauge.set_value(cur_val + val)

    @staticmethod
    def get_metric_name(entity_name, request, response=None):
        """Get metric name.

        :param entity_name: Entity Name.
        :param request: Http request.
        :param response: Response obj.
        :return: Metric name.
        """
        metric_name = [entity_name, request.method]
        if response:
            metric_name.insert(0, RESPONSE_PREFIX)
            metric_name.append(str(response.status_code))
        else:
            metric_name.insert(0, REQUEST_PREFIX)
        return '.'.join(metric_name)

    @staticmethod
    def get_entity_name(request):
        """Get entity name from the request.

        :param request: Http request.
        :return: Entity name.
        """
        resolver_match = request.resolver_match
        if resolver_match:
            entity_name = resolver_match.url_name
            if not entity_name:
                entity_name = resolver_match.view_name
            entity_name = entity_name.replace('-', '_').replace('/', '.'). \
                replace('{', '_').replace('}', '_')
        else:
            entity_name = 'UNKNOWN'
        return entity_name.lstrip('.').rstrip('.')

    # pylint: disable=too-many-arguments
    def get_tags_map(self, cluster=None, service=None, shard=None,
                     func_name=None, source=None):
        """Get tags of span as dict.

        :param cluster: Cluster from application tags.
        :param service: Service from application tags.
        :param shard: Shard from application tags.
        :param func_name: Name of flask func
        :param source: Name of source.
        :return: tags of span.
        """
        tags_map = {'application': self.APPLICATION}
        if cluster:
            tags_map['cluster'] = cluster
        if service:
            tags_map['service'] = service
        if shard:
            tags_map['shard'] = shard
        if func_name:
            tags_map['flask.func'] = func_name
        if source:
            tags_map['source'] = source
        return tags_map

    @staticmethod
    def get_metric_name_without_status(entity_name, request):
        """Get metric name w/o response.

        :param entity_name: Entity Name.
        :param request: Http request.
        :return: Metric name
        """
        metric_name = [entity_name, request.method]
        metric_name.insert(0, REQUEST_PREFIX)
        return '.'.join(metric_name)

    @staticmethod
    def is_error_status_code(response):
        """Check is response status code is error or not.

        :param response: Response obj
        :return: Is error response code or not.
        """
        return 400 <= response.status_code <= 599
