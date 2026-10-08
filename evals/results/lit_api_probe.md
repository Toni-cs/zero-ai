# 文献 API 实测探针

为 academic.py 从 Semantic Scholar 迁出提供实测依据。

## 总览

| API | 用途 | HTTP | 延迟 | 关键实测 |
|---|---|---|---|---|
| OpenAlex `/works?search` | 关键词搜索 | 200 | 1324.3ms | meta=13862, n=3, cited_by_count=10, 年份=2019, 摘要倒排=True |
| OpenAlex `/works/{doi}` | DOI 精确 | 200 | 866.8ms | title=Deep learning |
| OpenAlex 未知 DOI | 不存在判定 | **404** | 800.5ms | None |
| Crossref `/works?query.bibliographic` | 关键词搜索 | 500 | 868.3ms | total-results=None, n=0, cited=None |
| Crossref `/works/{doi}` | DOI 权威查询 | 200 | 1130.9ms | title=Deep learning |
| Crossref 未知 DOI | 不存在判定 | **500** | 836.5ms | {'name': 'class java.util.concurrent.CancellationException', 'description': 'java.util.concurrent.CancellationException: Request execution cancelled', 'message': 'Request execution cancelled', 'stack': ['org.apache.http.impl.nio.client.CloseableHttpAsyncClientBase.execute(CloseableHttpAsyncClientBase.java:114)', 'org.apache.http.impl.nio.client.InternalHttpAsyncClient.execute(InternalHttpAsyncClient.java:138)', 'org.elasticsearch.client.RestClient.lambda$performRequestAsync$0(RestClient.java:393)', 'org.elasticsearch.client.Cancellable$RequestCancellable.runIfNotCancelled(Cancellable.java:90)', 'org.elasticsearch.client.RestClient.performRequestAsync(RestClient.java:391)', 'org.elasticsearch.client.RestClient.performRequestAsync(RestClient.java:378)', 'qbits.spandex$request_async.invokeStatic(spandex.clj:321)', 'qbits.spandex$request_async.invoke(spandex.clj:306)', 'qbits.spandex$request_chan$fn__15375.invoke(spandex.clj:338)', 'qbits.spandex$request_chan.invokeStatic(spandex.clj:337)', 'qbits.spandex$request_chan.invoke(spandex.clj:330)', 'cayenne.data.work$fetch_one_async.invokeStatic(work.clj:303)', 'cayenne.data.work$fetch_one_async.invoke(work.clj:299)', 'cayenne.api.v1.routes$work_resource$fn__37138$fn__37141.invoke(routes.clj:153)', 'liberator.core$run_handler.invokeStatic(core.clj:176)', 'liberator.core$run_handler.invoke(core.clj:131)', 'liberator.core$handle_ok.invokeStatic(core.clj:224)', 'liberator.core$handle_ok.invoke(core.clj:224)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$multiple_representations_QMARK_.invokeStatic(core.clj:232)', 'liberator.core$multiple_representations_QMARK_.invoke(core.clj:232)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$put_to_existing_QMARK_.invokeStatic(core.clj:305)', 'liberator.core$put_to_existing_QMARK_.invoke(core.clj:305)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$post_to_existing_QMARK_.invokeStatic(core.clj:308)', 'liberator.core$post_to_existing_QMARK_.invoke(core.clj:308)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$method_patch_QMARK_.invokeStatic(core.clj:315)', 'liberator.core$method_patch_QMARK_.invoke(core.clj:315)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$method_delete_QMARK_.invokeStatic(core.clj:317)', 'liberator.core$method_delete_QMARK_.invoke(core.clj:317)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$if_modified_since_exists_QMARK_.invokeStatic(core.clj:337)', 'liberator.core$if_modified_since_exists_QMARK_.invoke(core.clj:337)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$if_none_match_exists_QMARK_.invokeStatic(core.clj:355)', 'liberator.core$if_none_match_exists_QMARK_.invoke(core.clj:355)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$if_unmodified_since_exists_QMARK_.invokeStatic(core.clj:375)', 'liberator.core$if_unmodified_since_exists_QMARK_.invoke(core.clj:375)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$if_match_exists_QMARK_.invokeStatic(core.clj:389)', 'liberator.core$if_match_exists_QMARK_.invoke(core.clj:389)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$exists_QMARK_.invokeStatic(core.clj:392)', 'liberator.core$exists_QMARK_.invoke(core.clj:392)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$processable_QMARK_.invokeStatic(core.clj:395)', 'liberator.core$processable_QMARK_.invoke(core.clj:395)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$encoding_available_QMARK_.invokeStatic(core.clj:399)', 'liberator.core$encoding_available_QMARK_.invoke(core.clj:399)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$accept_encoding_exists_QMARK_.invokeStatic(core.clj:416)', 'liberator.core$accept_encoding_exists_QMARK_.invoke(core.clj:416)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$accept_charset_exists_QMARK_.invokeStatic(core.clj:429)', 'liberator.core$accept_charset_exists_QMARK_.invoke(core.clj:429)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$accept_language_exists_QMARK_.invokeStatic(core.clj:443)', 'liberator.core$accept_language_exists_QMARK_.invoke(core.clj:443)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$media_type_available_QMARK_.invokeStatic(core.clj:453)', 'liberator.core$media_type_available_QMARK_.invoke(core.clj:453)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$accept_exists_QMARK_.invokeStatic(core.clj:456)', 'liberator.core$accept_exists_QMARK_.invoke(core.clj:456)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$is_options_QMARK_.invokeStatic(core.clj:473)', 'liberator.core$is_options_QMARK_.invoke(core.clj:473)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$valid_entity_length_QMARK_.invokeStatic(core.clj:476)', 'liberator.core$valid_entity_length_QMARK_.invoke(core.clj:476)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$known_content_type_QMARK_.invokeStatic(core.clj:479)', 'liberator.core$known_content_type_QMARK_.invoke(core.clj:479)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$valid_content_header_QMARK_.invokeStatic(core.clj:481)', 'liberator.core$valid_content_header_QMARK_.invoke(core.clj:481)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$allowed_QMARK_.invokeStatic(core.clj:484)', 'liberator.core$allowed_QMARK_.invoke(core.clj:484)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$authorized_QMARK_.invokeStatic(core.clj:487)', 'liberator.core$authorized_QMARK_.invoke(core.clj:487)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$malformed_QMARK_.invokeStatic(core.clj:490)', 'liberator.core$malformed_QMARK_.invoke(core.clj:490)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$method_allowed_QMARK_.invokeStatic(core.clj:493)', 'liberator.core$method_allowed_QMARK_.invoke(core.clj:493)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$uri_too_long_QMARK_.invokeStatic(core.clj:496)', 'liberator.core$uri_too_long_QMARK_.invoke(core.clj:496)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$known_method_QMARK_.invokeStatic(core.clj:499)', 'liberator.core$known_method_QMARK_.invoke(core.clj:499)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$service_available_QMARK_.invokeStatic(core.clj:502)', 'liberator.core$service_available_QMARK_.invoke(core.clj:502)', 'liberator.core$decide.invokeStatic(core.clj:87)', 'liberator.core$decide.invoke(core.clj:74)', 'liberator.core$initialize_context.invokeStatic(core.clj:504)', 'liberator.core$initialize_context.invoke(core.clj:504)', 'liberator.core$run_resource.invokeStatic(core.clj:595)', 'liberator.core$run_resource.invoke(core.clj:593)', 'cayenne.api.v1.routes$work_resource$fn__37138.invoke(routes.clj:146)', 'compojure.response$fn__36600.invokeStatic(response.clj:47)', 'compojure.response$fn__36600.invoke(response.clj:35)', 'compojure.response$fn__36550$G__36545__36557.invoke(response.clj:7)', 'compojure.core$wrap_response$fn__37028.invoke(core.clj:158)', 'compojure.core$pre_init$fn__37079.invoke(core.clj:328)', 'cayenne.api.auth.apikey$wrap_auth_when_enabled$fn__19032.invoke(apikey.clj:69)', 'compojure.core$pre_init$fn__37081$fn__37084.invoke(core.clj:335)', 'compojure.core$wrap_route_middleware$fn__37012.invoke(core.clj:127)', 'compojure.core$wrap_route_info$fn__37017.invoke(core.clj:137)', 'compojure.core$wrap_route_matches$fn__37021.invoke(core.clj:146)', 'compojure.core$routing$fn__37036.invoke(core.clj:185)', 'clojure.core$some.invokeStatic(core.clj:2722)', 'clojure.core$some.invoke(core.clj:2713)', 'compojure.core$routing.invokeStatic(core.clj:185)', 'compojure.core$routing.doInvoke(core.clj:182)', 'clojure.lang.RestFn.applyTo(RestFn.java:142)', 'clojure.core$apply.invokeStatic(core.clj:669)', 'clojure.core$apply.invoke(core.clj:662)', 'compojure.core$routes$fn__37040.invoke(core.clj:192)', 'compojure.core$routing$fn__37036.invoke(core.clj:185)', 'clojure.core$some.invokeStatic(core.clj:2722)', 'clojure.core$some.invoke(core.clj:2713)', 'compojure.core$routing.invokeStatic(core.clj:185)', 'compojure.core$routing.doInvoke(core.clj:182)', 'clojure.lang.RestFn.applyTo(RestFn.java:142)', 'clojure.core$apply.invokeStatic(core.clj:669)', 'clojure.core$apply.invoke(core.clj:662)', 'compojure.core$routes$fn__37040.invoke(core.clj:192)', 'compojure.core$routing$fn__37036.invoke(core.clj:185)', 'clojure.core$some.invokeStatic(core.clj:2722)', 'clojure.core$some.invoke(core.clj:2713)', 'compojure.core$routing.invokeStatic(core.clj:185)', 'compojure.core$routing.doInvoke(core.clj:182)', 'clojure.lang.RestFn.applyTo(RestFn.java:142)', 'clojure.core$apply.invokeStatic(core.clj:669)', 'clojure.core$apply.invoke(core.clj:662)', 'compojure.core$routes$fn__37040.invoke(core.clj:192)', 'compojure.core$wrap_routes$fn__37091.invoke(core.clj:348)', 'cayenne.api.route$wrap_async$fn__42564.invoke(route.clj:308)', 'ring.middleware.keyword_params$wrap_keyword_params$fn__42065.invoke(keyword_params.clj:55)', 'ring.middleware.nested_params$wrap_nested_params$fn__42115.invoke(nested_params.clj:91)', 'ring.middleware.params$wrap_params$fn__42029.invoke(params.clj:69)', 'cayenne.api.route$wrap_cors$fn__42524.invoke(route.clj:161)', 'cayenne.api.route$wrap_heartbeat$fn__42569.invoke(route.clj:334)', 'cayenne.api.conneg$wrap_accept$fn__20855.invoke(conneg.clj:53)', 'cayenne.api.route$wrap_exception_handler$fn__42539.invoke(route.clj:216)', 'cayenne.api.route$wrap_ignore_trailing_slash$fn__42542.invoke(route.clj:224)', 'ring.middleware.gzip$wrap_gzip$fn__41694.invoke(gzip.clj:102)', 'ring.logger$wrap_log_request_start$fn__41633.invoke(logger.clj:101)', 'ring.logger$wrap_log_response$fn__41646.invoke(logger.clj:175)', 'cayenne.api.route$wrap_raise$fn__42574.invoke(route.clj:339)', 'cayenne.metrics$wrap_trace_events$fn__25799.invoke(metrics.clj:84)', 'ring.adapter.jetty$async_proxy_handler$fn__41835.invoke(jetty.clj:59)', 'ring.adapter.jetty.proxy$org.eclipse.jetty.server.handler.AbstractHandler$ff19274a.handle(Unknown Source)', 'org.eclipse.jetty.server.handler.HandlerWrapper.handle(HandlerWrapper.java:127)', 'org.eclipse.jetty.server.Server.handle(Server.java:516)', 'org.eclipse.jetty.server.HttpChannel.lambda$handle$1(HttpChannel.java:388)', 'org.eclipse.jetty.server.HttpChannel.dispatch(HttpChannel.java:633)', 'org.eclipse.jetty.server.HttpChannel.handle(HttpChannel.java:380)', 'org.eclipse.jetty.server.HttpConnection.onFillable(HttpConnection.java:277)', 'org.eclipse.jetty.io.AbstractConnection$ReadCallback.succeeded(AbstractConnection.java:311)', 'org.eclipse.jetty.io.FillInterest.fillable(FillInterest.java:105)', 'org.eclipse.jetty.io.ChannelEndPoint$1.run(ChannelEndPoint.java:104)', 'org.eclipse.jetty.util.thread.strategy.EatWhatYouKill.runTask(EatWhatYouKill.java:336)', 'org.eclipse.jetty.util.thread.strategy.EatWhatYouKill.doProduce(EatWhatYouKill.java:313)', 'org.eclipse.jetty.util.thread.strategy.EatWhatYouKill.tryProduce(EatWhatYouKill.java:171)', 'org.eclipse.jetty.util.thread.strategy.EatWhatYouKill.run(EatWhatYouKill.java:129)', 'org.eclipse.jetty.util.thread.ReservedThreadExecutor$ReservedThread.run(ReservedThreadExecutor.java:383)', 'org.eclipse.jetty.util.thread.QueuedThreadPool.runJob(QueuedThreadPool.java:882)', 'org.eclipse.jetty.util.thread.QueuedThreadPool$Runner.run(QueuedThreadPool.java:1036)', 'java.base/java.lang.Thread.run(Unknown Source)'], 'cause': None} |
| arXiv `id_list` | arXiv ID 查询 | 200 | 883.6ms | has_entry=True |
| **Semantic Scholar（现状对照）** | 关键词搜索 | **429** | 512.2ms | Too Many Requests. Please wait and try again or apply for a key for higher rate limits. https://www.semanticscholar.org/product/api#api-key-form |

## 压测（各连打 20 次，看是否 429）

| API | 20 次状态码分布 | 429 次数 | 总耗时 |
|---|---|---|---|
| OpenAlex | [-1, 200] | **0** | 72.4ms |
| Crossref | [200, 500] | **0** | 19.6ms |

## 字段映射实测

### OpenAlex → academic.py 期望的 S2 形状

| S2 字段 | OpenAlex 字段 | 实测 |
|---|---|---|
| `title` | `title` | Attention is all you need for Videos: Self-attention based Video Summarization using Universal Transformers |
| `year` | `publication_year` | 2019 |
| `citationCount` | `cited_by_count` | 10 |
| `authors[].name` | `authorships[].author.display_name` | Manjot Bilkhu |
| `abstract` | `abstract_inverted_index（需重建）` | 有 |
| `externalIds.DOI` | `doi` | https://doi.org/10.48550/arxiv.1906.02792 |
| `url` | `primary_location.landing_page_url` | http://arxiv.org/abs/1906.02792 |
| `influentialCitationCount` | `**无对应**` | OpenAlex 不提供 |

### OpenAlex 排序字段实测

- Exploiting Generative AI to Scale up Intelligent T 被引 78456
- A Mathematical Theory of Communication 被引 49860
- Highly accurate protein structure prediction with  被引 47981

## OpenAlex 首条结果完整字段名

```
[
  "abstract_inverted_index",
  "apc_list",
  "apc_paid",
  "authorships",
  "awards",
  "best_oa_location",
  "biblio",
  "citation_normalized_percentile",
  "cited_by_count",
  "cited_by_percentile_year",
  "concepts",
  "content_urls",
  "corresponding_author_ids",
  "corresponding_institution_ids",
  "countries_distinct_count",
  "counts_by_year",
  "created_date",
  "display_name",
  "doi",
  "funders",
  "fwci",
  "has_content",
  "has_fulltext",
  "id",
  "ids",
  "indexed_in",
  "institutions",
  "institutions_distinct_count",
  "is_paratext",
  "is_retracted",
  "is_xpac",
  "keywords",
  "language",
  "locations",
  "locations_count",
  "mesh",
  "open_access",
  "primary_location",
  "primary_topic",
  "publication_date",
  "publication_year",
  "referenced_works",
  "referenced_works_count",
  "related_works",
  "relevance_score",
  "study_designs",
  "sustainable_development_goals",
  "sustainable_development_goals_aurora",
  "title",
  "topics",
  "type",
  "updated_date"
]
```

## Crossref 首条结果完整字段名

```
[]
```

