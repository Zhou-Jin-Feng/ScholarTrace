"""Synthetic responses shared by adapter and service integration checks."""

import json
from xml.sax.saxutils import escape

import httpx
from a2_core_fixtures import OfflineResearch


def research_responder(requests, lost_response=None, *, verification_status="supported",
                       synthesis_inputs=None):
    backend = OfflineResearch()
    verification_index = 0
    def respond(request):
        nonlocal verification_index
        requests.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == lost_response:
            raise httpx.ReadTimeout("synthetic dispatched response loss")
        if request.url.host == "export.arxiv.org":
            entries = []
            for case in backend.cases:
                p = case["paper"]
                entries.append(
                    f"<entry><id>http://arxiv.org/abs/{p['arxiv_id']}v1</id>"
                    f"<title>{escape(p['title'])}</title>"
                    f"<published>{p['publication_year']}-01-01T00:00:00Z</published>"
                    f"<doi xmlns='http://arxiv.org/schemas/atom'>{escape(p['doi'])}</doi>"
                    "<author><name>Researcher</name></author>"
                    "<summary>Retrieval signals and classifications</summary></entry>"
                )
            return httpx.Response(
                200,
                text='<feed xmlns="http://www.w3.org/2005/Atom">' + "".join(entries) + "</feed>",
            )
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            user = json.loads(body["messages"][1]["content"])
            if "allowed_evidence_ids" in user:
                if synthesis_inputs is not None:
                    synthesis_inputs.append(user)
                draft = {
                    "title": "Verified retrieval findings",
                    "answer_status": "answered",
                    "findings": [
                        {
                            "claim": "Retrieval depends on the evaluated setting.",
                            "evidence_ids": user["allowed_evidence_ids"][:1],
                            "support_status": "supported",
                        }
                    ],
                    "limitations": ["Synthetic engineering input only."],
                }
            else:
                status = verification_status
                if status == "mixed":
                    status = ("supported", "partially_supported", "unsupported")[verification_index]
                verification_index += 1
                draft = {
                    "status": status,
                    "reason": "Synthetic entailment response",
                    "recommended_action": (
                        "remove" if status == "unsupported" else "keep"
                    ),
                }
            return httpx.Response(
                200,
                json={
                    "model": "synthetic",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                    "choices": [{"message": {"content": json.dumps(draft)}}],
                },
            )
        response = backend.transport(request)
        if request.url.path == "/api/chat":
            body = response.json()
            body.update(model="local-synthetic", done=True, total_duration=1000)
            return httpx.Response(200, json=body)
        return response

    return respond
