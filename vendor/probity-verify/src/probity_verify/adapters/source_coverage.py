"""Selected source passages against a stored record using a pinned capture."""

from __future__ import annotations

import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ..common import CaseError, _artifact, _digest, _instant, _normalized, _object, _string

class SourceSelectionUnavailable(CaseError):
    """The pinned source cannot be read under the selected extraction rule."""


class _ArticleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []
        self.article_depth = 0
        self.saw_article = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "article":
            self.article_depth += 1
            self.saw_article = True
        if tag in {"head", "script", "style", "template", "noscript", "svg"}:
            self.hidden.append(tag)
        if self.article_depth:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        if self.article_depth:
            self.parts.append(" ")
        if tag == "article" and self.article_depth:
            self.article_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.article_depth and not self.hidden:
            self.parts.append(data)


def _source_text(data: bytes, source_format: str) -> str:
    raw = _normalized(data, "source artifact")
    if source_format == "text_utf8/v1":
        return raw
    if source_format == "html_article_text/v1":
        parser = _ArticleText()
        parser.feed(data.decode("utf-8"))
        parser.close()
        if not parser.saw_article:
            raise SourceSelectionUnavailable("source artifact has no article element")
        return " ".join(unicodedata.normalize("NFC", "".join(parser.parts)).split())
    raise CaseError("unsupported source_format")


def _decision(status: str, reason: str, **details: Any) -> dict:
    return {"decision": status, "reason": reason, **details}


def adjudicate(case: Any, policy: Any, artifact_root: Path) -> dict:
    """Return a scoped decision. Only a consumer-pinned witness can decide it.

    `contradicted` means a required literal passage occurs in the pinned
    source text and does not occur in the bound record text. It says nothing
    about editorial intent or the source at another time.
    """
    case = _object(case, "case", {"schema_version", "case_id", "artifacts"})
    policy = _object(policy, "policy", {"schema_version", "witnesses", "assessments"})
    schema_pair = (case["schema_version"], policy["schema_version"])
    if schema_pair not in {
        ("probity-case/v1", "probity-policy/v1"),
        ("source-coverage-case/v1", "source-coverage-policy/v1"),
    }:
        raise CaseError("unsupported schema_version")
    case_id = _string(case["case_id"], "case_id")
    artifacts = case["artifacts"]
    if not isinstance(artifacts, dict):
        raise CaseError("artifacts: expected object")
    assessments = policy["assessments"]
    witnesses = policy["witnesses"]
    if not isinstance(assessments, dict) or not isinstance(witnesses, dict):
        raise CaseError("policy assessments and witnesses: expected objects")
    if case_id not in assessments:
        raise CaseError("case_id has no consumer assessment")
    assessment = _object(assessments[case_id], "assessment", {
        "claim_type", "source_witness", "record_artifact", "record_sha256",
        "record_version", "source_window", "required_spans"
    })
    if assessment["claim_type"] != "source_text_coverage/v1":
        raise CaseError("unsupported claim_type")
    witness_id = _string(assessment["source_witness"], "source_witness")
    record_id = _string(assessment["record_artifact"], "record_artifact")
    record_pin = _digest(assessment["record_sha256"], "record_sha256")
    record_version = _string(assessment["record_version"], "record_version")
    if witness_id not in witnesses:
        raise CaseError("source_witness has no consumer pin")
    witness = _object(witnesses[witness_id], "witness", {
        "artifact", "sha256", "captured_at", "source_url", "authority", "source_format"
    })
    source_id = _string(witness["artifact"], "witness.artifact")
    pinned_digest = _digest(witness["sha256"], "witness.sha256")
    observed_at = _instant(witness["captured_at"], "witness.captured_at")
    source_url = _string(witness["source_url"], "witness.source_url")
    authority = _string(witness["authority"], "witness.authority")
    source_format = _string(witness["source_format"], "witness.source_format")
    if source_format not in {"text_utf8/v1", "html_article_text/v1"}:
        raise CaseError("unsupported source_format")
    window = _object(assessment["source_window"], "source_window", {"start", "end"})
    start = _instant(window["start"], "source_window.start")
    end = _instant(window["end"], "source_window.end")
    if end < start:
        raise CaseError("source_window.end precedes start")
    spans = assessment["required_spans"]
    if not isinstance(spans, list) or not spans:
        raise CaseError("required_spans: expected nonempty list")
    seen: set[str] = set()
    checked_spans: list[dict] = []
    for i, item in enumerate(spans):
        span = _object(item, f"required_spans[{i}]", {"id", "text"})
        span_id = _string(span["id"], f"required_spans[{i}].id")
        value = _string(span["text"], f"required_spans[{i}].text")
        if span_id in seen or not " ".join(unicodedata.normalize("NFC", value).split()):
            raise CaseError("required_spans: duplicate id or empty normalized text")
        seen.add(span_id)
        checked_spans.append({"id": span_id, "text": value})
    if source_id not in artifacts or record_id not in artifacts or source_id == record_id:
        raise CaseError("source and record must name distinct case artifacts")

    source, source_check = _artifact(artifact_root, artifacts[source_id], f"artifacts.{source_id}")
    record, record_check = _artifact(artifact_root, artifacts[record_id], f"artifacts.{record_id}")
    scope = {
        "claim_type": "source_text_coverage/v1",
        "source_window": window,
        "witness": {"id": witness_id, "source_url": source_url,
                    "captured_at": witness["captured_at"], "authority": authority,
                    "consumer_pinned_sha256": pinned_digest, "source_format": source_format},
        "artifacts": {source_id: source_check, record_id: record_check},
        "record_pin": {"version": record_version, "consumer_pinned_sha256": record_pin},
        "selected_span_ids": [s["id"] for s in checked_spans],
        "normalization": "Unicode NFC and collapsed whitespace",
        "limit": "Only selected literal passages in these bytes and this source window are judged. The policy's authority statement is not independently verified by this program.",
    }
    result = {"schema_version": "probity-decision/v1", "case_id": case_id,
              "claim_type": "source_text_coverage/v1", "scope": scope, "checks": []}
    if source is None or record is None:
        return {**result, **_decision("not_established", "artifact_unavailable_or_unbound")}
    if record_check["sha256"] != record_pin:
        return {**result, **_decision("not_established", "record_pin_mismatch")}
    if source_check["sha256"] != pinned_digest:
        return {**result, **_decision("not_established", "witness_pin_mismatch")}
    if not start <= observed_at <= end:
        return {**result, **_decision("not_established", "witness_outside_source_window")}

    try:
        source_text = _source_text(source, source_format)
        record_text = _normalized(record, "record artifact")
    except SourceSelectionUnavailable:
        return {**result, **_decision("not_established", "source_selection_unavailable")}
    except CaseError:
        return {**result, **_decision("not_established", "artifact_not_utf8")}
    span_results = []
    for span in checked_spans:
        needle = " ".join(unicodedata.normalize("NFC", span["text"]).split())
        span_results.append({"id": span["id"], "in_source": needle in source_text,
                             "in_record": needle in record_text})
    checks = [{"id": s["id"],
               "status": "unavailable" if not s["in_source"] else
                         "failed" if not s["in_record"] else "met",
               "observations": {"in_source": s["in_source"], "in_record": s["in_record"]}}
              for s in span_results]
    result["checks"] = checks
    if any(not s["in_source"] for s in span_results):
        return {**result, **_decision("not_established", "selected_span_not_in_witness")}
    if any(not s["in_record"] for s in span_results):
        return {**result, **_decision("contradicted", "selected_span_missing_from_record")}
    return {**result, **_decision("supported", "all_selected_spans_present")}
