# 2. Acquire from the PMC Open Access dataset on AWS, not the OA Web Service

Status: accepted
Date verified: 2026-08-01

## Context

The obvious route to PMC full text and figures is the OA Web Service
(`https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi`), which returns a link to a
`.tar.gz` package per article. That is what most documentation and most prior
art still describes.

It no longer works, and it is being switched off.

Verified directly while building this:

* `oa.fcgi` still answers, and still hands out `ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/…`
  links — but those links are dead. Fetching one returns FTP 550; the `https://`
  equivalent returns 404.
* `https://ftp.ncbi.nlm.nih.gov/pub/pmc/` now contains only `deprecated/`,
  `PMC-ids.csv.gz` and `readme.txt`. The readme states that all legacy PMC
  Article Dataset files were moved to `deprecated/` and will be **removed in
  August 2026**.
* NLM's own notice (30 July 2026) says that on or after **24 August 2026** the
  FTP Service files, **the PMC OA Web Service API**, and the legacy Cloud
  Service files "will no longer be available".

So building on `oa.fcgi` would mean building on an API with weeks to live and a
package link that is already broken.

## Decision

Acquire from the PMC Open Access dataset on AWS: the world-readable S3 bucket
`pmc-oa-opendata` in `us-east-1`, over plain HTTPS with no credentials.

Per article version, under the prefix `PMC<id>.<version>/`:

| Object | Use |
|---|---|
| `PMC<id>.<v>.xml` | JATS full text |
| `PMC<id>.<v>.json` | metadata: DOI, PMID, licence code, retraction flag, OA status |
| figure/media files | figures at deposit resolution, named as the JATS `graphic/@xlink:href` |

The article version is discovered by listing the bucket with
`?list-type=2&prefix=PMC<id>.`, which in one request yields both the available
versions and the full file inventory.

## Consequences

* No dependency on a service scheduled for removal.
* The metadata JSON gives licence code, open-access status and a retraction flag
  before the XML is parsed, so retracted and non-OA articles are identified
  cheaply and reported precisely.
* `graphic/@xlink:href` maps 1:1 onto object names, so figures need no guessing.
* NLM's terms require acknowledging NLM as the source of the data and forbid use
  of the PMC wordmark or logo. The colophon does the former; the edition uses no
  publisher or archive branding at all.

The other services used — Crossref for volume enumeration, the PMC ID Converter
for DOI→PMCID — are unaffected. Note that the ID Converter also moved: the old
`/pmc/utils/idconv/v1.0/` path now 301-redirects to
`https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/`.
